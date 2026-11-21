"""Tests de sauvegarde / restauration (fournisseur fichier)."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import database as db
import persistence


class PersistenceFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.db_path = self.base / "editos.db"
        self.backup_path = self.base / "backup.tar.gz"
        self.db_patcher = mock.patch.object(db, "DB_PATH", self.db_path)
        self.db_patcher.start()
        os.environ["EDITOS_PERSIST_PROVIDER"] = "file"
        os.environ["EDITOS_PERSIST_BACKUP_PATH"] = str(self.backup_path)
        persistence._restored = False  # noqa: SLF001
        persistence._dirty = False  # noqa: SLF001

    def tearDown(self) -> None:
        self.db_patcher.stop()
        self._tmp.cleanup()
        for name in ("EDITOS_PERSIST_PROVIDER", "EDITOS_PERSIST_BACKUP_PATH"):
            os.environ.pop(name, None)

    def test_ensure_backup_creates_archive(self) -> None:
        db.init_db()
        self.assertFalse(self.backup_path.exists())
        persistence.ensure_backup()
        self.assertTrue(self.backup_path.exists())

    def test_roundtrip_restores_shows(self) -> None:
        db.init_db()
        rss = "https://example.com/podcast/feed.xml"
        with mock.patch("rss_fetcher.fetch_feed") as fetch:
            fetch.return_value = mock.Mock(title="Test show")
            db.add_show(rss, title="Test show", category="eco")
        self.assertTrue(self.db_path.exists())
        self.assertTrue(persistence.sync_if_dirty(force=True))
        self.assertTrue(self.backup_path.exists())

        self.db_path.unlink(missing_ok=True)
        persistence._restored = False  # noqa: SLF001
        self.assertTrue(persistence.restore_if_configured())
        db.init_db()
        urls = [row["rss_url"] for row in db.list_shows()]
        self.assertIn(rss, urls)


if __name__ == "__main__":
    unittest.main()