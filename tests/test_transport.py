import io
import unittest
from unittest.mock import MagicMock
from urllib.error import HTTPError

from crypto_desk.models import HttpRequest
from crypto_desk.transport import (
    TransportError,
    UrllibTransport,
    _RejectRedirectHandler,
)


class TransportTests(unittest.TestCase):
    def opener(self, *, response=None, error=None):
        opener = MagicMock()
        if error is not None:
            opener.open.side_effect = error
        else:
            opener.open.return_value = response
        return opener

    def test_network_error_is_sanitized(self):
        opener = self.opener(
            error=OSError("failed https://secret.example/device-key")
        )
        request = HttpRequest("GET", "https://example.com/", {}, None, 3)
        with self.assertRaisesRegex(TransportError, "upstream request failed") as caught:
            UrllibTransport(opener=opener).send(request)
        self.assertNotIn("device-key", str(caught.exception))

    def test_response_limit_is_enforced(self):
        response = MagicMock()
        response.status = 200
        response.headers = {"Content-Type": "application/json"}
        response.read.return_value = b"x" * 9
        response.__enter__.return_value = response
        opener = self.opener(response=response)
        request = HttpRequest("GET", "https://example.com/", {}, None, 3)
        with self.assertRaisesRegex(TransportError, "response too large"):
            UrllibTransport(max_response_bytes=8, opener=opener).send(request)

    def test_http_error_is_returned_as_bounded_response(self):
        opener = self.opener(error=HTTPError(
            "https://example.com/",
            429,
            "rate limited",
            {"Content-Type": "application/json"},
            io.BytesIO(b'{"error":"rate"}'),
        ))
        response = UrllibTransport(max_response_bytes=64, opener=opener).send(
            HttpRequest("GET", "https://example.com/", {}, None, 3)
        )
        self.assertEqual(response.status, 429)
        self.assertEqual(response.body, b'{"error":"rate"}')

    def test_http_error_body_read_failure_is_sanitized(self):
        body = MagicMock()
        body.read.side_effect = OSError(
            "read failed https://secret.example/device-key"
        )
        opener = self.opener(error=HTTPError(
            "https://secret.example/device-key",
            502,
            "upstream failed",
            {"Content-Type": "application/json"},
            body,
        ))
        request = HttpRequest("GET", "https://example.com/", {}, None, 3)
        with self.assertRaises(TransportError) as caught:
            UrllibTransport(opener=opener).send(request)
        self.assertEqual(str(caught.exception), "upstream request failed")
        self.assertNotIn("device-key", str(caught.exception))
        self.assertNotIn("https://secret.example/device-key", str(caught.exception))

    def test_redirect_handler_never_constructs_a_follow_up_request(self):
        handler = _RejectRedirectHandler()
        self.assertIsNone(handler.redirect_request(
            MagicMock(), MagicMock(), 302, "redirect", {},
            "https://other.example/collect",
        ))

    def test_redirect_is_rejected_without_following_or_leaking_headers(self):
        opener = self.opener(error=HTTPError(
            "https://api.example/original",
            302,
            "redirect",
            {"Location": "http://private.example/collect"},
            io.BytesIO(b"redirect"),
        ))
        request = HttpRequest(
            "POST",
            "https://api.example/original",
            {
                "x-goog-api-key": "test-sensitive-value",
                "Cookie": "test-cookie",
            },
            b"{}",
            3,
        )
        with self.assertRaisesRegex(
            TransportError, "^upstream request failed$"
        ) as caught:
            UrllibTransport(opener=opener).send(request)
        self.assertEqual(opener.open.call_count, 1)
        self.assertNotIn("private.example", str(caught.exception))
        self.assertNotIn("test-sensitive-value", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
