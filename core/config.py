"""Configuration centralisée — chargée depuis les variables d'environnement."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── Chemins ────────────────────────────────────────────────────────────────────
ROOT_DIR    = Path(__file__).parent.parent
OUTPUT_DIR  = ROOT_DIR / "output"
UPLOAD_DIR  = ROOT_DIR / "uploads"
TEMPLATE_DIR = ROOT_DIR / "templates"

OUTPUT_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)

# ── NotebookLM ──────────────────────────────────────────────────────────────────
NOTEBOOKLM_BIN = str(Path(sys.executable).parent / "notebooklm")

# ── Gemini / Google AI ────────────────────────────────────────────────────────
GEMINI_API_KEY    = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL      = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_MAX_TOKENS = int(os.environ.get("GEMINI_MAX_TOKENS", "8192"))

# ── GitHub (publication statique optionnelle) ─────────────────────────────────
GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN", "")
GITHUB_OWNER      = os.environ.get("GITHUB_OWNER", "")
GITHUB_REPO       = os.environ.get("GITHUB_REPO", "")

# ── Supabase (désactivé jusqu'à câblage) ─────────────────────────────────────
SUPABASE_URL      = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

# ── Stockage backend ─────────────────────────────────────────────────────────
#   "local"    → stockage filesystem + SQLite jobs
#   "supabase" → Supabase Storage + Supabase DB  (nécessite SUPABASE_* vars)
STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local")

# ── Serveur Flask ─────────────────────────────────────────────────────────────
PORT  = int(os.environ.get("PORT", "5000"))
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
