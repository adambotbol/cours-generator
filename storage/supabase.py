"""Backend Supabase — STUB non câblé.

Ce fichier définit l'implémentation Supabase de StorageBackend.
Il lève NotImplementedError sur toutes les méthodes jusqu'à ce que
les dépendances et les variables d'environnement soient configurées.

Pour activer :
  1. pip install supabase
  2. Définir dans .env :
       SUPABASE_URL=https://<project>.supabase.co
       SUPABASE_SERVICE_KEY=<service_role_key>
       STORAGE_BACKEND=supabase
  3. Exécuter migrations/001_initial.sql dans le SQL Editor Supabase
  4. Implémenter les méthodes ci-dessous en suivant le schéma de 001_initial.sql

Référence SDK : https://supabase.com/docs/reference/python/introduction
"""

from __future__ import annotations

from pathlib import Path

from core.models import JobRecord
from storage.base import StorageBackend


class SupabaseBackend(StorageBackend):
    """Implémentation Supabase — à compléter lors de l'intégration."""

    def __init__(self) -> None:
        from core.config import SUPABASE_URL, SUPABASE_SERVICE_KEY
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            raise RuntimeError(
                "SUPABASE_URL et SUPABASE_SERVICE_KEY doivent être définis dans .env "
                "pour utiliser STORAGE_BACKEND=supabase."
            )
        # TODO: initialiser le client Supabase
        # from supabase import create_client
        # self.client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        raise NotImplementedError(
            "SupabaseBackend n'est pas encore implémenté. "
            "Utilisez STORAGE_BACKEND=local pour le mode MVP."
        )

    # ── Jobs ──────────────────────────────────────────────────────────────────

    def create_job(self, job: JobRecord) -> None:
        # TODO:
        # self.client.table("generation_jobs").insert({
        #     "id": job.id, "status": job.status.value, ...
        # }).execute()
        raise NotImplementedError

    def update_job(self, job: JobRecord) -> None:
        # TODO:
        # self.client.table("generation_jobs").update({...}).eq("id", job.id).execute()
        raise NotImplementedError

    def get_job(self, job_id: str) -> JobRecord | None:
        # TODO:
        # res = self.client.table("generation_jobs").select("*").eq("id", job_id).single().execute()
        # return _row_to_job(res.data) if res.data else None
        raise NotImplementedError

    def list_jobs(self, limit: int = 50) -> list[JobRecord]:
        # TODO:
        # res = self.client.table("generation_jobs").select("*")
        #     .order("created_at", desc=True).limit(limit).execute()
        # return [_row_to_job(r) for r in res.data]
        raise NotImplementedError

    # ── Fichiers ──────────────────────────────────────────────────────────────

    def save_file(self, local_path: Path, remote_path: str) -> str:
        # TODO:
        # with open(local_path, "rb") as f:
        #     self.client.storage.from_("courses").upload(remote_path, f)
        # return self.client.storage.from_("courses").get_public_url(remote_path)
        raise NotImplementedError

    def get_file_url(self, remote_path: str) -> str:
        # TODO:
        # return self.client.storage.from_("courses").get_public_url(remote_path)
        raise NotImplementedError
