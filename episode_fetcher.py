"""Ajout d'un épisode à l'unité : lecture de la page web d'un épisode, ou d'un flux RSS où le choisir."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any, Iterator
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import rss_fetcher

REQUEST_TIMEOUT = (8, 25)
MAX_BYTES = 20 * 1024 * 1024
USER_AGENT = "EditosINSP/1.0 (+suivi personnel de podcasts; ajout d'un épisode)"

_EPISODE_TYPES = {"RadioEpisode", "PodcastEpisode", "Episode", "AudioObject"}
_ARTICLE_TYPES = {"NewsArticle", "Article", "BlogPosting", "Report"}
_SERIES_TYPES = {"RadioSeries", "PodcastSeries", "CreativeWorkSeries"}
_AUDIO_URL_RE = re.compile(r"https?://[^\s\"'<>\\]+?\.(?:mp3|m4a|aac|ogg|opus|wav)(?:\?[^\s\"'<>\\]*)?", re.IGNORECASE)
_ISO_DURATION_RE = re.compile(
    r"^P(?:\d+Y)?(?:\d+M)?(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)


class EpisodeError(Exception):
    """Page injoignable ou dont on ne peut pas tirer un épisode."""


@dataclass
class Lookup:
    """Résultat d'une recherche : un épisode pour une page web, tous ceux du flux pour une adresse RSS."""

    show_title: str
    episodes: list[dict[str, Any]] = field(default_factory=list)
    from_feed: bool = False


# -------------------------------------------------------------------- petits outils


def _nodes(value: Any) -> Iterator[dict[str, Any]]:
    """Tous les objets d'un bloc JSON-LD, quel que soit leur niveau d'imbrication."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _nodes(child)


def _types(node: dict[str, Any]) -> set[str]:
    kinds = node.get("@type")
    return {str(kind) for kind in kinds} if isinstance(kinds, list) else {str(kinds)} if kinds else set()


def _first_node(nodes: list[dict[str, Any]], kinds: set[str]) -> dict[str, Any]:
    return next((node for node in nodes if _types(node) & kinds), {})


def _text(value: Any) -> str:
    """Texte d'une valeur JSON-LD, qui peut être une chaîne, un objet nommé ou une liste."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return _text(value.get("name") or value.get("url") or value.get("@id"))
    if isinstance(value, list):
        return ", ".join(part for part in (_text(item) for item in value) if part)
    return ""


def _url(value: Any, base: str) -> str | None:
    """Première adresse http(s) d'une valeur JSON-LD ou d'un attribut HTML."""
    if isinstance(value, list):
        return next((found for found in (_url(item, base) for item in value) if found), None)
    if isinstance(value, dict):
        return _url(value.get("contentUrl") or value.get("url") or value.get("@id"), base)
    if not isinstance(value, str) or not value.strip():
        return None
    absolute = urljoin(base, value.strip())
    return absolute if absolute.startswith(("http://", "https://")) else None


def parse_moment(raw: Any) -> tuple[str, str] | None:
    """(date AAAA-MM-JJ, horodatage ISO) à l'heure de Paris ; l'horodatage se réduit à la date si l'heure manque."""
    text = _text(raw)
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        try:
            day = date.fromisoformat(text[:10])
        except ValueError:
            return None
        return day.isoformat(), day.isoformat()
    if moment.tzinfo is not None:
        try:
            from zoneinfo import ZoneInfo

            moment = moment.astimezone(ZoneInfo("Europe/Paris"))
        except Exception:
            pass
    elif moment.time() == time(0, 0) and "T" not in text and " " not in text:
        return moment.date().isoformat(), moment.date().isoformat()
    return moment.date().isoformat(), moment.replace(microsecond=0).isoformat(timespec="seconds")


def parse_duration(raw: Any) -> str | None:
    """Durée ISO 8601 (« PT3M1S ») ou nombre de secondes, ramenée au format h:mm:ss des flux RSS."""
    if isinstance(raw, (int, float)):
        total = int(raw)
    else:
        text = _text(raw)
        if not text:
            return None
        if re.fullmatch(r"\d+(:\d{1,2}){1,2}", text):
            return text
        if text.isdigit():
            total = int(text)
        else:
            match = _ISO_DURATION_RE.match(text.upper())
            if not match or not any(match.groupdict().values()):
                return None
            parts = {name: float(value or 0) for name, value in match.groupdict().items()}
            total = int(parts["days"] * 86400 + parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"])
    if total <= 0:
        return None
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}"


# ------------------------------------------------------------------ page d'épisode


