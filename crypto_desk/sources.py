from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import html
import hashlib
import json
import logging
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence
import unicodedata
from urllib.parse import urlsplit
import xml.etree.ElementTree as ElementTree

from crypto_desk.models import HttpRequest, HttpResponse, NewsItem
from crypto_desk.source_http import ResponseTooLargeError, challenge_cookie, decoded_body
from crypto_desk.state import (
    MAX_BODY_LENGTH,
    MAX_SOURCE_ID_LENGTH,
    MAX_TITLE_LENGTH,
    MAX_URL_LENGTH,
)


_LOGGER = logging.getLogger(__name__)
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
_HTML_TAG = re.compile(r"<[^>]*>")
_TITLE_PREFIX = re.compile(r"^【(.{2,80}?)】\s*(.*)$", re.DOTALL)

_MAX_ITEMS = 30

_REFERERS = {
    "api.jinse.cn": "https://www.jinse.cn/",
    "api.jinse.com": "https://www.jinse.com/",
    "api.theblockbeats.news": "https://www.theblockbeats.info/",
    "www.panewslab.com": "https://www.panewslab.com/",
    "rss.panewslab.com": "https://www.panewslab.com/",
    "www.odaily.news": "https://www.odaily.news/",
    "rss.odaily.news": "https://www.odaily.news/",
    "www.techflowpost.com": "https://www.techflowpost.com/zh-CN/newsletter",
    "www.chaincatcher.com": "https://www.chaincatcher.com/",
}


class SourceError(RuntimeError):
    """A sanitized source retrieval or parsing failure."""


class _ParseError(ValueError):
    pass


class _EmptySourceError(ValueError):
    pass


@dataclass(frozen=True)
class SourceDefinition:
    name: str
    urls: tuple[str, ...]


@dataclass(frozen=True)
class SourceFetchResult:
    by_source: Mapping[str, tuple[NewsItem, ...]]

    @property
    def items(self) -> tuple[NewsItem, ...]:
        combined = [
            item
            for source_items in self.by_source.values()
            for item in source_items
        ]
        combined.sort(key=lambda item: item.published_at)
        return tuple(combined)


SOURCE_DEFINITIONS: Mapping[str, SourceDefinition] = MappingProxyType({
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
})

_SOURCE_FORMATS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "jinse": ("json", "json"),
    "blockbeats": ("json", "json"),
    "panews": ("rss", "json"),
    "binance": ("json",),
    "odaily": ("rss",),
    "ctcn": ("rss",),
    "techflow": ("json",),
    "catcher": ("rss",),
})


class SourceClient:
    def __init__(self, transport: Any, max_decompressed_bytes: int = 2 * 1024 * 1024):
        if max_decompressed_bytes < 1:
            raise ValueError("max_decompressed_bytes must be positive")
        self.transport = transport
        self.max_decompressed_bytes = max_decompressed_bytes
        self._waf_cookies: dict[str, str] = {}

    def fetch(self, source_key: str, now: datetime) -> tuple[NewsItem, ...]:
        definition = SOURCE_DEFINITIONS.get(source_key)
        if definition is None:
            raise SourceError("unknown source")
        instant = _utc(now)
        retained_error: SourceError | None = None
        formats = _SOURCE_FORMATS.get(source_key)
        if formats is None or len(definition.urls) != len(formats):
            raise SourceError("source configuration invalid")

        for url, expected_format in zip(definition.urls, formats):
            try:
                body = self._request(source_key, url)
                items = _parse(
                    source_key, definition, expected_format, body, instant
                )
                if not items:
                    raise _EmptySourceError
                return tuple(items[:_MAX_ITEMS])
            except ResponseTooLargeError:
                retained_error = SourceError("source response too large")
            except _ParseError:
                if retained_error is None:
                    retained_error = SourceError("source parse failed")
            except Exception:
                continue

        if retained_error is not None:
            raise retained_error
        raise SourceError("source fetch failed")

    def _request(self, source_key: str, url: str) -> bytes:
        host = (urlsplit(url).hostname or "").lower()
        cached_cookie = self._waf_cookies.get(host) if source_key == "techflow" else None
        response = self._send(url, host, cached_cookie)
        body = _decode(response, self.max_decompressed_bytes)
        if source_key == "techflow":
            cookie = challenge_cookie(body)
            if cookie is not None:
                self._waf_cookies[host] = cookie
                response = self._send(url, host, cookie)
                body = _decode(response, self.max_decompressed_bytes)
                if challenge_cookie(body) is not None:
                    raise RuntimeError("source request failed")
        if not 200 <= response.status < 300:
            raise RuntimeError("source request failed")
        return body

    def _send(self, url: str, host: str, cookie: str | None) -> HttpResponse:
        headers = {
            "User-Agent": _USER_AGENT,
            "Accept": "*/*",
            "Accept-Encoding": "gzip",
        }
        referer = _REFERERS.get(host)
        if referer:
            headers["Referer"] = referer
            headers["Origin"] = "https://" + host
        if host == "api.theblockbeats.news":
            headers["language"] = "cn"
        if host == "www.techflowpost.com":
            headers["Accept-Language"] = "zh-CN"
        if cookie:
            headers["Cookie"] = cookie
        return self.transport.send(HttpRequest("GET", url, headers, None, 30))


