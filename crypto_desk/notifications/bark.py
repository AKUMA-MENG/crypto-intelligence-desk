import json
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

from crypto_desk.models import HttpRequest


BARK_API_BASE = "https://api.day.app"
BARK_TIMEOUT_SECONDS = 15
SHANGHAI_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def bark_url(push_key):
    return "%s/%s" % (BARK_API_BASE, quote(push_key, safe=""))


def format_bark_body(text, now=None):
    instant = now or datetime.now(timezone.utc)
    pushed_at = instant.astimezone(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M:%S")
    return "内容：\n%s\n\n推送时间：%s" % (str(text), pushed_at)


class BarkClient:
    def __init__(self, push_key, transport, logger=None):
        self.push_key = push_key
        self.transport = transport
        self.logger = logger or logging.getLogger(__name__)

    def send(self, text, title, now=None):
        if not self.push_key:
            return False

        try:
            body = urlencode(
                {"title": title, "body": format_bark_body(text, now)}
            ).encode("utf-8")
            request = HttpRequest(
                method="POST",
                url=bark_url(self.push_key),
                headers={
                    "Content-Type": "application/x-www-form-urlencoded; charset=utf-8"
                },
                body=body,
                timeout_seconds=BARK_TIMEOUT_SECONDS,
            )
            response = self.transport.send(request)
            payload = json.loads(response.body)
            if not (200 <= response.status < 300 and payload.get("code") == 200):
                self.logger.error("Bark delivery failed")
                return False
            return True
        except Exception:
            self.logger.error("Bark delivery failed")
            return False