def parse_episode_page(markup: str, url: str) -> dict[str, Any]:
    """Tire d'une page web les métadonnées d'un épisode : JSON-LD d'abord, balises Open Graph ensuite."""
    soup = BeautifulSoup(markup, "html.parser")

    nodes: list[dict[str, Any]] = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            nodes.extend(_nodes(json.loads(script.string or script.get_text() or "")))
        except (json.JSONDecodeError, TypeError):
            continue
    episode = _first_node(nodes, _EPISODE_TYPES - {"AudioObject"}) or _first_node(nodes, {"AudioObject"})
    # Sur radiofrance.fr, l'article porte le titre et le chapô éditoriaux ; l'épisode, un libellé de référencement.
    article = _first_node(nodes, _ARTICLE_TYPES)
    series = _first_node(nodes, _SERIES_TYPES)
    audio = next(
        (node for node in [*_nodes(episode), *nodes] if "AudioObject" in _types(node) and _url(node, url)), {}
    )

    def meta(*names: str) -> str:
        for name in names:
            tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
            if tag and (tag.get("content") or "").strip():
                return tag["content"].strip()
        return ""

    site = meta("og:site_name")
    title = (
        _text(article.get("headline") or article.get("name"))
        or _text(episode.get("name") or episode.get("headline"))
        or meta("og:title", "twitter:title")
        or (soup.title.get_text(strip=True) if soup.title else "")
    )
    if site:
        title = re.sub(rf"\s*[|·–—-]\s*{re.escape(site)}\s*$", "", title, flags=re.IGNORECASE)
    if not title:
        raise EpisodeError("Cette page ne contient pas de titre d'épisode exploitable.")

    canonical = soup.find("link", rel="canonical")
    link = _url(canonical.get("href") if canonical else None, url) or _url(meta("og:url"), url) or url

    moment = next(
        (
            found
            for found in (
                parse_moment(raw)
                for raw in (
                    episode.get("datePublished"),
                    article.get("datePublished"),
                    episode.get("dateCreated"),
                    episode.get("uploadDate"),
                    article.get("dateCreated"),
                    meta("article:published_time", "og:article:published_time", "date"),
                )
            )
            if found
        ),
        None,
    )

    source = None
    for candidate in soup.select("audio[src], audio source[src]"):
        source = _url(candidate.get("src"), url)
        if source:
            break
    audio_url = (
        _url(audio, url)
        or _url(meta("og:audio:secure_url", "og:audio:url", "og:audio", "twitter:player:stream"), url)
        or source
    )
    if series and not episode and not article and not audio_url:
        raise EpisodeError(
            "Cette page présente une émission entière, pas un épisode : ouvrez l'épisode voulu et collez son adresse."
        )
    if not audio_url:
        found = _AUDIO_URL_RE.search(markup.replace("\\/", "/"))
        audio_url = found.group(0) if found else None

    show = (
        _text(episode.get("partOfSeries") or (episode.get("partOfSeason") or {}).get("partOfSeries"))
        or _text(series.get("name"))
        or site
        or urlparse(link).netloc.removeprefix("www.")
    )
    author = _text(article.get("author")) or _text(
        episode.get("director") or episode.get("author") or episode.get("creator")
    )
    summary = (
        _text(article.get("description"))
        or _text(episode.get("description"))
        or meta("og:description", "description", "twitter:description")
    )
    image = (
        _url(episode.get("image") or episode.get("thumbnailUrl"), url)
        or _url(article.get("image"), url)
        or _url(meta("og:image:secure_url", "og:image", "twitter:image"), url)
    )
    return {
        "guid": link,
        "title": title,
        "show_title": show,
        "pub_date": moment[0] if moment else None,
        "published_at": moment[1] if moment else None,
        "summary": rss_fetcher.clean_summary(summary),
        "author": author or None,
        "audio_url": audio_url,
        "audio_type": _text(audio.get("encodingFormat")) or meta("og:audio:type") or None,
        "image_url": image,
        "duration": parse_duration(audio.get("duration") or episode.get("duration") or episode.get("timeRequired")),
        "link": link,
    }


# ---------------------------------------------------------------------- recherche


def _download(url: str) -> tuple[bytes, str, str]:
    """Contenu de l'adresse (plafonné), type de contenu annoncé et adresse finale après redirections."""
    try:
        with requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT}, stream=True) as response:
            response.raise_for_status()
            content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type.startswith(("audio/", "video/")):
                raise EpisodeError(
                    "Cette adresse pointe vers un fichier audio : collez plutôt celle de la page de l'épisode."
                )
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_content(chunk_size=1 << 16):
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_BYTES:
                    raise EpisodeError("Cette page est trop volumineuse pour être analysée.")
            return b"".join(chunks), content_type, response.url
    except requests.RequestException as exc:
        raise EpisodeError(f"Adresse injoignable : {exc}") from exc


def _looks_like_feed(content: bytes, content_type: str) -> bool:
    if any(marker in content_type for marker in ("rss", "atom", "xml")):
        return True
    head = content[:600].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    return head.startswith((b"<?xml", b"<rss", b"<feed"))


def lookup(url: str) -> Lookup:
    """Lit l'adresse fournie : page d'un épisode, ou flux RSS dont on proposera les épisodes au choix."""
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise EpisodeError("L'adresse doit commencer par http:// ou https://.")
    content, content_type, final_url = _download(url)

    if _looks_like_feed(content, content_type):
        try:
            feed = rss_fetcher.parse_feed(content, keep_sundays=True)
        except rss_fetcher.FeedError as exc:
            raise EpisodeError(str(exc)) from exc
        if not feed.episodes:
            raise EpisodeError("Ce flux ne contient aucun épisode.")
        episodes = sorted(feed.episodes, key=lambda ep: ep["published_at"], reverse=True)
        link_counts = Counter(ep.get("link") for ep in episodes)
        for ep in episodes:
            # L'identifiant d'un épisode isolé est l'adresse de sa page, quand elle lui est propre : c'est elle
            # qui permet de le reconnaître s'il est déjà suivi. À défaut, le guid du flux, préfixé par sa source.
            link = ep.get("link")
            ep["guid"] = link if link and link_counts[link] == 1 else f"{link or final_url}#{ep['guid']}"
        return Lookup(show_title=feed.title, episodes=episodes, from_feed=True)

    try:
        markup = content.decode("utf-8")
    except UnicodeDecodeError:
        markup = content.decode("latin-1")
    episode = parse_episode_page(markup, final_url)
    return Lookup(show_title=episode.pop("show_title"), episodes=[episode])
