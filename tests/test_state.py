from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from crypto_desk.state import STATE_VERSION, StateStore, StateWriteError, news_fingerprint
from tests.helpers import sample_news


NOW = datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)

VALID_ANALYSIS = {
    "direction": "利好",
    "short_term": "正面",
    "long_term": "中性",
    "level": 4,
    "confidence": 80,
    "coins": ["BTC", "ETH"],
    "category": "监管",
    "priced_in": False,
    "why": "监管变化会影响市场预期。",
    "reverse": "政策没有正式实施。",
}


def complete_record(stage="pending_primary"):
    return {
        "stage": stage,
        "item": {
            "source_id": "sample-1",
            "source": "PANews",
            "title": "重大新闻",
            "body": "用于测试的新闻正文。",
            "url": "https://news.example/item/1",
            "published_at": (NOW - timedelta(minutes=1)).isoformat(),
        },
        "source_ids": ["sample-1"],
        "first_seen_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        "primary_analysis": None,
        "review_analysis": None,
        "primary_attempts": 0,
        "review_attempts": 0,
        "next_attempt_at": None,
        "review_failed": False,
        "delivery_started_at": None,
    }


class StateTests(unittest.TestCase):
    def test_per_source_baseline_round_trips_for_safe_source_additions(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            state = store.load()
            store.baseline_source(
                state,
                "panews",
                [replace(sample_news(), source_id="panews:guid:old")],
                NOW,
            )
            store.save(state)

            restarted = store.load()
            self.assertEqual(restarted.baselined_sources, ["panews"])
            self.assertFalse(restarted.baseline_initialized)

    def test_same_source_id_changed_title_is_deduped_after_restart(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            original = replace(
                sample_news(title="旧标题"), source_id="panews:guid:stable-1"
            )
            renamed = replace(original, title="同一事件的新标题")
            state = store.load()
            store.baseline_source(state, "panews", [original], NOW)
            store.save(state)

            restarted = store.load()
            self.assertEqual(store.register_new(restarted, [renamed], NOW), 0)
            self.assertEqual(len(restarted.items), 1)
            record = next(iter(restarted.items.values()))
            self.assertEqual(record["item"]["title"], "旧标题")
            self.assertEqual(record["source_ids"], ["panews:guid:stable-1"])

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
            fingerprint = news_fingerprint(sample_news())
            path.write_text(json.dumps({
                "version": STATE_VERSION,
                "baseline_initialized": True,
                "baselined_sources": ["panews"],
                "items": {fingerprint: complete_record("delivery_started")},
            }), encoding="utf-8")
            state = StateStore(path).load()
            self.assertEqual(
                state.items[fingerprint]["stage"], "delivery_unknown"
            )
            self.assertEqual(
                StateStore(path).load().items[fingerprint]["stage"],
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

    def test_duplicate_title_does_not_reopen_terminal_record(self):
        with TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            state = store.load()
            first = sample_news(source="PANews", title="same")
            second = replace(first, source_id="other-2", source="Odaily")
            store.baseline(state, [first], NOW)
            self.assertEqual(store.register_new(state, [second], NOW), 0)
            record = state.items[news_fingerprint(first)]
            self.assertEqual(record["stage"], "baseline")
            self.assertEqual(record["source_ids"], ["other-2", "sample-1"])

    def test_restart_dedupe_uses_canonical_terminal_fingerprint(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            state = store.load()
            first = sample_news(source="PANews", title="重大：BTC 获批！")
            second = replace(
                first,
                source_id="other-2",
                source="Odaily",
                title="重大 BTC获批",
            )
            store.baseline(state, [first], NOW)
            store.save(state)

            restarted = store.load()
            self.assertEqual(store.register_new(restarted, [second], NOW), 0)
            self.assertEqual(len(restarted.items), 1)
            record = restarted.items[news_fingerprint(first)]
            self.assertEqual(record["stage"], "baseline")
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

            def fail_after_partial_write(payload, handle, **kwargs):
                handle.write('{"partial":')
                raise RecursionError("nested payload")

            with patch("crypto_desk.state.json.dump", fail_after_partial_write):
                with self.assertRaisesRegex(
                    StateWriteError, "^state persistence failed$"
                ):
                    store.save(state)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(tmp).glob(".*.tmp")), [])

    def test_load_recursion_failure_quarantines_deep_state(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("[deeply nested state]", encoding="utf-8")
            with patch(
                "crypto_desk.state.json.loads",
                side_effect=RecursionError("nested payload"),
            ):
                state = StateStore(path).load()
            self.assertFalse(state.baseline_initialized)
            self.assertEqual(state.items, {})
            self.assertFalse(path.exists())
            self.assertEqual(len(list(Path(tmp).glob("state.json.corrupt-*"))), 1)

    def test_rejects_wrong_or_nested_record_shapes(self):
        invalid_records = []

        bool_attempt = complete_record()
        bool_attempt["primary_attempts"] = True
        invalid_records.append(("bool attempt", bool_attempt))

        nested_analysis = complete_record()
        nested_analysis["primary_analysis"] = {
            **VALID_ANALYSIS,
            "why": {"headers": {"Authorization": "secret"}},
        }
        invalid_records.append(("nested analysis", nested_analysis))

        unknown_stage = complete_record("not-a-stage")
        invalid_records.append(("unknown stage", unknown_stage))

        too_many_sources = complete_record()
        too_many_sources["source_ids"] = [str(index) for index in range(9)]
        invalid_records.append(("too many source IDs", too_many_sources))

        with TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            for name, record in invalid_records:
                with self.subTest(name=name):
                    state = store.load()
                    state.items[news_fingerprint(sample_news())] = record
                    with self.assertRaisesRegex(
                        StateWriteError, "^state persistence failed$"
                    ):
                        store.save(state)

    def test_load_rejects_invalid_nested_schema_as_corrupt(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            record = complete_record()
            record["review_attempts"] = False
            path.write_text(json.dumps({
                "version": STATE_VERSION,
                "baseline_initialized": True,
                "baselined_sources": ["panews"],
                "items": {news_fingerprint(sample_news()): record},
            }), encoding="utf-8")
            state = StateStore(path).load()
            self.assertFalse(state.baseline_initialized)
            self.assertEqual(state.items, {})
            self.assertEqual(len(list(Path(tmp).glob("state.json.corrupt-*"))), 1)

    def test_complete_analysis_shape_round_trips(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = StateStore(path).load()
            fingerprint = news_fingerprint(sample_news())
            state.items[fingerprint] = complete_record()
            state.items[fingerprint]["primary_analysis"] = dict(VALID_ANALYSIS)
            StateStore(path).save(state)
            loaded = StateStore(path).load()
            self.assertEqual(
                loaded.items[fingerprint]["primary_analysis"], VALID_ANALYSIS
            )

    def test_notification_pending_stage_round_trips(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = StateStore(path).load()
            fingerprint = news_fingerprint(sample_news())
            state.items[fingerprint] = complete_record("notification_pending")
            StateStore(path).save(state)
            self.assertEqual(
                StateStore(path).load().items[fingerprint]["stage"],
                "notification_pending",
            )

    def test_save_rejects_mismatched_fingerprint_and_preserves_target(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            state = store.load()
            store.save(state)
            original = path.read_bytes()
            state.items["0" * 64] = complete_record()
            with self.assertRaisesRegex(
                StateWriteError, "^state persistence failed$"
            ):
                store.save(state)
            self.assertEqual(path.read_bytes(), original)

    def test_load_quarantines_mismatched_fingerprint(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(json.dumps({
                "version": STATE_VERSION,
                "baseline_initialized": True,
                "baselined_sources": ["panews"],
                "items": {"0" * 64: complete_record()},
            }), encoding="utf-8")
            state = StateStore(path).load()
            self.assertFalse(state.baseline_initialized)
            self.assertEqual(state.items, {})
            self.assertFalse(path.exists())
            self.assertEqual(len(list(Path(tmp).glob("state.json.corrupt-*"))), 1)

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
