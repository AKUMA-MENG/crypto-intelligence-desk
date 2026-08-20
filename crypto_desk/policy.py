"""Durable double-model policy for major-news Bark notifications."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging

from crypto_desk.gemini import GeminiPermanentError, GeminiRetryableError
from crypto_desk.models import Analysis, NewsItem
from crypto_desk.state import StateWriteError


RETRY_DELAYS_SECONDS = (30, 120, 300)
MAJOR_LEVEL = 4

_DUE_STAGES = {
    "pending_primary",
    "primary_retry",
    "pending_review",
    "review_retry",
    "notification_pending",
}
_LOGGER = logging.getLogger(__name__)


def _utc(value):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc)


def _iso(value):
    return _utc(value).isoformat()


def _deserialize_item(payload):
    published_at = datetime.fromisoformat(
        payload["published_at"].replace("Z", "+00:00")
    )
    return NewsItem(
        source_id=payload["source_id"],
        source=payload["source"],
        title=payload["title"],
        body=payload["body"],
        url=payload["url"],
        published_at=published_at,
    )


def _serialize_analysis(analysis):
    return {
        "direction": analysis.direction,
        "short_term": analysis.short_term,
        "long_term": analysis.long_term,
        "level": analysis.level,
        "confidence": analysis.confidence,
        "coins": list(analysis.coins),
        "category": analysis.category,
        "priced_in": analysis.priced_in,
        "why": analysis.why,
        "reverse": analysis.reverse,
    }


def _deserialize_analysis(payload):
    return Analysis(
        direction=payload["direction"],
        short_term=payload["short_term"],
        long_term=payload["long_term"],
        level=payload["level"],
        confidence=payload["confidence"],
        coins=tuple(payload["coins"]),
        category=payload["category"],
        priced_in=payload["priced_in"],
        why=payload["why"],
        reverse=payload["reverse"],
    )


def format_notification(
    item, final_analysis, primary_analysis, review_failed, now
):
    """Return the approved Bark title and Chinese notification body."""
    del now
    selected = primary_analysis if review_failed else final_analysis
    if review_failed:
        title = "币圈重大情报 · L%s · 初判回退" % selected.level
    else:
        title = "币圈重大情报 · L%s · %s" % (
            selected.level,
            selected.direction,
        )

    fields = [
        item.title,
        "",
        "来源：%s" % item.source,
        "分类：%s" % selected.category,
        "影响资产：%s" % "、".join(selected.coins),
        "短期：%s" % selected.short_term,
        "长期：%s" % selected.long_term,
        "置信度：%s%%" % selected.confidence,
    ]
    if review_failed:
        fields.extend(("", "复核未完成，当前为初判结果。"))
    fields.extend((
        "",
        "核心判断：",
        selected.why,
        "",
        "解读失效条件：",
        selected.reverse,
        "",
        "原文：",
        item.url,
    ))
    return title, "\n".join(fields)


class NewsProcessor:
    """Advance due records through analysis, review, and one-shot delivery."""

    def __init__(
        self,
        gemini,
        bark,
        store,
        primary_model,
        review_model,
        logger=None,
    ):
        self.gemini = gemini
        self.bark = bark
        self.store = store
        self.primary_model = primary_model
        self.review_model = review_model
        self.logger = logger or _LOGGER

    def process_due(self, state, now):
        """Advance each currently due record once, isolating known failures."""
        instant = _utc(now)
        advanced = 0
        for record in list(state.items.values()):
            if record.get("stage") not in _DUE_STAGES:
                continue
            if not self._is_due(record, instant):
                continue
            self._advance_record(state, record, instant)
            advanced += 1
        return advanced

    @staticmethod
    def _is_due(record, now):
        if record["stage"] not in {"primary_retry", "review_retry"}:
            return True
        next_attempt_at = record["next_attempt_at"]
        if next_attempt_at is None:
            return True
        due_at = datetime.fromisoformat(next_attempt_at.replace("Z", "+00:00"))
        return due_at <= now

    def _advance_record(self, state, record, now):
        item = _deserialize_item(record["item"])
        while True:
            stage = record["stage"]
            if stage in {"pending_primary", "primary_retry"}:
                if not self._primary(state, record, item, now):
                    return
                continue
            if stage in {"pending_review", "review_retry"}:
                if not self._review(state, record, item, now):
                    return
                continue
            if stage == "notification_pending":
                self._deliver(state, record, item, now)
            return

    def _primary(self, state, record, item, now):
        try:
            result = self.gemini.analyze(
                item,
                self.primary_model,
                review=False,
            )
        except GeminiRetryableError:
            return self._retry_or_fail(state, record, "primary", now)
        except GeminiPermanentError:
            self._mark(record, "analysis_failed", now)
            record["next_attempt_at"] = None
            self._save(state)
            self.logger.error("analysis_failed stage=primary")
            return False

        record["primary_analysis"] = _serialize_analysis(result)
        record["next_attempt_at"] = None
        if result.level < MAJOR_LEVEL:
            self._mark(record, "ignored", now)
            self._save(state)
            return False

        self._mark(record, "pending_review", now)
        self._save(state)
        return True

    def _review(self, state, record, item, now):
        try:
            result = self.gemini.analyze(
                item,
                self.review_model,
                review=True,
            )
        except GeminiRetryableError:
            return self._retry_or_fail(state, record, "review", now)
        except GeminiPermanentError:
            return self._review_fallback(state, record, now)

        record["review_analysis"] = _serialize_analysis(result)
        record["next_attempt_at"] = None
        if result.level < MAJOR_LEVEL:
            self._mark(record, "review_downgraded", now)
            self._save(state)
            return False

        self._mark(record, "notification_pending", now)
        self._save(state)
        return True

    def _retry_or_fail(self, state, record, analysis_stage, now):
        attempts_field = "%s_attempts" % analysis_stage
        attempts = record[attempts_field] + 1
        record[attempts_field] = attempts
        if attempts <= len(RETRY_DELAYS_SECONDS):
            delay = RETRY_DELAYS_SECONDS[attempts - 1]
            record["next_attempt_at"] = _iso(now + timedelta(seconds=delay))
            self._mark(record, "%s_retry" % analysis_stage, now)
            self._save(state)
            self.logger.warning("analysis_retry stage=%s", analysis_stage)
            return False

        if analysis_stage == "review":
            return self._review_fallback(state, record, now)

        record["next_attempt_at"] = None
        self._mark(record, "analysis_failed", now)
        self._save(state)
        self.logger.error("analysis_failed stage=primary")
        return False

    def _review_fallback(self, state, record, now):
        record["review_failed"] = True
        record["review_analysis"] = None
        record["next_attempt_at"] = None
        self._mark(record, "notification_pending", now)
        self._save(state)
        self.logger.error("analysis_failed stage=review")
        self.logger.warning("review_fallback")
        return True

    def _deliver(self, state, record, item, now):
        primary = _deserialize_analysis(record["primary_analysis"])
        if record["review_failed"]:
            final = primary
        else:
            final = _deserialize_analysis(record["review_analysis"])
        title, text = format_notification(
            item,
            final,
            primary,
            record["review_failed"],
            now,
        )

        self._mark(record, "delivery_started", now)
        record["delivery_started_at"] = _iso(now)
        self._save(state)

        delivered = self.bark.send(text, title, now)
        self._mark(
            record,
            "delivered" if delivered else "delivery_failed",
            now,
        )
        self._save(state)
        if delivered:
            self.logger.info("bark_delivered")
        else:
            self.logger.error("bark_failed")

    @staticmethod
    def _mark(record, stage, now):
        record["stage"] = stage
        record["updated_at"] = _iso(now)

    def _save(self, state):
        try:
            self.store.save(state)
        except StateWriteError:
            self.logger.error("state_persistence_failed")
            raise
