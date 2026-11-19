"""Récupération et analyse des flux RSS Radio France (requests + feedparser)."""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser
import requests

import database as db

REQUEST_TIMEOUT = 20
USER_AGENT = "EditosINSP/1.0 (+suivi personnel de podcasts; feedparser)"
SUNDAY = 6

_PROMO_RE = re.compile(r"\s*Vous aimez ce podcast\s*\?.*$", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_PREFIX_RE = re.compile(r"^\s*durée\s*:\s*[\d:]+\s+-\s+[^\n]{1,120}?\s+-\s+", re.IGNORECASE)
_BYLINE_RE = re.compile(r"^par\s*:\s*[^\n]{2,120}?\s+-\s+", re.IGNORECASE)
_CREDITS_RE = re.compile(r"\s+-\s+(équipe|invités|invité|invitée|réalisation)\s*:\s*", re.IGNORECASE)


class FeedError(Exception):
    """Flux injoignable ou illisible."""


@dataclass
class ParsedFeed:
    title: str
    image_url: str | None
    episodes: list[dict[str, Any]] = field(default_factory=list)
    skipped_sundays: int = 0


@dataclass
class SyncReport:
    show_id: int
    label: str
    ok: bool
    added: int = 0
    known: int = 0
    skipped_sundays: int = 0
    error: str | None = None


def guess_category(title: str | None) -> str:
    """Déduit la famille de l'émission (et donc la couleur de son badge) du titre du flux."""
    text = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode().lower()
    if "geopoli" in text:
        return "geopolitique"
    if re.search(r"\beco", text):
        return "eco"
    if "politi" in text:
        return "politique"
    if "invite" in text or "8h20" in text or "entretien" in text:
        return "invite"
    return "autre"


def clean_summary(raw: str | None) -> str:
    """Résumé court : retire le HTML, la durée, le rappel du titre de l'émission et l'auto-promotion."""
    if not raw:
        return ""
    text = html.unescape(_TAG_RE.sub("", raw)).replace("\xa0", " ")
    text = _PROMO_RE.sub("", text)
    text = _PREFIX_RE.sub("", text, count=1)
    text = _BYLINE_RE.sub("", text, count=1)

    guests = ""
    parts = _CREDITS_RE.split(text)
    body = parts[0]
    for label, value in zip(parts[1::2], parts[2::2]):
        if label.lower().startswith("invit"):
            guests = value
    body = re.sub(r"\s+", " ", body).strip()
    guests = re.sub(r"\s+", " ", guests).strip()
    if guests:
        body = f"{body} Invités : {guests}".strip()
    return body


def _published_datetime(entry: Any) -> datetime | None:
    """Date de diffusion dans le fuseau du flux (heure de Paris pour Radio France)."""
    raw = entry.get("published") or entry.get("updated")
    if raw:
        try:
            parsed = parsedate_to_datetime(raw)
            if parsed.tzinfo is not None:
                return parsed
        except (TypeError, ValueError):
            pass
    struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if not struct:
        return None
    utc = datetime(*struct[:6], tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo

        return utc.astimezone(ZoneInfo("Europe/Paris"))
    except Exception:
        return utc


def _audio_enclosure(entry: Any) -> tuple[str | None, str | None]:
    enclosures = entry.get("enclosures") or []
    if not enclosures:
        return None, None
    chosen = next((e for e in enclosures if str(e.get("type", "")).startswith("audio/")), enclosures[0])
    return chosen.get("href"), chosen.get("type")


def _image_href(node: Any) -> str | None:
    image = node.get("image")
    if not image:
        return None
    return image.get("href") or image.get("url")


def parse_feed(content: bytes | str, keep_sundays: bool = False) -> ParsedFeed:
    """Analyse un flux et, sauf demande contraire, ne conserve que les épisodes diffusés du lundi au samedi."""
    parsed = feedparser.parse(content)
    feed_title = (parsed.feed.get("title") or "").strip()
    if not feed_title and not parsed.entries:
        raise FeedError("Le contenu reçu n'est pas un flux RSS de podcast valide.")

    result = ParsedFeed(title=feed_title or "Émission sans titre", image_url=_image_href(parsed.feed))
    for entry in parsed.entries:
        audio_url, audio_type = _audio_enclosure(entry)
        # Radio France termine chaque flux par un jingle promotionnel pour son application.
        if audio_url and "app_rf_promotion" in audio_url:
            continue
        published = _published_datetime(entry)
        if published is None:
            continue
        if published.weekday() == SUNDAY and not keep_sundays:
            result.skipped_sundays += 1
            continue

        title = (entry.get("title") or "").strip() or "Épisode sans titre"
        guid = entry.get("id") or audio_url or entry.get("link") or f"{published.isoformat()}|{title}"
        result.episodes.append(
            {
                "guid": guid,
                "title": title,
                "published_at": published.isoformat(timespec="seconds"),
                "pub_date": published.date().isoformat(),
                "summary": clean_summary(entry.get("summary") or entry.get("subtitle")),
                "author": (entry.get("author") or "").strip() or None,
                "audio_url": audio_url,
                "audio_type": audio_type,
                "image_url": _image_href(entry) or result.image_url,
                "duration": entry.get("itunes_duration"),
                "link": entry.get("link"),
            }
        )
    return result


def fetch_feed(url: str) -> ParsedFeed:
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT})
        response.raise_for_status()
    except requests.RequestException as exc:
        raise FeedError(f"Flux injoignable : {exc}") from exc
    return parse_feed(response.content)


def sync_show(show: dict[str, Any]) -> SyncReport:
    """Synchronise une émission : met à jour son nom, sa vignette et ses épisodes."""
    label = show.get("title") or show["rss_url"]
    try:
        feed = fetch_feed(show["rss_url"])
    except FeedError as exc:
        db.update_show(show["id"], last_error=str(exc))
        return SyncReport(show_id=show["id"], label=label, ok=False, error=str(exc))

    updates: dict[str, Any] = {
        "title": feed.title,
        "image_url": feed.image_url,
        "last_synced_at": datetime.now().isoformat(timespec="seconds"),
        "last_error": None,
    }
    if not show.get("category"):
        updates["category"] = guess_category(feed.title)
    db.update_show(show["id"], **updates)
    added, known = db.upsert_episodes(show["id"], feed.episodes)
    return SyncReport(
        show_id=show["id"],
        label=feed.title,
        ok=True,
        added=added,
        known=known,
        skipped_sundays=feed.skipped_sundays,
    )


def sync_all() -> list[SyncReport]:
    """Synchronise toutes les émissions actives (les épisodes ajoutés à l'unité n'ont pas de flux)."""
    return [sync_show(show) for show in db.list_shows(only_enabled=True, feeds_only=True)]
