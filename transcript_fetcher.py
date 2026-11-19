"""Récupération de la retranscription textuelle sur la page Radio France de l'épisode."""

from __future__ import annotations

import json
import re
from html import unescape
from urllib.parse import urlparse

import requests

USER_AGENT = "EditosINSP/1.0 (+préparation INSP; transcription page épisode)"
FETCH_TIMEOUT = (8, 25)
_TEXT_CHUNK_RE = re.compile(r'\{type:"text",value:"((?:\\.|[^"\\])*)"\}')


class TranscriptError(Exception):
    """Impossible de lire la retranscription sur la page de l'épisode."""


def _decode_chunk(raw: str) -> str:
    try:
        text = json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        text = raw
    text = unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()


def is_radiofrance_url(url: str | None) -> bool:
    """Seules les pages radiofrance.fr publient une retranscription que l'on sait lire."""
    host = (urlparse(url or "").netloc or "").lower()
    return host == "www.radiofrance.fr" or host.endswith(".radiofrance.fr")


def fetch_transcript(episode_url: str) -> str:
    """Télécharge la page épisode et en extrait les paragraphes de retranscription."""
    url = (episode_url or "").strip()
    if not url.startswith("http"):
        raise TranscriptError("Aucun lien vers la page de l'épisode (champ « Page de l'épisode » du flux).")
    if not is_radiofrance_url(url):
        raise TranscriptError("Seules les pages radiofrance.fr sont acceptées pour la retranscription.")

    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=FETCH_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise TranscriptError(f"Page de l'épisode inaccessible ({exc}).") from exc

    # Radio France sert du UTF-8 mais l'en-tête HTTP peut induire requests en erreur (ISO-8859-1).
    try:
        html = response.content.decode("utf-8")
    except UnicodeDecodeError:
        response.encoding = response.apparent_encoding or "utf-8"
        html = response.text
    paragraphs: list[str] = []
    seen: set[str] = set()
    for raw in _TEXT_CHUNK_RE.findall(html):
        chunk = _decode_chunk(raw)
        if len(chunk) < 50 or chunk in seen:
            continue
        seen.add(chunk)
        paragraphs.append(chunk)

    if not paragraphs:
        raise TranscriptError(
            "Aucune retranscription trouvée sur cette page. Essayez une resynchronisation du flux, "
            "ou rédigez / importez la fiche à la main."
        )

    body = "\n\n".join(paragraphs)
    if len(body) < 200:
        raise TranscriptError("La retranscription extraite est trop courte pour générer une fiche fiable.")
    return body