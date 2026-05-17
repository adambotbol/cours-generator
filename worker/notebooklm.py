"""Adaptateur NotebookLM — Python async API uniquement.

Toutes les interactions passent par notebooklm-py (API Python async),
plus aucun appel CLI.  Avantage clé : add_file(wait=True) garantit que
le PDF est indexé avant le lancement des générations → plus de "failed"
immédiat sur les artifacts.

Authentification : gérée par notebooklm-py (cookies Google du navigateur
local via `notebooklm auth login`).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable

# ── Type ──────────────────────────────────────────────────────────────────────
Emitter = Callable[[str], None]


# ── Pipeline public ───────────────────────────────────────────────────────────

def run_pipeline(
    title: str,
    source_files: list[Path],
    out_dir: Path,
    slug: str,
    skip_audio: bool,
    emit: Emitter,
) -> dict[str, str | int]:
    """Lance le pipeline NotebookLM complet (sync wrapper).

    Crée un notebook, indexe les sources, génère et télécharge les artifacts
    (infographie + slide-deck + audio) en parallèle.

    Retourne un dict avec les clés présentes si réussies :
        infographic_png, slides_pdf, slide_count, podcast_m4a
    Les clés absentes signifient que l'artifact a échoué (mode dégradé).
    """
    return asyncio.run(_pipeline_async(
        title=title,
        source_files=source_files,
        out_dir=out_dir,
        slug=slug,
        skip_audio=skip_audio,
        emit=emit,
    ))


# ── Async core ────────────────────────────────────────────────────────────────

async def _pipeline_async(
    title: str,
    source_files: list[Path],
    out_dir: Path,
    slug: str,
    skip_audio: bool,
    emit: Emitter,
) -> dict[str, str | int]:
    from notebooklm import NotebookLMClient
    from notebooklm.types import InfographicDetail, InfographicOrientation

    result: dict[str, str | int] = {}

    async with await NotebookLMClient.from_storage() as client:

        # ── 1. Créer le notebook ──────────────────────────────────────────────
        emit("📚 Création du notebook NotebookLM…")
        notebook = await client.notebooks.create(title)
        nb_id = notebook.id
        emit(f"   notebook_id = {nb_id}")

        # ── 2. Ajouter les sources (attend l'indexation) ──────────────────────
        for f in source_files:
            emit(f"📎 Ajout et indexation de {f.name} (peut prendre 1-2 min)…")
            await client.sources.add_file(
                nb_id, f, wait=True, wait_timeout=300,
            )
            emit(f"   ✅ {f.name} indexé")

        # ── 3. Lancer toutes les générations en parallèle ─────────────────────
        emit("🚀 Lancement des générations en parallèle…")

        kinds_to_run = ["infographic", "slide-deck"]
        if not skip_audio:
            kinds_to_run.append("audio")

        # Lancement des tâches
        statuses: dict[str, object] = {}

        info_status = await client.artifacts.generate_infographic(
            nb_id,
            language="fr",
            orientation=InfographicOrientation.PORTRAIT,
            detail_level=InfographicDetail.DETAILED,
        )
        statuses["infographic"] = info_status
        emit(f"   🖼️  infographic → task_id = {info_status.task_id[:8]}…")

        slides_status = await client.artifacts.generate_slide_deck(
            nb_id, language="fr",
        )
        statuses["slide-deck"] = slides_status
        emit(f"   📊 slide-deck → task_id = {slides_status.task_id[:8]}…")

        if not skip_audio:
            audio_status = await client.artifacts.generate_audio(
                nb_id, language="fr",
            )
            statuses["audio"] = audio_status
            emit(f"   🎧 audio → task_id = {audio_status.task_id[:8]}…")

        # ── 4. Attendre toutes les completions en parallèle ───────────────────
        emit("⏳ Attente des artifacts (peut prendre 5-30 min)…")

        async def _wait(kind: str, task_id: str) -> tuple[str, bool]:
            try:
                emit(f"   ⌛ Waiting {kind} ({task_id[:8]}…)")
                final = await client.artifacts.wait_for_completion(
                    nb_id, task_id, timeout=1800, poll_interval=15,
                )
                if final.is_failed:
                    emit(f"   ❌ {kind} échoué (status: {final})")
                    return kind, False
                emit(f"   ✅ {kind} prêt")
                return kind, True
            except Exception as exc:
                emit(f"   ❌ {kind} erreur : {exc}")
                return kind, False

        wait_tasks = [
            _wait(kind, st.task_id)
            for kind, st in statuses.items()
        ]
        completions = await asyncio.gather(*wait_tasks)
        done = {kind: ok for kind, ok in completions}

        # ── 5. Téléchargements ────────────────────────────────────────────────

        # Infographie
        if done.get("infographic"):
            info_path = out_dir / f"{slug}_infographie.png"
            info_path.parent.mkdir(parents=True, exist_ok=True)
            emit("⬇️  Téléchargement infographie…")
            try:
                await client.artifacts.download_infographic(
                    nb_id, str(info_path),
                    artifact_id=statuses["infographic"].task_id,
                )
                result["infographic_png"] = f"{slug}_infographie.png"
                emit(f"   ✅ {info_path.name} ({info_path.stat().st_size // 1024} Ko)")
            except Exception as exc:
                emit(f"   ⚠️  Téléchargement infographie échoué : {exc}")

        # Slide-deck
        if done.get("slide-deck"):
            slides_dir  = out_dir / "Presentation"
            slides_dir.mkdir(parents=True, exist_ok=True)
            slides_path = slides_dir / f"{slug}_presentation.pdf"
            emit("⬇️  Téléchargement slide-deck…")
            try:
                await client.artifacts.download_slide_deck(
                    nb_id, str(slides_path),
                    artifact_id=statuses["slide-deck"].task_id,
                    output_format="pdf",
                )
                # Split en pages individuelles (appel sync — ok dans asyncio)
                from worker.extractor import split_slides
                n = await asyncio.to_thread(split_slides, slides_path, slides_dir, slug)
                result["slides_pdf"]   = f"Presentation/{slug}_presentation.pdf"
                result["slide_count"]  = n
                emit(f"   ✅ {n} pages slides")
            except Exception as exc:
                emit(f"   ⚠️  Téléchargement slide-deck échoué : {exc}")

        # Audio
        if done.get("audio"):
            audio_path = out_dir / f"{slug}_podcast.m4a"
            emit("⬇️  Téléchargement audio…")
            try:
                await client.artifacts.download_audio(
                    nb_id, str(audio_path),
                    artifact_id=statuses["audio"].task_id,
                )
                result["podcast_m4a"] = f"{slug}_podcast.m4a"
                emit(f"   ✅ {audio_path.name} ({audio_path.stat().st_size // 1024} Ko)")
            except Exception as exc:
                emit(f"   ⚠️  Téléchargement audio échoué : {exc}")

    return result
