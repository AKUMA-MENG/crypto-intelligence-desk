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


STATE_VERSION = 2
MAX_SOURCE_ID_LENGTH = 512
MAX_BASELINED_SOURCES = 32
MAX_SOURCE_KEY_LENGTH = 64
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
    baselined_sources: list[str]
    items: dict[str, dict[str, Any]]


def news_fingerprint(item: NewsItem) -> str:
    return _title_fingerprint(item.title)


def _title_fingerprint(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title)
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
    if (
        type(state.baseline_initialized) is not bool
        or type(state.baselined_sources) is not list
        or type(state.items) is not dict
    ):
        raise ValueError("invalid worker state")
    if (
        len(state.baselined_sources) > MAX_BASELINED_SOURCES
        or state.baselined_sources != sorted(set(state.baselined_sources))
        or any(not _valid_source_key(key) for key in state.baselined_sources)
    ):
        raise ValueError("invalid baselined sources")
    seen_source_ids: set[str] = set()
    for fingerprint, record in state.items.items():
        _validate_record(record)
        if (
            type(fingerprint) is not str
            or len(fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in fingerprint)
            or fingerprint != _title_fingerprint(record["item"]["title"])
        ):
            raise ValueError("invalid state fingerprint")
        record_source_ids = set(record["source_ids"])
        if seen_source_ids.intersection(record_source_ids):
            raise ValueError("duplicate source id across state records")
        seen_source_ids.update(record_source_ids)


def _valid_source_key(value: Any) -> bool:
    return (
        _is_bounded_string(value, MAX_SOURCE_KEY_LENGTH)
        and value.isascii()
        and all(character.isalnum() or character in "-_" for character in value)
    )


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
                "baselined_sources",
                "items",
            }:
                raise ValueError("invalid state document")
            state = WorkerState(
                version=payload["version"],
                baseline_initialized=payload["baseline_initialized"],
                baselined_sources=payload["baselined_sources"],
                items=payload["items"],
            )
            _validate_state(state)
        except (
            OSError,
            UnicodeError,
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
                        "baselined_sources": state.baselined_sources,
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
        self._baseline_items(state, items, now)
        state.baseline_initialized = True

    def baseline_source(
        self,
        state: WorkerState,
        source_key: str,
        items: Iterable[NewsItem],
        now: datetime,
    ) -> None:
        if not _valid_source_key(source_key):
            raise ValueError("invalid source key")
        if source_key in state.baselined_sources:
            return
        self._baseline_items(state, items, now)
        state.baselined_sources = sorted(state.baselined_sources + [source_key])

    def _baseline_items(
        self, state: WorkerState, items: Iterable[NewsItem], now: datetime
    ) -> None:
        timestamp = _utc_iso(now)
        source_index = self._source_index(state)
        for item in items:
            if item.source_id in source_index:
                continue
            fingerprint = news_fingerprint(item)
            if fingerprint in state.items:
                self._merge_source_id(state.items[fingerprint], item.source_id, timestamp)
                source_index[item.source_id] = state.items[fingerprint]
                continue
            state.items[fingerprint] = {
                "stage": "baseline",
                "item": _serialized_item(item),
                "source_ids": [item.source_id],
                "first_seen_at": timestamp,
                "updated_at": timestamp,
            }
            source_index[item.source_id] = state.items[fingerprint]

    def register_new(
        self, state: WorkerState, items: Iterable[NewsItem], now: datetime
    ) -> int:
        timestamp = _utc_iso(now)
        created = 0
        source_index = self._source_index(state)
        for item in items:
            if item.source_id in source_index:
                continue
            fingerprint = news_fingerprint(item)
            if fingerprint in state.items:
                self._merge_source_id(state.items[fingerprint], item.source_id, timestamp)
                source_index[item.source_id] = state.items[fingerprint]
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
            source_index[item.source_id] = state.items[fingerprint]
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
            baselined_sources=[],
            items={},
        )

    @staticmethod
    def _source_index(state: WorkerState) -> dict[str, dict[str, Any]]:
        return {
            source_id: record
            for record in state.items.values()
            for source_id in record.get("source_ids", ())
        }

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
