# Bark Background Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an independent Python worker that monitors crypto news continuously, uses Gemini `gemini-3.1-flash-lite` plus `gemini-3.5-flash-lite` to confirm L4–L5 events, and sends each confirmed event to Bark at most once without exposing secrets or depending on an open browser.

**Architecture:** Add a focused `crypto_desk` Python package beside the existing static page and proxy. The worker owns source adapters, durable state, Gemini analysis, notification policy, and Bark delivery; it opens no HTTP port and runs independently from `proxy.py`. All external I/O is behind injectable transports, and every production behavior is introduced with a failing standard-library `unittest` first.

**Tech Stack:** Python 3.8+ standard library (`dataclasses`, `urllib`, `xml.etree.ElementTree`, `concurrent.futures`, `unittest`), JSON state with atomic replacement, systemd for the eventual production worker, existing HTML/JavaScript monitor unchanged.

## Global Constraints

- Preserve Python 3.8 compatibility and add no third-party Python dependency.
- Preserve the existing public web monitor and the `/ping` response schema; the returned version must track `VERSION` instead of remaining hard-coded.
- Do not add a public or private HTTP endpoint for Bark delivery.
- Read the device key only from `BARK_PUSH_KEY`; never return or log it.
- Read the Gemini credential only from `GEMINI_API_KEY`; send it only in the `x-goog-api-key` header.
- Use `gemini-3.1-flash-lite` as the default primary model and `gemini-3.5-flash-lite` as the default review model.
- Review only primary L4–L5 results. Send only reviewed L4–L5 results, except that exhausted review failures fall back to the primary L4–L5 result once.
- Suppress the cold-start backlog and persist deduplication across restarts.
- Send each Bark event at most once; persist `delivery_started` before the network call so a crash cannot cause an automatic duplicate.
- Bark uses POST, `application/x-www-form-urlencoded; charset=utf-8`, a 15-second timeout, and checks both HTTP success and JSON `code == 200`.
- Gemini analysis gets one initial request plus at most three durable retries delayed by 30, 120, and 300 seconds.
- Missing Gemini or Bark configuration leaves the worker quietly idle without advancing the baseline.
- Tests must inject fake environment mappings, clocks, files, and transports. They must not read real user environment values or access real news, Gemini, or Bark endpoints.
- Do not deploy, restart services, modify Nginx, read real keys, call real Gemini, or send a real Bark during implementation.
- Preserve the user's existing uncommitted `.gitignore` and `.env.example` work; Task 1 deliberately incorporates those exact files into the feature.

---

## Planned File Map

### New runtime package

- `crypto_desk/__init__.py`: package marker and public version-neutral exports only.
- `crypto_desk/models.py`: immutable `NewsItem`, `Analysis`, `HttpRequest`, and `HttpResponse` value objects.
- `crypto_desk/config.py`: non-overwriting `.env` loader and validated `WorkerConfig` construction.
- `crypto_desk/transport.py`: secret-agnostic `urllib` transport with bounded response reads and sanitized exceptions.
- `crypto_desk/notifications/__init__.py`: notification package marker.
- `crypto_desk/notifications/bark.py`: Bark URL encoding, Shanghai-time body formatting, POST, and response validation.
- `crypto_desk/gemini.py`: Gemini request creation, response parsing, and normalized analysis results.
- `crypto_desk/state.py`: schema version 1 state, atomic persistence, corruption recovery, deduplication, and pruning.
- `crypto_desk/source_http.py`: fixed-source HTTP headers, gzip handling, response limits, and TechFlow WAF retry.
- `crypto_desk/sources.py`: six default source adapters and concurrent source aggregation.
- `crypto_desk/policy.py`: durable primary/review retry state machine and at-most-once Bark decision.
- `crypto_desk/worker.py`: cold-start baseline, single-flight poll cycle, idle configuration behavior, and continuous loop.
- `background_worker.py`: minimal CLI and signal-aware entrypoint.

### New tests and fixtures

- `tests/__init__.py`: test package marker.
- `tests/helpers.py`: recording transport, fixed clock, and fixture helpers that reject unexpected network calls.
- `tests/test_config.py`: `.env`, defaults, validation, and configuration-idle tests.
- `tests/test_transport.py`: sanitized transport error and response-limit tests.
- `tests/test_bark.py`: Bark contract and secret-leak tests.
- `tests/test_gemini.py`: Gemini request and strict analysis parsing tests.
- `tests/test_state.py`: atomic state, corruption, pruning, and restart dedupe tests.
- `tests/test_sources.py`: source parsing, source isolation, and WAF retry tests.
- `tests/fixtures/*.json`: minimal JSON payloads for Binance and TechFlow.
- `tests/fixtures/*.xml`: minimal RSS payloads for PANews, Odaily, Wu Blockchain, and ChainCatcher.
- `tests/test_policy.py`: L4–L5 review, downgrade, fallback, retry, and at-most-once delivery tests.
- `tests/test_worker.py`: cold start, resumed processing, missing configuration, and loop isolation tests.

### Existing files to update

- `.gitignore`: retain `.env`; add only local worker state artifacts if the selected default path needs it.
- `.env.example`: expand the existing empty Bark entry with empty Gemini key and non-secret worker defaults.
- `README.md`: describe the optional background worker, configuration, start/stop behavior, and security boundary.
- `使用说明.md`: add Chinese setup, event policy, cold-start behavior, and troubleshooting.
- `SECURITY.md`: document server-side Gemini/Bark secrets and the absence of a notification endpoint.
- `CHANGELOG.md`: add the new background-notification feature under version 1.1.0.
- `VERSION`: change `1.0.1` to `1.1.0` only after the implementation and regression suite pass.
- `requirements.txt`: retain the no-third-party-dependency statement.
- `tools/check-release.ps1`: require the new runtime files, exclude `.env`, and scan new source/test files without printing suspected secrets.
- `deploy/crypto-intelligence-desk-worker.service`: production systemd template; creation does not install or start it.

---

### Task 1: Configuration, Models, and Sanitized HTTP Transport

**Files:**
- Create: `crypto_desk/__init__.py`
- Create: `crypto_desk/models.py`
- Create: `crypto_desk/config.py`
- Create: `crypto_desk/transport.py`
- Create: `tests/__init__.py`
- Create: `tests/helpers.py`
- Create: `tests/test_config.py`
- Create: `tests/test_transport.py`
- Modify: `.gitignore:1`
- Modify: `.env.example:1`

**Interfaces:**
- Produces: `NewsItem`, `Analysis`, `HttpRequest`, and `HttpResponse` frozen dataclasses in `crypto_desk.models`.
- Produces: `ConfigError`, `WorkerConfig.is_configured`, `load_env_file(path, environ)`, and `load_config(environ, env_file, base_dir)` in `crypto_desk.config`.
- Produces: `TransportError` and `UrllibTransport.send(request) -> HttpResponse` in `crypto_desk.transport`.
- Consumes: no feature code from later tasks.

- [ ] **Step 1: Write failing configuration and value-object tests**

Create `tests/test_config.py` with explicit fake environments so the test never reads `os.environ`:

```python
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
```

Create `tests/test_transport.py` to demand a bounded response and sanitized failure:

```python
import io
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from crypto_desk.models import HttpRequest
from crypto_desk.transport import TransportError, UrllibTransport


class TransportTests(unittest.TestCase):
    @patch("crypto_desk.transport.urllib.request.urlopen")
    def test_network_error_is_sanitized(self, urlopen):
        urlopen.side_effect = OSError("failed https://secret.example/device-key")
        request = HttpRequest("GET", "https://example.com/", {}, None, 3)
        with self.assertRaisesRegex(TransportError, "upstream request failed") as caught:
            UrllibTransport().send(request)
        self.assertNotIn("device-key", str(caught.exception))

    @patch("crypto_desk.transport.urllib.request.urlopen")
    def test_response_limit_is_enforced(self, urlopen):
        response = MagicMock()
        response.status = 200
        response.headers = {"Content-Type": "application/json"}
        response.read.return_value = b"x" * 9
        response.__enter__.return_value = response
        urlopen.return_value = response
        request = HttpRequest("GET", "https://example.com/", {}, None, 3)
        with self.assertRaisesRegex(TransportError, "response too large"):
            UrllibTransport(max_response_bytes=8).send(request)

    @patch("crypto_desk.transport.urllib.request.urlopen")
    def test_http_error_is_returned_as_bounded_response(self, urlopen):
        urlopen.side_effect = HTTPError(
            "https://example.com/",
            429,
            "rate limited",
            {"Content-Type": "application/json"},
            io.BytesIO(b'{"error":"rate"}'),
        )
        response = UrllibTransport(max_response_bytes=64).send(
            HttpRequest("GET", "https://example.com/", {}, None, 3)
        )
        self.assertEqual(response.status, 429)
        self.assertEqual(response.body, b'{"error":"rate"}')


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
python3 -m unittest -v tests.test_config tests.test_transport
```

Expected: import errors for `crypto_desk.config`, `crypto_desk.models`, and `crypto_desk.transport`, proving the new foundation is absent.

- [ ] **Step 3: Implement immutable models, non-overwriting config, and sanitized transport**

Create `crypto_desk/models.py` with these exact public fields:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Optional, Tuple


@dataclass(frozen=True)
class NewsItem:
    source_id: str
    source: str
    title: str
    body: str
    url: str
    published_at: datetime


