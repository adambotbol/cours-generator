"""Interface abstraite pour les backends de stockage.

Deux implémentations :
- local.py   → filesystem local + SQLite (fonctionne maintenant)
- supabase.py → Supabase Storage + DB (stub, prêt à câbler)

Le pipeline (worker/pipeline.py) n'importe PAS directement local.py ou supabase.py.
Il utilise get_backend() qui choisit l'implémentation via STORAGE_BACKEND dans .env.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from core.models import JobRecord, JobStatus


class StorageBackend(ABC):
    """Interface commune pour la persistance des jobs et le stockage des fichiers."""

    # ── Jobs ──────────────────────────────────────────────────────────────────

    @abstractmethod
    def create_job(self, job: JobRecord) -> None:
        """Persiste un nouveau job."""

    @abstractmethod
    def update_job(self, job: JobRecord) -> None:
        """Met à jour un job existant."""

    @abstractmethod
    def get_job(self, job_id: str) -> JobRecord | None:
        """Retourne un job par ID, ou None si absent."""

    @abstractmethod
    def list_jobs(self, limit: int = 50) -> list[JobRecord]:
        """Retourne les N derniers jobs, du plus récent au plus ancien."""

    # ── Fichiers ──────────────────────────────────────────────────────────────

    @abstractmethod
    def save_file(self, local_path: Path, remote_path: str) -> str:
        """Sauvegarde un fichier et retourne son URL ou chemin de stockage."""

    @abstractmethod
    def get_file_url(self, remote_path: str) -> str:
        """Retourne l'URL publique d'un fichier stocké."""


# ── Factory ───────────────────────────────────────────────────────────────────

_instance: StorageBackend | None = None


def get_backend() -> StorageBackend:
    """Retourne le backend configuré (singleton)."""
    global _instance
    if _instance is None:
        from core.config import STORAGE_BACKEND
        if STORAGE_BACKEND == "supabase":
            from storage.supabase import SupabaseBackend
            _instance = SupabaseBackend()
        else:
            from storage.local import LocalBackend
            _instance = LocalBackend()
    return _instance
