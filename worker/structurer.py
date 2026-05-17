"""Structuration du contenu : PDF text → CourseModel JSON via LLM.

Changement fondamental par rapport au prototype :
  Avant : LLM → HTML libre (fragile, non éditable, non versionnable)
  Maintenant : LLM → JSON structuré → renderer → HTML contrôlé

Le LLM reçoit le texte brut du PDF et produit un JSON valide correspondant
à CourseModel. Le renderer (renderer.py) se charge ensuite de le transformer
en HTML à partir de templates Jinja2 fixes.

Avantages :
- Le JSON peut être édité manuellement avant rendu
- Le HTML peut être re-généré sans ré-appeler le LLM
- La structure est vérifiable (on valide le JSON)
- Le prompt est plus court et plus fiable (demander JSON < demander HTML)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from core.config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_MAX_TOKENS
from core.models import CourseModel


# ── Prompt système ────────────────────────────────────────────────────────────

STRUCTURING_PROMPT = """\
Tu es un expert en pédagogie qui structure le contenu d'un cours pour lycéens.

À partir du texte d'un PDF de cours, tu dois extraire et structurer les informations
en JSON. Ne génère AUCUN HTML. Réponds UNIQUEMENT avec le JSON valide, sans markdown,
sans balises de code, sans texte avant ou après.

Format JSON attendu :
{
  "title": "Titre du cours",
  "matiere": "Matière — Niveau",
  "sections": [
    {
      "id": "s1",
      "title": "Titre de la section",
      "content": "Contenu textuel principal de la section (paragraphes clés, explications). Environ 150-300 mots.",
      "formulas": [
        {
          "latex": "P = U \\\\cdot I",
          "label": "Puissance électrique",
          "term_type": "def"
        }
      ],
      "key_terms": [
        {
          "term": "Puissance",
          "definition": "Énergie transférée par unité de temps (W = J/s)",
          "term_type": "def"
        }
      ]
    }
  ],
  "quiz": [
    {
      "question": "Quelle est la formule de la puissance électrique ?",
      "answer": "P = U × I (en watts)"
    }
  ]
}

Valeurs possibles pour term_type :
- "def"    → loi, principe, définition fondamentale (bleu)
- "var"    → grandeur physique, variable (violet)
- "unit"   → unité, rendement, point positif (vert)
- "trap"   → point de vigilance, erreur fréquente (orange)
- "danger" → danger, perte, risque (rouge)

Règles :
- Crée une section par grand thème détecté dans le cours (3 à 8 sections)
- Extrais TOUTES les formules mathématiques
- Identifie 2 à 5 termes clés par section
- Génère au minimum 8 questions de quiz pertinentes
- Le contenu de chaque section doit être une synthèse claire, pas une copie verbatim
- Le JSON doit être valide (utilise \\\\n pour les sauts de ligne dans les strings)
- N'invente aucune information absente du PDF
"""


# ── Appel LLM ─────────────────────────────────────────────────────────────────

def structure_course(
    pdf_text: str,
    title: str,
    matiere: str,
    slug: str,
    source_filename: str,
) -> CourseModel:
    """Appelle Gemini pour structurer le texte PDF en CourseModel.

    Raises RuntimeError si :
    - GEMINI_API_KEY absent
    - Le LLM ne retourne pas du JSON valide
    """
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY manquante dans .env")

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_API_KEY)

    user_prompt = (
        f"Titre fourni par l'utilisateur : {title}\n"
        f"Matière : {matiere}\n"
        f"Fichier source : {source_filename}\n\n"
        f"=== TEXTE DU COURS ===\n{pdf_text[:40000]}"  # limite de sécurité
    )

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=STRUCTURING_PROMPT,
            max_output_tokens=GEMINI_MAX_TOKENS,
        ),
    )

    raw = response.text.strip()

    # Nettoie les éventuelles balises markdown que le LLM ajouterait malgré la consigne
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Le LLM n'a pas retourné du JSON valide.\n"
            f"Erreur : {e}\n"
            f"Début de la réponse :\n{raw[:500]}"
        ) from e

    # Injecte les champs qui ne viennent pas du LLM
    data["slug"]            = slug
    data["source_filename"] = source_filename
    data["extraction_model"] = GEMINI_MODEL
    # Normalise title/matiere si le LLM les a renseignés différemment
    data.setdefault("title",   title)
    data.setdefault("matiere", matiere)

    return CourseModel.from_dict(data)