@dataclass(frozen=True)
class Analysis:
    direction: str
    short_term: str
    long_term: str
    level: int
    confidence: int
    coins: Tuple[str, ...]
    category: str
    priced_in: bool
    why: str
    reverse: str


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: Mapping[str, str]
    body: Optional[bytes]
    timeout_seconds: int


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
```

Create `tests/helpers.py` with the recording transport and deterministic news factory used by later tasks:

```python
from datetime import datetime, timezone

from crypto_desk.models import NewsItem


class RecordingTransport:
    def __init__(self, responses, error=None):
        self.responses = list(responses)
        self.error = error
        self.requests = []

    def send(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        if not self.responses:
            raise AssertionError("unexpected HTTP request")
        return self.responses.pop(0)


def sample_news(source="PANews", title="重大新闻"):
    return NewsItem(
        source_id="sample-1",
        source=source,
        title=title,
        body="用于测试的新闻正文。",
        url="https://news.example/item/1",
        published_at=datetime(2026, 8, 20, 5, 59, tzinfo=timezone.utc),
    )
```

Implement `WorkerConfig` as a frozen dataclass with `gemini_api_key`, `bark_push_key`, `primary_model`, `review_model`, `poll_seconds`, `state_file`, and `sources`. `load_env_file()` must parse only non-comment `NAME=value` lines, ignore names outside `[A-Za-z_][A-Za-z0-9_]*`, strip one matching quote pair, and use `environ.setdefault(name, value)`. Catch `FileNotFoundError`, `PermissionError`, and other `OSError` without logging file contents. `load_config()` must clamp `CID_WORKER_POLL_SECONDS` to 10–600, allow all eight known source keys while defaulting to the six confirmed sources, raise `ConfigError("unknown worker source")` for anything else, treat an empty `CID_WORKER_STATE_FILE` as unset, default the state path to `base_dir / ".runtime" / "worker-state.json"`, and compute `is_configured` from both secrets being non-empty.

Implement `UrllibTransport.send()` so it builds `urllib.request.Request`, returns bounded `urllib.error.HTTPError` bodies as `HttpResponse`, reads `max_response_bytes + 1`, raises `TransportError("upstream response too large")` when needed, and converts all other network exceptions to `TransportError("upstream request failed")` without including the original exception or URL. Do not catch and rewrite the transport's own size-limit error.

Update `.env.example` to this exact content while retaining empty secret values:

```env
# Gemini background analysis
GEMINI_API_KEY=
GEMINI_PRIMARY_MODEL=gemini-3.1-flash-lite
GEMINI_REVIEW_MODEL=gemini-3.5-flash-lite

# Bark iPhone notifications
BARK_PUSH_KEY=

# Background worker
CID_WORKER_POLL_SECONDS=30
CID_WORKER_STATE_FILE=
CID_WORKER_SOURCES=panews,binance,odaily,ctcn,techflow,catcher
```

Keep `.env` ignored and add `.runtime/worker-state.json.corrupt-*` only if the existing `.runtime/` rule does not already cover it. Do not stage unrelated files.

- [ ] **Step 4: Run focused tests and the existing proxy syntax check**

Run:

```bash
python3 -m unittest -v tests.test_config tests.test_transport
python3 -c "compile(open('proxy.py', encoding='utf-8').read(), 'proxy.py', 'exec')"
git diff --check
```

Expected: all focused tests pass, `proxy.py` compiles, and the diff check prints nothing.

- [ ] **Step 5: Commit the foundation**

```bash
git add .gitignore .env.example crypto_desk/__init__.py crypto_desk/models.py crypto_desk/config.py crypto_desk/transport.py tests/__init__.py tests/helpers.py tests/test_config.py tests/test_transport.py
git commit -m "feat: add worker configuration foundation"
```

### Task 2: Bark Delivery Module

**Files:**
- Create: `crypto_desk/notifications/__init__.py`
- Create: `crypto_desk/notifications/bark.py`
- Create: `tests/test_bark.py`
- Modify: `tests/helpers.py:1`

**Interfaces:**
- Consumes: `HttpRequest`, `HttpResponse`, and transport objects exposing `send(HttpRequest) -> HttpResponse`.
- Produces: `bark_url(push_key) -> str`, `format_bark_body(text, now) -> str`, and `BarkClient.send(text, title, now) -> bool`.

- [ ] **Step 1: Write failing Bark contract tests**

Create `tests/test_bark.py` with a recording transport and an in-memory logger:

```python
from datetime import datetime, timezone
import io
import logging
import unittest
from urllib.parse import parse_qs

from crypto_desk.models import HttpResponse
from crypto_desk.notifications.bark import BarkClient, bark_url, format_bark_body
from tests.helpers import RecordingTransport


class BarkTests(unittest.TestCase):
    def test_url_encodes_the_entire_push_key_path_segment(self):
        self.assertEqual(
            bark_url("key/with space"),
            "https://api.day.app/key%2Fwith%20space",
        )

    def test_post_form_and_shanghai_timestamp(self):
        transport = RecordingTransport(
            [HttpResponse(200, {"Content-Type": "application/json"}, b'{"code":200}')]
        )
        client = BarkClient("key/with space", transport)
        delivered = client.send(
            "重大新闻",
            "币圈重大情报 · L4 · 正面",
            datetime(2026, 7, 30, 4, 34, 56, tzinfo=timezone.utc),
        )
        self.assertTrue(delivered)
        request = transport.requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.timeout_seconds, 15)
        self.assertEqual(
            request.headers["Content-Type"],
            "application/x-www-form-urlencoded; charset=utf-8",
        )
        form = parse_qs(request.body.decode("utf-8"))
        self.assertEqual(form["title"], ["币圈重大情报 · L4 · 正面"])
        self.assertIn("2026-07-30 12:34:56", form["body"][0])

    def test_business_failure_returns_false_without_leaking_key(self):
        key = "secret/test key"
        transport = RecordingTransport(
            [HttpResponse(200, {}, b'{"code":400,"message":"rejected"}')]
        )
        stream = io.StringIO()
        logger = logging.getLogger("test.bark.business")
        logger.handlers = [logging.StreamHandler(stream)]
        self.assertFalse(BarkClient(key, transport, logger).send("x", "title"))
        self.assertIn("Bark delivery failed", stream.getvalue())
        self.assertNotIn(key, stream.getvalue())
        self.assertNotIn("api.day.app", stream.getvalue())

    def test_network_failure_and_missing_key_are_isolated(self):
        transport = RecordingTransport([], error=RuntimeError("secret/test key"))
        self.assertFalse(BarkClient("secret/test key", transport).send("x", "title"))
        unused = RecordingTransport([])
        self.assertFalse(BarkClient("", unused).send("x", "title"))
        self.assertEqual(unused.requests, [])


if __name__ == "__main__":
    unittest.main()
```

Add these methods to the same test class for HTTP and response-format failures:

```python
    def test_http_500_returns_false(self):
        transport = RecordingTransport([HttpResponse(500, {}, b'{"code":200}')])
        self.assertFalse(BarkClient("fake-bark-secret", transport).send("x", "title"))

    def test_non_json_success_response_returns_false(self):
        transport = RecordingTransport([HttpResponse(200, {}, b"not-json")])
        self.assertFalse(BarkClient("fake-bark-secret", transport).send("x", "title"))
```

- [ ] **Step 2: Run the Bark test and verify RED**

Run:

```bash
python3 -m unittest -v tests.test_bark
```

Expected: import error for `crypto_desk.notifications.bark`.

- [ ] **Step 3: Implement the minimal Bark client**

Implement these constants and public functions:

```python
BARK_API_BASE = "https://api.day.app"
BARK_TIMEOUT_SECONDS = 15
SHANGHAI_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def bark_url(push_key):
    return "%s/%s" % (BARK_API_BASE, quote(push_key, safe=""))


