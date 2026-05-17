"""Backend de stockage local : filesystem + SQLite.

SQLite est utilisé pour persister les jobs entre redémarrages du serveur Flask.
Les fichiers restent sur le système de fichiers local (pas de cloud).

État : FONCTIONNEL — utilisable tel quel pour le MVP local.
Migration Supabase : remplacer par storage/supabase.py en changeant STORAGE_BACKEND=supabase.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from core.config import ROOT_DIR
from core.models import JobRecord, JobStatus
from storage.base import StorageBackend

DB_PATH = ROOT_DIR / "jobs.db"


class LocalBackend(StorageBackend):

    def __init__(self) -> None:
        self._init_db()

    # ── DB init ───────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL DEFAULT 'pending',
                    title TEXT,
                    matiere TEXT,
                    slug TEXT,
                    config TEXT DEFAULT '{}',
                    step TEXT,
                    error TEXT,
                    output_dir TEXT,
                    preview_url TEXT,
                    course_model_path TEXT,
                    created_at TEXT,
                    started_at TEXT,
                    completed_at TEXT
                )
            """)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        return conn

    # ── Jobs ──────────────────────────────────────────────────────────────────

    def create_job(self, job: JobRecord) -> None:
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO jobs
                  (id, status, title, matiere, slug, config, step, error,
                   output_dir, preview_url, course_model_path, created_at, started_at, completed_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                job.id, job.status.value, job.title, job.matiere, job.slug,
                json.dumps(job.config), job.step, job.error,
                job.output_dir, job.preview_url, job.course_model_path,
                job.created_at, job.started_at, job.completed_at,
            ))

    def update_job(self, job: JobRecord) -> None:
        with self._conn() as conn:
            conn.execute("""
                UPDATE jobs SET
                  status=?, step=?, error=?, output_dir=?, preview_url=?,
                  course_model_path=?, started_at=?, completed_at=?
                WHERE id=?
            """, (
                job.status.value, job.step, job.error,
                job.output_dir, job.preview_url, job.course_model_path,
                job.started_at, job.completed_at,
                job.id,
            ))

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    def list_jobs(self, limit: int = 50) -> list[JobRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_job(r) for r in rows]

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            id=row["id"],
            status=JobStatus(row["status"]),
            title=row["title"] or "",
            matiere=row["matiere"] or "",
            slug=row["slug"] or "",
            config=json.loads(row["config"] or "{}"),
            step=row["step"] or "",
            error=row["error"] or "",
            output_dir=row["output_dir"] or "",
            preview_url=row["preview_url"] or "",
            course_model_path=row["course_model_path"] or "",
            created_at=row["created_at"] or "",
            started_at=row["started_at"] or "",
            completed_at=row["completed_at"] or "",
        )

    # ── Fichiers ──────────────────────────────────────────────────────────────

    def save_file(self, local_path: Path, remote_path: str) -> str:
        """En mode local, les fichiers restent sur le filesystem — no-op."""
        return str(local_path)

    def get_file_url(self, remote_path: str) -> str:
        """En mode local, retourne le chemin relatif pour le serving Flask."""
        return f"/files/{remote_path}"
