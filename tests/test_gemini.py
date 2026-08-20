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


if __name__ == "__main__":
    unittest.main()
