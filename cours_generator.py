#!/usr/bin/env python3
"""Génère un site de cours depuis un PDF via NotebookLM + Claude."""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import fitz
import google.generativeai as genai
from dotenv import load_dotenv


load_dotenv()

GENERATION_TIMEOUT_MIN = 60
POLL_INTERVAL_SEC = 30
GEMINI_MODEL = "gemini-2.0-flash"
HTML_MAX_TOKENS = 16000

SYSTEM_PROMPT = r"""
Tu es un expert en création de sites web pédagogiques.
Tu vas générer un fichier HTML complet et autonome
(tout en un seul fichier, CSS et JS inclus) pour un chapitre de cours lycée.

STYLE OBLIGATOIRE — Apple Liquid Glass :
- Font : -apple-system, BlinkMacSystemFont, SF Pro Display
- Background animé : deux blobs colorés floutés
  (filter: blur(100px), opacity: 0.35) qui bougent lentement
- Navigation sticky avec backdrop-filter: blur(30px) saturate(180%)
- Cards avec background: rgba(255,255,255,0.75),
  border: 1px solid rgba(255,255,255,0.4),
  box-shadow: 0 20px 40px rgba(0,0,0,0.08)
- Border-radius 20-30px partout
- Mode sombre toggle via data-theme=dark sur le body
- Barre de progression scroll en haut (div#progress)

COULEURS SÉMANTIQUES :
- .c-def → #0071e3 (Lois et Principes)
- .c-var → #af52de (Grandeurs Physiques)
- .c-unit → #34c759 (Rendement et Atouts)
- .c-trap → #ff9500 (Points de vigilance)
- .c-danger → #ff3b30 (Pertes et Risques)

SECTIONS OBLIGATOIRES :

1. Navigation sticky :
   - Logo avec nom du chapitre
   - Input recherche id="searchInput"
   - Bouton mode sombre onclick="toggleTheme()"
   - Bouton mode révision id="revBtn" onclick="toggleRevisionMode()"

2. Hero section :
   - Grand titre h1
   - Sous-titre matière
   - Les deux blobs animés

3. Quick Access bar sticky :
   - 4 boutons : Support PDF, Présentation, Podcast, Quiz

4. Dashboard (id="mainDashboard") :
   - UNIQUEMENT une tile par section du cours détectée dans le texte
   - NE PAS créer de tiles pour Support PDF / Présentation / Podcast / Quiz
     (ces accès sont déjà dans la Quick Access bar — ne pas dupliquer en cartes)

5. Pages de contenu (id="p1", "p2", etc.) :
   - display:none par défaut
   - Bouton retour onclick="closePage()"
   - Titre h1 de la section
   - Texte avec spans colorés
   - Formula cards avec MathJax pour chaque formule :
     <div class="formula-card">
       <span class="formula-math">\( FORMULE \)</span>
     </div>

6. Visionneuse PDF (id="p-viewer") :
   - iframe id="viewerFrame"
   - Navigation précédent/suivant
   - Affichage Page X/Y
   - Mode portrait pour cours, paysage pour présentation
   - Fichiers : {chapter_name}_cours.pdf
     et Presentation/{chapter_name}_presentation_page{N}.pdf

7. Page Audio (id="p-audio") :
   - <audio controls style="width:100%">
   - <source src="{chapter_name}_podcast.m4a">

8. Page Quiz (id="p-quiz") :
   - 8 questions minimum basées sur le contenu
   - Flip cards CSS : clic pour retourner
   - Array quizData = [{q:"question", a:"réponse"}]
   - Bouton Suivant

JAVASCRIPT OBLIGATOIRE :
- openPage(id) : cache dashboard, affiche page, appelle MathJax.typeset()
- closePage() : pause audio/video, cache page, réaffiche dashboard
- initViewer(type, pages) : initialise visionneuse PDF
- changePage(delta) : navigation visionneuse
- toggleTheme() : bascule dark/light
- toggleRevisionMode() : blur/unblur .formula-math
- Recherche live sur les tiles
- Scroll progress bar

MathJax CDN : https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js
Réponds UNIQUEMENT avec le HTML complet, aucun texte avant ou après.
"""


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, text=True)


def notebooklm_setup(pdf_path: Path, title: str) -> None:
    print(f"📚 Création du notebook « {title} »...")
    run(["notebooklm", "create", title])
    print(f"📎 Ajout du PDF : {pdf_path.name}")
    run(["notebooklm", "source", "add", str(pdf_path)])


