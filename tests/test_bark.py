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

    def test_http_500_returns_false(self):
        transport = RecordingTransport([HttpResponse(500, {}, b'{"code":200}')])
        self.assertFalse(BarkClient("fake-bark-secret", transport).send("x", "title"))

    def test_non_json_success_response_returns_false(self):
        transport = RecordingTransport([HttpResponse(200, {}, b"not-json")])
        self.assertFalse(BarkClient("fake-bark-secret", transport).send("x", "title"))


if __name__ == "__main__":
    unittest.main()