def fetch_sources(
    source_keys: Sequence[str], client: Any, now: datetime
) -> tuple[NewsItem, ...]:
    return fetch_sources_with_status(source_keys, client, now).items


def fetch_sources_with_status(
    source_keys: Sequence[str], client: Any, now: datetime
) -> SourceFetchResult:
    keys = tuple(source_keys)
    if not keys:
        raise SourceError("all sources failed")

    by_source: dict[str, tuple[NewsItem, ...]] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(keys))) as executor:
        futures = {executor.submit(client.fetch, key, now): key for key in keys}
        for future in as_completed(futures):
            key = futures[future]
            try:
                items = tuple(future.result())
                if not items:
                    raise SourceError("empty source")
                by_source[key] = items
            except Exception:
                _LOGGER.warning("source_failed source=%s", key)

    if not by_source:
        raise SourceError("all sources failed")
    ordered = {key: by_source[key] for key in keys if key in by_source}
    return SourceFetchResult(MappingProxyType(ordered))


def _decode(response: HttpResponse, max_bytes: int) -> bytes:
    encoding = ""
    for name, value in response.headers.items():
        if name.lower() == "content-encoding":
            encoding = value
            break
    try:
        return decoded_body(response.body, encoding, max_bytes)
    except ResponseTooLargeError:
        raise
    except (EOFError, OSError, ValueError):
        raise _ParseError from None


def _parse(
    source_key: str,
    definition: SourceDefinition,
    expected_format: str,
    body: bytes,
    now: datetime,
) -> list[NewsItem]:
    try:
        text = body.decode("utf-8-sig").strip()
        if not text:
            raise _EmptySourceError
        if expected_format == "rss":
            return _parse_rss(source_key, definition, text, now)
        if expected_format == "json":
            payload = json.loads(text)
            return _parse_json(source_key, definition, payload, now)
        raise _ParseError
    except _EmptySourceError:
        raise
    except (ElementTree.ParseError, UnicodeDecodeError, TypeError, ValueError):
        raise _ParseError from None


def _parse_rss(
    source_key: str,
    definition: SourceDefinition,
    text: str,
    now: datetime,
) -> list[NewsItem]:
    root = ElementTree.fromstring(text)
    if _local_name(root.tag).lower() != "rss":
        raise _ParseError
    channel = next(
        (
            child for child in root
            if _local_name(child.tag).lower() == "channel"
        ),
        None,
    )
    if channel is None:
        raise _ParseError
    items = []
    for element in channel:
        if _local_name(element.tag).lower() != "item":
            continue
        fields: dict[str, str] = {}
        for child in element:
            fields[_local_name(child.tag)] = "".join(child.itertext())
        raw_title = _clean(fields.get("title"))
        if not raw_title:
            continue
        description = _clean(fields.get("description") or fields.get("encoded"))
        match = _TITLE_PREFIX.match(raw_title)
        title = _clean(match.group(1)) if match else raw_title
        body = _clean(((match.group(2) + " " + description).strip()) if match else description)
        guid = _clean(fields.get("guid"))
        link = _clean(fields.get("link"))
        if guid:
            source_id = _source_identity(source_key, "guid", guid)
        elif link:
            source_id = _source_identity(source_key, "link", link)
        else:
            source_id = _source_identity(
                source_key, "title", _title_hash(raw_title)
            )
        items.append(_item(
            source_id,
            definition.name,
            title,
            body,
            link or "#",
            _timestamp(fields.get("pubDate"), now),
        ))
        if len(items) == _MAX_ITEMS:
            break
    return items


