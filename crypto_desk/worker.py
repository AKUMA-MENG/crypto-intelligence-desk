"""Single-flight polling runtime for the background news worker."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import signal
import threading
from typing import Any, Callable, Sequence

from crypto_desk.config import WorkerConfig, load_config
from crypto_desk.gemini import GeminiClient
from crypto_desk.notifications.bark import BarkClient
from crypto_desk.policy import NewsProcessor
from crypto_desk.sources import SourceClient, SourceFetchResult, fetch_sources_with_status
from crypto_desk.state import StateStore
from crypto_desk.transport import UrllibTransport


_LOGGER = logging.getLogger(__name__)


class Worker:
    """Compose one durable polling cycle without permitting overlap."""

    def __init__(
        self,
        config: WorkerConfig,
        store: Any,
        source_fetcher: Callable[[Sequence[str], datetime], Sequence[Any]],
        processor: Any,
    ) -> None:
        self.config = config
        self.store = store
        self.source_fetcher = source_fetcher
        self.processor = processor
        self._cycle_lock = threading.Lock()

    def run_once(self, now: datetime) -> str:
        """Run one polling cycle and return its stable outcome name."""
        if not self._cycle_lock.acquire(blocking=False):
            return "poll_skipped_overlap"

        try:
            if not self.config.is_configured:
                return "configuration_missing"

            state = self.store.load()
            try:
                result = self.source_fetcher(self.config.sources, now)
            except Exception:
                _LOGGER.error("source_cycle_failed")
                return "poll_completed"

            if not isinstance(result, SourceFetchResult):
                if len(self.config.sources) != 1:
                    _LOGGER.error("source_cycle_failed")
                    return "poll_completed"
                result = SourceFetchResult({self.config.sources[0]: tuple(result)})

            previously_baselined = set(state.baselined_sources)
            new_items = []
            for source_key in self.config.sources:
                items = result.by_source.get(source_key)
                if items is None:
                    continue
                if source_key not in previously_baselined:
                    self.store.baseline_source(state, source_key, items, now)
                else:
                    new_items.extend(items)

            state.baseline_initialized = all(
                source_key in state.baselined_sources
                for source_key in self.config.sources
            )
            created_baseline = bool(
                set(state.baselined_sources) - previously_baselined
            )

            self.store.register_new(state, new_items, now)
            self.store.save(state)
            if created_baseline and not new_items:
                return "baseline_created"
            self.processor.process_due(state, now)
            self.store.prune(state, now)
            self.store.save(state)
            return "poll_completed"
        finally:
            self._cycle_lock.release()

    def run_forever(self, stop_event: Any) -> None:
        """Poll until stopped, using the event wait for interruptible pacing."""
        while not stop_event.is_set():
            try:
                self.run_once(datetime.now(timezone.utc))
            except Exception:
                _LOGGER.error("worker_cycle_failed")
            stop_event.wait(self.config.poll_seconds)


def _build_worker(config: WorkerConfig) -> Worker:
    transport = UrllibTransport()
    source_client = SourceClient(transport)
    store = StateStore(config.state_file)
    gemini = GeminiClient(config.gemini_api_key, transport)
    bark = BarkClient(config.bark_push_key, transport)
    processor = NewsProcessor(
        gemini,
        bark,
        store,
        config.primary_model,
        config.review_model,
    )

    def source_fetcher(source_names: Sequence[str], now: datetime):
        return fetch_sources_with_status(source_names, source_client, now)

    return Worker(config, store, source_fetcher, processor)


def _argument_parser(base_dir: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the crypto news worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="run one polling cycle and exit",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=base_dir / ".env",
        metavar="PATH",
        help="load worker configuration from PATH",
    )
    return parser


def main(argv=None) -> int:
    """Run the safe command-line entry point."""
    base_dir = Path(__file__).resolve().parent.parent
    arguments = _argument_parser(base_dir).parse_args(argv)
    config = load_config(os.environ, arguments.env_file, base_dir)
    worker = _build_worker(config)

    if arguments.once:
        result = worker.run_once(datetime.now(timezone.utc))
        if result == "configuration_missing":
            print("worker configuration missing; no requests sent")
        return 0

    stop_event = threading.Event()

    def request_stop(signum, frame):
        del signum, frame
        stop_event.set()

    previous_handlers = {}
    for signal_number in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signal_number] = signal.signal(signal_number, request_stop)
    try:
        worker.run_forever(stop_event)
    finally:
        for signal_number, previous_handler in previous_handlers.items():
            signal.signal(signal_number, previous_handler)
    return 0
