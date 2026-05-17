#!/usr/bin/env python3
"""Serveur Flask — couche HTTP mince.

Responsabilités de ce fichier :
  - Valider les entrées HTTP (upload, form data)
  - Créer et suivre les jobs via storage.base.get_backend()
  - Lancer worker.pipeline.run() dans un thread daemon
  - Streamer les logs via SSE
  - Servir les prévisualisations locales et le CourseModel JSON

Ce fichier NE contient PAS :
  - de logique de génération (→ worker/pipeline.py)
  - d'appels directs à NotebookLM ou Gemini (→ worker/)
  - de push GitHub (supprimé — publication découplée)
  - d'état in-memory des jobs (→ storage/local.py, SQLite)
"""

from __future__ import annotations

import json
import queue
import re
import threading
import uuid
from pathlib import Path

from flask import (
    Flask, Response, jsonify, redirect, render_template,
    request, send_from_directory, url_for,
)

from core.config import OUTPUT_DIR, UPLOAD_DIR, PORT, DEBUG
from core.models import JobRecord, JobStatus, CourseModel
from storage.base import get_backend
import worker.pipeline as pipeline


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200 MB

# SSE queues in-memory (ok : recréées si le serveur redémarre, les jobs SQLite persistent)
_SSE_QUEUES: dict[str, queue.Queue] = {}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _emit_to_job(job_id: str, msg: str) -> None:
    """Émet un message SSE + met à jour le job en DB."""
    q = _SSE_QUEUES.get(job_id)
    if q:
        q.put(msg)
    print(f"[{job_id}] {msg}")


# ── Background runner ─────────────────────────────────────────────────────────

def _run_job(job: JobRecord, source_files: list[Path], skip_audio: bool) -> None:
    """Exécuté dans un thread daemon.  Met à jour le job SQLite en continu."""
    store = get_backend()

    def emit(msg: str) -> None:
        _emit_to_job(job.id, msg)
        job.step = msg[:120]
        store.update_job(job)

    try:
        course = pipeline.run(
            job=job,
            source_files=source_files,
            emit=emit,
            skip_audio=skip_audio,
        )
        store.update_job(job)
        _emit_to_job(job.id, f"__DONE__:{job.preview_url}")

    except Exception as exc:
        job.status = JobStatus.ERROR
        job.error  = str(exc)
        store.update_job(job)
        emit(f"❌ Erreur : {exc}")
        _emit_to_job(job.id, "__ERROR__")


# ── Routes — UI Flask ─────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    files      = request.files.getlist("pdfs")
    title      = request.form.get("title", "").strip()
    matiere    = request.form.get("matiere", "").strip()
    skip_audio = request.form.get("skip_audio") == "on"

    if not files or not files[0].filename:
        return jsonify({"error": "Aucun fichier sélectionné"}), 400

    job_id = str(uuid.uuid4())[:8]
    slug   = _slugify(title or Path(files[0].filename).stem)

    # Sauvegarde des uploads
    job_upload_dir = UPLOAD_DIR / job_id
    job_upload_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for f in files:
        dest = job_upload_dir / Path(f.filename).name
        f.save(dest)
        saved.append(dest)

    if not title:
        title = saved[0].stem

    # Création du job
    job = JobRecord(
        id=job_id, slug=slug, title=title, matiere=matiere,
        config={"skip_audio": skip_audio},
    )
    store = get_backend()
    store.create_job(job)
    _SSE_QUEUES[job_id] = queue.Queue()

    # Lancement pipeline en arrière-plan
    threading.Thread(
        target=_run_job,
        args=(job, saved, skip_audio),
        daemon=True,
    ).start()

    return redirect(url_for("progress", job_id=job_id))


@app.route("/progress/<job_id>")
def progress(job_id: str):
    job = get_backend().get_job(job_id)
    if job is None:
        return "Job introuvable", 404
    return render_template("progress.html", job_id=job_id)


# ── Routes — API JSON ─────────────────────────────────────────────────────────

@app.route("/api/jobs")
def api_jobs():
    jobs = get_backend().list_jobs(limit=20)
    return jsonify([j.to_dict() for j in jobs])


@app.route("/api/jobs/<job_id>")
def api_job(job_id: str):
    job = get_backend().get_job(job_id)
    if job is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(job.to_dict())


@app.route("/api/jobs/<job_id>/model")
def api_course_model(job_id: str):
    """Retourne le CourseModel JSON brut (pour édition future)."""
    job = get_backend().get_job(job_id)
    if job is None or not job.course_model_path:
        return jsonify({"error": "CourseModel non disponible"}), 404
    model_path = Path(job.course_model_path)
    if not model_path.exists():
        return jsonify({"error": "Fichier introuvable"}), 404
    return Response(
        model_path.read_text(encoding="utf-8"),
        mimetype="application/json",
    )


# ── SSE stream ────────────────────────────────────────────────────────────────

@app.route("/stream/<job_id>")
def stream(job_id: str):
    if job_id not in _SSE_QUEUES:
        # Le job existe peut-être en DB (redémarrage) mais la queue est perdue
        job = get_backend().get_job(job_id)
        if job and job.status == JobStatus.DONE:
            def already_done():
                yield f"data: {json.dumps('__DONE__:' + job.preview_url)}\n\n"
            return Response(already_done(), mimetype="text/event-stream")
        if job and job.status == JobStatus.ERROR:
            def already_error():
                yield f"data: {json.dumps('__ERROR__')}\n\n"
            return Response(already_error(), mimetype="text/event-stream")
        return "Job introuvable", 404

    def gen():
        q = _SSE_QUEUES[job_id]
        while True:
            try:
                msg = q.get(timeout=60)
                yield f"data: {json.dumps(msg)}\n\n"
                if msg.startswith("__DONE__") or msg == "__ERROR__":
                    break
            except queue.Empty:
                yield "data: \"ping\"\n\n"

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Preview locale ────────────────────────────────────────────────────────────

@app.route("/preview/<job_id>/")
@app.route("/preview/<job_id>/<path:filename>")
def preview(job_id: str, filename: str = "index.html"):
    """Sert le site généré en local (avant publication éventuelle)."""
    job = get_backend().get_job(job_id)
    if not job or not job.output_dir:
        return "Site non encore prêt", 404
    return send_from_directory(job.output_dir, filename)


# ── Re-render à la demande ────────────────────────────────────────────────────

@app.route("/api/jobs/<job_id>/rerender", methods=["POST"])
def rerender(job_id: str):
    """Re-génère le HTML depuis le CourseModel JSON sans ré-appeler le LLM.

    Utile après modification manuelle du JSON.
    """
    job = get_backend().get_job(job_id)
    if not job or not job.course_model_path:
        return jsonify({"error": "CourseModel non disponible"}), 404

    model_path = Path(job.course_model_path)
    if not model_path.exists():
        return jsonify({"error": "Fichier JSON introuvable"}), 404

    try:
        course = CourseModel.from_json(model_path.read_text(encoding="utf-8"))
        from worker.renderer import render_course
        render_course(course, Path(job.output_dir) / "index.html")
        return jsonify({"ok": True, "preview_url": job.preview_url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Entrée ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"🚀  http://localhost:{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=DEBUG, threaded=True)