def _parse_json(
    source_key: str,
    definition: SourceDefinition,
    payload: Any,
    now: datetime,
) -> list[NewsItem]:
    if not isinstance(payload, dict) or payload.get("error"):
        return []
    parsers = {
        "jinse": _jinse,
        "blockbeats": _blockbeats,
        "panews": _panews,
        "binance": _binance,
        "odaily": _odaily,
        "techflow": _techflow,
    }
    parser = parsers.get(source_key)
    if parser is None:
        return []
    return [
        replace(
            item,
            source_id=_source_identity(source_key, "json", item.source_id),
        )
        for item in parser(payload, definition, now)[:_MAX_ITEMS]
    ]


def _jinse(payload: Mapping[str, Any], definition: SourceDefinition, now: datetime) -> list[NewsItem]:
    groups = payload.get("list")
    records = groups[0].get("lives", []) if isinstance(groups, list) and groups and isinstance(groups[0], dict) else []
    output = []
    for record in _dicts(records):
        content = _clean(record.get("content"))
        match = _TITLE_PREFIX.match(content)
        title = _clean(match.group(1)) if match else content[:60]
        body = _clean(match.group(2)) if match else content
        link = _clean(record.get("link"))
        output.append(_item(
            _json_record_identity("js", record.get("id"), link, title, body),
            definition.name, title, body,
            link or "https://www.jinse.cn/lives/" + str(record.get("id", "")) + ".html",
            _timestamp(record.get("created_at"), now),
        ))
    return _titled(output)


def _blockbeats(payload: Mapping[str, Any], definition: SourceDefinition, now: datetime) -> list[NewsItem]:
    data = payload.get("data")
    if isinstance(data, dict):
        records = data.get("data") or data.get("list") or []
    elif isinstance(data, list):
        records = data
    else:
        records = []
    output = []
    for record in _dicts(records):
        title = _clean(record.get("title"))
        body = _clean(record.get("content") or record.get("abstract"))
        link = _clean(record.get("link") or record.get("url"))
        output.append(_item(
            _json_record_identity(
                "bb", record.get("id") or record.get("article_id"),
                link, title, body,
            ),
            definition.name, title, body,
            link or "https://www.theblockbeats.info/newsflash",
            _timestamp(record.get("create_time") or record.get("add_time") or record.get("created_at"), now),
        ))
    return _titled(output)


def _panews(payload: Mapping[str, Any], definition: SourceDefinition, now: datetime) -> list[NewsItem]:
    data = payload.get("data")
    records: list[Any] = []
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict) and isinstance(data.get("flashNews"), list):
        for group in data["flashNews"]:
            if isinstance(group, dict):
                candidate = group.get("list") or group.get("List") or []
                if isinstance(candidate, list):
                    records.extend(candidate)
    elif isinstance(data, dict) and isinstance(data.get("list"), list):
        records = data["list"]
    output = []
    for record in _dicts(records):
        title = _clean(record.get("title"))
        body = _clean(record.get("desc") or record.get("description") or record.get("content"))
        link = _clean(record.get("link") or record.get("url") or record.get("news_url"))
        output.append(_item(
            _json_record_identity(
                "pa", record.get("id") or record.get("newsId"),
                link, title, body,
            ),
            definition.name, title, body,
            link or (
                "https://www.panewslab.com/zh/newsflash/" + str(record["id"])
                if record.get("id") else "https://www.panewslab.com/zh/newsflash"
            ),
            _timestamp(record.get("publishTime") or record.get("publish_time") or record.get("dateInfo"), now),
        ))
    return _titled(output)


def _binance(payload: Mapping[str, Any], definition: SourceDefinition, now: datetime) -> list[NewsItem]:
    data = payload.get("data")
    records: Any = []
    if isinstance(data, dict):
        catalogs = data.get("catalogs")
        if isinstance(catalogs, list) and catalogs and isinstance(catalogs[0], dict):
            records = catalogs[0].get("articles") or []
        if not records:
            records = data.get("articles") or []
    output = []
    for record in _dicts(records):
        code = record.get("code") or record.get("id") or ""
        title = _clean(record.get("title"))
        link = _clean(record.get("link") or record.get("url"))
        output.append(_item(
            _json_record_identity("bn", code, link, title, ""),
            definition.name, title, "",
            link or "https://www.binance.com/zh-CN/support/announcement/" + str(record.get("code") or ""),
            _timestamp(record.get("releaseDate") or record.get("releasedate"), now),
        ))
    return _titled(output)


