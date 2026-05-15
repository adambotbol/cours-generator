#!/usr/bin/env python3
"""Interface web pour cours-generator.

Démarre avec : python app.py
Puis ouvre http://localhost:5000
"""

import json
import os
import queue
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, redirect, render_template, request, send_from_directory, url_for

load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200 MB
UPLOAD_DIR = Path("uploads")
OUTPUT_DIR = Path("output")
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# job_id → {"status": str, "log": [str], "output_dir": str|None, "error": str|None}
JOBS: dict[str, dict] = {}
JOB_QUEUES: dict[str, queue.Queue] = {}

# ─── Helpers ──────────────────────────────────────────────────────────────────

NOTEBOOKLM_BIN = str(Path(sys.executable).parent / "notebooklm")


def _run(cmd: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, text=True, capture_output=capture)


def _emit(q: queue.Queue, msg: str) -> None:
    print(msg)
    q.put(msg)


# ─── Pipeline (runs in background thread) ─────────────────────────────────────

def run_pipeline(job_id: str, files: list[Path], title: str, matiere: str,
                 skip_audio: bool) -> None:
    q = JOB_QUEUES[job_id]
    job = JOBS[job_id]
    job["status"] = "running"

    try:
        chapter_name = files[0].stem  # use first file name as chapter key
        out_dir = OUTPUT_DIR / job_id
        out_dir.mkdir(parents=True, exist_ok=True)

        # ── 1. Create notebook ────────────────────────────────────────────────
        _emit(q, f"📚 Création du notebook « {title} »…")
        res = _run([NOTEBOOKLM_BIN, "create", title, "--json"])
        data = json.loads(res.stdout)
        notebook = data.get("notebook") if isinstance(data, dict) else None
        nb_id = notebook.get("id") if isinstance(notebook, dict) else None
        if not nb_id:
            raise RuntimeError(f"ID notebook introuvable: {data}")
        _emit(q, f"   notebook_id = {nb_id}")

        # ── 2. Add sources ────────────────────────────────────────────────────
        for f in files:
            _emit(q, f"📎 Ajout source : {f.name}")
            _run([NOTEBOOKLM_BIN, "source", "add", str(f),
                  "-n", nb_id, "--type", "file"])

        # ── 3. Launch generation ──────────────────────────────────────────────
        kinds = ["slide-deck"]
        if not skip_audio:
            kinds.append("audio")

        artifact_ids: dict[str, str] = {}
        for kind in kinds:
            _emit(q, f"🎬 Lancement génération {kind}…")
            res = _run([NOTEBOOKLM_BIN, "generate", kind,
                        "-n", nb_id, "--json", "--retry", "3"])
            d = json.loads(res.stdout)
            art_id = _extract_artifact_id(d, kind)
            artifact_ids[kind] = art_id
            _emit(q, f"   {kind} → artifact_id = {art_id}")

        # ── 4. Wait in parallel ───────────────────────────────────────────────
        _emit(q, "⏳ Attente des artifacts (peut prendre 20-30 min)…")

        def wait_one(kind: str, art_id: str) -> None:
            _emit(q, f"   ⌛ Waiting {kind} ({art_id[:8]}…)")
            _run([NOTEBOOKLM_BIN, "artifact", "wait", art_id,
                  "-n", nb_id, "--timeout", "3600"], capture=False)
            _emit(q, f"   ✅ {kind} prêt")

        threads = [
            threading.Thread(target=wait_one, args=(k, v), daemon=True)
            for k, v in artifact_ids.items()
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # ── 5. Download ───────────────────────────────────────────────────────
        slides_path = out_dir / "Presentation" / f"{chapter_name}_presentation.pdf"
        slides_path.parent.mkdir(parents=True, exist_ok=True)

        _emit(q, f"⬇️  Téléchargement slide-deck…")
        _run([NOTEBOOKLM_BIN, "download", "slide-deck", str(slides_path),
              "-n", nb_id, "--force"])

        audio_path = None
        if not skip_audio:
            audio_path = out_dir / f"{chapter_name}_podcast.m4a"
            _emit(q, f"⬇️  Téléchargement audio…")
            _run([NOTEBOOKLM_BIN, "download", "audio", str(audio_path),
                  "-n", nb_id, "--force"])

        # ── 6. Split slides ───────────────────────────────────────────────────
        _emit(q, "✂️  Découpage des slides par page…")
        import fitz
        src = fitz.open(slides_path)
        n = len(src)
        for i in range(n):
            pg = fitz.open()
            pg.insert_pdf(src, from_page=i, to_page=i)
            pg.save(slides_path.parent / f"{chapter_name}_presentation_page{i+1}.pdf")
            pg.close()
        src.close()
        _emit(q, f"   {n} pages générées")

        # ── 7. Generate HTML ──────────────────────────────────────────────────
        _emit(q, "🌐 Génération du site HTML…")
        pdf_text = _extract_text(files[0])
        html = _generate_html(pdf_text, chapter_name, title, matiere)
        index = out_dir / "index.html"
        index.write_text(html, encoding="utf-8")
        _emit(q, f"   → {index}")

        # ── 8. Git push ───────────────────────────────────────────────────────
        _emit(q, "🚀 Push Git…")
        try:
            _run(["git", "add", str(out_dir)], capture=False)
            _run(["git", "commit", "-m", f"Add course: {title}"], capture=False)
            _run(["git", "push", "origin", "main"], capture=False)
            _emit(q, "✅ Publié sur GitHub !")
        except subprocess.CalledProcessError as e:
            _emit(q, f"⚠️  Git push échoué (code {e.returncode}) — site généré localement.")

        job["status"] = "done"
        job["output_dir"] = str(out_dir)
        _emit(q, f"__DONE__:{job_id}")

    except Exception as exc:
        job["status"] = "error"
        job["error"] = str(exc)
        _emit(q, f"❌ Erreur : {exc}")
        _emit(q, "__ERROR__")


def _extract_artifact_id(data: dict, kind: str) -> str:
    if isinstance(data, dict):
        tid = data.get("task_id")
        if isinstance(tid, str) and tid:
            return tid
        for key in ("artifact", kind, kind.replace("-", "_")):
            w = data.get(key)
            if isinstance(w, dict):
                wid = w.get("id") or w.get("task_id")
                if isinstance(wid, str) and wid:
                    return wid
        top = data.get("id")
        if isinstance(top, str) and top:
            return top
    raise RuntimeError(f"Artifact ID introuvable dans `generate {kind}`: {data}")


def _extract_text(pdf_path: Path) -> str:
    import fitz
    doc = fitz.open(pdf_path)
    try:
        return "\n\n".join(p.get_text() for p in doc)
    finally:
        doc.close()


def _generate_html(pdf_text: str, chapter_name: str, titre: str, matiere: str) -> str:
    from google import genai
    from google.genai import types

    # Import system prompt from cours_generator
    sys.path.insert(0, str(Path(__file__).parent))
    from cours_generator import SYSTEM_PROMPT, GEMINI_MODEL, HTML_MAX_TOKENS

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY manquante dans .env")

    client = genai.Client(api_key=api_key)
    user_prompt = f"Titre: {titre} | Matière: {matiere} | Fichier: {chapter_name}\n{pdf_text}"
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            max_output_tokens=HTML_MAX_TOKENS,
        ),
    )
    return response.text


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    files = request.files.getlist("pdfs")
    title = request.form.get("title", "").strip()
    matiere = request.form.get("matiere", "").strip()
    skip_audio = request.form.get("skip_audio") == "on"

    if not files or not files[0].filename:
        return jsonify({"error": "Aucun fichier sélectionné"}), 400

    job_id = str(uuid.uuid4())[:8]
    JOBS[job_id] = {"status": "pending", "log": [], "output_dir": None, "error": None}
    JOB_QUEUES[job_id] = queue.Queue()

    saved: list[Path] = []
    job_upload_dir = UPLOAD_DIR / job_id
    job_upload_dir.mkdir(parents=True, exist_ok=True)
    for f in files:
        dest = job_upload_dir / Path(f.filename).name
        f.save(dest)
        saved.append(dest)

    if not title:
        title = saved[0].stem

    t = threading.Thread(
        target=run_pipeline,
        args=(job_id, saved, title, matiere, skip_audio),
        daemon=True,
    )
    t.start()

    return redirect(url_for("progress", job_id=job_id))


@app.route("/progress/<job_id>")
def progress(job_id: str):
    if job_id not in JOBS:
        return "Job introuvable", 404
    return render_template("progress.html", job_id=job_id)


@app.route("/stream/<job_id>")
def stream(job_id: str):
    """SSE endpoint — streams log lines to the browser."""
    if job_id not in JOB_QUEUES:
        return "Job introuvable", 404

    def event_generator():
        q = JOB_QUEUES[job_id]
        while True:
            try:
                msg = q.get(timeout=60)
                yield f"data: {json.dumps(msg)}\n\n"
                if msg.startswith("__DONE__") or msg == "__ERROR__":
                    break
            except queue.Empty:
                yield "data: ping\n\n"

    return Response(event_generator(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/status/<job_id>")
def status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify(job)


@app.route("/site/<job_id>/")
@app.route("/site/<job_id>/<path:filename>")
def serve_site(job_id: str, filename: str = "index.html"):
    job = JOBS.get(job_id)
    if not job or not job.get("output_dir"):
        return "Site non encore prêt", 404
    return send_from_directory(job["output_dir"], filename)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀  http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
