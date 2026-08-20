import ast
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from crypto_desk.models import HttpResponse
import crypto_desk.sources as sources_module
from crypto_desk.sources import (
    SOURCE_DEFINITIONS,
    SourceClient,
    SourceError,
    fetch_sources,
    fetch_sources_with_status,
)
from tests.helpers import RecordingTransport, sample_news


FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)
CHALLENGE = b"<script>var arg1='0123456789ABCDEF0123456789ABCDEF01234567'</script>"


class SourceTests(unittest.TestCase):
    def test_source_module_does_not_use_python_310_zip_strict_keyword(self):
        source_path = Path(sources_module.__file__)
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        strict_zip_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "zip"
            and any(keyword.arg == "strict" for keyword in node.keywords)
        ]
        self.assertEqual(strict_zip_calls, [])

    def test_endpoint_format_length_mismatch_is_sanitized_before_request(self):
        transport = RecordingTransport([])
        with patch.object(sources_module, "_SOURCE_FORMATS", {"binance": ()}):
            with self.assertRaisesRegex(
                SourceError, "source configuration invalid"
            ) as caught:
                SourceClient(transport).fetch("binance", NOW)
        self.assertNotIn("binance.com", str(caught.exception))
        self.assertEqual(transport.requests, [])

    def test_each_default_source_parses_two_records(self):
        fixture_names = {
            "panews": "panews.xml",
            "binance": "binance.json",
            "odaily": "odaily.xml",
            "ctcn": "ctcn.xml",
            "techflow": "techflow.json",
            "catcher": "catcher.xml",
        }
        for source, fixture in fixture_names.items():
            with self.subTest(source=source):
                body = (FIXTURES / fixture).read_bytes()
                transport = RecordingTransport([HttpResponse(200, {}, body)])
                items = SourceClient(transport).fetch(source, NOW)
                self.assertEqual(len(items), 2)
                self.assertTrue(all(item.title for item in items))
                self.assertTrue(all(item.source for item in items))

    def test_one_source_failure_does_not_discard_successful_sources(self):
        class FakeClient:
            def fetch(self, source, now):
                if source == "panews":
                    raise RuntimeError("failed")
                return (sample_news(source="Binance"),)

        items = fetch_sources(("panews", "binance"), FakeClient(), NOW)
        self.assertEqual([item.source for item in items], ["Binance"])

    def test_status_result_identifies_each_successful_source(self):
        class FakeClient:
            def fetch(self, source, now):
                if source == "binance":
                    raise RuntimeError("failed")
                return (sample_news(source="PANews"),)

        result = fetch_sources_with_status(
            ("panews", "binance"), FakeClient(), NOW
        )
        self.assertEqual(tuple(result.by_source), ("panews",))
        self.assertEqual(result.by_source["panews"][0].source, "PANews")

    def test_all_source_failures_raise_without_exposing_details(self):
        class FailingClient:
            def fetch(self, source, now):
                raise RuntimeError("private-response-body")

        with self.assertRaisesRegex(SourceError, "all sources failed") as caught:
            fetch_sources(("panews", "binance"), FailingClient(), NOW)
        self.assertNotIn("private-response-body", str(caught.exception))

    def test_techflow_waf_challenge_retries_once_with_cookie(self):
        success = (FIXTURES / "techflow.json").read_bytes()
        transport = RecordingTransport([
            HttpResponse(200, {}, CHALLENGE),
            HttpResponse(200, {}, success),
        ])
        items = SourceClient(transport).fetch("techflow", NOW)
        self.assertEqual(len(items), 2)
        self.assertIn("Cookie", transport.requests[1].headers)

    def test_panews_primary_requires_rss_before_json_fallback(self):
        fallback = json.dumps({
            "data": [{
                "id": 1,
                "publishTime": 1787202000000,
                "title": "PANews JSON fallback",
            }],
        }).encode()
        transport = RecordingTransport([
            HttpResponse(200, {}, fallback),
            HttpResponse(200, {}, fallback),
        ])
        items = SourceClient(transport).fetch("panews", NOW)
        self.assertEqual([item.title for item in items], ["PANews JSON fallback"])
        self.assertEqual(len(transport.requests), 2)

    def test_rss_requires_rss_channel_envelope(self):
        body = (
            b"<unrelated><item><title>incompatible</title>"
            b"<pubDate>Thu, 20 Aug 2026 05:00:00 GMT</pubDate>"
            b"</item></unrelated>"
        )
        transport = RecordingTransport([HttpResponse(200, {}, body)])
        with self.assertRaisesRegex(SourceError, "source parse failed") as caught:
            SourceClient(transport).fetch("odaily", NOW)
        self.assertNotIn("incompatible", str(caught.exception))

    def test_json_only_source_rejects_well_formed_rss(self):
        body = (FIXTURES / "panews.xml").read_bytes()
        transport = RecordingTransport([HttpResponse(200, {}, body)])
        with self.assertRaisesRegex(SourceError, "source parse failed"):
            SourceClient(transport).fetch("binance", NOW)
        self.assertEqual(len(transport.requests), 1)

    def test_non_techflow_challenge_does_not_retry(self):
        success = (FIXTURES / "binance.json").read_bytes()
        transport = RecordingTransport([
            HttpResponse(200, {}, CHALLENGE),
            HttpResponse(200, {}, success),
        ])
        with self.assertRaisesRegex(SourceError, "source parse failed"):
            SourceClient(transport).fetch("binance", NOW)
        self.assertEqual(len(transport.requests), 1)

    def test_repeated_techflow_challenge_stops_after_one_retry(self):
        transport = RecordingTransport([
            HttpResponse(200, {}, CHALLENGE),
            HttpResponse(200, {}, CHALLENGE),
        ])
        with self.assertRaisesRegex(SourceError, "source fetch failed") as caught:
            SourceClient(transport).fetch("techflow", NOW)
        self.assertNotIn("arg1", str(caught.exception))
        self.assertEqual(len(transport.requests), 2)

    def test_gzip_decompression_limit_is_enforced(self):
        compressed = gzip.compress(b"x" * 65)
        transport = RecordingTransport([
            HttpResponse(200, {"Content-Encoding": "gzip"}, compressed)
        ])
        with self.assertRaisesRegex(SourceError, "source response too large"):
            SourceClient(transport, max_decompressed_bytes=64).fetch("panews", NOW)

    def test_invalid_rss_and_json_raise_sanitized_source_error(self):
        cases = (("panews", b"<rss>"), ("binance", b"{not-json"))
        for source, body in cases:
            with self.subTest(source=source):
                transport = RecordingTransport([HttpResponse(200, {}, body)])
                with self.assertRaisesRegex(SourceError, "source parse failed") as caught:
                    SourceClient(transport).fetch(source, NOW)
                self.assertNotIn(body.decode("utf-8"), str(caught.exception))

    def test_missing_title_is_filtered_and_bad_timestamp_uses_now(self):
        body = (
            b"<rss><channel>"
            b"<item><guid>missing</guid><description>x</description></item>"
            b"<item><guid>kept</guid><title>kept</title><pubDate>bad</pubDate></item>"
            b"</channel></rss>"
        )
        items = SourceClient(
            RecordingTransport([HttpResponse(200, {}, body)])
        ).fetch("panews", NOW)
        self.assertEqual([item.title for item in items], ["kept"])
        self.assertEqual(items[0].published_at, NOW)

    def test_rss_guid_is_preserved_and_namespaced_across_title_changes(self):
        first = (
            b"<rss><channel><item><guid>stable-guid-1</guid>"
            b"<title>first title</title><link>https://news.example/a</link>"
            b"</item></channel></rss>"
        )
        second = first.replace(b"first title", b"renamed title")
        first_item = SourceClient(
            RecordingTransport([HttpResponse(200, {}, first)])
        ).fetch("panews", NOW)[0]
        second_item = SourceClient(
            RecordingTransport([HttpResponse(200, {}, second)])
        ).fetch("panews", NOW)[0]
        self.assertEqual(first_item.source_id, "panews:guid:stable-guid-1")
        self.assertEqual(second_item.source_id, first_item.source_id)

    def test_rss_uses_namespaced_link_then_title_as_identity_fallbacks(self):
        with_link = (
            b"<rss><channel><item><title>linked</title>"
            b"<link>https://news.example/stable</link></item></channel></rss>"
        )
        title_only = (
            b"<rss><channel><item><title>title only</title>"
            b"</item></channel></rss>"
        )
        linked_item = SourceClient(
            RecordingTransport([HttpResponse(200, {}, with_link)])
        ).fetch("odaily", NOW)[0]
        title_item = SourceClient(
            RecordingTransport([HttpResponse(200, {}, title_only)])
        ).fetch("odaily", NOW)[0]
        self.assertEqual(
            linked_item.source_id,
            "odaily:link:https://news.example/stable",
        )
        self.assertTrue(title_item.source_id.startswith("odaily:title:"))

    def test_json_source_ids_are_namespaced_by_configured_source_key(self):
        body = (FIXTURES / "binance.json").read_bytes()
        item = SourceClient(
            RecordingTransport([HttpResponse(200, {}, body)])
        ).fetch("binance", NOW)[0]
        self.assertTrue(item.source_id.startswith("binance:"))

    def test_source_urls_are_fixed_https_and_each_source_is_capped_at_thirty(self):
        self.assertTrue(all(
            url.startswith("https://")
            for definition in SOURCE_DEFINITIONS.values()
            for url in definition.urls
        ))
        articles = [
            {"code": str(index), "releaseDate": 1787202000000, "title": str(index)}
            for index in range(31)
        ]
        body = json.dumps({"data": {"catalogs": [{"articles": articles}]}}).encode()
        items = SourceClient(
            RecordingTransport([HttpResponse(200, {}, body)])
        ).fetch("binance", NOW)
        self.assertEqual(len(items), 30)

    def test_optional_sources_parse_only_when_explicitly_requested(self):
        payloads = {
            "jinse": {
                "list": [{"lives": [{
                    "id": 1,
                    "created_at": 1787202000,
                    "content": "【金色测试】正文",
                    "link": "https://news.example/jinse/1",
                }]}],
            },
            "blockbeats": {
                "data": {"data": [{
                    "id": 2,
                    "create_time": 1787202000,
                    "title": "律动测试",
                    "content": "正文",
                    "link": "https://news.example/blockbeats/2",
                }]},
            },
        }
        for source, payload in payloads.items():
            with self.subTest(source=source):
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                items = SourceClient(
                    RecordingTransport([HttpResponse(200, {}, body)])
                ).fetch(source, NOW)
                self.assertEqual(len(items), 1)


if __name__ == "__main__":
    unittest.main()
