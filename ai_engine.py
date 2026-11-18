"""Moteur IA : fiches de révision depuis l'audio intégral et synthèse mensuelle (SDK google-genai)."""

from __future__ import annotations

import logging
import os
import tempfile
import time
from typing import Any, Callable
from urllib.parse import urlparse

import requests
import streamlit as st
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-4-hydrogen"
MODEL_CHOICES = ("gemini-4-hydrogen", "gemini-4-argon", "gemini-4-carbon", "gemini-3.8-flash")

DOWNLOAD_TIMEOUT = (10, 120)
FILE_READY_TIMEOUT = 300
USER_AGENT = "EditosINSP/1.0 (+suivi personnel de podcasts)"

FICHE_SECTIONS = (
    "1. Thèse centrale et problématique",
    "2. Faits, chiffres précis et dates clés",
    "3. Acteurs, institutions et rapports de force",
    "4. Enjeux pour l'action publique et ouverture concours",
)

# Types MIME audio acceptés par Gemini, indexés par extension de fichier.
_MIME_BY_EXTENSION = {
    ".mp3": "audio/mp3",
    ".m4a": "audio/m4a",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".aiff": "audio/aiff",
    ".webm": "audio/webm",
}
_MIME_ALIASES = {
    "audio/x-m4a": "audio/m4a",
    "audio/mp4": "audio/m4a",
    "audio/mpeg": "audio/mp3",
    "audio/mpeg3": "audio/mp3",
    "audio/x-mp3": "audio/mp3",
    "audio/x-wav": "audio/wav",
    "audio/x-aac": "audio/aac",
    "audio/x-flac": "audio/flac",
}

_SYSTEM_INSTRUCTION = (
    "Tu es un préparateur expérimenté aux concours de l'Institut national du service public (INSP). "
    "Tu rédiges en français, dans un style dense, précis et neutre, des fiches de révision directement "
    "exploitables en culture générale, en questions contemporaines, en économie, en questions "
    "européennes et internationales et pour l'oral. Tu n'inventes jamais un chiffre, une date ou une citation."
)

_FICHE_PROMPT = """Écoute l'intégralité de cet enregistrement audio, une chronique de France Inter, puis rédige \
une fiche de révision complète pour un candidat au concours de l'INSP.

Contexte de l'épisode :
- Émission : {show}
- Date de diffusion : {date}
- Titre : {title}
- Intervenant(s) : {author}
- Résumé du flux RSS : {summary}

Structure obligatoire : exactement ces quatre parties, dans cet ordre, avec ces intitulés comme titres \
Markdown de niveau 2 :

## {s1}
La thèse défendue par le chroniqueur, formulée en deux ou trois phrases, puis la problématique qu'elle \
soulève, rédigée sous forme de question.

## {s2}
Liste à puces de tous les faits, chiffres, dates, textes et citations mentionnés dans l'audio, restitués \
avec exactitude et avec leur unité ou leur source quand elle est donnée.

## {s3}
Liste à puces des acteurs (personnalités, partis, États, entreprises, institutions, juridictions), de \
leur position et des rapports de force qui les opposent ou les lient.

## {s4}
Ce que le sujet implique pour la décision et l'action publiques (leviers, contraintes juridiques et \
budgétaires, arbitrages), puis une ouverture concours : notions et références à mobiliser, deux ou trois \
sujets de composition ou questions d'oral auxquels cet édito peut servir d'accroche ou d'exemple.

Règles :
- Appuie-toi d'abord sur ce qui est réellement dit dans l'audio. Si un chiffre ou un nom est inaudible \
ou incertain, signale-le par « (à vérifier) » au lieu de le deviner.
- Les compléments qui viennent de tes connaissances et non de l'audio sont réservés à la partie 4 et \
introduits par « Pour aller plus loin : ».
- Distingue les faits de l'opinion du chroniqueur.
- Mise en forme Markdown sobre : titres de niveau 2 pour les quatre parties, listes à puces avec « - », \
gras pour les notions clés. Pas de tableau, pas de bloc de code, pas d'émoji.
- Commence directement par le premier titre, sans introduction ni conclusion hors plan.
"""

