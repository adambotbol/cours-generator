"""Modèles de domaine — représentation structurée d'un cours.

CourseModel est le format intermédiaire JSON produit par le LLM et consommé
par le renderer.  Il découple complètement l'extraction de contenu du rendu HTML.

Toutes les classes sont des dataclasses Python standard (pas de dépendance externe).
Méthodes .to_dict() / .from_dict() pour sérialisation JSON.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any


# ── Enums ─────────────────────────────────────────────────────────────────────

class TermType(str, Enum):
    """Couleur sémantique d'un terme clé."""
    DEF     = "def"      # Loi, Principe       → bleu
    VAR     = "var"      # Grandeur physique    → violet
    UNIT    = "unit"     # Rendement, Atout     → vert
    TRAP    = "trap"     # Point de vigilance   → orange
    DANGER  = "danger"   # Perte, Risque        → rouge


class JobStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    DONE      = "done"
    ERROR     = "error"


# ── Course content models ──────────────────────────────────────────────────────

@dataclass
class Formula:
    """Formule mathématique avec contexte pédagogique."""
    latex: str                          # ex: "P = U \\cdot I"
    label: str                          # ex: "Puissance électrique"
    term_type: TermType = TermType.DEF  # couleur sémantique


@dataclass
class KeyTerm:
    """Terme clé avec définition courte."""
    term: str
    definition: str
    term_type: TermType = TermType.DEF


@dataclass
class Section:
    """Section de cours (équivalent d'un chapitre ou sous-chapitre)."""
    id: str                              # ex: "s1"
    title: str
    content: str                         # texte principal (markdown-like, plain text)
    formulas: list[Formula]  = field(default_factory=list)
    key_terms: list[KeyTerm] = field(default_factory=list)


@dataclass
class QuizItem:
    """Flashcard pour le quiz interactif."""
    question: str
    answer: str


@dataclass
class CourseAssets:
    """Chemins relatifs vers les fichiers médias générés."""
    source_pdf: str      = ""   # ex: "energie_elec_cours.pdf"
    podcast_m4a: str     = ""   # ex: "energie_elec_podcast.m4a"  — vide si absent
    slides_pdf: str      = ""   # ex: "Presentation/energie_elec_presentation.pdf"
    slide_count: int     = 0    # nombre de pages de présentation


@dataclass
class CourseModel:
    """Modèle complet d'un cours — format intermédiaire JSON entre LLM et renderer."""

    # ── Identité ─────────────────────────────────────────────────────────────
    id: str                = field(default_factory=lambda: str(uuid.uuid4()))
    slug: str              = ""         # ex: "energie-electrique"
    title: str             = ""
    matiere: str           = ""         # ex: "Physique-Chimie Terminale"

    # ── Contenu ──────────────────────────────────────────────────────────────
    sections: list[Section]  = field(default_factory=list)
    quiz: list[QuizItem]     = field(default_factory=list)

    # ── Médias ───────────────────────────────────────────────────────────────
    assets: CourseAssets  = field(default_factory=CourseAssets)

    # ── Métadonnées ───────────────────────────────────────────────────────────
    generated_at: str     = field(default_factory=lambda: datetime.utcnow().isoformat())
    source_filename: str  = ""
    extraction_model: str = ""

    # ── Sérialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # convert Enums to their values for JSON serialisation
        for sec in d.get("sections", []):
            for f in sec.get("formulas", []):
                f["term_type"] = f["term_type"]  # already str via TermType(str)
            for k in sec.get("key_terms", []):
                k["term_type"] = k["term_type"]
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CourseModel":
        sections = [
            Section(
                id=s["id"],
                title=s["title"],
                content=s["content"],
                formulas=[Formula(
                    latex=f["latex"],
                    label=f["label"],
                    term_type=TermType(f.get("term_type", "def")),
                ) for f in s.get("formulas", [])],
                key_terms=[KeyTerm(
                    term=k["term"],
                    definition=k["definition"],
                    term_type=TermType(k.get("term_type", "def")),
                ) for k in s.get("key_terms", [])],
            )
            for s in d.get("sections", [])
        ]
        quiz = [QuizItem(question=q["question"], answer=q["answer"])
                for q in d.get("quiz", [])]
        assets_d = d.get("assets", {})
        return cls(
            id=d.get("id", str(uuid.uuid4())),
            slug=d.get("slug", ""),
            title=d.get("title", ""),
            matiere=d.get("matiere", ""),
            sections=sections,
            quiz=quiz,
            assets=CourseAssets(
                source_pdf=assets_d.get("source_pdf", ""),
                podcast_m4a=assets_d.get("podcast_m4a", ""),
                slides_pdf=assets_d.get("slides_pdf", ""),
                slide_count=assets_d.get("slide_count", 0),
            ),
            generated_at=d.get("generated_at", datetime.utcnow().isoformat()),
            source_filename=d.get("source_filename", ""),
            extraction_model=d.get("extraction_model", ""),
        )

    @classmethod
    def from_json(cls, s: str) -> "CourseModel":
        return cls.from_dict(json.loads(s))


# ── Job record ────────────────────────────────────────────────────────────────

@dataclass
class JobRecord:
    """Enregistrement d'un job de génération (persisté dans SQLite ou Supabase)."""
    id: str               = field(default_factory=lambda: str(uuid.uuid4())[:8])
    status: JobStatus     = JobStatus.PENDING
    title: str            = ""
    matiere: str          = ""
    slug: str             = ""
    config: dict          = field(default_factory=dict)  # {skip_audio, ...}
    step: str             = ""                           # étape courante
    error: str            = ""
    output_dir: str       = ""
    preview_url: str      = ""                           # URL locale ou GitHub Pages
    course_model_path: str = ""                          # chemin vers le JSON CourseModel
    created_at: str       = field(default_factory=lambda: datetime.utcnow().isoformat())
    started_at: str       = ""
    completed_at: str     = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status.value,
            "title": self.title,
            "matiere": self.matiere,
            "slug": self.slug,
            "config": self.config,
            "step": self.step,
            "error": self.error,
            "output_dir": self.output_dir,
            "preview_url": self.preview_url,
            "course_model_path": self.course_model_path,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }
