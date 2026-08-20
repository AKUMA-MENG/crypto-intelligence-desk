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
MAX_SOURCE_ID_LENGTH = 512
MAX_SOURCE_NAME_LENGTH = 128
MAX_TITLE_LENGTH = 1000
MAX_BODY_LENGTH = 10000
MAX_URL_LENGTH = 4096
MAX_TIMESTAMP_LENGTH = 64
MAX_CATEGORY_LENGTH = 64
MAX_WHY_LENGTH = 4000
MAX_REVERSE_LENGTH = 2000
MAX_SOURCE_IDS = 8
MAX_COINS = 5
TERMINAL_STAGES = {
    "baseline",
    "ignored",
    "review_downgraded",
    "analysis_failed",
    "delivered",
    "delivery_failed",
    "delivery_unknown",
}
ALLOWED_STAGES = TERMINAL_STAGES | {
    "pending_primary",
    "primary_retry",
    "pending_review",
    "review_retry",
    "notification_pending",
    "delivery_started",
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
_BASELINE_RECORD_FIELDS = {
    "stage",
    "item",
    "source_ids",
    "first_seen_at",
    "updated_at",
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
_DIRECTIONS = {"利好", "利空", "中性"}
_HORIZONS = {"正面", "负面", "中性"}
_CATEGORIES = {
    "监管",
    "ETF",
    "宏观",
    "安全",
    "上币",
    "解锁",
    "机构",
    "技术",
    "生态",
    "其他",
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
    if not _is_bounded_string(value, MAX_TIMESTAMP_LENGTH):
        raise ValueError("timestamp must be an ISO datetime")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    if parsed.utcoffset() != timedelta(0):
        raise ValueError("timestamp must use UTC")
    return parsed


def _is_bounded_string(value: Any, limit: int, *, allow_empty: bool = False) -> bool:
    return (
        type(value) is str
        and (allow_empty or bool(value))
        and len(value) <= limit
    )


def _serialized_item(item: NewsItem) -> dict[str, Any]:
    return {
        "source_id": item.source_id,
        "source": item.source,
        "title": item.title,
        "body": item.body,
        "url": item.url,
        "published_at": _utc_iso(item.published_at),
    }


def _validate_item(item: Any) -> None:
    if type(item) is not dict or set(item) != _ITEM_FIELDS:
        raise ValueError("invalid serialized item")
    string_fields = (
        ("source_id", MAX_SOURCE_ID_LENGTH, False),
        ("source", MAX_SOURCE_NAME_LENGTH, False),
        ("title", MAX_TITLE_LENGTH, False),
        ("body", MAX_BODY_LENGTH, True),
        ("url", MAX_URL_LENGTH, False),
    )
    for field, limit, allow_empty in string_fields:
        if not _is_bounded_string(item[field], limit, allow_empty=allow_empty):
            raise ValueError("invalid serialized item field")
    _parse_utc(item["published_at"])


def _validate_source_ids(source_ids: Any, item: dict[str, Any]) -> None:
    if type(source_ids) is not list or not 1 <= len(source_ids) <= MAX_SOURCE_IDS:
        raise ValueError("invalid source ids")
    if any(
        not _is_bounded_string(source_id, MAX_SOURCE_ID_LENGTH)
        for source_id in source_ids
    ):
        raise ValueError("invalid source ids")
    if source_ids != sorted(set(source_ids)):
        raise ValueError("invalid source ids")
    if item["source_id"] not in source_ids:
        raise ValueError("invalid source ids")


def _validate_analysis(analysis: Any) -> None:
    if type(analysis) is not dict or set(analysis) != _ANALYSIS_FIELDS:
        raise ValueError("invalid serialized analysis")
    if analysis["direction"] not in _DIRECTIONS:
        raise ValueError("invalid analysis direction")
    if analysis["short_term"] not in _HORIZONS:
        raise ValueError("invalid short-term analysis")
    if analysis["long_term"] not in _HORIZONS:
        raise ValueError("invalid long-term analysis")
    if type(analysis["level"]) is not int or not 1 <= analysis["level"] <= 5:
        raise ValueError("invalid analysis level")
    if (
        type(analysis["confidence"]) is not int
        or not 0 <= analysis["confidence"] <= 100
    ):
        raise ValueError("invalid analysis confidence")

    coins = analysis["coins"]
    if type(coins) is not list or len(coins) > MAX_COINS:
        raise ValueError("invalid analysis coins")
    if any(
        not _is_bounded_string(coin, 8)
        or not coin.isascii()
        or not coin.isalnum()
        or coin != coin.upper()
        for coin in coins
    ):
        raise ValueError("invalid analysis coins")

    category = analysis["category"]
    if (
        not _is_bounded_string(category, MAX_CATEGORY_LENGTH)
        or category not in _CATEGORIES
    ):
        raise ValueError("invalid analysis category")
    if type(analysis["priced_in"]) is not bool:
        raise ValueError("invalid priced-in analysis")
    if not _is_bounded_string(analysis["why"], MAX_WHY_LENGTH):
        raise ValueError("invalid analysis reason")
    if not _is_bounded_string(analysis["reverse"], MAX_REVERSE_LENGTH):
        raise ValueError("invalid analysis reversal")


def _validate_record(record: Any) -> None:
    if type(record) is not dict:
        raise ValueError("invalid state record")
    stage = record.get("stage")
    if type(stage) is not str or stage not in ALLOWED_STAGES:
        raise ValueError("invalid state stage")

    expected_fields = (
        _BASELINE_RECORD_FIELDS if stage == "baseline" else _RECORD_FIELDS
    )
    if set(record) != expected_fields:
        raise ValueError("invalid state record fields")

    _validate_item(record["item"])
    _validate_source_ids(record["source_ids"], record["item"])
    _parse_utc(record["first_seen_at"])
    _parse_utc(record["updated_at"])

    if stage == "baseline":
        return

    for field in ("primary_attempts", "review_attempts"):
        attempts = record[field]
        if type(attempts) is not int or not 0 <= attempts <= 4:
            raise ValueError("invalid attempt count")
    if type(record["review_failed"]) is not bool:
        raise ValueError("invalid review failure marker")
    for field in ("next_attempt_at", "delivery_started_at"):
        if record[field] is not None:
            _parse_utc(record[field])
    for field in ("primary_analysis", "review_analysis"):
        if record[field] is not None:
            _validate_analysis(record[field])


def _validate_state(state: WorkerState) -> None:
    if type(state.version) is not int or state.version != STATE_VERSION:
        raise ValueError("unsupported state version")
    if type(state.baseline_initialized) is not bool or type(state.items) is not dict:
        raise ValueError("invalid worker state")
    for fingerprint, record in state.items.items():
        if type(fingerprint) is not str:
            raise ValueError("invalid state record")
        _validate_record(record)


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
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            KeyError,
            ValueError,
            TypeError,
            RecursionError,
        ):
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
            temporary_path = None
        except (OSError, UnicodeError, TypeError, ValueError, RecursionError):
            raise StateWriteError("state persistence failed") from None
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

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
        cutoff = _parse_utc(_utc_iso(now)) - timedelta(days=7)
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
        if source_id not in source_ids and len(source_ids) < MAX_SOURCE_IDS:
            if not _is_bounded_string(source_id, MAX_SOURCE_ID_LENGTH):
                raise ValueError("invalid source id")
            source_ids.add(source_id)
            record["source_ids"] = sorted(source_ids)
            record["updated_at"] = timestamp

    def _quarantine_corrupt_state(self) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        quarantine = self.path.with_name(f"{self.path.name}.corrupt-{timestamp}")
        os.replace(self.path, quarantine)
        _LOGGER.warning("state_corrupt_recovered %s", self.path.name)
