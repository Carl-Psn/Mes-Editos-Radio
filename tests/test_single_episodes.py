"""Tests de l'ajout d'un épisode à l'unité : lecture d'une page d'épisode et stockage hors flux RSS."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import database as db
import episode_fetcher
import rss_fetcher

PAGE_URL = "https://www.radiofrance.fr/franceculture/podcasts/le-cours-de-l-histoire/besoin-de-toit-3193856"

_JSON_LD = {
    "@context": "https://schema.org",
    "@graph": [
        {
            "@type": "NewsArticle",
            "headline": "Besoin de toit, se mobiliser pour le logement",
            "description": "À partir du 19ᵉ siècle, les locataires demandent des droits.",
            "datePublished": "2026-11-19T08:00:00.000Z",
            "author": [{"@type": "Person", "name": "Xavier Mauduit"}, {"@type": "Person", "name": "Maïwenn Guiziou"}],
        },
        {
            "@type": "RadioEpisode",
            "name": "Besoin de toit : épisode du podcast Se loger | France Culture",
            "description": "AUDIO Se loger, épisode : Besoin de toit. Écoutez nos podcasts en ligne.",
            "dateCreated": "2026-11-19T08:00:00.000Z",
            "mainEntity": {
                "@type": "AudioObject",
                "contentUrl": "https://media.radiofrance-podcast.net/podcast09/besoin-de-toit.m4a",
                "duration": "P0Y0M0DT0H58M32S",
                "encodingFormat": "audio/mp4",
            },
            "image": {"@type": "ImageObject", "url": "https://www.radiofrance.fr/pikapi/images/abc/1200x680"},
            "partOfSeries": {"@type": "RadioSeries", "name": "Le Cours de l'histoire"},
        },
    ],
}

RADIO_FRANCE_PAGE = f"""<html><head>
<title>Besoin de toit : épisode du podcast Se loger | France Culture</title>
<link rel="canonical" href="{PAGE_URL}">
<meta property="og:site_name" content="France Culture">
<script type="application/ld+json">{json.dumps(_JSON_LD)}</script>
</head><body></body></html>"""

OPEN_GRAPH_PAGE = """<html><head>
<title>À cœur ouvert | Radio Exemple</title>
<meta property="og:site_name" content="Radio Exemple">
<meta property="og:title" content="À cœur ouvert | Radio Exemple">
<meta property="og:description" content="Il aura fallu toute la vie pour pouvoir en parler.">
<meta property="og:image" content="/images/coeur.jpg">
</head><body><audio><source src="/sons/coeur.mp3" type="audio/mpeg"></audio></body></html>"""

SHOW_PAGE = """<html><head><title>Affaires sensibles</title>
<script type="application/ld+json">{"@type": "RadioSeries", "name": "Affaires sensibles"}</script>
</head><body></body></html>"""

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>A voix nue</title>
<item><title>Épisode du dimanche</title><guid>guid-1</guid><link>https://exemple.fr/episodes/1</link>
<pubDate>Sun, 15 Nov 2026 20:00:00 +0100</pubDate>
<enclosure url="https://exemple.fr/1.mp3" type="audio/mpeg" length="1"/></item>
<item><title>Épisode du lundi</title><guid>guid-2</guid><link>https://exemple.fr/episodes/2</link>
<pubDate>Mon, 16 Nov 2026 20:00:00 +0100</pubDate>
<enclosure url="https://exemple.fr/2.mp3" type="audio/mpeg" length="1"/></item>
</channel></rss>"""


