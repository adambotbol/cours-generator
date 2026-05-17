"""Orchestrateur du pipeline de génération.

Flux complet :
  PDF source
    → extraction texte (extractor.py)
    → [optionnel] NotebookLM : génération audio + slides (notebooklm.py)
    → structuration JSON via LLM (structurer.py)  ← NOUVEAU
    → rendu HTML via template Jinja2 (renderer.py) ← NOUVEAU
    → persistance locale (storage/local.py)
    → [optionnel] publication GitHub Pages

Ce module est indépendant de Flask.  Il peut être appelé :
- depuis app.py dans un thread daemon (mode actuel)
- depuis un worker Celery/RQ (mode futur)
- depuis la CLI (cours_generator.py)

Paramètres d'entrée → JobConfig
Sorties → les fichiers dans output/<slug>/ + le CourseModel JSON + le HTML

Gestion des erreurs :
- chaque étape lève une exception claire
- le pipeline la capture, met à jour le job en erreur, et émet un signal
- aucune exception n'est silencieuse
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Callable

from core.config import OUTPUT_DIR
from core.models import CourseAssets, CourseModel, JobRecord, JobStatus
from worker import extractor, notebooklm, structurer, renderer


# ── Type alias pour la fonction d'émission de log ─────────────────────────────
Emitter = Callable[[str], None]


def _noop_emit(msg: str) -> None:
    """Emitter par défaut (mode CLI) : simple print."""
    print(msg)


# ── Pipeline principal ────────────────────────────────────────────────────────

def run(
    job: JobRecord,
    source_files: list[Path],
    emit: Emitter = _noop_emit,
    skip_audio: bool = False,
) -> CourseModel:
    """Exécute le pipeline complet pour un job.

    Met à jour job.status au fil de l'exécution.
    Retourne le CourseModel produit.
    Lève une exception en cas d'erreur (le caller met à jour job.error).
    """
    from datetime import datetime
    job.status = JobStatus.RUNNING
    job.started_at = datetime.utcnow().isoformat()

    slug     = job.slug
    title    = job.title
    matiere  = job.matiere
    out_dir  = OUTPUT_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)

    source_pdf = source_files[0]

    # ── 1. Copie du PDF source ────────────────────────────────────────────────
    _step(job, emit, "copy_source", "📄 Copie du PDF source…")
    cours_copy = out_dir / f"{slug}_cours.pdf"
    if source_pdf.resolve() != cours_copy.resolve():
        shutil.copy(source_pdf, cours_copy)

    # ── 2. Extraction du texte ────────────────────────────────────────────────
    _step(job, emit, "extract", "📖 Extraction du texte PDF…")
    pdf_text = extractor.extract_text(source_pdf)
    meta     = extractor.extract_metadata(source_pdf)
    emit(f"   {meta['page_count']} pages · {len(pdf_text)} caractères")

    # ── 3. NotebookLM (slides + audio) ───────────────────────────────────────
    assets = CourseAssets(
        source_pdf=f"{slug}_cours.pdf",
    )

    _step(job, emit, "notebooklm_create", f"📚 Création du notebook NotebookLM…")
    try:
        nb_id = notebooklm.create_notebook(title)
        emit(f"   notebook_id = {nb_id}")

        for f in source_files:
            _step(job, emit, "notebooklm_source", f"📎 Ajout source : {f.name}")
            notebooklm.add_source(nb_id, f)

        # Lance les générations
        kinds = ["slide-deck"] + ([] if skip_audio else ["audio"])
        artifact_ids: dict[str, str] = {}
        for kind in kinds:
            _step(job, emit, f"generate_{kind}", f"🎬 Lancement génération {kind}…")
            aid = notebooklm.generate_artifact(kind, nb_id)
            artifact_ids[kind] = aid
            emit(f"   {kind} → artifact_id = {aid}")

        # Attente en parallèle — on collecte les échecs par kind
        _step(job, emit, "wait_artifacts", "⏳ Attente des artifacts (20-30 min)…")
        failed_kinds: set[str] = set()
        threads = [
            threading.Thread(
                target=_wait_one,
                args=(kind, aid, nb_id, emit, failed_kinds),
                daemon=True,
            )
            for kind, aid in artifact_ids.items()
        ]
        for t in threads: t.start()
        for t in threads: t.join()

        if failed_kinds:
            raise RuntimeError(
                f"Artifact(s) NotebookLM échoués : {', '.join(failed_kinds)}"
            )

        # Téléchargements
        slides_dir  = out_dir / "Presentation"
        slides_path = slides_dir / f"{slug}_presentation.pdf"
        slides_dir.mkdir(parents=True, exist_ok=True)

        _step(job, emit, "download_slides", "⬇️  Téléchargement slide-deck…")
        notebooklm.download_artifact("slide-deck", nb_id, slides_path)

        _step(job, emit, "split_slides", "✂️  Découpage des slides par page…")
        n = extractor.split_slides(slides_path, slides_dir, slug)
        emit(f"   {n} pages générées")
        assets.slides_pdf  = f"Presentation/{slug}_presentation.pdf"
        assets.slide_count = n

        if not skip_audio:
            audio_path = out_dir / f"{slug}_podcast.m4a"
            _step(job, emit, "download_audio", "⬇️  Téléchargement audio…")
            notebooklm.download_artifact("audio", nb_id, audio_path)
            assets.podcast_m4a = f"{slug}_podcast.m4a"

    except Exception as e:
        emit(f"⚠️  NotebookLM indisponible : {e}")
        emit("   → Poursuite sans slides ni audio (mode dégradé).")

    # ── 4. Structuration JSON via LLM ─────────────────────────────────────────
    _step(job, emit, "structure", "🧠 Structuration du contenu par le LLM…")
    course = structurer.structure_course(
        pdf_text=pdf_text,
        title=title,
        matiere=matiere,
        slug=slug,
        source_filename=source_pdf.name,
    )
    course.assets = assets
    emit(f"   {len(course.sections)} sections · {len(course.quiz)} questions quiz")

    # Sauvegarde du CourseModel JSON
    model_path = out_dir / "course_model.json"
    model_path.write_text(course.to_json(), encoding="utf-8")
    job.course_model_path = str(model_path)
    emit(f"   → {model_path.name}")

    # ── 5. Rendu HTML via template ────────────────────────────────────────────
    _step(job, emit, "render", "🌐 Rendu HTML via template Jinja2…")
    renderer.render_course(course, out_dir / "index.html")
    emit(f"   → {out_dir / 'index.html'}")

    # ── 6. Finalisation ───────────────────────────────────────────────────────
    job.status      = JobStatus.DONE
    job.output_dir  = str(out_dir)
    job.preview_url = f"/preview/{job.id}"
    job.completed_at = datetime.utcnow().isoformat()

    return course


# ── Helpers privés ────────────────────────────────────────────────────────────

def _step(job: JobRecord, emit: Emitter, step_name: str, msg: str) -> None:
    job.step = step_name
    emit(msg)


def _wait_one(kind: str, art_id: str, nb_id: str, emit: Emitter,
              failed: set[str]) -> None:
    """Attend un artifact dans un thread. Stocke les échecs dans `failed`."""
    try:
        emit(f"   ⌛ Waiting {kind} ({art_id[:8]}…)")
        notebooklm.wait_artifact(art_id, nb_id)
        emit(f"   ✅ {kind} prêt")
    except Exception as e:
        emit(f"   ❌ {kind} échoué : {e}")
        failed.add(kind)
