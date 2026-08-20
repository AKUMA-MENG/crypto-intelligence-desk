import urllib.error
import urllib.request
from typing import Mapping

from crypto_desk.models import HttpRequest, HttpResponse


class TransportError(Exception):
    """Raised when an upstream request cannot safely return a response."""


class UrllibTransport:
    def __init__(self, max_response_bytes: int = 2 * 1024 * 1024):
        self.max_response_bytes = max_response_bytes

    def send(self, request: HttpRequest) -> HttpResponse:
        urllib_request = urllib.request.Request(
            request.url,
            data=request.body,
            headers=dict(request.headers),
            method=request.method,
        )
        try:
            with urllib.request.urlopen(urllib_request, timeout=request.timeout_seconds) as response:
                return self._response(response, response.status)
        except urllib.error.HTTPError as error:
            try:
                return self._response(error, error.code)
            finally:
                error.close()
        except TransportError:
            raise
        except (OSError, urllib.error.URLError):
            raise TransportError("upstream request failed") from None

    def _response(self, response, status: int) -> HttpResponse:
        body = response.read(self.max_response_bytes + 1)
        if len(body) > self.max_response_bytes:
            raise TransportError("upstream response too large")
        headers: Mapping[str, str] = dict(response.headers.items()) if response.headers else {}
        return HttpResponse(status=status, headers=headers, body=body)
