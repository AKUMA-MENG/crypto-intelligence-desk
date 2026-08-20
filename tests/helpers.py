from datetime import datetime, timezone

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


def sample_news(source="PANews", title="重大新闻"):
    return NewsItem(
        source_id="sample-1",
        source=source,
        title=title,
        body="用于测试的新闻正文。",
        url="https://news.example/item/1",
        published_at=datetime(2026, 8, 20, 5, 59, tzinfo=timezone.utc),
    )