_SYNTHESIS_PROMPT = """Voici l'ensemble des chroniques de France Inter suivies en {month_label} par un candidat \
au concours de l'INSP ({count} épisodes). Pour chaque épisode figurent l'émission, la date, le titre, le \
niveau de priorité attribué par le candidat (1 secondaire, 2 important, 3 incontournable), le résumé du \
flux RSS et, lorsqu'elle existe, la fiche de révision détaillée.

Rédige une synthèse transversale des grandes tendances du mois, structurée ainsi (titres Markdown de niveau 2) :

## Vue d'ensemble du mois
Cinq à six lignes sur le climat général et les faits dominants.

## Tendances politiques
## Tendances économiques
## Tendances géopolitiques
Pour chacune de ces trois parties : les trois à cinq dynamiques majeures, chacune illustrée par les épisodes \
qui la documentent (date et titre entre parenthèses) et par les faits ou chiffres marquants.

## Fils rouges et lignes de fracture
Les thèmes qui traversent plusieurs émissions et les liens entre politique intérieure, économie et \
international.

## À retenir pour le concours
Les notions, débats et exemples à mémoriser en priorité, et trois à cinq sujets de composition ou questions \
d'oral que l'actualité du mois rend probables.

Règles :
- Fonde-toi uniquement sur le corpus fourni ; n'ajoute aucun fait qui n'y figure pas. Si une rubrique est \
peu documentée ce mois-ci, dis-le.
- Accorde davantage de poids aux épisodes de priorité 2 et 3.
- Markdown sobre : titres de niveau 2, listes à puces avec « - », gras pour les notions clés. Pas de \
tableau, pas d'émoji.

Corpus du mois :

{corpus}
"""


class AIEngineError(Exception):
    """Erreur présentable à l'utilisateur (clé absente, audio indisponible, refus de l'API...)."""


def get_api_key() -> str | None:
    try:
        key = st.secrets["GEMINI_API_KEY"]
    except Exception:
        return None
    key = str(key).strip()
    return key or None


def is_configured() -> bool:
    return get_api_key() is not None


def get_client() -> genai.Client:
    api_key = get_api_key()
    if not api_key:
        raise AIEngineError(
            "Clé Gemini introuvable. Ajoutez GEMINI_API_KEY dans .streamlit/secrets.toml "
            "(ou dans les secrets de votre hébergement Streamlit)."
        )
    return genai.Client(api_key=api_key)


def resolve_mime_type(audio_url: str, declared_type: str | None = None, header_type: str | None = None) -> str:
    """Type MIME transmis à Gemini : l'extension réelle du fichier prime sur les types déclarés."""
    extension = os.path.splitext(urlparse(audio_url).path)[1].lower()
    if extension in _MIME_BY_EXTENSION:
        return _MIME_BY_EXTENSION[extension]
    for candidate in (declared_type, header_type):
        if not candidate:
            continue
        mime = candidate.split(";")[0].strip().lower()
        mime = _MIME_ALIASES.get(mime, mime)
        if mime in _MIME_BY_EXTENSION.values():
            return mime
    return "audio/mp3"


def _extension_for(mime_type: str) -> str:
    return next((ext for ext, mime in _MIME_BY_EXTENSION.items() if mime == mime_type), ".mp3")


def _download_audio(audio_url: str, declared_type: str | None) -> tuple[str, str]:
    """Télécharge l'audio dans un fichier temporaire. Retourne (chemin, type MIME)."""
    try:
        response = requests.get(
            audio_url, stream=True, timeout=DOWNLOAD_TIMEOUT, headers={"User-Agent": USER_AGENT}
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise AIEngineError(f"Téléchargement de l'audio impossible : {exc}") from exc

    header_type = response.headers.get("Content-Type")
    if header_type and header_type.lower().startswith("text/"):
        response.close()
        raise AIEngineError("Le lien audio de cet épisode ne renvoie pas un fichier son.")
    mime_type = resolve_mime_type(audio_url, declared_type, header_type)

    handle = tempfile.NamedTemporaryFile(prefix="edito_", suffix=_extension_for(mime_type), delete=False)
    size = 0
    try:
        with handle, response:
            for chunk in response.iter_content(chunk_size=1 << 16):
                if chunk:
                    handle.write(chunk)
                    size += len(chunk)
    except (requests.RequestException, OSError) as exc:
        _remove_local(handle.name)
        raise AIEngineError(f"Téléchargement de l'audio interrompu : {exc}") from exc
    if size == 0:
        _remove_local(handle.name)
        raise AIEngineError("Le fichier audio téléchargé est vide.")
    return handle.name, mime_type


def _remove_local(path: str | None) -> None:
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            logger.warning("Fichier temporaire non supprimé : %s", path)


def _state_name(file: types.File) -> str:
    state = getattr(file, "state", None)
    return str(getattr(state, "name", state) or "").upper()


def _wait_until_active(client: genai.Client, file: types.File) -> types.File:
    deadline = time.monotonic() + FILE_READY_TIMEOUT
    while _state_name(file) == "PROCESSING":
        if time.monotonic() > deadline:
            raise AIEngineError("Gemini met trop de temps à préparer le fichier audio. Réessayez plus tard.")
        time.sleep(2)
        file = client.files.get(name=file.name)
    if _state_name(file) == "FAILED":
        raise AIEngineError("Gemini n'a pas réussi à traiter le fichier audio.")
    return file


def _response_text(response: types.GenerateContentResponse) -> str:
    text = (response.text or "").strip()
    if text:
        return text
    reason = None
    if response.candidates:
        reason = getattr(response.candidates[0], "finish_reason", None)
    if not reason and response.prompt_feedback:
        reason = getattr(response.prompt_feedback, "block_reason", None)
    detail = f" (motif : {getattr(reason, 'name', reason)})" if reason else ""
    raise AIEngineError(f"Gemini n'a renvoyé aucun texte{detail}.")


def _api_error_message(exc: genai_errors.APIError) -> str:
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None) or str(exc)
    return f"Erreur de l'API Gemini{f' ({code})' if code else ''} : {message}"