class EpisodePageTests(unittest.TestCase):
    def test_radio_france_page(self) -> None:
        episode = episode_fetcher.parse_episode_page(RADIO_FRANCE_PAGE, PAGE_URL)
        self.assertEqual(episode["title"], "Besoin de toit, se mobiliser pour le logement")
        self.assertEqual(episode["show_title"], "Le Cours de l'histoire")
        self.assertEqual(episode["pub_date"], "2026-11-19")
        self.assertEqual(episode["published_at"], "2026-11-19T09:00:00+01:00")
        self.assertEqual(episode["author"], "Xavier Mauduit, Maïwenn Guiziou")
        self.assertEqual(episode["audio_url"], "https://media.radiofrance-podcast.net/podcast09/besoin-de-toit.m4a")
        self.assertEqual(episode["audio_type"], "audio/mp4")
        self.assertEqual(episode["duration"], "0:58:32")
        self.assertEqual(episode["link"], PAGE_URL)
        self.assertEqual(episode["guid"], PAGE_URL)
        self.assertTrue(episode["summary"].startswith("À partir du 19ᵉ siècle"))

    def test_open_graph_fallback(self) -> None:
        episode = episode_fetcher.parse_episode_page(OPEN_GRAPH_PAGE, "https://radio.exemple.fr/son/a-coeur-ouvert")
        self.assertEqual(episode["title"], "À cœur ouvert")
        self.assertEqual(episode["show_title"], "Radio Exemple")
        self.assertIsNone(episode["pub_date"])
        self.assertEqual(episode["audio_url"], "https://radio.exemple.fr/sons/coeur.mp3")
        self.assertEqual(episode["image_url"], "https://radio.exemple.fr/images/coeur.jpg")

    def test_show_page_is_rejected(self) -> None:
        with self.assertRaises(episode_fetcher.EpisodeError):
            episode_fetcher.parse_episode_page(SHOW_PAGE, "https://www.radiofrance.fr/franceinter/podcasts/affaires-sensibles")

    def test_durations_and_dates(self) -> None:
        self.assertEqual(episode_fetcher.parse_duration("PT3M1S"), "0:03:01")
        self.assertEqual(episode_fetcher.parse_duration(3725), "1:02:05")
        self.assertEqual(episode_fetcher.parse_duration("00:21:56"), "00:21:56")
        self.assertIsNone(episode_fetcher.parse_duration("bientôt"))
        self.assertEqual(episode_fetcher.parse_moment("2026-11-19"), ("2026-11-19", "2026-11-19"))
        self.assertEqual(
            episode_fetcher.parse_moment("2026-07-01T22:30:00Z"), ("2026-07-02", "2026-07-02T00:30:00+02:00")
        )
        self.assertIsNone(episode_fetcher.parse_moment("hier"))

    def test_feed_address_lists_every_episode(self) -> None:
        with mock.patch.object(
            episode_fetcher, "_download", return_value=(FEED.encode(), "application/rss+xml", "https://exemple.fr/rss")
        ):
            found = episode_fetcher.lookup("https://exemple.fr/rss")
        self.assertTrue(found.from_feed)
        self.assertEqual(found.show_title, "A voix nue")
        # Les dimanches, écartés des émissions suivies, restent proposés pour un ajout à l'unité.
        self.assertEqual([ep["title"] for ep in found.episodes], ["Épisode du lundi", "Épisode du dimanche"])
        self.assertEqual(found.episodes[0]["guid"], "https://exemple.fr/episodes/2")

    def test_address_must_be_http(self) -> None:
        with self.assertRaises(episode_fetcher.EpisodeError):
            episode_fetcher.lookup("radiofrance.fr/un-episode")


class SingleEpisodeStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patcher = mock.patch.object(db, "DB_PATH", Path(folder.name) / "test.db")
        patcher.start()
        self.addCleanup(patcher.stop)
        db.init_db()
        self.episode = episode_fetcher.parse_episode_page(RADIO_FRANCE_PAGE, PAGE_URL)
        self.show_title = self.episode.pop("show_title")

    def test_single_episode_keeps_its_podcast_name(self) -> None:
        episode_id = db.add_single_episode(self.episode, self.show_title, "autre")
        stored = db.get_episode(episode_id)
        self.assertEqual(stored["show_title"], "Le Cours de l'histoire")
        self.assertEqual(stored["show_category"], "autre")
        self.assertEqual(stored["priority"], db.DEFAULT_PRIORITY)
        self.assertEqual([ep["id"] for ep in db.list_episodes(month="2026-11")], [episode_id])
        self.assertEqual([ep["id"] for ep in db.list_single_episodes()], [episode_id])
        self.assertEqual(db.find_episode_by_link(PAGE_URL)["id"], episode_id)

    def test_single_episodes_are_never_synchronised(self) -> None:
        db.add_single_episode(self.episode, self.show_title, "autre")
        shows = db.list_shows()
        self.assertTrue(db.is_manual_show(shows[-1]))
        self.assertEqual(shows[-1]["title"], db.MANUAL_SHOW_TITLE)
        self.assertFalse(any(db.is_manual_show(show) for show in db.list_shows(feeds_only=True)))
        with mock.patch.object(rss_fetcher, "sync_show", side_effect=lambda show: show["rss_url"]) as sync:
            synced = rss_fetcher.sync_all()
        self.assertEqual(sync.call_count, len(synced))
        self.assertNotIn(db.MANUAL_FEED_URL, synced)

    def test_same_episode_cannot_be_added_twice(self) -> None:
        db.add_single_episode(self.episode, self.show_title, "autre")
        with self.assertRaises(sqlite3.IntegrityError):
            db.add_single_episode(self.episode, self.show_title, "autre")

    def test_removing_the_last_single_episode_removes_its_group(self) -> None:
        feeds = len(db.list_shows())
        episode_id = db.add_single_episode(self.episode, self.show_title, "autre")
        self.assertEqual(len(db.list_shows()), feeds + 1)
        db.delete_single_episode(episode_id)
        self.assertIsNone(db.get_episode(episode_id))
        self.assertEqual(len(db.list_shows()), feeds)

    def test_feed_episodes_cannot_be_removed_as_single(self) -> None:
        show = db.list_shows()[0]
        db.upsert_episodes(show["id"], [{**self.episode, "guid": "depuis-le-flux"}])
        episode_id = db.list_episodes()[0]["id"]
        db.delete_single_episode(episode_id)
        self.assertIsNotNone(db.get_episode(episode_id))

    def test_existing_database_is_upgraded(self) -> None:
        with db.connect() as conn:
            conn.execute("ALTER TABLE episodes DROP COLUMN source_title")
            conn.execute("ALTER TABLE episodes DROP COLUMN source_category")
        db.init_db()
        self.assertIsNotNone(db.get_episode(db.add_single_episode(self.episode, self.show_title, "eco")))


if __name__ == "__main__":
    unittest.main()
