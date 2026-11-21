"""Mesure le délai réseau + API Gemini depuis votre PC (hors interface Streamlit).

Usage (venv activé, depuis la racine du projet) :
    python scripts/benchmark_gemini.py

Optionnel : URL d'un épisode radiofrance.fr
    python scripts/benchmark_gemini.py "https://www.radiofrance.fr/..."
"""

from __future__ import annotations

import sys
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_api_key() -> str:
    path = ROOT / ".streamlit" / "secrets.toml"
    if not path.is_file():
        raise SystemExit(f"Fichier introuvable : {path}")
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    key = str(data.get("GEMINI_API_KEY", "")).strip()
    if not key:
        raise SystemExit("GEMINI_API_KEY manquant dans .streamlit/secrets.toml")
    return key


def bench_transcript(url: str) -> tuple[float, int]:
    import transcript_fetcher

    start = time.perf_counter()
    text = transcript_fetcher.fetch_transcript(url)
    return time.perf_counter() - start, len(text)


def bench_gemini_minimal(model: str) -> tuple[float, str]:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=load_api_key())
    config = types.GenerateContentConfig(thinking_config=types.ThinkingConfig(thinking_budget=0))
    start = time.perf_counter()
    response = client.models.generate_content(
        model=model,
        contents="Réponds exactement par le mot : PING",
        config=config,
    )
    elapsed = time.perf_counter() - start
    return elapsed, (response.text or "").strip()[:80]


def main() -> None:
    model = "gemini-3.5-flash-lite"
    episode_url = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "https://www.radiofrance.fr/franceinter/podcasts/l-edito-politique"
    )
    print(f"Modèle : {model}")
    print(f"Page test : {episode_url}\n")

    try:
        t_tr, chars = bench_transcript(episode_url)
        print(f"1) Retranscription Radio France : {t_tr:.1f} s ({chars} caractères)")
    except Exception as exc:
        print(f"1) Retranscription : ÉCHEC — {exc}")
        t_tr, chars = -1.0, 0

    try:
        t_api, ping = bench_gemini_minimal(model)
        print(f"2) Appel Gemini minimal (mot PING) : {t_api:.1f} s — réponse : {ping!r}")
    except Exception as exc:
        print(f"2) Appel Gemini minimal : ÉCHEC — {exc}")
        t_api = -1.0

    print()
    if t_api >= 0 and t_api > 60:
        print(
            "→ L'appel API seul dépasse 1 min : le blocage vient du réseau/PC/FAI "
            "vers Google, pas de Streamlit ni de la taille de la fiche."
        )
    elif t_tr >= 0 and t_tr > 30:
        print("→ La page Radio France met trop de temps à répondre depuis chez vous.")
    elif t_api >= 0 and t_api < 30 and chars > 0:
        print(
            "→ Réseau et API Gemini sont rapides ici. Si l'interface met 5 min, ouvrez "
            "le statut de génération et cherchez des messages « code 503 » (réessais)."
        )


if __name__ == "__main__":
    main()