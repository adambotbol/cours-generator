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
    """Bloque jusqu'à ce que l'artifact soit prêt (timeout en secondes).

    Note : notebooklm-py retourne parfois exit code 0 même quand le statut
    est 'failed'.  On capture le stdout pour détecter ce cas et lever une
    exception explicite (le pipeline passera alors en mode dégradé).
    """
    result = subprocess.run(
        [NOTEBOOKLM_BIN, "artifact", "wait", artifact_id,
         "-n", notebook_id, "--timeout", str(timeout)],
        text=True, capture_output=True,
    )
    # Affiche la sortie pour la visibilité dans les logs
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="")

    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, result.args, result.stdout, result.stderr
        )
    # Le CLI peut retourner exit 0 avec "Status: failed" dans le stdout
    combined = (result.stdout + result.stderr).lower()
    if "status: failed" in combined or "status:failed" in combined:
        raise RuntimeError(
            f"NotebookLM : génération de l'artifact échouée ({artifact_id[:8]}…)"
        )


def download_artifact(kind: str, notebook_id: str, output_path: Path) -> None:
    """Télécharge un artifact vers output_path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run([NOTEBOOKLM_BIN, "download", kind, str(output_path),
          "-n", notebook_id, "--force"])


def generate_and_download_infographic(
    notebook_id: str,
    output_path: Path,
    emit=None,
) -> None:
    """Génère et télécharge une infographie via la Python API NotebookLM.

    Wrapper synchrone autour de l'API async — peut être appelé depuis n'importe
    quel thread (crée son propre event loop via asyncio.run()).

    Raises RuntimeError si la génération échoue côté NotebookLM.
    """
    import asyncio

    async def _run_async() -> None:
        from notebooklm import NotebookLMClient  # noqa: PLC0415
        from notebooklm.types import InfographicDetail, InfographicOrientation  # noqa: PLC0415

        async with await NotebookLMClient.from_storage() as client:
            status = await client.artifacts.generate_infographic(
                notebook_id,
                language="fr",
                orientation=InfographicOrientation.PORTRAIT,
                detail_level=InfographicDetail.DETAILED,
            )
            if emit:
                emit(f"   infographie → task_id = {status.task_id[:8]}…")

            final = await client.artifacts.wait_for_completion(
                notebook_id, status.task_id, timeout=600, poll_interval=10,
            )
            if final.is_failed:
                raise RuntimeError(
                    f"Infographie NotebookLM échouée : {final.status}"
                )

            output_path.parent.mkdir(parents=True, exist_ok=True)
            await client.artifacts.download_infographic(
                notebook_id, str(output_path), artifact_id=status.task_id,
            )

    asyncio.run(_run_async())
