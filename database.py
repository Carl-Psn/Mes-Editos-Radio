"""Base SQLite locale : émissions suivies, épisodes, fiches et synthèses mensuelles."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("EDITOS_DB_PATH", BASE_DIR / "editos_insp.db"))

CATEGORIES = ("politique", "eco", "geopolitique", "invite", "autre")

PRIORITY_LABELS = {
    1: "1 - Secondaire",
    2: "2 - Important",
    3: "3 - Incontournable",
}
DEFAULT_PRIORITY = 1

# Émission interne qui regroupe les épisodes ajoutés à l'unité (hors flux RSS suivis). Elle n'est jamais
# synchronisée ; chaque épisode y garde le nom et le badge de son podcast d'origine (colonnes source_*).
MANUAL_FEED_URL = "local://episodes-a-l-unite"
MANUAL_SHOW_TITLE = "Épisodes à l'unité"

# Les trois chroniques quotidiennes suivies par défaut. Leur nom et leur catégorie
# sont déduits du <title> de chaque flux lors de la première synchronisation.
DEFAULT_FEEDS: tuple[dict[str, Any], ...] = (
    {
        "rss_url": "https://radiofrance-podcast.net/podcast09/podcast_700d1d9a-a288-4184-a462-3c45ae243795.xml",
        "enabled": 1,
    },
    {
        "rss_url": "https://radiofrance-podcast.net/podcast09/podcast_6f03e972-cada-490c-a455-cb8223327109.xml",
        "enabled": 1,
    },
    {
        "rss_url": "https://radiofrance-podcast.net/podcast09/podcast_355586fd-7eaa-433e-979c-98ca082b5fb1.xml",
        "enabled": 1,
    },
    {
        "rss_url": "https://radiofrance-podcast.net/podcast09/rss_10239.xml",
        "title": "L'Invité de 8h20 : le grand entretien",
        "category": "invite",
        "enabled": 0,
    },
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS shows (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    rss_url        TEXT NOT NULL UNIQUE,
    title          TEXT,
    category       TEXT,
    image_url      TEXT,
    custom_logo    TEXT,
    enabled        INTEGER NOT NULL DEFAULT 1,
    position       INTEGER NOT NULL DEFAULT 0,
    last_synced_at TEXT,
    last_error     TEXT
);

CREATE TABLE IF NOT EXISTS episodes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    show_id          INTEGER NOT NULL REFERENCES shows(id) ON DELETE CASCADE,
    guid             TEXT NOT NULL,
    title            TEXT NOT NULL,
    published_at     TEXT NOT NULL,
    pub_date         TEXT NOT NULL,
    summary          TEXT,
    author           TEXT,
    audio_url        TEXT,
    audio_type       TEXT,
    image_url        TEXT,
    duration         TEXT,
    link             TEXT,
    listened         INTEGER NOT NULL DEFAULT 0,
    priority         INTEGER NOT NULL DEFAULT 1 CHECK (priority BETWEEN 1 AND 3),
    fiche            TEXT,
    fiche_draft      TEXT,
    fiche_updated_at TEXT,
    source_title     TEXT,
    source_category  TEXT,
    UNIQUE (show_id, guid)
);

CREATE INDEX IF NOT EXISTS idx_episodes_pub_date ON episodes (pub_date);

CREATE TABLE IF NOT EXISTS monthly_syntheses (
    month      TEXT PRIMARY KEY,
    content    TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

_EPISODE_SELECT = """
SELECT e.*,
       COALESCE(e.source_title, s.title)       AS show_title,
       COALESCE(e.source_category, s.category) AS show_category,
       s.image_url   AS show_image_url,
       s.custom_logo AS show_custom_logo,
       s.rss_url     AS show_rss_url
