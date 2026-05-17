# Architecture — cours-generator MVP SaaS

## Vue d'ensemble

```
┌─────────────────────────────────────────────────────────────────┐
│  Browser                                                        │
│  GET /   → upload form                                          │
│  POST /upload → redirige vers /progress/<id>                    │
│  GET /stream/<id> (SSE) → logs temps réel                       │
│  GET /preview/<id>/ → site généré local                         │
└──────────────┬──────────────────────────────────────────────────┘
               │ HTTP
┌──────────────▼──────────────────────────────────────────────────┐
│  app.py — Flask (couche HTTP mince)                             │
│  • Valide uploads                                               │
│  • Crée JobRecord → storage.get_backend().create_job()          │
│  • Lance worker.pipeline.run() dans un thread daemon            │
│  • SSE via queue.Queue en mémoire                               │
│  • API JSON : GET /api/jobs, GET /api/jobs/<id>/model           │
│  • POST /api/jobs/<id>/rerender (re-rendu sans LLM)             │
└──────┬────────────────────────────────────┬─────────────────────┘
       │                                    │
┌──────▼──────────────────┐    ┌────────────▼──────────────────────┐
│  worker/pipeline.py     │    │  storage/                         │
│  Orchestrateur          │    │  base.py  → interface StorageBackend│
│  ┌─────────────────┐    │    │  local.py → SQLite + filesystem    │
│  │ extractor.py    │    │    │  supabase.py → STUB (à câbler)    │
│  │ PyMuPDF         │    │    └───────────────────────────────────┘
│  ├─────────────────┤    │
│  │ notebooklm.py   │    │
│  │ CLI adapter     │    │
│  ├─────────────────┤    │
│  │ structurer.py   │◄───┼── NOUVEAU : LLM → JSON (pas HTML)
│  │ Gemini → JSON   │    │
│  ├─────────────────┤    │
│  │ renderer.py     │◄───┼── NOUVEAU : JSON → HTML via Jinja2
│  │ Jinja2 template │    │
│  └─────────────────┘    │
└─────────────────────────┘
```

## Arborescence

```
cours-generator/
├── app.py                    # Serveur Flask (HTTP uniquement)
├── cours_generator.py        # CLI legacy (conservé, git_push supprimé)
├── requirements.txt
├── .env                      # Variables d'environnement (non versionné)
├── jobs.db                   # SQLite (auto-créé, non versionné)
│
├── core/                     # Domaine partagé
│   ├── config.py             # Variables d'env centralisées
│   └── models.py             # CourseModel, JobRecord, Section, Formula...
│
├── worker/                   # Pipeline de génération
│   ├── pipeline.py           # Orchestrateur principal
│   ├── extractor.py          # Extraction PDF (PyMuPDF)
│   ├── notebooklm.py         # Adaptateur CLI NotebookLM
│   ├── structurer.py         # LLM → CourseModel JSON  ← clé de voûte
│   └── renderer.py           # CourseModel → HTML (Jinja2)
│
├── storage/                  # Backends de stockage pluggables
│   ├── base.py               # Interface abstraite
│   ├── local.py              # SQLite + filesystem (MVP actuel)
│   └── supabase.py           # Supabase (stub, prêt à câbler)
│
├── templates/
│   ├── course/
│   │   └── base.html         # Template Jinja2 du site de cours
│   ├── index.html            # UI upload Flask
│   └── progress.html         # UI suivi de progression Flask
│
├── migrations/
│   └── 001_initial.sql       # Schéma Supabase (tables + RLS)
│
├── docs/
│   └── ARCHITECTURE.md       # Ce fichier
│
├── output/                   # Fichiers générés (gitignored pour les médias)
│   └── <slug>/
│       ├── index.html         # Site HTML généré
│       ├── course_model.json  # CourseModel JSON (pivot éditable)
│       ├── <slug>_cours.pdf
│       ├── <slug>_podcast.m4a
│       └── Presentation/
│           └── <slug>_presentation_page{N}.pdf
└── uploads/                  # PDFs uploadés temporaires
    └── <job_id>/
```

## Flux de données

```
PDF source
  │
  ▼ extractor.extract_text()
texte brut (string)
  │
  ▼ structurer.structure_course() — appel Gemini
CourseModel (JSON)
  │                    │
  │                    └──→ course_model.json (persisté → éditable)
  ▼ renderer.render_course()
index.html (Jinja2 template)
  │
  ▼ storage.save_file() [local: no-op / supabase: upload]
URL publique ou /preview/<id>/
```

## Changement fondamental : JSON intermédiaire

