"""Orchestrateur du pipeline de génération.

Flux complet :
  PDF source
    → extraction texte (extractor.py)
    → NotebookLM Python API : indexation + génération parallèle de
      l'infographie, slide-deck et audio (notebooklm.py)
    → structuration JSON via LLM (structurer.py)
    → rendu HTML via template Jinja2 (renderer.py)
    → persistance locale (storage/local.py)

Ce module est indépendant de Flask.  Il peut être appelé :
- depuis app.py dans un thread daemon (mode actuel)
- depuis un worker Celery/RQ (mode futur)
- depuis la CLI (cours_generator.py)

Gestion des erreurs :
- NotebookLM est optionnel : toute exception → mode dégradé (site sans médias)
- les étapes LLM et rendu HTML sont obligatoires
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from core.config import OUTPUT_DIR
from core.models import CourseAssets, CourseModel, JobRecord, JobStatus
from worker import extractor, notebooklm, structurer, renderer


# ── Type alias ────────────────────────────────────────────────────────────────
Emitter = Callable[[str], None]


def _noop_emit(msg: str) -> None:
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
    Lève une exception en cas d'erreur fatale.
    """
    from datetime import datetime
    job.status = JobStatus.RUNNING
    job.started_at = datetime.utcnow().isoformat()

    slug    = job.slug
    title   = job.title
    matiere = job.matiere
    out_dir = OUTPUT_DIR / slug
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

    # ── 3. NotebookLM — pipeline complet via Python API ──────────────────────
    assets = CourseAssets(source_pdf=f"{slug}_cours.pdf")

    _step(job, emit, "notebooklm", "🤖 Pipeline NotebookLM (indexation + génération)…")
    try:
        nb_result = notebooklm.run_pipeline(
            title=title,
            source_files=source_files,
            out_dir=out_dir,
            slug=slug,
            skip_audio=skip_audio,
            emit=emit,
        )
        # Applique les résultats disponibles (clé absente = artifact échoué)
        assets.infographic_png = nb_result.get("infographic_png", "")
        assets.slides_pdf      = nb_result.get("slides_pdf", "")
        assets.slide_count     = nb_result.get("slide_count", 0)
        assets.podcast_m4a     = nb_result.get("podcast_m4a", "")

    except Exception as e:
        emit(f"⚠️  NotebookLM indisponible : {e}")
        emit("   → Poursuite sans médias (mode dégradé).")

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
    job.status       = JobStatus.DONE
    job.output_dir   = str(out_dir)
    job.preview_url  = f"/preview/{job.id}"
    job.completed_at = datetime.utcnow().isoformat()

    return course


# ── Helper ────────────────────────────────────────────────────────────────────

def _step(job: JobRecord, emit: Emitter, step_name: str, msg: str) -> None:
    job.step = step_name
    emit(msg)