FROM episodes e
JOIN shows s ON s.id = e.show_id
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Crée les tables et, sur une base vierge, enregistre les flux par défaut."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(_SCHEMA)
        # Bases créées avant l'ajout d'épisodes à l'unité.
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(episodes)")}
        for column in ("source_title", "source_category"):
            if column not in columns:
                conn.execute(f"ALTER TABLE episodes ADD COLUMN {column} TEXT")
        if conn.execute("SELECT COUNT(*) FROM shows").fetchone()[0] == 0:
            for position, feed in enumerate(DEFAULT_FEEDS):
                conn.execute(
                    "INSERT INTO shows (rss_url, title, category, enabled, position) VALUES (?, ?, ?, ?, ?)",
                    (feed["rss_url"], feed.get("title"), feed.get("category"), feed["enabled"], position),
                )


# --------------------------------------------------------------------------- émissions


def is_manual_show(show: dict[str, Any]) -> bool:
    return show.get("rss_url") == MANUAL_FEED_URL


def list_shows(only_enabled: bool = False, feeds_only: bool = False) -> list[dict[str, Any]]:
    """Émissions dans l'ordre d'affichage. `feeds_only` écarte le regroupement des épisodes ajoutés à l'unité."""
    clauses = []
    if only_enabled:
        clauses.append("enabled = 1")
    if feeds_only:
        clauses.append("rss_url <> :manual")
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    query = f"SELECT * FROM shows{where} ORDER BY rss_url = :manual, position, id"
    with connect() as conn:
        return [dict(row) for row in conn.execute(query, {"manual": MANUAL_FEED_URL})]


def get_show(show_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM shows WHERE id = ?", (show_id,)).fetchone()
    return dict(row) if row else None


def add_show(rss_url: str, title: str | None = None, category: str | None = None, enabled: bool = True) -> int:
    """Ajoute un flux. Lève sqlite3.IntegrityError si l'URL est déjà suivie."""
    with connect() as conn:
        position = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM shows").fetchone()[0]
        cursor = conn.execute(
            "INSERT INTO shows (rss_url, title, category, enabled, position) VALUES (?, ?, ?, ?, ?)",
            (rss_url.strip(), title, category, int(enabled), position),
        )
        return int(cursor.lastrowid)


_SHOW_FIELDS = {
    "rss_url",
    "title",
    "category",
    "image_url",
    "custom_logo",
    "enabled",
    "position",
    "last_synced_at",
    "last_error",
}


def update_show(show_id: int, **fields: Any) -> None:
    unknown = set(fields) - _SHOW_FIELDS
    if unknown:
        raise ValueError(f"Champs inconnus : {sorted(unknown)}")
    if not fields:
        return
    assignments = ", ".join(f"{name} = ?" for name in fields)
    with connect() as conn:
        conn.execute(f"UPDATE shows SET {assignments} WHERE id = ?", (*fields.values(), show_id))


def delete_show(show_id: int) -> None:
    """Supprime l'émission ainsi que ses épisodes et fiches (ON DELETE CASCADE)."""
    with connect() as conn:
        conn.execute("DELETE FROM shows WHERE id = ?", (show_id,))


def show_counts(show_id: int) -> dict[str, int]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS episodes,
                   COALESCE(SUM(fiche IS NOT NULL AND TRIM(fiche) <> ''), 0) AS fiches
            FROM episodes WHERE show_id = ?
            """,
            (show_id,),
        ).fetchone()
    return {"episodes": row["episodes"], "fiches": row["fiches"]}


# ---------------------------------------------------------------------------- épisodes


def upsert_episodes(show_id: int, episodes: Iterable[dict[str, Any]]) -> tuple[int, int]:
    """Insère les nouveaux épisodes et rafraîchit les métadonnées RSS des épisodes connus.

    Le statut d'écoute, la priorité et les fiches ne sont jamais écrasés.
    Retourne (nombre d'épisodes ajoutés, nombre d'épisodes déjà connus).
    """
    inserted = 0
    known = 0
    with connect() as conn:
        for ep in episodes:
            params = (
                ep["title"],
                ep["published_at"],
                ep["pub_date"],
                ep.get("summary"),
                ep.get("author"),
                ep.get("audio_url"),
                ep.get("audio_type"),
                ep.get("image_url"),
                ep.get("duration"),
                ep.get("link"),
            )
            exists = conn.execute(
                "SELECT id FROM episodes WHERE show_id = ? AND guid = ?", (show_id, ep["guid"])
            ).fetchone()
            if exists:
                conn.execute(
                    """
                    UPDATE episodes
                    SET title = ?, published_at = ?, pub_date = ?, summary = ?, author = ?,
                        audio_url = ?, audio_type = ?, image_url = ?, duration = ?, link = ?
                    WHERE id = ?
                    """,
                    (*params, exists["id"]),
                )
                known += 1
            else:
                conn.execute(
                    """
                    INSERT INTO episodes (show_id, guid, title, published_at, pub_date, summary, author,
                                          audio_url, audio_type, image_url, duration, link, priority)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (show_id, ep["guid"], *params, DEFAULT_PRIORITY),
                )
                inserted += 1
    return inserted, known


