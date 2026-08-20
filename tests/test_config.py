from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from crypto_desk.config import ConfigError, load_config, load_env_file


class ConfigTests(unittest.TestCase):
    def test_env_file_does_not_override_existing_environment(self):
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(
                "GEMINI_API_KEY=file-gemini\nBARK_PUSH_KEY=file-bark\n",
                encoding="utf-8",
            )
            environ = {"GEMINI_API_KEY": "system-gemini"}
            load_env_file(env_file, environ)
            self.assertEqual(environ["GEMINI_API_KEY"], "system-gemini")
            self.assertEqual(environ["BARK_PUSH_KEY"], "file-bark")

    def test_config_uses_confirmed_models_and_defaults(self):
        with TemporaryDirectory() as tmp:
            config = load_config(
                {
                    "GEMINI_API_KEY": "fake-gemini",
                    "BARK_PUSH_KEY": "fake-bark",
                },
                Path(tmp) / ".env",
                Path(tmp),
            )
            self.assertEqual(config.primary_model, "gemini-3.1-flash-lite")
            self.assertEqual(config.review_model, "gemini-3.5-flash-lite")
            self.assertEqual(config.poll_seconds, 30)
            self.assertEqual(
                config.sources,
                ("panews", "binance", "odaily", "ctcn", "techflow", "catcher"),
            )
            self.assertTrue(config.is_configured)

    def test_missing_secret_leaves_worker_unconfigured(self):
        with TemporaryDirectory() as tmp:
            config = load_config({}, Path(tmp) / ".env", Path(tmp))
            self.assertFalse(config.is_configured)

    def test_env_parser_accepts_one_quote_pair_and_ignores_invalid_names(self):
        with TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text('GOOD_NAME="quoted value"\nBAD-NAME=ignored\n', encoding="utf-8")
            environ = {}
            load_env_file(env_file, environ)
            self.assertEqual(environ["GOOD_NAME"], "quoted value")
            self.assertNotIn("BAD-NAME", environ)

    def test_optional_sources_are_allowed_poll_is_clamped_and_empty_state_uses_default(self):
        with TemporaryDirectory() as tmp:
            config = load_config(
                {
                    "GEMINI_API_KEY": "fake",
                    "BARK_PUSH_KEY": "fake",
                    "CID_WORKER_POLL_SECONDS": "1",
                    "CID_WORKER_STATE_FILE": "",
                    "CID_WORKER_SOURCES": "jinse,blockbeats",
                },
                Path(tmp) / ".env",
                Path(tmp),
            )
            self.assertEqual(config.poll_seconds, 10)
            self.assertEqual(config.sources, ("jinse", "blockbeats"))
            self.assertEqual(config.state_file, Path(tmp) / ".runtime" / "worker-state.json")

    def test_unknown_source_is_rejected_without_echoing_the_value(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ConfigError, "unknown worker source") as caught:
                load_config(
                    {"CID_WORKER_SOURCES": "secret-upstream"},
                    Path(tmp) / ".env",
                    Path(tmp),
                )
            self.assertNotIn("secret-upstream", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
