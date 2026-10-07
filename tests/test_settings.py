"""Settings persistence, migration, and request validation."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import config, data
from backend.api.routes_settings import router


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.settings_path = root / "settings.json"
        self.rss_path = root / "rss_settings.json"
        self.app_patch = patch.object(data, "_APP_SETTINGS_FILE", self.settings_path)
        self.rss_patch = patch.object(data, "_SETTINGS_FILE", self.rss_path)
        self.app_patch.start()
        self.rss_patch.start()
        config._overrides.clear()
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        config._overrides.clear()
        self.rss_patch.stop()
        self.app_patch.stop()
        self.directory.cleanup()

    def test_settings_persist_and_invalid_updates_are_rejected(self):
        response = self.client.patch("/api/settings", json={"RSS_POLL_INTERVAL_MIN": 45})
        self.assertEqual(response.status_code, 200)
        config._overrides.clear()
        self.assertEqual(self.client.get("/api/settings").json()["RSS_POLL_INTERVAL_MIN"], 45)
        self.assertEqual(self.client.patch("/api/settings", json={"RSS_POLL_INTERVAL_MIN": 0}).status_code, 422)
        self.assertEqual(self.client.patch("/api/settings", json={"unknown": 1}).status_code, 422)
        self.assertEqual(json.loads(self.settings_path.read_text())["RSS_POLL_INTERVAL_MIN"], 45)

    def test_legacy_rss_exclusions_migrate(self):
        self.rss_path.write_text(json.dumps({"exclude_patterns": ["全集", "预告"]}))
        self.assertEqual(self.client.get("/api/settings").json()["RSS_EXCLUDE_PATTERNS"], ["全集", "预告"])
        self.assertEqual(json.loads(self.settings_path.read_text())["RSS_EXCLUDE_PATTERNS"], ["全集", "预告"])

    def test_fontinass_settings_validation_and_persistence(self):
        values = {"FONTINASS_ENABLED": True, "FONTINASS_URL": "https://example.test", "FONTINASS_TIMEOUT": 90}
        self.assertEqual(self.client.patch("/api/settings", json=values).status_code, 200)
        config._overrides.clear()
        settings = self.client.get("/api/settings").json()
        for key, value in values.items():
            self.assertEqual(settings[key], value)
        for invalid in ({"FONTINASS_ENABLED": "true"}, {"FONTINASS_TIMEOUT": 0},
                        {"FONTINASS_URL": "file:///tmp"}, {"FONTINASS_URL": "https://user:secret@example.test"}):
            self.assertEqual(self.client.patch("/api/settings", json=invalid).status_code, 422)


if __name__ == "__main__":
    unittest.main()