def find_episode_by_link(link: str) -> dict[str, Any] | None:
    """Épisode déjà suivi pour cette page web, qu'il vienne d'un flux actif ou d'un ajout à l'unité."""
    with connect() as conn:
        row = conn.execute(
            f"{_EPISODE_SELECT} WHERE s.enabled = 1 AND (e.link = :link OR e.guid = :link) ORDER BY e.id LIMIT 1",
            {"link": link},
        ).fetchone()
    return dict(row) if row else None


def add_single_episode(episode: dict[str, Any], source_title: str, source_category: str) -> int:
    """Enregistre un épisode isolé, sans suivre le flux de son podcast. Retourne son identifiant.

    Lève sqlite3.IntegrityError si cet épisode a déjà été ajouté à l'unité.
    """
    with connect() as conn:
        row = conn.execute("SELECT id FROM shows WHERE rss_url = ?", (MANUAL_FEED_URL,)).fetchone()
        if row:
            show_id = row["id"]
        else:
            position = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM shows").fetchone()[0]
            show_id = conn.execute(
                "INSERT INTO shows (rss_url, title, category, enabled, position) VALUES (?, ?, 'autre', 1, ?)",
                (MANUAL_FEED_URL, MANUAL_SHOW_TITLE, position),
            ).lastrowid
        cursor = conn.execute(
            """
            INSERT INTO episodes (show_id, guid, title, published_at, pub_date, summary, author, audio_url,
                                  audio_type, image_url, duration, link, priority, source_title, source_category)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                show_id,
                episode["guid"],
                episode["title"],
                episode["published_at"],
                episode["pub_date"],
                episode.get("summary"),
                episode.get("author"),
                episode.get("audio_url"),
                episode.get("audio_type"),
                episode.get("image_url"),
                episode.get("duration"),
                episode.get("link"),
                DEFAULT_PRIORITY,
                source_title,
                source_category,
            ),
        )
        return int(cursor.lastrowid)


def list_single_episodes() -> list[dict[str, Any]]:
    """Épisodes ajoutés à l'unité, du plus récent au plus ancien."""
    query = (
        f"{_EPISODE_SELECT} WHERE s.rss_url = ? "
        "ORDER BY e.pub_date DESC, e.published_at DESC, e.id DESC"
    )
    with connect() as conn:
        return [dict(row) for row in conn.execute(query, (MANUAL_FEED_URL,))]


def delete_single_episode(episode_id: int) -> None:
    """Supprime un épisode ajouté à l'unité (et sa fiche). Les épisodes issus d'un flux ne sont pas concernés."""
    with connect() as conn:
        conn.execute(
            "DELETE FROM episodes WHERE id = ? AND show_id IN (SELECT id FROM shows WHERE rss_url = ?)",
            (episode_id, MANUAL_FEED_URL),
        )
        # Sans épisode, le regroupement n'a plus lieu d'apparaître dans les filtres et le bilan.
        conn.execute(
            "DELETE FROM shows WHERE rss_url = ? AND id NOT IN (SELECT show_id FROM episodes)",
            (MANUAL_FEED_URL,),
        )


def list_episodes(
    month: str | None = None,
    show_ids: Iterable[int] | None = None,
    priorities: Iterable[int] | None = None,
    min_priority: int | None = None,
    with_fiche: bool | None = None,
    newest_first: bool = False,
) -> list[dict[str, Any]]:
    """Épisodes des émissions actives. `month` est au format AAAA-MM."""
    clauses = ["s.enabled = 1"]
    params: list[Any] = []
    if month:
        clauses.append("e.pub_date LIKE ?")
        params.append(f"{month}-%")
    if show_ids is not None:
        ids = list(show_ids)
        if not ids:
            return []
        clauses.append(f"e.show_id IN ({', '.join('?' * len(ids))})")
        params.extend(ids)
    if priorities is not None:
        levels = list(priorities)
        if not levels:
            return []
        clauses.append(f"e.priority IN ({', '.join('?' * len(levels))})")
        params.extend(levels)
    if min_priority is not None:
        clauses.append("e.priority >= ?")
        params.append(min_priority)
    if with_fiche is True:
        clauses.append("e.fiche IS NOT NULL AND TRIM(e.fiche) <> ''")
    elif with_fiche is False:
        clauses.append("(e.fiche IS NULL OR TRIM(e.fiche) = '')")
    order = "DESC" if newest_first else "ASC"
    query = (
        f"{_EPISODE_SELECT} WHERE {' AND '.join(clauses)} "
        f"ORDER BY e.pub_date {order}, e.published_at {order}, e.id {order}"
    )
    with connect() as conn:
        return [dict(row) for row in conn.execute(query, params)]


def get_episode(episode_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(f"{_EPISODE_SELECT} WHERE e.id = ?", (episode_id,)).fetchone()
    return dict(row) if row else None


def set_listened(episode_id: int, listened: bool) -> None:
    with connect() as conn:
        conn.execute("UPDATE episodes SET listened = ? WHERE id = ?", (int(listened), episode_id))


def set_priority(episode_id: int, priority: int) -> None:
    if priority not in PRIORITY_LABELS:
        raise ValueError("La priorité doit être comprise entre 1 et 3.")
    with connect() as conn:
        conn.execute("UPDATE episodes SET priority = ? WHERE id = ?", (priority, episode_id))


# ------------------------------------------------------------------------------ fiches


def save_fiche_draft(episode_id: int, text: str) -> None:
    """Conserve une fiche tout juste générée tant qu'elle n'a pas été relue et enregistrée."""
    with connect() as conn:
        conn.execute("UPDATE episodes SET fiche_draft = ? WHERE id = ?", (text, episode_id))


def discard_fiche_draft(episode_id: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE episodes SET fiche_draft = NULL WHERE id = ?", (episode_id,))


def save_fiche(episode_id: int, text: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE episodes SET fiche = ?, fiche_draft = NULL, fiche_updated_at = ? WHERE id = ?",
            (text.strip(), _now(), episode_id),
        )


def delete_fiche(episode_id: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE episodes SET fiche = NULL, fiche_draft = NULL, fiche_updated_at = NULL WHERE id = ?",
            (episode_id,),
        )


# ------------------------------------------------------------------- synthèses du mois


def get_synthesis(month: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM monthly_syntheses WHERE month = ?", (month,)).fetchone()
    return dict(row) if row else None


def save_synthesis(month: str, content: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO monthly_syntheses (month, content, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(month) DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at
            """,
            (month, content.strip(), _now()),
        )


def delete_synthesis(month: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM monthly_syntheses WHERE month = ?", (month,))


# ---------------------------------------------------------------------------- réglages


def get_setting(key: str, default: str | None = None) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row and row["value"] is not None else default


def set_setting(key: str, value: str | None) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def available_months() -> list[str]:
    """Mois (AAAA-MM) pour lesquels au moins un épisode est enregistré, du plus récent au plus ancien."""
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT substr(e.pub_date, 1, 7) AS month
            FROM episodes e JOIN shows s ON s.id = e.show_id
            WHERE s.enabled = 1
            ORDER BY month DESC
            """
        )
        return [row["month"] for row in rows]