def format_bark_body(text, now=None):
    instant = now or datetime.now(timezone.utc)
    pushed_at = instant.astimezone(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S")
    return "内容：\n%s\n\n推送时间：%s" % (str(text), pushed_at)
```

`BarkClient.send()` must return `False` immediately for an empty key. Otherwise construct a UTF-8 `urlencode({"title": title, "body": format_bark_body(text, now)})` POST request, call the injected transport, parse JSON with `json.loads`, and return `True` only when `200 <= status < 300` and numeric `code == 200`. Catch every transport or JSON exception, log a fixed message without the exception object, and return `False`.

- [ ] **Step 4: Run Bark and foundation regression tests**

Run:

```bash
python3 -m unittest -v tests.test_config tests.test_transport tests.test_bark
git diff --check
```

Expected: all tests pass and no diff errors.

- [ ] **Step 5: Commit Bark delivery**

```bash
git add crypto_desk/notifications/__init__.py crypto_desk/notifications/bark.py tests/helpers.py tests/test_bark.py
git commit -m "feat: add isolated Bark delivery"
```

### Task 3: Gemini Analysis Client

**Files:**
- Create: `crypto_desk/gemini.py`
- Create: `tests/test_gemini.py`

**Interfaces:**
- Consumes: `NewsItem`, `Analysis`, and the injected HTTP transport.
- Produces: `GeminiClient.analyze(item, model, review=False) -> Analysis`.
- Produces: `GeminiRetryableError` for network, 429, 5xx, invalid JSON, and invalid model output; `GeminiPermanentError` for other 4xx responses.
- Produces: `parse_analysis(text) -> Analysis` and `analysis_prompt(item) -> str`.

- [ ] **Step 1: Write failing Gemini request and parsing tests**

Create `tests/test_gemini.py`:

```python
import json
import unittest

from crypto_desk.gemini import (
    GeminiClient,
    GeminiPermanentError,
    GeminiRetryableError,
    parse_analysis,
)
from crypto_desk.models import HttpResponse
from tests.helpers import RecordingTransport, sample_news


VALID_ANALYSIS = {
    "direction": "利空",
    "st": "负面",
    "lt": "中性",
    "level": 5,
    "conf": 92,
    "coins": ["btc", "ETH", "bad-coin"],
    "cat": "监管",
    "priced_in": False,
    "why": "监管限制会压低短期风险偏好。",
    "reverse": "政策未正式生效。",
}


def gemini_response(payload):
    text = json.dumps(payload, ensure_ascii=False)
    body = json.dumps({
        "candidates": [{"content": {"parts": [{"text": text}]}}]
    }, ensure_ascii=False).encode("utf-8")
    return HttpResponse(200, {}, body)


class GeminiTests(unittest.TestCase):
    def test_request_uses_selected_model_and_header_key(self):
        transport = RecordingTransport([gemini_response(VALID_ANALYSIS)])
        client = GeminiClient("fake-gemini-secret", transport)
        result = client.analyze(sample_news(), "gemini-3.1-flash-lite")
        request = transport.requests[0]
        self.assertIn("/models/gemini-3.1-flash-lite:generateContent", request.url)
        self.assertEqual(request.headers["x-goog-api-key"], "fake-gemini-secret")
        self.assertNotIn("fake-gemini-secret", request.url)
        self.assertEqual(result.level, 5)
        self.assertEqual(result.confidence, 92)
        self.assertEqual(result.coins, ("BTC", "ETH"))

    def test_invalid_json_and_429_are_retryable(self):
        bad_json = HttpResponse(200, {}, b'{"candidates":[{"content":{"parts":[{"text":"no json"}]}}]}')
        with self.assertRaises(GeminiRetryableError):
            GeminiClient("fake", RecordingTransport([bad_json])).analyze(
                sample_news(), "gemini-3.1-flash-lite"
            )
        with self.assertRaises(GeminiRetryableError):
            GeminiClient("fake", RecordingTransport([HttpResponse(429, {}, b"rate")])).analyze(
                sample_news(), "gemini-3.1-flash-lite"
            )

    def test_non_retryable_4xx_is_permanent(self):
        with self.assertRaises(GeminiPermanentError):
            GeminiClient("fake", RecordingTransport([HttpResponse(403, {}, b"denied")])).analyze(
                sample_news(), "gemini-3.1-flash-lite"
            )

    def test_review_call_uses_deeper_instruction_and_larger_output_budget(self):
        transport = RecordingTransport([gemini_response(VALID_ANALYSIS)])
        GeminiClient("fake", transport).analyze(
            sample_news(), "gemini-3.5-flash-lite", review=True
        )
        payload = json.loads(transport.requests[0].body.decode("utf-8"))
        prompt = payload["contents"][0]["parts"][0]["text"]
        self.assertIn("初判为重大的新闻", prompt)
        self.assertIn("重新独立判断", prompt)
        self.assertEqual(payload["generationConfig"]["maxOutputTokens"], 1000)


if __name__ == "__main__":
    unittest.main()
```

Add these normalization and rejection methods to the same class:

```python
    def test_analysis_clamps_numbers_rejects_bad_coins_and_limits_five(self):
        payload = dict(VALID_ANALYSIS)
        payload.update({
            "level": 9,
            "conf": -4,
            "coins": ["btc", "ETH", "bad-coin", "SOL", "XRP", "ADA", "DOGE"],
        })
        result = parse_analysis(json.dumps(payload, ensure_ascii=False))
        self.assertEqual(result.level, 5)
        self.assertEqual(result.confidence, 0)
        self.assertEqual(result.coins, ("BTC", "ETH", "SOL", "XRP", "ADA"))

    def test_missing_reason_fields_are_retryable_without_response_echo(self):
        payload = dict(VALID_ANALYSIS)
        payload.pop("why")
        raw = json.dumps(payload, ensure_ascii=False)
        with self.assertRaises(GeminiRetryableError) as caught:
            parse_analysis(raw)
        self.assertEqual(str(caught.exception), "invalid Gemini analysis")
        self.assertNotIn("监管限制", str(caught.exception))

    def test_missing_reverse_is_retryable(self):
        payload = dict(VALID_ANALYSIS)
        payload.pop("reverse")
        with self.assertRaisesRegex(GeminiRetryableError, "invalid Gemini analysis"):
            parse_analysis(json.dumps(payload, ensure_ascii=False))

    def test_invalid_enums_boolean_and_coin_container_are_retryable(self):
        cases = (
            ("direction", "上涨"),
            ("st", "短期"),
            ("lt", "长期"),
            ("cat", "交易建议"),
            ("priced_in", "false"),
            ("coins", "BTC"),
        )
        for field, value in cases:
            with self.subTest(field=field):
                payload = dict(VALID_ANALYSIS)
                payload[field] = value
                with self.assertRaisesRegex(
                    GeminiRetryableError, "invalid Gemini analysis"
                ):
                    parse_analysis(json.dumps(payload, ensure_ascii=False))
```

- [ ] **Step 2: Run Gemini tests and verify RED**

Run:

```bash
python3 -m unittest -v tests.test_gemini
```

Expected: import error for `crypto_desk.gemini`.

- [ ] **Step 3: Implement request building and strict output normalization**

Implement the endpoint as:

```python
url = "%s/models/%s:generateContent" % (
    GEMINI_API_BASE,
    quote(model, safe=""),
)
```

Use `x-goog-api-key` and `Content-Type: application/json; charset=utf-8` headers. The request JSON must include the existing conservative market-analysis system instruction, one user prompt containing source/time/title/body, `temperature: 0.2`, and `maxOutputTokens: 600` for primary or 1,000 when `review=True`. Review behavior must depend on the explicit flag, not on comparing model-name strings, so model overrides remain correct.

Port the existing prompt into named constants rather than reading it dynamically from `index.html`:

```python
NEWS_SYSTEM = (
    "你是专业的加密行业新闻研究员。任务是识别事实、相关方、时效性与潜在行业影响；"
    "必须客观谨慎，宁可降低评级也不夸大，不提供买卖、仓位或价格点位建议，"
    "并且只输出一个JSON对象。"
)

OUTPUT_CONTRACT = """请分析以下币圈快讯对市场的影响，严格输出JSON：
{"direction":"利好|利空|中性","st":"正面|负面|中性","lt":"正面|负面|中性","level":"1到5的整数","conf":"0到100整数","coins":["受影响币种基础代码，最多5个"],"cat":"监管|ETF|宏观|安全|上币|解锁|机构|技术|生态|其他","priced_in":"true或false","why":"2-3句具体因果分析","reverse":"一句话说明解读失效条件"}
字段说明：
- st=短期（小时/天级），lt=长期（周/月级），两者可以不同
- level：1=噪音/日常，2=轻微，3=值得关注，4=重大，5=极重大
- priced_in：旧闻、预期兑现或市场大概率已提前反应时为 true
- 绝大多数快讯是 L1-L2；L4 以上必须有充分理由
- 只做新闻影响解读，不给出任何交易操作建议"""

REVIEW_SUFFIX = (
    "\n\n这是一条初判为重大的新闻。请给出更深入的 why（4-6句，包含历史类似事件的"
    "行业影响类比），其余字段重新独立判断。"
)
```

`analysis_prompt(item, review=False)` appends `来源`, UTC ISO `时间`, `标题`, and at most 800 characters of cleaned `内容` to `OUTPUT_CONTRACT`; append `REVIEW_SUFFIX` only for review. Do not include the primary model's JSON in the review prompt, which keeps the second judgment independent.

`parse_analysis()` must remove optional Markdown fences, extract the first outer JSON object, and create `Analysis` with:

```python
level = min(5, max(1, int(round(float(payload.get("level", 1))))))
confidence = min(100, max(0, int(round(float(payload.get("conf", 50))))))
coins = tuple(
    str(coin).upper()
    for coin in payload.get("coins", [])
    if re.fullmatch(r"[A-Za-z0-9]{1,8}", str(coin))
)[:5]
```

Permit only `利好`, `利空`, or `中性` direction; `正面`, `负面`, or `中性` time-horizon values; and the ten categories listed in `OUTPUT_CONTRACT`. Require `priced_in` to be a JSON boolean, `coins` to be a JSON array, and `why`/`reverse` to be non-empty strings. Reject rather than rewrite coin strings containing punctuation, signs, spaces, or more than eight characters. Invalid/missing required analysis fields raise `GeminiRetryableError("invalid Gemini analysis")`. Error messages and logs must never include the request URL, response body, key, or original exception.

- [ ] **Step 4: Run Gemini, Bark, and foundation tests**

Run:

```bash
python3 -m unittest -v tests.test_config tests.test_transport tests.test_bark tests.test_gemini
git diff --check
```

Expected: all tests pass.

- [ ] **Step 5: Commit Gemini analysis**

```bash
git add crypto_desk/gemini.py tests/test_gemini.py tests/helpers.py
git commit -m "feat: add Gemini news analysis"
```

### Task 4: Durable State, Cold-Start Baseline, and Restart Deduplication

**Files:**
- Create: `crypto_desk/state.py`
- Create: `tests/test_state.py`

**Interfaces:**
- Consumes: `NewsItem` and serialized `Analysis` dictionaries.
- Produces: `news_fingerprint(item) -> str`.
- Produces: `WorkerState` with `version`, `baseline_initialized`, and `items`.
- Produces: `StateStore.load() -> WorkerState`, `save(state)`, `baseline(state, items, now)`, `register_new(state, items, now)`, `prune(state, now)`, and `recover_uncertain_deliveries(state)`.

- [ ] **Step 1: Write failing state tests**

Create `tests/test_state.py`:

```python
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

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


if __name__ == "__main__":
    unittest.main()
```

Add `import os` and `from unittest.mock import patch`, then insert these methods into the same class before the existing `if __name__ == "__main__"` block:

```python
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
```

- [ ] **Step 2: Run state tests and verify RED**

Run:

```bash
python3 -m unittest -v tests.test_state
```

Expected: import error for `crypto_desk.state`.

- [ ] **Step 3: Implement schema version 1 and atomic persistence**

Use these terminal stages:

```python
TERMINAL_STAGES = {
    "baseline",
    "ignored",
    "review_downgraded",
    "analysis_failed",
    "delivered",
    "delivery_failed",
    "delivery_unknown",
}
```

Normalize titles by Unicode NFKC, remove Unicode punctuation and whitespace, lowercase remaining characters, and hash with SHA-256. Include no source name in the cross-source fingerprint; use source IDs only as auxiliary fields in the record.

Each new non-baseline record must persist only this bounded, reconstructible information: `stage`; a serialized item containing `source_id`, `source`, `title`, `body`, `url`, and UTC `published_at`; sorted unique `source_ids`; `first_seen_at`; `updated_at`; `primary_analysis`/`review_analysis` dictionaries or `None`; integer `primary_attempts`/`review_attempts`; `next_attempt_at`; `review_failed`; and `delivery_started_at`. Baseline records need only their terminal stage, identifying item fields, `source_ids`, and timestamps. `register_new()` returns the number of newly created records and merges a duplicate record's source ID without reopening terminal work. Never persist credentials, headers, complete Gemini requests, Bark URLs, or exception text.

`save()` must write UTF-8 JSON to a sibling temporary file, flush, call `os.fsync()`, and then `os.replace()` the target. A failure must remove only the newly created temporary file and raise `StateWriteError("state persistence failed")` without exposing file contents.

When loading a corrupt file, atomically rename it to `state.json.corrupt-YYYYMMDDHHMMSS`, return an empty `WorkerState(version=1, baseline_initialized=False, items={})`, and log only `state_corrupt_recovered` plus the basename. Convert all `delivery_started` records to `delivery_unknown` immediately inside `load()`, atomically save the converted state before returning it, and ensure the included second-load assertion proves that recovery is durable.

- [ ] **Step 4: Run state and all prior tests**

Run:

```bash
python3 -m unittest -v tests.test_config tests.test_transport tests.test_bark tests.test_gemini tests.test_state
git diff --check
```

Expected: all tests pass.

- [ ] **Step 5: Commit durable state**

```bash
git add crypto_desk/state.py tests/test_state.py tests/helpers.py
git commit -m "feat: persist worker notification state"
```

### Task 5: News Source Adapters and Isolated Concurrent Fetching

**Files:**
- Create: `crypto_desk/source_http.py`
- Create: `crypto_desk/sources.py`
- Create: `tests/test_sources.py`
- Create: `tests/fixtures/binance.json`
- Create: `tests/fixtures/techflow.json`
- Create: `tests/fixtures/panews.xml`
- Create: `tests/fixtures/odaily.xml`
- Create: `tests/fixtures/ctcn.xml`
- Create: `tests/fixtures/catcher.xml`
- Modify: `THIRD_PARTY_NOTICES.md:1`

**Interfaces:**
- Consumes: `HttpRequest`, `HttpResponse`, `NewsItem`, and an injected transport.
- Produces: `SourceClient.fetch(source_key, now) -> tuple[NewsItem, ...]`.
- Produces: `fetch_sources(source_keys, client, now) -> tuple[NewsItem, ...]` with per-source failure isolation.
- Produces: immutable `SOURCE_DEFINITIONS` keyed by `panews`, `binance`, `odaily`, `ctcn`, `techflow`, and `catcher`; optional definitions for `jinse` and `blockbeats` remain disabled unless configured.

- [ ] **Step 1: Add minimal source fixtures and failing parser tests**

Each fixture must contain exactly two small fictional news records and no copied article body beyond the fields needed to exercise parsing. Use these exact payload shapes.

`tests/fixtures/binance.json`:

```json
{"data":{"catalogs":[{"articles":[{"code":"bn-1","releaseDate":1787202000000,"title":"币安测试公告一"},{"code":"bn-2","releaseDate":1787202060000,"title":"币安测试公告二"}]}]}}
```

`tests/fixtures/techflow.json`:

```json
{"data":[{"id":1,"created_at":"2026-08-20 13:00:00","title":"深潮测试快讯一","abstract":"测试正文一"},{"id":2,"created_at":"2026-08-20 13:01:00","title":"深潮测试快讯二","abstract":"测试正文二"}]}
```

`tests/fixtures/panews.xml`:

```xml
<rss><channel><item><guid>pa-1</guid><title>PANews 测试一</title><description>测试正文一</description><link>https://news.example/panews/1</link><pubDate>Thu, 20 Aug 2026 05:00:00 GMT</pubDate></item><item><guid>pa-2</guid><title>PANews 测试二</title><description>测试正文二</description><link>https://news.example/panews/2</link><pubDate>Thu, 20 Aug 2026 05:01:00 GMT</pubDate></item></channel></rss>
```

`tests/fixtures/odaily.xml`:

```xml
<rss><channel><item><guid>od-1</guid><title>Odaily 测试一</title><description>测试正文一</description><link>https://news.example/odaily/1</link><pubDate>Thu, 20 Aug 2026 05:02:00 GMT</pubDate></item><item><guid>od-2</guid><title>Odaily 测试二</title><description>测试正文二</description><link>https://news.example/odaily/2</link><pubDate>Thu, 20 Aug 2026 05:03:00 GMT</pubDate></item></channel></rss>
```

`tests/fixtures/ctcn.xml`:

```xml
<rss><channel><item><guid>ct-1</guid><title>吴说测试一</title><description>测试正文一</description><link>https://news.example/ctcn/1</link><pubDate>Thu, 20 Aug 2026 05:04:00 GMT</pubDate></item><item><guid>ct-2</guid><title>吴说测试二</title><description>测试正文二</description><link>https://news.example/ctcn/2</link><pubDate>Thu, 20 Aug 2026 05:05:00 GMT</pubDate></item></channel></rss>
```

`tests/fixtures/catcher.xml`:

```xml
<rss><channel><item><guid>cc-1</guid><title>链捕手测试一</title><description>测试正文一</description><link>https://news.example/catcher/1</link><pubDate>Thu, 20 Aug 2026 05:06:00 GMT</pubDate></item><item><guid>cc-2</guid><title>链捕手测试二</title><description>测试正文二</description><link>https://news.example/catcher/2</link><pubDate>Thu, 20 Aug 2026 05:07:00 GMT</pubDate></item></channel></rss>
```

Create `tests/test_sources.py`:

```python
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import unittest

from crypto_desk.models import HttpResponse
from crypto_desk.sources import SOURCE_DEFINITIONS, SourceClient, SourceError, fetch_sources
from tests.helpers import RecordingTransport, sample_news


FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)


