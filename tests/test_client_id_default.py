"""Zero-setup Jamendo configuration with optional user overrides."""
import json
import tempfile
import unittest
from pathlib import Path

from jamendo import DEFAULT_CLIENT_ID, resolve_client_id, valid_client_id


class ClientIdDefaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = Path(self.tmp.name) / "user-data" / "jamendo.json"

    def test_first_run_never_requires_user_input(self):
        self.assertEqual(DEFAULT_CLIENT_ID, "fdfc0cdf")
        self.assertTrue(valid_client_id(DEFAULT_CLIENT_ID))
        self.assertEqual(resolve_client_id(self.settings, environment={}),
                         (DEFAULT_CLIENT_ID, "built-in default"))

    def test_existing_saved_id_is_preserved(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text(json.dumps({"client_id": "1234abcd"}))
        self.assertEqual(resolve_client_id(self.settings, environment={}),
                         ("1234abcd", "saved override"))

    def test_environment_override_takes_precedence(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text(json.dumps({"client_id": "1234abcd"}))
        self.assertEqual(resolve_client_id(self.settings, environment={"JAMENDO_CLIENT_ID": "fedcba98"}),
                         ("fedcba98", "environment override"))

    def test_bad_settings_or_environment_fall_back(self):
        self.settings.parent.mkdir(parents=True)
        for raw in ("{broken", "null", '["unexpected"]', '{"client_id": "bad !"}'):
            with self.subTest(raw=raw):
                self.settings.write_text(raw)
                self.assertEqual(resolve_client_id(self.settings, environment={"JAMENDO_CLIENT_ID": "invalid !"}),
                                 (DEFAULT_CLIENT_ID, "built-in default"))

    def test_invalid_environment_does_not_displace_valid_saved_override(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text(json.dumps({"client_id": "1234abcd"}))
        self.assertEqual(resolve_client_id(self.settings, environment={"JAMENDO_CLIENT_ID": "INVALID!"}),
                         ("1234abcd", "saved override"))


if __name__ == "__main__":
    unittest.main()
