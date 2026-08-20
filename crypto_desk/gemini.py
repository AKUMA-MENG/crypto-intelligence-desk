"""Strict, transport-injected Gemini news analysis client."""

from __future__ import annotations

import json
import re
from datetime import timezone
from urllib.parse import quote

from crypto_desk.models import Analysis, HttpRequest


GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_TIMEOUT_SECONDS = 30

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

_DIRECTIONS = frozenset(("利好", "利空", "中性"))
_HORIZONS = frozenset(("正面", "负面", "中性"))
_CATEGORIES = frozenset(("监管", "ETF", "宏观", "安全", "上币", "解锁", "机构", "技术", "生态", "其他"))
_COIN_CODE = re.compile(r"[A-Za-z0-9]{1,8}\Z")


class GeminiRetryableError(Exception):
    """The Gemini request or response may succeed on a later attempt."""


class GeminiPermanentError(Exception):
    """The Gemini request cannot succeed without changing its configuration."""


def analysis_prompt(item, review=False):
    """Create the independent primary or review prompt for one news item."""
    published_at = item.published_at.astimezone(timezone.utc).isoformat()
    cleaned_body = " ".join(str(item.body).split())[:800]
    prompt = (
        "%s\n\n来源：%s\n时间：%s\n标题：%s\n内容：%s"
        % (OUTPUT_CONTRACT, item.source, published_at, item.title, cleaned_body)
    )
    return prompt + (REVIEW_SUFFIX if review else "")


def _first_json_object(text):
    if not isinstance(text, str):
        raise ValueError("not text")
    without_fences = re.sub(r"(?im)^\s*```(?:json)?\s*$", "", text).strip()
    start = without_fences.find("{")
    if start < 0:
        raise ValueError("no object")
    payload, _ = json.JSONDecoder().raw_decode(without_fences[start:])
    if not isinstance(payload, dict):
        raise ValueError("not object")
    return payload


def _invalid_analysis():
    raise GeminiRetryableError("invalid Gemini analysis")


def parse_analysis(text):
    """Parse the first Gemini JSON object into a safe, normalized analysis."""
    try:
        payload = _first_json_object(text)
        direction = payload.get("direction")
        short_term = payload.get("st")
        long_term = payload.get("lt")
        category = payload.get("cat")
        priced_in = payload.get("priced_in")
        coins_payload = payload.get("coins")
        why = payload.get("why")
        reverse = payload.get("reverse")

        if direction not in _DIRECTIONS or short_term not in _HORIZONS:
            _invalid_analysis()
        if long_term not in _HORIZONS or category not in _CATEGORIES:
            _invalid_analysis()
        if not isinstance(priced_in, bool) or not isinstance(coins_payload, list):
            _invalid_analysis()
        if not isinstance(why, str) or not why.strip():
            _invalid_analysis()
        if not isinstance(reverse, str) or not reverse.strip():
            _invalid_analysis()

        level = min(5, max(1, int(round(float(payload.get("level", 1))))))
        confidence = min(100, max(0, int(round(float(payload.get("conf", 50))))))
        coins = tuple(
            str(coin).upper()
            for coin in coins_payload
            if _COIN_CODE.fullmatch(str(coin))
        )[:5]
        return Analysis(
            direction=direction,
            short_term=short_term,
            long_term=long_term,
            level=level,
            confidence=confidence,
            coins=coins,
            category=category,
            priced_in=priced_in,
            why=why,
            reverse=reverse,
        )
    except GeminiRetryableError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError, OverflowError):
        _invalid_analysis()


class GeminiClient:
    def __init__(self, api_key, transport):
        self.api_key = api_key
        self.transport = transport

    def analyze(self, item, model, review=False):
        """Request one independent Gemini assessment without retrying."""
        url = "%s/models/%s:generateContent" % (
            GEMINI_API_BASE,
            quote(model, safe=""),
        )
        payload = {
            "systemInstruction": {"parts": [{"text": NEWS_SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": analysis_prompt(item, review)}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 1000 if review else 600,
            },
        }
        request = HttpRequest(
            method="POST",
            url=url,
            headers={
                "x-goog-api-key": self.api_key,
                "Content-Type": "application/json; charset=utf-8",
            },
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout_seconds=GEMINI_TIMEOUT_SECONDS,
        )
        try:
            response = self.transport.send(request)
        except Exception:
            raise GeminiRetryableError("Gemini request failed") from None

        if response.status == 429 or response.status >= 500:
            raise GeminiRetryableError("Gemini request failed")
        if 400 <= response.status < 500:
            raise GeminiPermanentError("Gemini request rejected")
        if not 200 <= response.status < 300:
            raise GeminiRetryableError("Gemini request failed")

        try:
            response_payload = json.loads(response.body)
            text = response_payload["candidates"][0]["content"]["parts"][0]["text"]
        except (
            KeyError,
            IndexError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
            json.JSONDecodeError,
        ):
            raise GeminiRetryableError("invalid Gemini response") from None
        return parse_analysis(text)
