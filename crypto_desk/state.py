from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable
import unicodedata

from crypto_desk.models import NewsItem


STATE_VERSION = 1
TERMINAL_STAGES = {
    "baseline",
    "ignored",
    "review_downgraded",
    "analysis_failed",
    "delivered",
    "delivery_failed",
    "delivery_unknown",
}
_RECORD_FIELDS = {
    "stage",
    "item",
    "source_ids",
    "first_seen_at",
    "updated_at",
    "primary_analysis",
    "review_analysis",
    "primary_attempts",
    "review_attempts",
    "next_attempt_at",
    "review_failed",
    "delivery_started_at",
}
_ITEM_FIELDS = {"source_id", "source", "title", "body", "url", "published_at"}
_ANALYSIS_FIELDS = {
    "direction",
    "short_term",
    "long_term",
    "level",
    "confidence",
    "coins",
    "category",
    "priced_in",
    "why",
    "reverse",
}
_LOGGER = logging.getLogger(__name__)


class StateWriteError(RuntimeError):
    pass


@dataclass
class WorkerState:
    version: int
    baseline_initialized: bool
    items: dict[str, dict[str, Any]]


def news_fingerprint(item: NewsItem) -> str:
    normalized = unicodedata.normalize("NFKC", item.title)
    normalized = "".join(
        character.lower()
        for character in normalized
        if not character.isspace()
        and not unicodedata.category(character).startswith("P")
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _parse_utc(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO datetime")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _serialized_item(item: NewsItem) -> dict[str, Any]:
    return {
        "source_id": item.source_id,
        "source": item.source,
        "title": item.title,
        "body": item.body,
        "url": item.url,
        "published_at": _utc_iso(item.published_at),
    }


def _validate_state(state: WorkerState) -> None:
    if state.version != STATE_VERSION:
        raise ValueError("unsupported state version")
    if not isinstance(state.baseline_initialized, bool) or not isinstance(
        state.items, dict
    ):
        raise ValueError("invalid worker state")

    timestamp_fields = {
        "first_seen_at",
        "updated_at",
        "next_attempt_at",
        "delivery_started_at",
    }
    for fingerprint, record in state.items.items():
        if not isinstance(fingerprint, str) or not isinstance(record, dict):
            raise ValueError("invalid state record")
        if not set(record).issubset(_RECORD_FIELDS):
            raise ValueError("invalid state record fields")
        if not isinstance(record.get("stage"), str):
            raise ValueError("invalid state stage")

        item = record.get("item")
        if item is not None:
            if not isinstance(item, dict) or set(item) != _ITEM_FIELDS:
                raise ValueError("invalid serialized item")
            _parse_utc(item["published_at"])

        source_ids = record.get("source_ids")
        if source_ids is not None and (
            not isinstance(source_ids, list)
            or any(not isinstance(source_id, str) for source_id in source_ids)
            or source_ids != sorted(set(source_ids))
        ):
            raise ValueError("invalid source ids")

        for field in timestamp_fields:
            value = record.get(field)
            if value is not None:
                _parse_utc(value)

        for field in ("primary_analysis", "review_analysis"):
            analysis = record.get(field)
            if analysis is not None and (
                not isinstance(analysis, dict)
                or not set(analysis).issubset(_ANALYSIS_FIELDS)
            ):
                raise ValueError("invalid serialized analysis")


class StateStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> WorkerState:
        if not self.path.exists():
            return self._empty_state()

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or set(payload) != {
                "version",
                "baseline_initialized",
                "items",
            }:
                raise ValueError("invalid state document")
            state = WorkerState(
                version=payload["version"],
                baseline_initialized=payload["baseline_initialized"],
                items=payload["items"],
            )
            _validate_state(state)
        except (OSError, UnicodeError, json.JSONDecodeError, KeyError, ValueError, TypeError):
            self._quarantine_corrupt_state()
            return self._empty_state()

        if self.recover_uncertain_deliveries(state):
            self.save(state)
        return state

    def save(self, state: WorkerState) -> None:
        temporary_path: Path | None = None
        try:
            _validate_state(state)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(
                    {
                        "version": state.version,
                        "baseline_initialized": state.baseline_initialized,
                        "items": state.items,
                    },
                    temporary,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, self.path)
        except (OSError, TypeError, ValueError):
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise StateWriteError("state persistence failed") from None

    def baseline(
        self, state: WorkerState, items: Iterable[NewsItem], now: datetime
    ) -> None:
        if state.baseline_initialized:
            return
        timestamp = _utc_iso(now)
        for item in items:
            fingerprint = news_fingerprint(item)
            if fingerprint in state.items:
                self._merge_source_id(state.items[fingerprint], item.source_id, timestamp)
                continue
            state.items[fingerprint] = {
                "stage": "baseline",
                "item": _serialized_item(item),
                "source_ids": [item.source_id],
                "first_seen_at": timestamp,
                "updated_at": timestamp,
            }
        state.baseline_initialized = True

    def register_new(
        self, state: WorkerState, items: Iterable[NewsItem], now: datetime
    ) -> int:
        timestamp = _utc_iso(now)
        created = 0
        for item in items:
            fingerprint = news_fingerprint(item)
            if fingerprint in state.items:
                self._merge_source_id(state.items[fingerprint], item.source_id, timestamp)
                continue
            state.items[fingerprint] = {
                "stage": "pending_primary",
                "item": _serialized_item(item),
                "source_ids": [item.source_id],
                "first_seen_at": timestamp,
                "updated_at": timestamp,
                "primary_analysis": None,
                "review_analysis": None,
                "primary_attempts": 0,
                "review_attempts": 0,
                "next_attempt_at": None,
                "review_failed": False,
                "delivery_started_at": None,
            }
            created += 1
        return created

    def prune(self, state: WorkerState, now: datetime) -> None:
        cutoff = now.astimezone(timezone.utc) - timedelta(days=7)
        terminal = []
        for fingerprint, record in list(state.items.items()):
            if record.get("stage") not in TERMINAL_STAGES:
                continue
            try:
                updated_at = _parse_utc(record.get("updated_at"))
            except (TypeError, ValueError):
                updated_at = datetime.min.replace(tzinfo=timezone.utc)
            if updated_at < cutoff:
                del state.items[fingerprint]
            else:
                terminal.append((updated_at, fingerprint))

        terminal.sort(reverse=True)
        for _, fingerprint in terminal[2000:]:
            del state.items[fingerprint]

    def recover_uncertain_deliveries(self, state: WorkerState) -> int:
        recovered = 0
        for record in state.items.values():
            if record.get("stage") == "delivery_started":
                record["stage"] = "delivery_unknown"
                recovered += 1
        return recovered

    @staticmethod
    def _empty_state() -> WorkerState:
        return WorkerState(
            version=STATE_VERSION,
            baseline_initialized=False,
            items={},
        )

    @staticmethod
    def _merge_source_id(
        record: dict[str, Any], source_id: str, timestamp: str
    ) -> None:
        source_ids = set(record.get("source_ids", []))
        if source_id not in source_ids:
            source_ids.add(source_id)
            record["source_ids"] = sorted(source_ids)
            record["updated_at"] = timestamp

    def _quarantine_corrupt_state(self) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        quarantine = self.path.with_name(f"{self.path.name}.corrupt-{timestamp}")
        os.replace(self.path, quarantine)
        _LOGGER.warning("state_corrupt_recovered %s", self.path.name)
