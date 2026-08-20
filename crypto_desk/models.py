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
