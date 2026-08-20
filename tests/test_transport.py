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