def generate_fiche(
    episode: dict[str, Any],
    model: str | None = None,
    on_step: Callable[[str], None] | None = None,
) -> str:
    """Génère la fiche INSP d'un épisode à partir de son audio intégral.

    L'audio est téléchargé dans un fichier temporaire, envoyé à Gemini via l'API Files, analysé,
    puis le fichier local et le fichier distant sont supprimés, y compris en cas d'échec.
    """
    notify = on_step or (lambda _message: None)
    audio_url = episode.get("audio_url")
    if not audio_url:
        raise AIEngineError("Cet épisode n'a pas de fichier audio dans le flux RSS.")
    client = get_client()

    local_path: str | None = None
    uploaded: types.File | None = None
    try:
        notify("Téléchargement de l'audio de l'épisode…")
        local_path, mime_type = _download_audio(audio_url, episode.get("audio_type"))

        notify("Envoi de l'audio à Gemini…")
        uploaded = client.files.upload(file=local_path, config=types.UploadFileConfig(mime_type=mime_type))
        uploaded = _wait_until_active(client, uploaded)

        notify("Analyse de l'audio intégral et rédaction de la fiche…")
        prompt = _FICHE_PROMPT.format(
            show=episode.get("show_title") or "Chronique France Inter",
            date=episode.get("pub_date") or "non précisée",
            title=episode.get("title") or "sans titre",
            author=episode.get("author") or "non précisé",
            summary=episode.get("summary") or "non fourni",
            s1=FICHE_SECTIONS[0],
            s2=FICHE_SECTIONS[1],
            s3=FICHE_SECTIONS[2],
            s4=FICHE_SECTIONS[3],
        )
        response = client.models.generate_content(
            model=model or DEFAULT_MODEL,
            contents=[uploaded, prompt],
            config=types.GenerateContentConfig(system_instruction=_SYSTEM_INSTRUCTION),
        )
        return _response_text(response)
    except AIEngineError:
        raise
    except genai_errors.APIError as exc:
        raise AIEngineError(_api_error_message(exc)) from exc
    except Exception as exc:
        raise AIEngineError(f"Échec de l'appel à Gemini ({type(exc).__name__}) : {exc}") from exc
    finally:
        _remove_local(local_path)
        if uploaded is not None and uploaded.name:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                logger.warning("Fichier distant Gemini non supprimé : %s", uploaded.name, exc_info=True)
                notify(
                    "Le fichier audio n'a pas pu être supprimé des serveurs Gemini ; "
                    "il y expirera automatiquement sous 48 heures."
                )


def build_month_corpus(episodes: list[dict[str, Any]]) -> str:
    blocks = []
    for ep in episodes:
        lines = [
            f"### {ep.get('show_title') or 'Émission'} — {ep.get('pub_date')} — {ep.get('title')}",
            f"Priorité : {ep.get('priority', 1)}",
        ]
        if ep.get("summary"):
            lines.append(f"Résumé RSS : {ep['summary']}")
        if (ep.get("fiche") or "").strip():
            lines.append(f"Fiche de révision :\n{ep['fiche'].strip()}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def generate_monthly_synthesis(month_label: str, episodes: list[dict[str, Any]], model: str | None = None) -> str:
    """Synthèse transversale des tendances politiques, économiques et géopolitiques du mois."""
    if not episodes:
        raise AIEngineError("Aucun épisode enregistré pour ce mois : rien à synthétiser.")
    client = get_client()
    prompt = _SYNTHESIS_PROMPT.format(
        month_label=month_label, count=len(episodes), corpus=build_month_corpus(episodes)
    )
    try:
        response = client.models.generate_content(
            model=model or DEFAULT_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=_SYSTEM_INSTRUCTION),
        )
    except genai_errors.APIError as exc:
        raise AIEngineError(_api_error_message(exc)) from exc
    except Exception as exc:
        raise AIEngineError(f"Échec de l'appel à Gemini ({type(exc).__name__}) : {exc}") from exc
    return _response_text(response)
