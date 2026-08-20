from datetime import datetime, timezone

from crypto_desk.gemini import GeminiRetryableError
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


def sample_news(source="PANews", title="重大新闻"):
    return NewsItem(
        source_id="sample-1",
        source=source,
        title=title,
        body="用于测试的新闻正文。",
        url="https://news.example/item/1",
        published_at=datetime(2026, 8, 20, 5, 59, tzinfo=timezone.utc),
    )
