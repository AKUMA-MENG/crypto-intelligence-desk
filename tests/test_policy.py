from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from crypto_desk.gemini import GeminiPermanentError
from crypto_desk.models import Analysis
from crypto_desk.policy import NewsProcessor, format_notification
from crypto_desk.state import StateStore, StateWriteError, news_fingerprint
from tests.helpers import FakeBark, FakeGemini, sample_news


NOW = datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)


def analysis(level, direction="利空"):
    return Analysis(
        direction, "负面", "中性", level, 90, ("BTC",), "监管",
        False, "具体因果判断。", "政策未正式生效。",
    )


class PolicyTests(unittest.TestCase):
    def make_state(self, tmp):
        store = StateStore(Path(tmp) / "state.json")
        state = store.load()
        state.baseline_initialized = True
        store.register_new(state, [sample_news()], NOW)
        store.save(state)
        return store, state

    def test_primary_l3_never_calls_review_or_bark(self):
        with TemporaryDirectory() as tmp:
            store, state = self.make_state(tmp)
            gemini = FakeGemini([analysis(3)])
            bark = FakeBark(True)
            NewsProcessor(gemini, bark, store, "primary", "review").process_due(state, NOW)
            self.assertEqual(gemini.models, ["primary"])
            self.assertEqual(bark.calls, [])

    def test_review_downgrade_does_not_send(self):
        with TemporaryDirectory() as tmp:
            store, state = self.make_state(tmp)
            gemini = FakeGemini([analysis(4), analysis(3)])
            bark = FakeBark(True)
            NewsProcessor(gemini, bark, store, "primary", "review").process_due(state, NOW)
            self.assertEqual(gemini.models, ["primary", "review"])
            self.assertEqual(gemini.review_flags, [False, True])
            self.assertEqual(bark.calls, [])

    def test_review_l4_sends_once_and_restart_does_not_repeat(self):
        with TemporaryDirectory() as tmp:
            store, state = self.make_state(tmp)
            gemini = FakeGemini([analysis(4), analysis(4)])
            bark = FakeBark(True)
            processor = NewsProcessor(gemini, bark, store, "primary", "review")
            processor.process_due(state, NOW)
            processor.process_due(store.load(), NOW + timedelta(minutes=1))
            self.assertEqual(len(bark.calls), 1)

    def test_exhausted_review_falls_back_to_primary_once(self):
        with TemporaryDirectory() as tmp:
            store, state = self.make_state(tmp)
            gemini = FakeGemini([analysis(5), "retry", "retry", "retry", "retry"])
            bark = FakeBark(True)
            processor = NewsProcessor(gemini, bark, store, "primary", "review")
            for offset in (0, 30, 150, 450):
                processor.process_due(state, NOW + timedelta(seconds=offset))
            self.assertEqual(len(bark.calls), 1)
            self.assertIn("初判回退", bark.calls[0][1])
            self.assertIn("复核未完成，当前为初判结果。", bark.calls[0][0])

    def test_permanent_review_error_falls_back_immediately(self):
        with TemporaryDirectory() as tmp:
            store, state = self.make_state(tmp)
            gemini = FakeGemini([analysis(5), GeminiPermanentError("fixed")])
            bark = FakeBark(True)
            NewsProcessor(gemini, bark, store, "primary", "review").process_due(state, NOW)
            self.assertEqual(gemini.review_flags, [False, True])
            self.assertEqual(len(bark.calls), 1)
            self.assertIn("初判回退", bark.calls[0][1])

    def test_review_retry_survives_restart_with_persisted_primary_analysis(self):
        with TemporaryDirectory() as tmp:
            store, state = self.make_state(tmp)
            first_gemini = FakeGemini([analysis(5), "retry"])
            NewsProcessor(
                first_gemini,
                FakeBark(True),
                store,
                "primary",
                "review",
            ).process_due(state, NOW)

            restarted_store = StateStore(Path(tmp) / "state.json")
            restarted_state = restarted_store.load()
            restarted_gemini = FakeGemini(["retry", "retry", "retry"])
            bark = FakeBark(True)
            processor = NewsProcessor(
                restarted_gemini,
                bark,
                restarted_store,
                "primary",
                "review",
            )
            for offset in (30, 150, 450):
                processor.process_due(
                    restarted_state,
                    NOW + timedelta(seconds=offset),
                )
            self.assertEqual(restarted_gemini.review_flags, [True, True, True])
            self.assertEqual(len(bark.calls), 1)
            self.assertIn("初判回退", bark.calls[0][1])

    def test_primary_retry_schedule_is_exact_then_exhausts_without_bark(self):
        with TemporaryDirectory() as tmp:
            store, state = self.make_state(tmp)
            gemini = FakeGemini(["retry", "retry", "retry", "retry"])
            bark = FakeBark(True)
            processor = NewsProcessor(gemini, bark, store, "primary", "review")
            fingerprint = news_fingerprint(sample_news())
            expected = ((0, 30), (30, 150), (150, 450))
            for offset, due_at in expected:
                processor.process_due(state, NOW + timedelta(seconds=offset))
                self.assertEqual(
                    state.items[fingerprint]["next_attempt_at"],
                    (NOW + timedelta(seconds=due_at)).isoformat(),
                )
            processor.process_due(state, NOW + timedelta(seconds=449))
            self.assertEqual(len(gemini.models), 3)
            processor.process_due(state, NOW + timedelta(seconds=450))
            self.assertEqual(state.items[fingerprint]["stage"], "analysis_failed")
            self.assertEqual(bark.calls, [])

    def test_permanent_primary_error_is_not_retried(self):
        with TemporaryDirectory() as tmp:
            store, state = self.make_state(tmp)
            gemini = FakeGemini([GeminiPermanentError("fixed")])
            bark = FakeBark(True)
            NewsProcessor(gemini, bark, store, "primary", "review").process_due(state, NOW)
            record = state.items[news_fingerprint(sample_news())]
            self.assertEqual(record["stage"], "analysis_failed")
            self.assertEqual(len(gemini.models), 1)
            self.assertEqual(bark.calls, [])

    def test_state_write_failure_before_delivery_causes_zero_bark_calls(self):
        class FailDeliveryStartedStore(StateStore):
            def save(self, state):
                if any(
                    record.get("stage") == "delivery_started"
                    for record in state.items.values()
                ):
                    raise StateWriteError("state persistence failed")
                return super().save(state)

        with TemporaryDirectory() as tmp:
            store = FailDeliveryStartedStore(Path(tmp) / "state.json")
            state = store.load()
            state.baseline_initialized = True
            store.register_new(state, [sample_news()], NOW)
            store.save(state)
            bark = FakeBark(True)
            processor = NewsProcessor(
                FakeGemini([analysis(4), analysis(4)]),
                bark,
                store,
                "primary",
                "review",
            )
            with self.assertRaisesRegex(StateWriteError, "state persistence failed"):
                processor.process_due(state, NOW)
            self.assertEqual(bark.calls, [])

    def test_delivery_started_is_on_disk_before_bark_transport(self):
        with TemporaryDirectory() as tmp:
            store, state = self.make_state(tmp)
            fingerprint = news_fingerprint(sample_news())

            class InspectingBark(FakeBark):
                def send(inner_self, text, title, now=None):
                    payload = json.loads(
                        (Path(tmp) / "state.json").read_text(encoding="utf-8")
                    )
                    inner_self.persisted_stage = payload["items"][fingerprint]["stage"]
                    return super().send(text, title, now)

            bark = InspectingBark(True)
            NewsProcessor(
                FakeGemini([analysis(4), analysis(4)]),
                bark,
                store,
                "primary",
                "review",
            ).process_due(state, NOW)
            self.assertEqual(bark.persisted_stage, "delivery_started")

    def test_bark_false_result_becomes_terminal_delivery_failed(self):
        with TemporaryDirectory() as tmp:
            store, state = self.make_state(tmp)
            NewsProcessor(
                FakeGemini([analysis(4), analysis(4)]),
                FakeBark(False),
                store,
                "primary",
                "review",
            ).process_due(state, NOW)
            self.assertEqual(
                state.items[news_fingerprint(sample_news())]["stage"],
                "delivery_failed",
            )

    def test_transport_interruption_recovers_unknown_without_resend(self):
        class InterruptedBark(FakeBark):
            def send(self, text, title, now=None):
                super().send(text, title, now)
                raise RuntimeError("simulated process interruption")

        with TemporaryDirectory() as tmp:
            store, state = self.make_state(tmp)
            bark = InterruptedBark(True)
            processor = NewsProcessor(
                FakeGemini([analysis(4), analysis(4)]),
                bark,
                store,
                "primary",
                "review",
            )
            with self.assertRaisesRegex(RuntimeError, "process interruption"):
                processor.process_due(state, NOW)

            restarted_state = store.load()
            self.assertEqual(
                restarted_state.items[news_fingerprint(sample_news())]["stage"],
                "delivery_unknown",
            )
            processor.process_due(restarted_state, NOW + timedelta(minutes=1))
            self.assertEqual(len(bark.calls), 1)

    def test_post_transport_state_failure_recovers_unknown_without_resend(self):
        class FailDeliveredStore(StateStore):
            def save(self, state):
                if any(
                    record.get("stage") == "delivered"
                    for record in state.items.values()
                ):
                    raise StateWriteError("state persistence failed")
                return super().save(state)

        with TemporaryDirectory() as tmp:
            store = FailDeliveredStore(Path(tmp) / "state.json")
            state = store.load()
            state.baseline_initialized = True
            store.register_new(state, [sample_news()], NOW)
            store.save(state)
            bark = FakeBark(True)
            processor = NewsProcessor(
                FakeGemini([analysis(4), analysis(4)]),
                bark,
                store,
                "primary",
                "review",
            )
            with self.assertRaisesRegex(StateWriteError, "state persistence failed"):
                processor.process_due(state, NOW)
            self.assertEqual(len(bark.calls), 1)

            restarted_state = store.load()
            self.assertEqual(
                restarted_state.items[news_fingerprint(sample_news())]["stage"],
                "delivery_unknown",
            )
            processor.process_due(restarted_state, NOW + timedelta(minutes=1))
            self.assertEqual(len(bark.calls), 1)

    def test_state_write_failure_stops_pass_before_later_record(self):
        class FailDeliveryStartedStore(StateStore):
            def save(self, state):
                if any(
                    record.get("stage") == "delivery_started"
                    for record in state.items.values()
                ):
                    raise StateWriteError("state persistence failed")
                return super().save(state)

        with TemporaryDirectory() as tmp:
            store = FailDeliveryStartedStore(Path(tmp) / "state.json")
            state = store.load()
            state.baseline_initialized = True
            first = sample_news(title="first")
            second = replace(sample_news(title="second"), source_id="sample-2")
            store.register_new(state, [first, second], NOW)
            store.save(state)
            gemini = FakeGemini([
                analysis(4),
                analysis(4),
                analysis(4),
                analysis(4),
            ])
            bark = FakeBark(True)
            with self.assertRaisesRegex(StateWriteError, "state persistence failed"):
                NewsProcessor(
                    gemini,
                    bark,
                    store,
                    "primary",
                    "review",
                ).process_due(state, NOW)
            self.assertEqual(gemini.models, ["primary", "review"])
            self.assertEqual(bark.calls, [])

    def test_process_due_counts_each_advanced_record_once(self):
        with TemporaryDirectory() as tmp:
            store, state = self.make_state(tmp)
            processor = NewsProcessor(
                FakeGemini([analysis(3)]),
                FakeBark(True),
                store,
                "primary",
                "review",
            )
            self.assertEqual(processor.process_due(state, NOW), 1)
            self.assertEqual(
                processor.process_due(store.load(), NOW + timedelta(minutes=1)),
                0,
            )

    def test_review_fallback_formats_the_saved_primary_analysis(self):
        title, text = format_notification(
            sample_news(), analysis(4, "利好"), analysis(5, "利空"), True, NOW
        )
        self.assertEqual(title, "币圈重大情报 · L5 · 初判回退")
        self.assertIn("复核未完成，当前为初判结果。", text)
        self.assertIn("短期：负面", text)

    def test_bark_false_result_is_not_retried_after_restart(self):
        with TemporaryDirectory() as tmp:
            store, state = self.make_state(tmp)
            bark = FakeBark(False)
            processor = NewsProcessor(
                FakeGemini([analysis(4), analysis(4)]),
                bark,
                store,
                "primary",
                "review",
            )
            processor.process_due(state, NOW)
            processor.process_due(store.load(), NOW + timedelta(minutes=1))
            self.assertEqual(len(bark.calls), 1)

    def test_notification_uses_approved_chinese_fields(self):
        title, text = format_notification(
            sample_news(), analysis(5), analysis(4), False, NOW
        )
        self.assertEqual(title, "币圈重大情报 · L5 · 利空")
        self.assertEqual(text, """重大新闻

来源：PANews
分类：监管
影响资产：BTC
短期：负面
长期：中性
置信度：90%

核心判断：
具体因果判断。

解读失效条件：
政策未正式生效。

原文：
https://news.example/item/1""")

    def test_one_record_failure_does_not_block_another_due_record(self):
        with TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            state = store.load()
            state.baseline_initialized = True
            failed_item = sample_news(title="first")
            delivered_item = replace(
                sample_news(title="second"), source_id="sample-2"
            )
            store.register_new(state, [failed_item, delivered_item], NOW)
            store.save(state)
            bark = FakeBark(True)
            NewsProcessor(
                FakeGemini([
                    GeminiPermanentError("fixed"),
                    analysis(4),
                    analysis(4),
                ]),
                bark,
                store,
                "primary",
                "review",
            ).process_due(state, NOW)
            self.assertEqual(
                state.items[news_fingerprint(failed_item)]["stage"],
                "analysis_failed",
            )
            self.assertEqual(
                state.items[news_fingerprint(delivered_item)]["stage"],
                "delivered",
            )
            self.assertEqual(len(bark.calls), 1)


if __name__ == "__main__":
    unittest.main()
