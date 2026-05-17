"""Extraction de texte et métadonnées depuis un PDF source (PyMuPDF).

Responsabilité unique : lire le fichier, extraire le texte brut page par page,
découper la présentation en pages individuelles.
"""

from __future__ import annotations

from pathlib import Path


def extract_text(pdf_path: Path) -> str:
    """Extrait le texte brut de toutes les pages du PDF (PyMuPDF)."""
    import fitz  # PyMuPDF — import tardif pour éviter des crashes si absent
    doc = fitz.open(pdf_path)
    try:
        return "\n\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def extract_metadata(pdf_path: Path) -> dict:
    """Retourne des métadonnées basiques (nombre de pages, taille)."""
    import fitz
    doc = fitz.open(pdf_path)
    try:
        return {
            "page_count": len(doc),
            "size_bytes": pdf_path.stat().st_size,
            "filename": pdf_path.name,
        }
    finally:
        doc.close()


def split_slides(slides_pdf: Path, output_dir: Path, slug: str) -> int:
    """Découpe le PDF de présentation en pages individuelles.

    Génère : output_dir/<slug>_presentation_page{N}.pdf
    Retourne le nombre de pages générées.
    """
    import fitz
    output_dir.mkdir(parents=True, exist_ok=True)
    src = fitz.open(slides_pdf)
    try:
        for i in range(len(src)):
            page_doc = fitz.open()
            page_doc.insert_pdf(src, from_page=i, to_page=i)
            page_doc.save(output_dir / f"{slug}_presentation_page{i + 1}.pdf")
            page_doc.close()
        return len(src)
    finally:
        src.close()