class SourceTests(unittest.TestCase):
    def test_each_default_source_parses_two_records(self):
        fixture_names = {
            "panews": "panews.xml",
            "binance": "binance.json",
            "odaily": "odaily.xml",
            "ctcn": "ctcn.xml",
            "techflow": "techflow.json",
            "catcher": "catcher.xml",
        }
        for source, fixture in fixture_names.items():
            with self.subTest(source=source):
                body = (FIXTURES / fixture).read_bytes()
                transport = RecordingTransport([HttpResponse(200, {}, body)])
                items = SourceClient(transport).fetch(source, NOW)
                self.assertEqual(len(items), 2)
                self.assertTrue(all(item.title for item in items))
                self.assertTrue(all(item.source for item in items))

    def test_one_source_failure_does_not_discard_successful_sources(self):
        class FakeClient:
            def fetch(self, source, now):
                if source == "panews":
                    raise RuntimeError("failed")
                return (sample_news(source="Binance"),)

        items = fetch_sources(("panews", "binance"), FakeClient(), NOW)
        self.assertEqual([item.source for item in items], ["Binance"])

    def test_all_source_failures_raise_without_exposing_details(self):
        class FailingClient:
            def fetch(self, source, now):
                raise RuntimeError("private-response-body")

        with self.assertRaisesRegex(SourceError, "all sources failed") as caught:
            fetch_sources(("panews", "binance"), FailingClient(), NOW)
        self.assertNotIn("private-response-body", str(caught.exception))

    def test_techflow_waf_challenge_retries_once_with_cookie(self):
        challenge = b"<script>var arg1='0123456789ABCDEF0123456789ABCDEF01234567'</script>"
        success = (FIXTURES / "techflow.json").read_bytes()
        transport = RecordingTransport([
            HttpResponse(200, {}, challenge),
            HttpResponse(200, {}, success),
        ])
        items = SourceClient(transport).fetch("techflow", NOW)
        self.assertEqual(len(items), 2)
        self.assertIn("Cookie", transport.requests[1].headers)

    def test_gzip_decompression_limit_is_enforced(self):
        compressed = gzip.compress(b"x" * 65)
        transport = RecordingTransport([
            HttpResponse(200, {"Content-Encoding": "gzip"}, compressed)
        ])
        with self.assertRaisesRegex(SourceError, "source response too large"):
            SourceClient(transport, max_decompressed_bytes=64).fetch("panews", NOW)

    def test_invalid_rss_and_json_raise_sanitized_source_error(self):
        cases = (("panews", b"<rss>"), ("binance", b"{not-json"))
        for source, body in cases:
            with self.subTest(source=source):
                transport = RecordingTransport([HttpResponse(200, {}, body)])
                with self.assertRaisesRegex(SourceError, "source parse failed") as caught:
                    SourceClient(transport).fetch(source, NOW)
                self.assertNotIn(body.decode("utf-8"), str(caught.exception))

    def test_missing_title_is_filtered_and_bad_timestamp_uses_now(self):
        body = (
            b"<rss><channel>"
            b"<item><guid>missing</guid><description>x</description></item>"
            b"<item><guid>kept</guid><title>kept</title><pubDate>bad</pubDate></item>"
            b"</channel></rss>"
        )
        items = SourceClient(
            RecordingTransport([HttpResponse(200, {}, body)])
        ).fetch("panews", NOW)
        self.assertEqual([item.title for item in items], ["kept"])
        self.assertEqual(items[0].published_at, NOW)

    def test_source_urls_are_fixed_https_and_each_source_is_capped_at_thirty(self):
        self.assertTrue(all(
            url.startswith("https://")
            for definition in SOURCE_DEFINITIONS.values()
            for url in definition.urls
        ))
        articles = [
            {"code": str(index), "releaseDate": 1787202000000, "title": str(index)}
            for index in range(31)
        ]
        body = json.dumps({"data": {"catalogs": [{"articles": articles}]}}).encode()
        items = SourceClient(
            RecordingTransport([HttpResponse(200, {}, body)])
        ).fetch("binance", NOW)
        self.assertEqual(len(items), 30)

    def test_optional_sources_parse_only_when_explicitly_requested(self):
        payloads = {
            "jinse": {
                "list": [{"lives": [{
                    "id": 1,
                    "created_at": 1787202000,
                    "content": "【金色测试】正文",
                    "link": "https://news.example/jinse/1",
                }]}],
            },
            "blockbeats": {
                "data": {"data": [{
                    "id": 2,
                    "create_time": 1787202000,
                    "title": "律动测试",
                    "content": "正文",
                    "link": "https://news.example/blockbeats/2",
                }]},
            },
        }
        for source, payload in payloads.items():
            with self.subTest(source=source):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                items = SourceClient(
                    RecordingTransport([HttpResponse(200, {}, body)])
                ).fetch(source, NOW)
                self.assertEqual(len(items), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run source tests and verify RED**

Run:

```bash
python3 -m unittest -v tests.test_sources
```

Expected: import error for `crypto_desk.sources`.

- [ ] **Step 3: Implement source definitions, parsers, and WAF retry**

Port the current browser source endpoints and parsing semantics without changing `index.html`. RSS parsing uses `xml.etree.ElementTree`; JSON parsing uses `json.loads`. Standardize every record into UTC-aware `NewsItem` values and strip HTML tags/entities before analysis.

Define `SourceDefinition(name, urls)` as an immutable value object and use these fixed HTTPS endpoint tuples so the Worker cannot be turned into a general-purpose fetcher:

```python
SOURCE_DEFINITIONS = {
    "jinse": SourceDefinition("金色财经", (
        "https://api.jinse.cn/noah/v2/lives?limit=30&reading=false&source=web&flag=down&id=0&category=0",
        "https://api.jinse.com/noah/v2/lives?limit=30&reading=false&source=web&flag=down&id=0&category=0",
    )),
    "blockbeats": SourceDefinition("BlockBeats", (
        "https://api.theblockbeats.news/v2/rss/newsflash",
        "https://api.theblockbeats.news/v1/open-api/home-xml",
    )),
    "panews": SourceDefinition("PANews", (
        "https://rss.panewslab.com/zh/gtimg/rss",
        "https://www.panewslab.com/webapi/flashnews?LId=1&Rn=30&tw=0",
    )),
    "binance": SourceDefinition("币安公告", (
        "https://www.binance.com/bapi/apex/v1/public/apex/cms/article/list/query?type=1&pageNo=1&pageSize=15&catalogId=48",
    )),
    "odaily": SourceDefinition("Odaily", (
        "https://rss.odaily.news/rss/newsflash",
    )),
    "ctcn": SourceDefinition("吴说", (
        "https://wublockchain.substack.com/feed",
    )),
    "techflow": SourceDefinition("深潮", (
        "https://www.techflowpost.com/api/client/newsflashes?page=1&page_size=30",
    )),
    "catcher": SourceDefinition("链捕手", (
        "https://www.chaincatcher.com/rss/clist",
    )),
}
```

For a definition with multiple endpoints, try them in order and stop at the first successfully parsed non-empty result. The six configured defaults remain `panews,binance,odaily,ctcn,techflow,catcher`; `jinse` and `blockbeats` are used only when explicitly listed in `CID_WORKER_SOURCES`.

`SourceClient.fetch()` must add a fixed browser-compatible User-Agent, `Accept: */*`, `Accept-Encoding: gzip`, source-specific Referer/Origin headers, and a 30-second timeout. Its constructor accepts `max_decompressed_bytes` only to make the default 2 MiB decompression ceiling testable. Export `SourceError`; parse/decompression failures must use the fixed messages asserted above and never include a response body or URL. Reuse the existing AGPL-compatible `acw_sc_v2` algorithm in `source_http.py`, attribute it in `THIRD_PARTY_NOTICES.md`, cache only the non-secret WAF cookie in memory, and retry a detected challenge once.

`fetch_sources()` must use `ThreadPoolExecutor(max_workers=min(8, len(source_keys)))`, collect successful non-empty tuples, log only `source_failed source=<key>`, sort results by `published_at`, and return the combined tuple. A source whose response is empty after title filtering or structurally incompatible is a failed source. If no configured source succeeds, raise `SourceError("all sources failed")`; this prevents an outage during cold start from creating an empty baseline that would later push old news. Never log response bodies or full source URLs.

- [ ] **Step 4: Run source tests, full unit suite, and attribution check**

Run:

```bash
python3 -m unittest -v tests.test_config tests.test_transport tests.test_bark tests.test_gemini tests.test_state tests.test_sources
rg -n "RSSHub|AGPL" THIRD_PARTY_NOTICES.md crypto_desk/source_http.py
git diff --check
```

Expected: all tests pass, attribution references are present, and diff check is clean.

- [ ] **Step 5: Commit source adapters**

```bash
git add crypto_desk/source_http.py crypto_desk/sources.py tests/test_sources.py tests/fixtures THIRD_PARTY_NOTICES.md
git commit -m "feat: add background news sources"
```

### Task 6: Double-Model Policy, Durable Retries, and At-Most-Once Delivery

**Files:**
- Create: `crypto_desk/policy.py`
- Create: `tests/test_policy.py`
- Modify: `tests/helpers.py:1`

**Interfaces:**
- Consumes: `GeminiClient.analyze(item, model, review=False)`, `BarkClient.send(text, title, now)`, `StateStore.save(state)`, and state records created by `StateStore.register_new()`.
- Produces: `NewsProcessor.process_due(state, now) -> int`, returning the number of records advanced.
- Produces: `format_notification(item, final_analysis, primary_analysis, review_failed, now) -> tuple[str, str]`.
- Produces: durable record stages `pending_primary`, `primary_retry`, `pending_review`, `review_retry`, `review_downgraded`, `notification_pending`, `delivery_started`, `delivered`, `delivery_failed`, `analysis_failed`, and `delivery_unknown`.

- [ ] **Step 1: Write failing policy tests for every confirmed branch**

Create `tests/test_policy.py` with fake Gemini and Bark clients. The central cases must read:

```python
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
            delivered_item = sample_news(title="second")
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
```

Extend `tests/helpers.py` with these exact fakes before running the policy test:

```python
from crypto_desk.gemini import GeminiRetryableError


class FakeGemini:
    def __init__(self, results):
        self.results = list(results)
        self.models = []
        self.review_flags = []

    def analyze(self, item, model, review=False):
        self.models.append(model)
        self.review_flags.append(review)
        if not self.results:
            raise AssertionError("unexpected Gemini call")
        result = self.results.pop(0)
        if result == "retry":
            raise GeminiRetryableError("retryable Gemini failure")
        if isinstance(result, Exception):
            raise result
        return result


class FakeBark:
    def __init__(self, delivered):
        self.delivered = delivered
        self.calls = []

    def send(self, text, title, now=None):
        self.calls.append((text, title, now))
        return self.delivered
```

- [ ] **Step 2: Run policy tests and verify RED**

Run:

```bash
python3 -m unittest -v tests.test_policy
```

Expected: import error for `crypto_desk.policy`.

- [ ] **Step 3: Implement the durable state machine**

Define retry delays exactly:

```python
RETRY_DELAYS_SECONDS = (30, 120, 300)
MAJOR_LEVEL = 4
```

Call Gemini with `review=False` for the primary stage and `review=True` for the review stage. For a retryable primary/review failure, increment the stage-specific attempt count. If the failure occurred on the initial call or first two retries, persist the next attempt time using the corresponding delay. When the third retry fails, primary becomes `analysis_failed`; review becomes `notification_pending` with `review_failed=True` and the saved primary analysis. A permanent primary failure becomes `analysis_failed` immediately; a permanent review failure immediately uses the same explicit fallback path as exhausted review retries.

Serialize a successful `Analysis` into the record as its eleven normalized fields, converting `coins` to a JSON list; reconstruct it with `coins=tuple(...)` when a due record is loaded after restart. Persist the primary analysis before entering `pending_review`/`review_retry`, and persist the review analysis before entering `notification_pending`. The restart test above must prove the fallback uses the stored primary result without repeating primary analysis.

Before invoking Bark:

1. Format title and body from the final or fallback analysis.
2. Set `stage="delivery_started"` and `delivery_started_at`.
3. Persist the state successfully.
4. Call Bark exactly once.
5. Set `stage="delivered"` or `stage="delivery_failed"` and persist again.

If step 3 fails, do not call Bark. If the process dies during step 4, `StateStore.load()` converts the record to `delivery_unknown` and no automatic resend occurs.

Use the reviewed analysis for normal notification fields. For review fallback, use the primary analysis, set the approved `初判回退` title suffix, and add `复核未完成，当前为初判结果。` to the body without including an exception message.

Emit only stable, low-cardinality log events: `analysis_retry stage=primary|review`, `analysis_failed stage=primary|review`, `review_fallback`, `bark_delivered`, `bark_failed`, and `state_persistence_failed`. An optional identifier may be the first 12 hexadecimal characters of the title fingerprint; never log a title, body, upstream response, exception object, request URL, API key, or Bark key. A known Gemini failure advances only its own record and processing continues with later due records. A state persistence failure is safety-critical: log the fixed event, propagate `StateWriteError`, and stop the current notification-processing pass before any unpersisted Bark call.

- [ ] **Step 4: Run policy tests and the complete suite**

Run:

```bash
python3 -m unittest discover -s tests -v
git diff --check
```

Expected: every test passes and no diff errors.

- [ ] **Step 5: Commit policy orchestration**

```bash
git add crypto_desk/policy.py tests/test_policy.py tests/helpers.py
git commit -m "feat: gate major news notifications"
```

### Task 7: Worker Poll Cycle, CLI, and Failure Isolation

**Files:**
- Create: `crypto_desk/worker.py`
- Create: `background_worker.py`
- Create: `tests/test_worker.py`

**Interfaces:**
- Consumes: `WorkerConfig`, `SourceClient`, `fetch_sources`, `StateStore`, and `NewsProcessor`.
- Produces: `Worker.run_once(now) -> str` with `configuration_missing`, `baseline_created`, `poll_completed`, or `poll_skipped_overlap`.
- Produces: `Worker.run_forever(stop_event)` and `main(argv=None) -> int`.
- Produces: CLI flags `--once` and `--env-file PATH`; neither accepts a secret value.

- [ ] **Step 1: Write failing worker-cycle tests**

Create `tests/test_worker.py`:

```python
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest

from crypto_desk.config import WorkerConfig
from crypto_desk.state import StateStore
from crypto_desk.worker import Worker
from tests.helpers import FakeProcessor, FakeSourceFetcher, sample_news


NOW = datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)


class WorkerTests(unittest.TestCase):
    def config(self, tmp, configured=True):
        return WorkerConfig(
            gemini_api_key="fake" if configured else "",
            bark_push_key="fake" if configured else "",
            primary_model="gemini-3.1-flash-lite",
            review_model="gemini-3.5-flash-lite",
            poll_seconds=30,
            state_file=Path(tmp) / "state.json",
            sources=("panews",),
        )

    def test_missing_configuration_makes_no_source_or_state_progress(self):
        with TemporaryDirectory() as tmp:
            fetcher = FakeSourceFetcher([sample_news()])
            worker = Worker(
                self.config(tmp, configured=False),
                StateStore(Path(tmp) / "state.json"),
                fetcher,
                FakeProcessor(),
            )
            self.assertEqual(worker.run_once(NOW), "configuration_missing")
            self.assertEqual(fetcher.calls, 0)
            self.assertFalse((Path(tmp) / "state.json").exists())

    def test_first_configured_poll_creates_baseline_without_processing(self):
        with TemporaryDirectory() as tmp:
            processor = FakeProcessor()
            worker = Worker(
                self.config(tmp),
                StateStore(Path(tmp) / "state.json"),
                FakeSourceFetcher([sample_news()]),
                processor,
            )
            self.assertEqual(worker.run_once(NOW), "baseline_created")
            self.assertEqual(processor.calls, 0)

    def test_next_poll_registers_and_processes_only_new_items(self):
        with TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            processor = FakeProcessor()
            fetcher = FakeSourceFetcher([sample_news(title="old")])
            worker = Worker(self.config(tmp), store, fetcher, processor)
            worker.run_once(NOW)
            fetcher.items = [sample_news(title="old"), sample_news(title="new")]
            self.assertEqual(worker.run_once(NOW), "poll_completed")
            self.assertEqual(processor.calls, 1)

    def test_source_failure_is_logged_generically_and_cycle_completes(self):
        class FailingFetcher:
            def __call__(self, source_names, now):
                raise RuntimeError("sensitive-response-body")

        with TemporaryDirectory() as tmp:
            worker = Worker(
                self.config(tmp),
                StateStore(Path(tmp) / "state.json"),
                FailingFetcher(),
                FakeProcessor(),
            )
            with self.assertLogs("crypto_desk.worker", level="ERROR") as caught:
                self.assertEqual(worker.run_once(NOW), "poll_completed")
            output = "\n".join(caught.output)
            self.assertIn("source_cycle_failed", output)
            self.assertNotIn("sensitive-response-body", output)

    def test_two_concurrent_cycles_never_overlap_source_fetches(self):
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
            first = threading.Thread(target=worker.run_once, args=(NOW,))
            second = threading.Thread(target=worker.run_once, args=(NOW,))
            first.start()
            self.assertTrue(fetcher.entered.wait(1))
            second.start()
            fetcher.release.set()
            first.join(2)
            second.join(2)
            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())
            self.assertEqual(fetcher.max_active, 1)

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


if __name__ == "__main__":
    unittest.main()
```

Extend `tests/helpers.py` with the Worker fakes used above:

```python
class FakeSourceFetcher:
    def __init__(self, items):
        self.items = list(items)
        self.calls = 0

    def __call__(self, source_names, now):
        self.calls += 1
        return tuple(self.items)


class FakeProcessor:
    def __init__(self):
        self.calls = 0

    def process_due(self, state, now):
        self.calls += 1
        return 0
```

- [ ] **Step 2: Run worker tests and verify RED**

Run:

```bash
python3 -m unittest -v tests.test_worker
```

Expected: import error for `crypto_desk.worker`.

- [ ] **Step 3: Implement single-flight worker and CLI**

`Worker.run_once()` must acquire a nonblocking `threading.Lock`; if already held, return `poll_skipped_overlap`. With missing config, return `configuration_missing` before loading state or fetching sources. With an uninitialized state, fetch sources, baseline them, persist, return `baseline_created`, and never call the processor. Otherwise fetch, register new items, persist, call `processor.process_due()`, prune, persist, and return `poll_completed`. An unexpected aggregate source-fetch exception must log only `source_cycle_failed`, leave an uninitialized baseline untouched, and return `poll_completed`; per-source failures should already have been isolated by `fetch_sources()`.

`run_forever()` waits `poll_seconds` through `stop_event.wait(timeout)` after every cycle. It catches cycle exceptions, logs `worker_cycle_failed` without the exception object, and continues. The CLI must load `.env` before config construction, create the concrete transport/clients/store/processor, support a safe `--once`, and install SIGINT/SIGTERM handlers that set the stop event.

When `--once` is used with missing keys, print only `worker configuration missing; no requests sent` and exit 0. Never print key values, parsed `.env` content, HTTP headers, or request URLs.

- [ ] **Step 4: Run all unit tests and a no-key CLI smoke test**

Run with an explicitly empty environment for the sensitive names:

```bash
env -u GEMINI_API_KEY -u BARK_PUSH_KEY python3 -m unittest discover -s tests -v
cid_worker_tmp_dir="$(mktemp -d)"
env -u GEMINI_API_KEY -u BARK_PUSH_KEY python3 background_worker.py --once --env-file "$cid_worker_tmp_dir/missing.env"
rmdir "$cid_worker_tmp_dir"
git diff --check
```

Expected: all tests pass; CLI exits 0 with the single safe missing-configuration message and performs no network request.

- [ ] **Step 5: Commit the worker runtime**

```bash
git add crypto_desk/worker.py background_worker.py tests/test_worker.py tests/helpers.py
git commit -m "feat: run background news monitoring"
```

### Task 8: Deployment Template, Documentation, Release Checks, and Full Verification

**Files:**
- Create: `deploy/crypto-intelligence-desk-worker.service`
- Modify: `README.md:1`
- Modify: `使用说明.md:1`
- Modify: `SECURITY.md:1`
- Modify: `CHANGELOG.md:1`
- Modify: `VERSION:1`
- Modify: `requirements.txt:1`
- Modify: `tools/check-release.ps1:1`
- Modify: `proxy.py:25`
- Create: `tests/test_release_contract.py`

**Interfaces:**
- Consumes: all runtime modules from Tasks 1–7.
- Produces: a documented `python3 background_worker.py` local command and an uninstalled systemd template.
- Produces: version `1.1.0` and release-contract tests that inspect tracked files without reading `.env`.

- [ ] **Step 1: Write failing release-contract tests**

Create `tests/test_release_contract.py`:

```python
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReleaseContractTests(unittest.TestCase):
    def test_version_and_documented_models(self):
        self.assertEqual((ROOT / "VERSION").read_text(encoding="utf-8").strip(), "1.1.0")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("gemini-3.1-flash-lite", readme)
        self.assertIn("gemini-3.5-flash-lite", readme)
        self.assertIn("background_worker.py", readme)

    def test_systemd_template_is_isolated_and_has_no_secret(self):
        unit = (ROOT / "deploy" / "crypto-intelligence-desk-worker.service").read_text(
            encoding="utf-8"
        )
        self.assertIn("StateDirectory=crypto-intelligence-desk", unit)
        self.assertIn("NoNewPrivileges=true", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("EnvironmentFile=-/opt/crypto-intelligence-desk/.env", unit)
        self.assertNotIn("ListenStream", unit)
        self.assertNotIn("BARK_PUSH_KEY=", unit)
        self.assertNotIn("GEMINI_API_KEY=", unit)

    def test_env_example_has_empty_secrets(self):
        values = {}
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and "=" in line:
                name, value = line.split("=", 1)
                values[name] = value
        self.assertEqual(values["BARK_PUSH_KEY"], "")
        self.assertEqual(values["GEMINI_API_KEY"], "")

    def test_proxy_reads_the_release_version_instead_of_hard_coding_it(self):
        proxy_source = (ROOT / "proxy.py").read_text(encoding="utf-8")
        self.assertIn("APP_VERSION", proxy_source)
        self.assertIn('open(os.path.join(BASE, "VERSION")', proxy_source)
        self.assertNotIn('"version":"1.0.1"', proxy_source)

    def test_local_env_is_ignored(self):
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".env", ignored)

    def test_docs_state_cold_start_and_l4_l5_contract(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        chinese = (ROOT / "使用说明.md").read_text(encoding="utf-8")
        self.assertIn("first configured run creates a baseline", readme)
        self.assertIn("only L4-L5 results are reviewed", readme)
        self.assertIn("第一次有效启动只建立新闻基线", chinese)
        self.assertIn("只有初判达到 L4-L5 才", chinese)

    def test_requirements_remain_standard_library_only(self):
        lines = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        declarations = [line for line in lines if line.strip() and not line.startswith("#")]
        self.assertEqual(declarations, [])
        self.assertIn("No third-party Python packages are required", "\n".join(lines))

    def test_release_scanner_explicitly_excludes_dot_env(self):
        scanner = (ROOT / "tools" / "check-release.ps1").read_text(encoding="utf-8")
        self.assertIn("$_.Name -ne '.env'", scanner)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run release-contract tests and verify RED**

Run:

```bash
python3 -m unittest -v tests.test_release_contract
```

Expected: failures for missing systemd template, version `1.0.1`, and missing worker documentation.

- [ ] **Step 3: Add the uninstalled systemd template and complete documentation**

Create `deploy/crypto-intelligence-desk-worker.service` with this exact security and runtime shape:

```ini
[Unit]
Description=Crypto Intelligence Desk Background Worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/opt/crypto-intelligence-desk
EnvironmentFile=-/opt/crypto-intelligence-desk/.env
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=CID_WORKER_STATE_FILE=/var/lib/crypto-intelligence-desk/worker-state.json
ExecStart=/usr/bin/python3 /opt/crypto-intelligence-desk/background_worker.py
Restart=on-failure
RestartSec=10
TimeoutStopSec=20
UMask=0027
StateDirectory=crypto-intelligence-desk
StateDirectoryMode=0750
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictRealtime=true
LockPersonality=true
MemoryDenyWriteExecute=true
CapabilityBoundingSet=
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX

[Install]
WantedBy=multi-user.target
```

Add a `README.md` section containing this exact operational contract, with normal Markdown links adjusted to the surrounding document:

```markdown
## Optional 24-hour Bark worker

`background_worker.py` runs independently from the web monitor. It watches PANews, Binance announcements, Odaily, Wu Blockchain, TechFlow, and ChainCatcher even when no browser is open. New articles are first analyzed by `gemini-3.1-flash-lite`; only L4-L5 results are reviewed by `gemini-3.5-flash-lite`. Bark is sent only when the review remains L4-L5. If all review attempts fail, the notification is sent once from the primary result and is marked as a fallback.

Copy `.env.example` to `.env`, fill `GEMINI_API_KEY` and `BARK_PUSH_KEY` locally, and never commit `.env`. Start the worker separately with `python3 background_worker.py`. Configuration is loaded at startup, so restart only the worker after changing it.

The first configured run creates a baseline and does not push existing articles. State is retained for seven days or 2,000 terminal records and prevents duplicate delivery across restarts. If either required key is missing, the worker stays idle and sends no network requests. The worker opens no HTTP port and does not expose either key to the browser or `/ping`.
```

Add a Chinese `使用说明.md` section with this exact content:

```markdown
## 十七、24 小时 Bark 后台通知（可选）

后台通知由独立的 `background_worker.py` 提供，不依赖浏览器页面保持打开。默认监控 PANews、币安公告、Odaily、吴说、深潮和链捕手。初判模型为 `gemini-3.1-flash-lite`；只有初判达到 L4-L5 才交给 `gemini-3.5-flash-lite` 复核。复核仍为 L4-L5 才发送 Bark；复核降级不发送；复核全部失败时按初判结果发送一次，并标记“初判回退”。

复制 `.env.example` 为 `.env`，只在本机或服务器填写 `GEMINI_API_KEY` 和 `BARK_PUSH_KEY`。不要把真实 Key 发到聊天、Issue、截图或 Git。运行 `python3 background_worker.py` 启动 Worker。环境变量只在启动时读取，修改后需要重启 Worker，不需要重启网页服务。

第一次有效启动只建立新闻基线，不推送已有新闻。状态默认保留最近 7 天或最多 2,000 条终态记录，以避免服务重启后重复通知。缺少任一 Key 时 Worker 安静等待，不抓取新闻、不调用 Gemini、不调用 Bark。Worker 不监听端口，也不会通过网页或 `/ping` 返回配置状态。
```

Append this security paragraph to `SECURITY.md`:

```markdown
## Background worker secrets

The optional background worker reads `GEMINI_API_KEY` and `BARK_PUSH_KEY` only from its startup environment or an ignored `.env` file. It opens no listening port, and the web page and `/ping` endpoint do not expose worker configuration. Logs, state files, tests, and release artifacts must not contain either key, an authorization header, or a complete Bark request URL.
```

Add this exact top entry to `CHANGELOG.md`:

```markdown
## 1.1.0

- Add an optional independent 24-hour news worker using Gemini primary/review analysis.
- Push only confirmed L4-L5 news through Bark, with cold-start suppression and durable deduplication.
- Keep Gemini and Bark credentials server-side and expose no notification endpoint.
```

Document all six default sources, both Gemini models, L4–L5 review rules, review-failure fallback, cold-start suppression, seven-day/2,000-record state retention, missing-config idle behavior, manual local worker command, environment reload requiring worker restart, and the explicit prohibition on committing `.env`.

Update `SECURITY.md` to say the worker opens no port and neither `/ping` nor the front end exposes worker configuration. Update `CHANGELOG.md` with a `1.1.0` entry and then change `VERSION` to `1.1.0`. Keep `requirements.txt` dependency-free. Extend `tools/check-release.ps1` required-file checks for the runtime package, worker entrypoint, unit template, and tests; ensure the scanner ignores `.env` contents by never enumerating `.env` as a publishable text file.

Add these exact paths to the PowerShell `$required` array:

```powershell
'.env.example',
'requirements.txt',
'使用说明.md',
'background_worker.py',
'crypto_desk\__init__.py',
'crypto_desk\config.py',
'crypto_desk\gemini.py',
'crypto_desk\models.py',
'crypto_desk\notifications\__init__.py',
'crypto_desk\notifications\bark.py',
'crypto_desk\policy.py',
'crypto_desk\source_http.py',
'crypto_desk\sources.py',
'crypto_desk\state.py',
'crypto_desk\transport.py',
'crypto_desk\worker.py',
'deploy\crypto-intelligence-desk-worker.service',
'tests\__init__.py',
'tests\helpers.py',
'tests\fixtures\binance.json',
'tests\fixtures\catcher.xml',
'tests\fixtures\ctcn.xml',
'tests\fixtures\odaily.xml',
'tests\fixtures\panews.xml',
'tests\fixtures\techflow.json',
'tests\test_bark.py',
'tests\test_config.py',
'tests\test_gemini.py',
'tests\test_policy.py',
'tests\test_release_contract.py',
'tests\test_sources.py',
'tests\test_state.py',
'tests\test_transport.py',
'tests\test_worker.py'
```

Also make the existing recursive `$files` filter explicitly exclude the live secret file before applying any extension rule:

```powershell
$files = Get-ChildItem -LiteralPath $projectRoot -Recurse -File | Where-Object {
    $_.Name -ne '.env' -and (
        $textExtensions -contains $_.Extension.ToLowerInvariant() -or
        $_.Name -eq '.gitignore' -or
        $_.Name -eq 'VERSION'
    )
}
```

This is defense in depth: `.env` remains ignored and unpublished, and the release checker must neither read nor print it even if it is present locally.

Replace the hard-coded `/ping` version in `proxy.py` with a release-file value while preserving the response keys:

```python
def read_app_version():
    try:
        with open(os.path.join(BASE, "VERSION"), "r", encoding="utf-8") as version_file:
            return version_file.read().strip() or "unknown"
    except OSError:
        return "unknown"


APP_VERSION = read_app_version()
```

Build the ping response with `json.dumps({"ok": True, "app": "crypto-intelligence-desk", "version": APP_VERSION}, separators=(",", ":")).encode("utf-8")`. Add the standard-library `json` import. Do not expose Worker configuration in this response.

- [ ] **Step 4: Run the complete verification gate**

Run:

```bash
env -u GEMINI_API_KEY -u BARK_PUSH_KEY python3 -m unittest discover -s tests -v
python3 -m compileall -q crypto_desk background_worker.py proxy.py
sed -n '/^<script>$/,/^<\/script>$/p' index.html | sed '1d;$d' | node --check -
sh -n start.sh
git diff --check
git status --short
```

Expected: all tests pass; Python, JavaScript, and shell syntax checks exit 0; diff check prints nothing; status lists only intended feature files.

Run the no-key worker smoke test and existing local web regression:

```bash
cid_worker_tmp_dir="$(mktemp -d)"
env -u GEMINI_API_KEY -u BARK_PUSH_KEY python3 background_worker.py --once --env-file "$cid_worker_tmp_dir/missing.env"
rmdir "$cid_worker_tmp_dir"
CID_PORT=18999 python3 proxy.py
```

In a second terminal while the proxy is running:

```bash
curl -fsS http://127.0.0.1:18999/ping
curl -fsS http://127.0.0.1:18999/ | grep -F '<title>币圈新闻监控台</title>'
```

Expected: worker reports missing configuration without network activity; `/ping` returns `{"ok":true,"app":"crypto-intelligence-desk","version":"1.1.0"}` with the existing response schema; page title is found. Stop the local proxy with Ctrl+C.

Run a repository secret scan that reports file names only and excludes the scanner's own patterns:

```bash
git grep -Il -E 'sk-(proj-|ant-)?[A-Za-z0-9_-]{16,}|AIza[A-Za-z0-9_-]{20,}|xai-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY' -- . ':!tools/check-release.ps1'
```

Expected: no output and exit 1, meaning no tracked-file match. Do not print matching lines or secret-like values.

If PowerShell is available, additionally run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\check-release.ps1
```

Expected: `Release check passed`. If PowerShell is unavailable on macOS, report that platform-specific check as unavailable rather than installing software or claiming it ran.

- [ ] **Step 5: Commit documentation and release readiness**

```bash
git add deploy/crypto-intelligence-desk-worker.service README.md 使用说明.md SECURITY.md CHANGELOG.md VERSION requirements.txt tools/check-release.ps1 proxy.py tests/test_release_contract.py
git commit -m "docs: document background Bark worker"
```

- [ ] **Step 6: Perform final scope and history review without publishing**

Run:

```bash
git status -sb
git log --oneline --decorate -10
git diff origin/codex/import-current-source...HEAD --stat
git diff origin/codex/import-current-source...HEAD -- . ':!docs/superpowers/specs/*' ':!docs/superpowers/plans/*'
```

Expected: the branch contains the approved design, this plan, and the focused implementation commits; no `.env`, runtime state, real key, deployment mutation, or unrelated user file appears. Do not push, deploy, install the systemd unit, restart a service, call real Gemini, or send Bark without new explicit authorization.

---

## Implementation Completion Checklist

- [ ] Every production function introduced by this plan was preceded by a focused failing test that failed for the expected missing behavior.
- [ ] Full standard-library test discovery passes with sensitive environment variables explicitly unset.
- [ ] No test transport accesses the network.
- [ ] Bark path encoding, POST form, 15-second timeout, HTTP status, business code, Shanghai time, and secret-free errors are covered.
- [ ] Gemini model routing, header-only key, strict normalization, retryable/permanent errors, and secret-free errors are covered.
- [ ] Cold start, restart dedupe, cross-source dedupe, state corruption, pruning, and at-most-once delivery are covered.
- [ ] Primary L1–L3, review downgrade, review confirmation, primary exhaustion, and review fallback branches are covered.
- [ ] Existing page, JavaScript, shell entrypoint, proxy syntax, and `/ping` contract still pass.
- [ ] `.env.example` contains only empty secrets and `.env` is ignored.
- [ ] No real key was read, no real Gemini/Bark request was sent, and no online service was changed.
- [ ] Git history contains focused commits and no unrelated user changes.

---

### Task 9: Repair Production PANews and Catcher Compatibility

**Files:**
- Modify: `crypto_desk/sources.py`
- Modify: `tests/test_sources.py`
- Modify: `tests/test_release_contract.py`
- Modify: `VERSION`
- Modify: `CHANGELOG.md`
- Modify: `docs/superpowers/specs/2026-08-20-bark-background-notifications-design.md`
- Modify: `docs/superpowers/plans/2026-08-20-bark-background-notifications.md`

**Interfaces:**
- Preserves: `SOURCE_DEFINITIONS["panews"] -> SourceDefinition` and `SourceClient.fetch(source_key, now) -> tuple[NewsItem, ...]`.
- Changes: PANews has one final RSS endpoint; `SourceClient` default `max_decompressed_bytes` is exactly `3 * 1024 * 1024`.
- Preserves: injected `max_decompressed_bytes` overrides remain testable, raw transport remains bounded, and responses above the configured decoded limit raise sanitized `SourceError("source response too large")`.

- [ ] **Step 1: Write failing production regressions**

Add focused tests equivalent to:

```python
def test_panews_uses_current_final_rss_without_redirect_fallback(self):
    self.assertEqual(
        SOURCE_DEFINITIONS["panews"].urls,
        ("https://www.panewslab.com/rss.xml?lang=zh&featured=true",),
    )
    self.assertEqual(sources_module._SOURCE_FORMATS["panews"], ("rss",))

def test_default_limit_accepts_current_catcher_sized_gzip(self):
    large_description = "x" * 2262367
    body = (
        "<rss><channel><item><guid>cc-large</guid><title>链捕手测试</title>"
        "<description>%s</description></item></channel></rss>" % large_description
    ).encode("utf-8")
    transport = RecordingTransport([
        HttpResponse(200, {"Content-Encoding": "gzip"}, gzip.compress(body))
    ])
    self.assertEqual(len(SourceClient(transport).fetch("catcher", NOW)), 1)

def test_default_limit_rejects_gzip_over_three_mib(self):
    oversized = b"x" * (3 * 1024 * 1024 + 1)
    transport = RecordingTransport([
        HttpResponse(200, {"Content-Encoding": "gzip"}, gzip.compress(oversized))
    ])
    with self.assertRaisesRegex(SourceError, "source response too large"):
        SourceClient(transport).fetch("catcher", NOW)
```

Update the release contract to expect `VERSION == "1.1.1"`.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
env -u GEMINI_API_KEY -u BARK_PUSH_KEY python3 -m unittest \\
  tests.test_sources.SourceTests.test_panews_uses_current_final_rss_without_redirect_fallback \\
  tests.test_sources.SourceTests.test_default_limit_accepts_current_catcher_sized_gzip \\
  tests.test_sources.SourceTests.test_default_limit_rejects_gzip_over_three_mib \\
  tests.test_release_contract.ReleaseContractTests.test_version_and_documented_models -v
```

Expected: PANews endpoint, 2.26 MiB default-limit, and version assertions fail on 1.1.0; the over-3-MiB assertion already passes or remains safely failing only because the default is still lower.

- [ ] **Step 3: Implement the minimal source and release changes**

In `crypto_desk/sources.py`, define:

```python
_DEFAULT_MAX_DECOMPRESSED_BYTES = 3 * 1024 * 1024
```

Use it as the `SourceClient` constructor default. Replace PANews definitions with exactly one final RSS URL and one `rss` format. Do not alter redirect policy or other sources.

Set `VERSION` to `1.1.1` and prepend a CHANGELOG entry containing exactly the two operational fixes.

- [ ] **Step 4: Run focused and full GREEN gates**

Run:

```bash
env -u GEMINI_API_KEY -u BARK_PUSH_KEY python3 -m unittest tests.test_sources tests.test_release_contract -v
env -u GEMINI_API_KEY -u BARK_PUSH_KEY python3 -m unittest discover -s tests -v
python3 -m compileall -q crypto_desk background_worker.py proxy.py
sh -n start.sh
git diff --check
```

Expected: focused and complete suites pass, syntax checks exit 0, and diff check prints nothing.

- [ ] **Step 5: Commit, push, stage, and incrementally deploy**

```bash
git add crypto_desk/sources.py tests/test_sources.py tests/test_release_contract.py VERSION CHANGELOG.md docs/superpowers/specs/2026-08-20-bark-background-notifications-design.md docs/superpowers/plans/2026-08-20-bark-background-notifications.md
git commit -m "fix: restore production news sources"
git push origin codex/bark-background-worker
```

Generate an exact Git archive from the new commit, verify no `.env` or state, run the complete suite in remote staging, create a 1.1.0 rollback backup, sync while excluding production `.env` and state, then restart `crypto-intelligence-desk.service` and `crypto-intelligence-desk-worker.service`.

- [ ] **Step 6: Verify 6/6 production baseline and public release**

Verify both services active/enabled with zero unexpected restarts, public and loopback `/ping` return 1.1.1, state contains all six configured `baselined_sources`, PANews and catcher records are baseline-only on first success, delivery count remains zero for those recovered historical items, logs contain no secret patterns, and unrelated Nginx/services remain unchanged.