def generate_and_download(kind: str, output: Path, prompt: str | None = None) -> bool:
    """Lance la génération NotebookLM puis poll le download jusqu'à succès.

    Polling: tente `notebooklm download` toutes les 30 s. Code retour 0
    + fichier non-vide → prêt. Sinon on attend. Timeout 60 min.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    print(f"⏳ Lancement de la génération {kind}...")
    cmd = ["notebooklm", "generate", kind]
    if prompt:
        cmd.append(prompt)
    run(cmd)

    print(f"⏳ Polling du téléchargement {kind} (timeout {GENERATION_TIMEOUT_MIN} min)...")
    start = time.time()
    while True:
        elapsed_min = int((time.time() - start) / 60)
        print(f"\r⏳ {kind}: {elapsed_min} min écoulées... ", end="", flush=True)

        result = subprocess.run(
            ["notebooklm", "download", kind, "--output", str(output)],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and output.exists() and output.stat().st_size > 0:
            print(f"\n✅ {kind} prêt en {elapsed_min} min → {output}")
            return True

        if elapsed_min >= GENERATION_TIMEOUT_MIN:
            print(f"\n⚠️  Timeout après {GENERATION_TIMEOUT_MIN} min pour {kind}.")
            print(f"   Le fichier est peut-être quand même disponible.")
            print(f"   Vérifie sur https://notebooklm.google.com, télécharge manuellement vers")
            print(f"   {output}, puis relance avec --skip-generation.")
            return False

        time.sleep(POLL_INTERVAL_SEC)


def extract_pdf_text(pdf_path: Path) -> str:
    """Extrait le texte brut de toutes les pages du PDF avec PyMuPDF."""
    doc = fitz.open(pdf_path)
    try:
        return "\n\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def split_pdf(pdf_path: Path, output_dir: Path, chapter_name: str) -> int:
    """Découpe le PDF page par page en {chapter_name}_presentation_page{N}.pdf.

    Retourne le nombre de pages générées.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    src = fitz.open(pdf_path)
    try:
        for i in range(len(src)):
            page_doc = fitz.open()
            page_doc.insert_pdf(src, from_page=i, to_page=i)
            page_doc.save(output_dir / f"{chapter_name}_presentation_page{i + 1}.pdf")
            page_doc.close()
        return len(src)
    finally:
        src.close()


def generate_html(pdf_text: str, chapter_name: str, titre: str, matiere: str) -> str:
    """Appelle Gemini pour générer le site HTML complet en un seul fichier."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY introuvable. Crée un fichier .env avec GEMINI_API_KEY=..."
        )
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        system_instruction=SYSTEM_PROMPT,
    )
    user_prompt = (
        f"Titre: {titre} | Matière: {matiere} | Fichier: {chapter_name}\n{pdf_text}"
    )
    response = model.generate_content(
        user_prompt,
        generation_config={"max_output_tokens": HTML_MAX_TOKENS},
    )
    return response.text


def build_site(
    output_dir: Path, pdf_path: Path, chapter_name: str, titre: str, matiere: str
) -> Path:
    print("📖 Extraction du texte du PDF...")
    pdf_text = extract_pdf_text(pdf_path)
    print(f"   {len(pdf_text)} caractères extraits.")

    cours_copy = output_dir / f"{chapter_name}_cours.pdf"
    if pdf_path.resolve() != cours_copy.resolve():
        shutil.copy(pdf_path, cours_copy)
        print(f"📄 PDF source copié → {cours_copy.name}")

    print(f"🌐 Génération du HTML via {GEMINI_MODEL}...")
    html = generate_html(pdf_text, chapter_name, titre, matiere)
    index = output_dir / "index.html"
    index.write_text(html, encoding="utf-8")
    print(f"   → {index}")
    return index


def git_push(output_dir: Path, title: str) -> None:
    print("🚀 Push Git...")
    run(["git", "add", str(output_dir)])
    run(["git", "commit", "-m", f"Add course site: {title}"])
    run(["git", "push", "origin", "main"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path, help="Chemin vers le PDF source")
    parser.add_argument("--title", help="Titre du cours (défaut : nom du PDF)")
    parser.add_argument("--matiere", default="", help="Matière (ex: 'Physique-Chimie')")
    parser.add_argument("--output-dir", type=Path, default=Path("output"),
                        help="Dossier de sortie (défaut : ./output)")
    parser.add_argument("--skip-generation", action="store_true",
                        help="Saute l'étape NotebookLM (fichiers déjà présents)")
    parser.add_argument("--skip-push", action="store_true",
                        help="Saute le git push")
    parser.add_argument("--html-only", action="store_true",
                        help="Génère uniquement le HTML (implique --skip-generation et --skip-push)")
    args = parser.parse_args()

    if args.html_only:
        args.skip_generation = True
        args.skip_push = True

    if not args.pdf.exists():
        print(f"❌ PDF introuvable : {args.pdf}", file=sys.stderr)
        return 1

    chapter_name = args.pdf.stem
    title = args.title or chapter_name
    args.output_dir.mkdir(parents=True, exist_ok=True)
    audio_file = args.output_dir / f"{chapter_name}_podcast.m4a"
    slides_dir = args.output_dir / "Presentation"
    slides_source = slides_dir / f"{chapter_name}_presentation.pdf"

    if not args.skip_generation:
        notebooklm_setup(args.pdf, title)
        ok_audio = generate_and_download(
            "audio", audio_file, prompt=f"Podcast pédagogique sur : {title}"
        )
        ok_slides = generate_and_download("slide-deck", slides_source)
        if not (ok_audio and ok_slides):
            return 2

    if args.html_only:
        print("⏭️  Mode --html-only : NotebookLM, vérifs fichiers et split sautés.")
    else:
        if args.skip_generation:
            print("⏭️  Étape NotebookLM sautée.")
            for f in (audio_file, slides_source):
                if not f.exists():
                    print(f"❌ Fichier manquant : {f}", file=sys.stderr)
                    print(f"   Télécharge-le manuellement puis relance.", file=sys.stderr)
                    return 1

        print("✂️  Découpage de la présentation par page...")
        n_pages = split_pdf(slides_source, slides_dir, chapter_name)
        print(f"   {n_pages} pages → {slides_dir}/")

    build_site(args.output_dir, args.pdf, chapter_name, title, args.matiere)

    if args.skip_push:
        print("⏭️  Git push sauté.")
    else:
        git_push(args.output_dir, title)

    print("✨ Terminé.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
