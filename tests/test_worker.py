from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timezone
import io
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from crypto_desk.config import WorkerConfig
from crypto_desk.sources import SourceFetchResult
from crypto_desk.state import StateStore
from crypto_desk.worker import Worker, main
from tests.helpers import FakeProcessor, FakeSourceFetcher, sample_news


NOW = datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)


class WorkerTests(unittest.TestCase):
    def config(self, tmp, configured=True, sources=("panews",)):
        return WorkerConfig(
            gemini_api_key="fake" if configured else "",
            bark_push_key="fake" if configured else "",
            primary_model="gemini-3.1-flash-lite",
            review_model="gemini-3.5-flash-lite",
            poll_seconds=30,
            state_file=Path(tmp) / "state.json",
            sources=sources,
        )

    def test_missing_configuration_makes_no_source_or_state_progress(self):
        class LoadForbiddenStore:
            def load(self):
                raise AssertionError("state must not be loaded")

        with TemporaryDirectory() as tmp:
            fetcher = FakeSourceFetcher([sample_news()])
            worker = Worker(
                self.config(tmp, configured=False),
                LoadForbiddenStore(),
                fetcher,
                FakeProcessor(),
            )
            self.assertEqual(worker.run_once(NOW), "configuration_missing")
            self.assertEqual(fetcher.calls, 0)
            self.assertFalse((Path(tmp) / "state.json").exists())

    def test_first_configured_poll_creates_baseline_without_processing(self):
        with TemporaryDirectory() as tmp:
            processor = FakeProcessor()
            state_path = Path(tmp) / "state.json"
            worker = Worker(
                self.config(tmp),
                StateStore(state_path),
                FakeSourceFetcher([sample_news()]),
                processor,
            )
            self.assertEqual(worker.run_once(NOW), "baseline_created")
            self.assertEqual(processor.calls, 0)
            state = StateStore(state_path).load()
            self.assertTrue(state.baseline_initialized)
            self.assertEqual(len(state.items), 1)

    def test_next_poll_registers_and_processes_new_and_due_items(self):
        with TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            processor = FakeProcessor()
            fetcher = FakeSourceFetcher([sample_news(title="old")])
            worker = Worker(self.config(tmp), store, fetcher, processor)
            worker.run_once(NOW)
            fetcher.items = [
                sample_news(title="old"),
                replace(sample_news(title="new"), source_id="sample-2"),
            ]
            self.assertEqual(worker.run_once(NOW), "poll_completed")
            self.assertEqual(processor.calls, 1)
            state = store.load()
            self.assertEqual(
                sorted(record["stage"] for record in state.items.values()),
                ["baseline", "pending_primary"],
            )

    def test_source_failure_is_logged_generically_and_cycle_completes(self):
        class FailingFetcher:
            def __call__(self, source_names, now):
                raise RuntimeError("sensitive-response-body")

        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            worker = Worker(
                self.config(tmp),
                StateStore(state_path),
                FailingFetcher(),
                FakeProcessor(),
            )
            with self.assertLogs("crypto_desk.worker", level="ERROR") as caught:
                self.assertEqual(worker.run_once(NOW), "poll_completed")
            output = "\n".join(caught.output)
            self.assertIn("source_cycle_failed", output)
            self.assertNotIn("sensitive-response-body", output)
            self.assertFalse(state_path.exists())

    def test_failed_cold_start_can_retry_and_create_a_real_baseline(self):
        class RecoveringFetcher:
            def __init__(self):
                self.calls = 0

            def __call__(self, source_names, now):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("private-upstream-detail")
                return (sample_news(),)

        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            fetcher = RecoveringFetcher()
            worker = Worker(
                self.config(tmp), StateStore(state_path), fetcher, FakeProcessor()
            )
            with self.assertLogs("crypto_desk.worker", level="ERROR"):
                self.assertEqual(worker.run_once(NOW), "poll_completed")
            self.assertFalse(state_path.exists())
            self.assertEqual(worker.run_once(NOW), "baseline_created")
            self.assertTrue(StateStore(state_path).load().baseline_initialized)

    def test_partial_cold_start_baselines_each_source_when_it_first_recovers(self):
        panews_old = sample_news(title="PANews old")
        panews_new = replace(
            panews_old, source_id="panews:new", title="PANews new"
        )
        binance_old = replace(
            panews_old,
            source_id="binance:old",
            source="Binance",
            title="Binance old",
        )

        class PartialFetcher:
            def __init__(self):
                self.results = [
                    SourceFetchResult({"panews": (panews_old,)}),
                    SourceFetchResult({
                        "panews": (panews_old, panews_new),
                        "binance": (binance_old,),
                    }),
                ]

            def __call__(self, source_names, now):
                return self.results.pop(0)

        with TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            processor = FakeProcessor()
            config = self.config(tmp, sources=("panews", "binance"))
            fetcher = PartialFetcher()

            first_worker = Worker(config, store, fetcher, processor)
            self.assertEqual(first_worker.run_once(NOW), "baseline_created")
            after_partial = store.load()
            self.assertEqual(after_partial.baselined_sources, ["panews"])
            self.assertFalse(after_partial.baseline_initialized)

            restarted_worker = Worker(config, store, fetcher, processor)
            self.assertEqual(restarted_worker.run_once(NOW), "poll_completed")
            final_state = store.load()
            self.assertEqual(
                final_state.baselined_sources, ["binance", "panews"]
            )
            self.assertTrue(final_state.baseline_initialized)
            self.assertEqual(processor.calls, 1)
            stages = {
                record["item"]["title"]: record["stage"]
                for record in final_state.items.values()
            }
            self.assertEqual(stages["Binance old"], "baseline")
            self.assertEqual(stages["PANews new"], "pending_primary")

    def test_corrupt_state_recovery_still_baselines_sources_individually(self):
        with TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state_path.write_text("not-json", encoding="utf-8")
            config = self.config(tmp, sources=("panews", "binance"))
            result = SourceFetchResult({"panews": (sample_news(),)})
            worker = Worker(
                config,
                StateStore(state_path),
                lambda source_names, now: result,
                FakeProcessor(),
            )
            self.assertEqual(worker.run_once(NOW), "baseline_created")
            state = StateStore(state_path).load()
            self.assertEqual(state.baselined_sources, ["panews"])
            self.assertFalse(state.baseline_initialized)

    def test_two_concurrent_cycles_return_overlap_without_overlapping_fetches(self):
        class BlockingFetcher:
            def __init__(self):
                self.active = 0
                self.max_active = 0
                self.calls = 0
                self.lock = threading.Lock()
                self.entered = threading.Event()
                self.release = threading.Event()

            def __call__(self, source_names, now):
                with self.lock:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                    self.calls += 1
                    call_number = self.calls
                try:
                    if call_number == 1:
                        self.entered.set()
                        self.release.wait(2)
                    return ()
                finally:
                    with self.lock:
                        self.active -= 1

        with TemporaryDirectory() as tmp:
            fetcher = BlockingFetcher()
            worker = Worker(
                self.config(tmp),
                StateStore(Path(tmp) / "state.json"),
                fetcher,
                FakeProcessor(),
            )
            results = []
            first = threading.Thread(target=lambda: results.append(worker.run_once(NOW)))
            second = threading.Thread(target=lambda: results.append(worker.run_once(NOW)))
            first.start()
            self.assertTrue(fetcher.entered.wait(1))
            second.start()
            second.join(1)
            self.assertFalse(second.is_alive())
            fetcher.release.set()
            first.join(2)
            self.assertFalse(first.is_alive())
            self.assertEqual(fetcher.max_active, 1)
            self.assertEqual(fetcher.calls, 1)
            self.assertIn("poll_skipped_overlap", results)

    def test_run_forever_waits_when_configuration_is_missing(self):
        class StopAfterOneWait:
            def __init__(self):
                self.wait_calls = []

            def is_set(self):
                return bool(self.wait_calls)

            def wait(self, timeout):
                self.wait_calls.append(timeout)
                return True

        with TemporaryDirectory() as tmp:
            fetcher = FakeSourceFetcher([])
            worker = Worker(
                self.config(tmp, configured=False),
                StateStore(Path(tmp) / "state.json"),
                fetcher,
                FakeProcessor(),
            )
            stop = StopAfterOneWait()
            worker.run_forever(stop)
            self.assertEqual(stop.wait_calls, [30])
            self.assertEqual(fetcher.calls, 0)

    def test_run_forever_logs_cycle_failure_generically_and_still_waits(self):
        class BrokenWorker(Worker):
            def run_once(self, now):
                raise RuntimeError("sensitive-cycle-detail")

        class StopAfterOneWait:
            def __init__(self):
                self.wait_calls = []

            def is_set(self):
                return bool(self.wait_calls)

            def wait(self, timeout):
                self.wait_calls.append(timeout)
                return True

        with TemporaryDirectory() as tmp:
            worker = BrokenWorker(
                self.config(tmp),
                StateStore(Path(tmp) / "state.json"),
                FakeSourceFetcher([]),
                FakeProcessor(),
            )
            stop = StopAfterOneWait()
            with self.assertLogs("crypto_desk.worker", level="ERROR") as caught:
                worker.run_forever(stop)
            output = "\n".join(caught.output)
            self.assertEqual(stop.wait_calls, [30])
            self.assertIn("worker_cycle_failed", output)
            self.assertNotIn("sensitive-cycle-detail", output)

    def test_once_cli_with_explicit_missing_env_is_safe_and_idle(self):
        with TemporaryDirectory() as tmp:
            missing_env = Path(tmp) / "missing.env"
            output = io.StringIO()
            safe_environment = {
                key: value
                for key, value in os.environ.items()
                if key not in {"GEMINI_API_KEY", "BARK_PUSH_KEY"}
            }
            with patch.dict(os.environ, safe_environment, clear=True):
                with redirect_stdout(output):
                    result = main(["--once", "--env-file", str(missing_env)])
            self.assertEqual(result, 0)
            self.assertEqual(
                output.getvalue(),
                "worker configuration missing; no requests sent\n",
            )


if __name__ == "__main__":
    unittest.main()