def _odaily(payload: Mapping[str, Any], definition: SourceDefinition, now: datetime) -> list[NewsItem]:
    data = payload.get("data")
    if isinstance(data, dict):
        records = data.get("items") or data.get("list") or data.get("arr") or []
    elif isinstance(data, list):
        records = data
    else:
        records = []
    output = []
    for record in _dicts(records):
        title = _clean(record.get("title"))
        body = _clean(record.get("description") or record.get("content"))
        link = _clean(record.get("news_url") or record.get("link") or record.get("url"))
        output.append(_item(
            _json_record_identity("od", record.get("id"), link, title, body),
            definition.name, title, body,
            link or "https://www.odaily.news/newsflash/" + str(record.get("id") or ""),
            _timestamp(record.get("published_at"), now),
        ))
    return _titled(output)


def _techflow(payload: Mapping[str, Any], definition: SourceDefinition, now: datetime) -> list[NewsItem]:
    records = payload.get("data")
    output = []
    for record in _dicts(records):
        identity = record.get("id") or ""
        title = _clean(record.get("title"))
        body = _clean(record.get("abstract"))
        link = _clean(record.get("link") or record.get("url"))
        output.append(_item(
            _json_record_identity("tf", identity, link, title, body),
            definition.name, title, body,
            link or "https://www.techflowpost.com/zh-CN/newsletter/" + str(identity),
            _timestamp(record.get("created_at"), now),
        ))
    return _titled(output)


def _dicts(value: Any) -> Iterable[Mapping[str, Any]]:
    if not isinstance(value, list):
        return ()
    return (item for item in value if isinstance(item, dict))


def _titled(items: Iterable[NewsItem]) -> list[NewsItem]:
    return [item for item in items if item.title][:_MAX_ITEMS]


def _item(
    source_id: Any,
    source: str,
    title: Any,
    body: Any,
    url: Any,
    published_at: datetime,
) -> NewsItem:
    return NewsItem(
        source_id=_bounded(_clean(source_id), MAX_SOURCE_ID_LENGTH) or "unknown",
        source=source,
        title=_bounded(_clean(title), MAX_TITLE_LENGTH),
        body=_bounded(_clean(body), MAX_BODY_LENGTH),
        url=_bounded(_clean(url), MAX_URL_LENGTH) or "#",
        published_at=published_at.astimezone(timezone.utc),
    )


def _clean(value: Any) -> str:
    text = "" if value is None else str(value)
    text = html.unescape(text)
    text = _HTML_TAG.sub(" ", text)
    text = html.unescape(text)
    return " ".join(text.split())


def _bounded(value: str, limit: int) -> str:
    return value[:limit]


def _title_hash(title: str) -> str:
    normalized = "".join(
        character for character in title
        if not character.isspace() and not unicodedata.category(character).startswith("P")
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _source_identity(source_key: str, kind: str, value: str) -> str:
    prefix = source_key + ":" + kind + ":"
    candidate = prefix + value
    if len(candidate) <= MAX_SOURCE_ID_LENGTH:
        return candidate
    return prefix + "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_record_identity(
    prefix: str,
    provider_id: Any,
    canonical_link: Any,
    title: Any,
    body: Any,
) -> str:
    identity = _clean(provider_id)
    if identity:
        return prefix + identity
    link = _clean(canonical_link)
    if link:
        return "link-sha256:" + hashlib.sha256(link.encode("utf-8")).hexdigest()
    normalized = unicodedata.normalize(
        "NFKC", _clean(title) + "\n" + _clean(body)
    ).casefold()
    return "content:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _timestamp(value: Any, now: datetime) -> datetime:
    fallback = now.astimezone(timezone.utc)
    if value is None or value == "":
        return fallback
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            timestamp = float(value)
            if abs(timestamp) >= 1_000_000_000_000:
                timestamp /= 1000
            return datetime.fromtimestamp(timestamp, timezone.utc)

        text = str(value).strip()
        if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
            return _timestamp(float(text), fallback)
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            parsed = parsed.replace(tzinfo=_SHANGHAI)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return fallback


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SourceError("invalid source time")
    return value.astimezone(timezone.utc)
