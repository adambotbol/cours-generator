"""Adaptateur NotebookLM CLI.

Encapsule toutes les interactions avec le binaire `notebooklm` (notebooklm-py).
Chaque fonction correspond à une commande CLI.  Les erreurs subprocess sont
propagées telles quelles — c'est au pipeline de les gérer.

Dépendance : notebooklm-py installé dans le même venv.
Authentification : gérée par notebooklm-py (cookies Google du navigateur local).
             → Ne peut pas tourner sur un serveur cloud sans credentials.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from core.config import NOTEBOOKLM_BIN


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run(cmd: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, text=True, capture_output=capture)


def _extract_artifact_id(data: dict, kind: str) -> str:
    """Extrait l'artifact_id depuis la réponse JSON de `generate`.

    Forme observée : {"task_id": "<uuid>", "status": "pending"}.
    task_id == artifact_id (accepté par `artifact wait/poll/download`).
    """
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


# ── API publique ──────────────────────────────────────────────────────────────

def create_notebook(title: str) -> str:
    """Crée un notebook NotebookLM, retourne son ID.

    CLI: notebooklm create <title> --json
    Réponse: {"notebook": {"id": "...", ...}}
    """
    result = _run([NOTEBOOKLM_BIN, "create", title, "--json"])
    data   = json.loads(result.stdout)
    nb     = data.get("notebook") if isinstance(data, dict) else None
    nb_id  = nb.get("id") if isinstance(nb, dict) else None
    if isinstance(nb_id, str) and nb_id:
        return nb_id
    raise RuntimeError(f"ID notebook introuvable: {data}")


def add_source(notebook_id: str, file_path: Path) -> None:
    """Ajoute un fichier PDF comme source du notebook."""
    _run([NOTEBOOKLM_BIN, "source", "add", str(file_path),
          "-n", notebook_id, "--type", "file"])


def generate_artifact(kind: str, notebook_id: str) -> str:
    """Lance la génération d'un artifact (no-wait), retourne l'artifact_id.

    kind: "audio" | "slide-deck"
    """
    result = _run([NOTEBOOKLM_BIN, "generate", kind,
                   "-n", notebook_id, "--json", "--retry", "3"])
    data   = json.loads(result.stdout)
    return _extract_artifact_id(data, kind)


def wait_artifact(artifact_id: str, notebook_id: str, timeout: int = 3600) -> None:
    """Bloque jusqu'à ce que l'artifact soit prêt (timeout en secondes)."""
    _run([NOTEBOOKLM_BIN, "artifact", "wait", artifact_id,
          "-n", notebook_id, "--timeout", str(timeout)],
         capture=False)


def download_artifact(kind: str, notebook_id: str, output_path: Path) -> None:
    """Télécharge un artifact vers output_path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run([NOTEBOOKLM_BIN, "download", kind, str(output_path),
          "-n", notebook_id, "--force"])
