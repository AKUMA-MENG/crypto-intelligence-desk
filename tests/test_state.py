from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from crypto_desk.state import StateStore, StateWriteError, news_fingerprint
from tests.helpers import sample_news


NOW = datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)


class StateTests(unittest.TestCase):
    def test_cold_start_baselines_without_pending_analysis(self):
        with TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            state = store.load()
            store.baseline(state, [sample_news()], NOW)
            store.save(state)
            saved = store.load()
            record = saved.items[news_fingerprint(sample_news())]
            self.assertTrue(saved.baseline_initialized)
            self.assertEqual(record["stage"], "baseline")

    def test_same_normalized_title_deduplicates_across_sources(self):
        first = sample_news(source="PANews", title="重大：BTC 获批！")
        second = sample_news(source="Odaily", title="重大 BTC获批")
        self.assertEqual(news_fingerprint(first), news_fingerprint(second))

    def test_delivery_started_becomes_unknown_after_restart(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(json.dumps({
                "version": 1,
                "baseline_initialized": True,
                "items": {"abc": {"stage": "delivery_started"}},
            }), encoding="utf-8")
            state = StateStore(path).load()
            self.assertEqual(state.items["abc"]["stage"], "delivery_unknown")
            self.assertEqual(
                StateStore(path).load().items["abc"]["stage"],
                "delivery_unknown",
            )

    def test_duplicate_title_merges_source_ids_without_new_work(self):
        with TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            state = store.load()
            first = sample_news(source="PANews", title="same")
            second = replace(first, source_id="other-2", source="Odaily")
            self.assertEqual(store.register_new(state, [first], NOW), 1)
            self.assertEqual(store.register_new(state, [second], NOW), 0)
            record = state.items[news_fingerprint(first)]
            self.assertEqual(record["source_ids"], ["other-2", "sample-1"])

    def test_corrupt_state_enters_safe_cold_start(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("not-json", encoding="utf-8")
            state = StateStore(path).load()
            self.assertFalse(state.baseline_initialized)
            self.assertEqual(state.items, {})
            self.assertEqual(len(list(Path(tmp).glob("state.json.corrupt-*"))), 1)

    def test_save_creates_parent_and_uses_atomic_replace(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "state.json"
            store = StateStore(path)
            state = store.load()
            with patch("crypto_desk.state.os.replace", wraps=os.replace) as replace:
                store.save(state)
            self.assertTrue(path.exists())
            replace.assert_called_once()

    def test_serialization_failure_preserves_previous_target(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            state = store.load()
            store.save(state)
            original = path.read_bytes()
            state.items["bad"] = {"stage": "pending_primary", "bad": {1, 2}}
            with self.assertRaisesRegex(StateWriteError, "state persistence failed"):
                store.save(state)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(tmp).glob("*.tmp")), [])

    def test_prune_keeps_pending_and_removes_old_terminal_records(self):
        with TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            state = store.load()
            state.items["pending"] = {
                "stage": "primary_retry",
                "updated_at": (NOW - timedelta(days=30)).isoformat(),
            }
            state.items["old"] = {
                "stage": "delivered",
                "updated_at": (NOW - timedelta(days=8)).isoformat(),
            }
            state.items["new"] = {
                "stage": "delivered",
                "updated_at": NOW.isoformat(),
            }
            store.prune(state, NOW)
            self.assertIn("pending", state.items)
            self.assertNotIn("old", state.items)
            self.assertIn("new", state.items)

    def test_prune_caps_terminal_records_at_two_thousand(self):
        with TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            state = store.load()
            for index in range(2002):
                state.items[str(index)] = {
                    "stage": "ignored",
                    "updated_at": (NOW + timedelta(seconds=index)).isoformat(),
                }
            store.prune(state, NOW + timedelta(hours=1))
            self.assertEqual(len(state.items), 2000)
            self.assertNotIn("0", state.items)
            self.assertNotIn("1", state.items)


if __name__ == "__main__":
    unittest.main()
