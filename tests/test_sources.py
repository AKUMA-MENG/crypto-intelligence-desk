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
    def test_every_json_adapter_gives_idless_records_distinct_stable_ids(self):
        payloads = {
            "jinse": {
                "list": [{"lives": [
                    {"content": "【同标题】正文 A"},
                    {"content": "【同标题】正文 B"},
                ]}],
            },
            "blockbeats": {
                "data": {"data": [
                    {"title": "同标题", "content": "正文 A"},
                    {"title": "同标题", "content": "正文 B"},
                ]},
            },
            "panews": {
                "data": [
                    {"title": "同标题", "desc": "正文 A"},
                    {"title": "同标题", "desc": "正文 B"},
                ],
            },
            "binance": {
                "data": {"articles": [
                    {"title": "公告 A"},
                    {"title": "公告 B"},
                ]},
            },
            "odaily": {
                "data": {"items": [
                    {"title": "同标题", "content": "正文 A"},
                    {"title": "同标题", "content": "正文 B"},
                ]},
            },
            "techflow": {
                "data": [
                    {"title": "同标题", "abstract": "正文 A"},
                    {"title": "同标题", "abstract": "正文 B"},
                ],
            },
        }
        for source_key, payload in payloads.items():
            with self.subTest(source=source_key):
                definition = SOURCE_DEFINITIONS[source_key]
                first_poll = sources_module._parse_json(
                    source_key, definition, payload, NOW
                )
                second_poll = sources_module._parse_json(
                    source_key, definition, payload, NOW
                )
                self.assertEqual(len(first_poll), 2)
                self.assertEqual(len({item.source_id for item in first_poll}), 2)
                self.assertEqual(
                    [item.source_id for item in second_poll],
                    [item.source_id for item in first_poll],
                )
                self.assertTrue(all(
                    item.source_id.startswith(source_key + ":json:")
                    for item in first_poll
                ))

    def test_every_json_adapter_preserves_normal_provider_id_behavior(self):
        cases = {
            "jinse": (
                {"list": [{"lives": [{"id": 101, "content": "标题"}]}]},
                "jinse:json:js101",
            ),
            "blockbeats": (
                {"data": {"data": [{"id": 102, "title": "标题"}]}},
                "blockbeats:json:bb102",
            ),
            "panews": (
                {"data": [{"id": 103, "title": "标题"}]},
                "panews:json:pa103",
            ),
            "binance": (
                {"data": {"articles": [{"code": 104, "title": "标题"}]}},
                "binance:json:bn104",
            ),
            "odaily": (
                {"data": {"items": [{"id": 105, "title": "标题"}]}},
                "odaily:json:od105",
            ),
            "techflow": (
                {"data": [{"id": 106, "title": "标题"}]},
                "techflow:json:tf106",
            ),
        }
        for source_key, (payload, expected) in cases.items():
            with self.subTest(source=source_key):
                items = sources_module._parse_json(
                    source_key, SOURCE_DEFINITIONS[source_key], payload, NOW
                )
                self.assertEqual(items[0].source_id, expected)

    def test_idless_long_links_are_hashed_before_bounded_item_storage(self):
        shared_prefix = "https://news.example/" + ("x" * 600)
        payload = {
            "data": {"data": [
                {
                    "title": "同标题",
                    "content": "相同正文",
                    "link": shared_prefix + "-first",
                },
                {
                    "title": "同标题",
                    "content": "相同正文",
                    "link": shared_prefix + "-second",
                },
            ]},
        }
        definition = SOURCE_DEFINITIONS["blockbeats"]
        first_poll = sources_module._parse_json(
            "blockbeats", definition, payload, NOW
        )
        second_poll = sources_module._parse_json(
            "blockbeats", definition, payload, NOW
        )
        self.assertEqual(len({item.source_id for item in first_poll}), 2)
        self.assertEqual(
            [item.source_id for item in second_poll],
            [item.source_id for item in first_poll],
        )
        self.assertTrue(all(
            item.source_id.startswith("blockbeats:json:link-sha256:")
            and len(item.source_id) <= sources_module.MAX_SOURCE_ID_LENGTH
            for item in first_poll
        ))

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

    def test_panews_uses_current_final_rss_without_redirect_fallback(self):
        self.assertEqual(
            SOURCE_DEFINITIONS["panews"].urls,
            ("https://www.panewslab.com/rss.xml?lang=zh&featured=true",),
        )
        self.assertEqual(sources_module._SOURCE_FORMATS["panews"], ("rss",))

    def test_default_limit_accepts_current_catcher_sized_gzip(self):
        large_description = "x" * 2262367
        body = (
            "<rss><channel><item><guid>cc-large</guid><title>链捕手测试</title>"
            "<description>%s</description></item></channel></rss>"
            % large_description
        ).encode("utf-8")
        transport = RecordingTransport([
            HttpResponse(200, {"Content-Encoding": "gzip"}, gzip.compress(body))
        ])
        self.assertEqual(len(SourceClient(transport).fetch("catcher", NOW)), 1)

    def test_default_limit_rejects_gzip_over_three_mib(self):
        oversized = b"x" * (3 * 1024 * 1024 + 1)
        transport = RecordingTransport([
            HttpResponse(
                200,
                {"Content-Encoding": "gzip"},
                gzip.compress(oversized),
            )
        ])
        with self.assertRaisesRegex(SourceError, "source response too large"):
            SourceClient(transport).fetch("catcher", NOW)

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