### Avant (prototype)
```
PDF text → Gemini → HTML libre (une seule étape, fragile)
```
Problèmes :
- HTML variable selon la humeur du LLM
- Impossible d'éditer le contenu sans re-générer
- Style potentiellement différent à chaque appel
- Non testable

### Maintenant (architecture cible)
```
PDF text → Gemini → CourseModel JSON → Jinja2 → HTML contrôlé
```
Avantages :
- JSON validable et éditable manuellement
- Re-rendu HTML sans LLM (`POST /api/jobs/<id>/rerender`)
- Style cohérent (template fixe)
- Versioning possible du contenu (table `course_versions`)
- Découple extraction / structuration / présentation

## Supabase — plan de migration

### Phase 1 (actuelle) : local
```
STORAGE_BACKEND=local
→ SQLite pour les jobs
→ Filesystem local pour les fichiers
→ Pas d'auth
```

### Phase 2 : Supabase DB + Auth
1. Créer projet Supabase
2. Exécuter `migrations/001_initial.sql`
3. `pip install supabase`
4. Configurer `.env` :
   ```
   SUPABASE_URL=https://<project>.supabase.co
   SUPABASE_SERVICE_KEY=<service_role>
   STORAGE_BACKEND=supabase
   ```
5. Implémenter `storage/supabase.py` (stubs annotés)
6. Activer Supabase Auth dans `app.py` (middleware à ajouter)

### Phase 3 : Supabase Storage
- Uploader les PDFs sources dans bucket `sources`
- Uploader les artifacts (audio, slides) dans bucket `assets`
- `save_file()` dans `SupabaseBackend` retourne une URL publique

### Phase 4 : Worker découplé
- Remplacer `threading.Thread` par une queue (Redis/Celery ou Supabase Edge Functions)
- Le worker Python tourne sur un serveur dédié (Railway, Render, etc.)
- NotebookLM reste local (contrainte : cookies Google)

## Contrainte NotebookLM

**NotebookLM ne peut pas tourner sur un serveur cloud.**
La CLI `notebooklm-py` utilise les cookies de session Google du navigateur local.
→ La génération audio/slides doit toujours être déclenchée depuis la machine de l'utilisateur.

Solution possible à terme :
- L'utilisateur génère slides+audio sur sa machine (notebooklm local)
- Il uploade les fichiers manuellement dans l'interface
- Le pipeline cloud prend en charge structuration + rendu + publication

## Variables d'environnement

| Variable | Requis | Description |
|---|---|---|
| `GEMINI_API_KEY` | ✅ | Clé Google AI Studio |
| `GEMINI_MODEL` | non | Défaut: `gemini-2.0-flash` |
| `STORAGE_BACKEND` | non | `local` (défaut) ou `supabase` |
| `SUPABASE_URL` | si supabase | URL du projet Supabase |
| `SUPABASE_SERVICE_KEY` | si supabase | Clé service_role |
| `GITHUB_TOKEN` | non | Pour publication GitHub Pages |
| `GITHUB_OWNER` | non | Propriétaire du repo GitHub |
| `GITHUB_REPO` | non | Nom du repo GitHub |
| `PORT` | non | Port Flask (défaut: 5000) |
| `DEBUG` | non | Mode debug Flask (défaut: false) |

## Ce qui fonctionne (MVP local)

- ✅ Upload PDF via interface web
- ✅ Extraction texte (PyMuPDF)
- ✅ Structuration JSON par Gemini (`GEMINI_API_KEY` requis)
- ✅ Rendu HTML via Jinja2 (template fixe, cohérent)
- ✅ Persistance des jobs en SQLite (survit aux redémarrages)
- ✅ Suivi en temps réel via SSE
- ✅ Prévisualisation locale (`/preview/<id>/`)
- ✅ Re-rendu sans LLM (`POST /api/jobs/<id>/rerender`)
- ✅ CourseModel JSON éditable (`GET /api/jobs/<id>/model`)
- ✅ NotebookLM slides + audio (si `notebooklm-py` auth OK)

## Ce qui reste à brancher

- ⏳ Supabase Auth (login/signup)
- ⏳ Supabase DB (remplacer SQLite)
- ⏳ Supabase Storage (remplacer filesystem local)
- ⏳ Publication automatique (GitHub Pages, Vercel, ou Supabase Storage public)
- ⏳ Édition manuelle du CourseModel via UI
- ⏳ Worker découplé (Celery/RQ/Supabase Edge Functions)
- ⏳ Multi-utilisateurs + RLS Supabase
