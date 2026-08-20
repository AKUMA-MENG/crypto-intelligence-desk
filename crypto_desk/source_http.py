from __future__ import annotations

import gzip
import io
import re


_ACW_CHALLENGE = re.compile(rb"var\s+arg1\s*=\s*['\"]([0-9A-Fa-f]{40})['\"]")


class ResponseTooLargeError(ValueError):
    """Raised when an upstream body exceeds the configured decoded limit."""


def acw_sc_v2(arg1: str) -> str:
    """Calculate the Aliyun ``acw_sc__v2`` challenge cookie value.

    This is the small AGPL-compatible routine already used by ``proxy.py``;
    its RSSHub attribution is retained in ``THIRD_PARTY_NOTICES.md``.
    """

    password = "3000176000856006061501533003690027800375"
    positions = (
        15, 35, 29, 24, 33, 16, 1, 38, 10, 9,
        19, 31, 40, 27, 22, 23, 25, 13, 6, 11,
        39, 18, 20, 8, 14, 21, 32, 26, 2, 30,
        7, 4, 17, 5, 3, 28, 34, 37, 12, 36,
    )
    if len(arg1) != len(positions) or not re.fullmatch(r"[0-9A-Fa-f]{40}", arg1):
        raise ValueError("invalid WAF challenge")

    reordered = [""] * len(positions)
    for index, character in enumerate(arg1):
        for output_index, position in enumerate(positions):
            if position == index + 1:
                reordered[output_index] = character
                break

    boxed = "".join(reordered)
    return "".join(
        format(int(boxed[index:index + 2], 16) ^ int(password[index:index + 2], 16), "02x")
        for index in range(0, len(password), 2)
    )


def challenge_cookie(body: bytes) -> str | None:
    """Return a non-secret WAF compatibility cookie for a detected challenge."""

    match = _ACW_CHALLENGE.search(body[:4000])
    if match is None:
        return None
    return "acw_sc__v2=" + acw_sc_v2(match.group(1).decode("ascii"))


def decoded_body(body: bytes, content_encoding: str, max_bytes: int) -> bytes:
    """Decode a response body without permitting unbounded gzip expansion."""

    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    if content_encoding.lower().strip() == "gzip":
        with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:
            decoded = stream.read(max_bytes + 1)
    else:
        decoded = body
    if len(decoded) > max_bytes:
        raise ResponseTooLargeError("response too large")
    return decoded
