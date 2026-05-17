"""Renderer : CourseModel → HTML via Jinja2.

Responsabilité unique : prendre un CourseModel et produire un fichier index.html
en utilisant le template Jinja2 templates/course/base.html.

Avantages par rapport à la génération HTML libre par le LLM :
- HTML toujours bien formé (template contrôlé)
- Style et JS cohérents (pas de variation selon le LLM)
- Re-rendu possible sans ré-appeler le LLM
- Modifications globales de style = changer le template, pas le prompt
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from core.config import TEMPLATE_DIR
from core.models import CourseModel


COURSE_TEMPLATE_DIR = TEMPLATE_DIR / "course"


def render_course(model: CourseModel, output_path: Path) -> None:
    """Rend le CourseModel en HTML et écrit le résultat dans output_path."""
    env = Environment(
        loader=FileSystemLoader(str(COURSE_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    template = env.get_template("base.html")
    html = template.render(course=model)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")


def render_course_to_string(model: CourseModel) -> str:
    """Variante qui retourne le HTML sans écrire sur disque (utile pour preview)."""
    env = Environment(
        loader=FileSystemLoader(str(COURSE_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    return env.get_template("base.html").render(course=model)
