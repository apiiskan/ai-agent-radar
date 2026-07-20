import json

from ai_agent_radar.config import FeedConfig
from ai_agent_radar.news import collect_news
from ai_agent_radar.normalize import canonicalize_url


def test_canonicalize_url_removes_tracking_and_fragment() -> None:
    url = "https://OpenAI.com/news/codex/?utm_source=x&ref=home#top"

    assert canonicalize_url(url) == "https://openai.com/news/codex"


def test_canonicalize_url_preserves_blank_non_tracking_query_values() -> None:
    url = "https://OpenAI.com/news/codex/?edition=&utm_source=x&ref=home&source=rss#top"

    assert canonicalize_url(url) == "https://openai.com/news/codex?edition="


def test_collect_news_dedupes_and_preserves_failed_source() -> None:
    feed = b"""<rss><channel><item><title>Codex update</title>
    <link>https://openai.com/news/codex?utm_source=rss</link>
    <pubDate>Sun, 19 Jul 2026 00:00:00 GMT</pubDate><description>New release</description>
    </item></channel></rss>"""
    feeds = [
        FeedConfig(name="OpenAI", url="https://ok/feed", tier="official"),
        FeedConfig(name="Broken", url="https://bad/feed", tier="trusted"),
    ]

    def fetch(url: str) -> bytes:
        if "bad" in url:
            raise TimeoutError("timeout")
        return feed

    result = collect_news(feeds, fetch)

    assert len(result.items) == 1
    assert result.items[0].canonical_url == "https://openai.com/news/codex"
    assert [status.ok for status in result.statuses] == [True, False]


def test_nonempty_malformed_rss_degrades_source_instead_of_reporting_healthy_empty() -> None:
    source = FeedConfig(
        name="Malformed", url="https://example.com/feed", tier="trusted", kind="rss"
    )

    result = collect_news([source], lambda url: b"<rss><channel><item>")

    assert result.items == ()
    assert result.statuses[0].ok is False
    assert result.statuses[0].error in {"ParseError", "ValueError"}


def test_structurally_valid_empty_rss_remains_healthy() -> None:
    source = FeedConfig(
        name="Empty", url="https://example.com/feed", tier="trusted", kind="rss"
    )

    result = collect_news(
        [source], lambda url: b"<rss version='2.0'><channel></channel></rss>"
    )

    assert result.items == ()
    assert result.statuses[0].ok is True
    assert result.statuses[0].item_count == 0


def test_structurally_valid_rss_with_only_invalid_entries_degrades_source() -> None:
    source = FeedConfig(
        name="Invalid entries",
        url="https://example.com/feed",
        tier="trusted",
        kind="rss",
    )
    payload = b"""<rss version='2.0'><channel>
    <item><title>Missing link and date</title></item>
    </channel></rss>"""

    result = collect_news([source], lambda url: payload)

    assert result.items == ()
    assert result.statuses[0].ok is False
    assert result.statuses[0].error == "ValueError"


def test_collect_news_discards_non_http_relative_or_hostless_rss_links() -> None:
    feed = b"""<rss><channel>
    <item><title>Valid</title><link>https://openai.com/news/valid</link><pubDate>Sun, 19 Jul 2026 00:00:00 GMT</pubDate></item>
    <item><title>Script</title><link>javascript:alert(1)</link><pubDate>Sun, 19 Jul 2026 00:00:00 GMT</pubDate></item>
    <item><title>Mail</title><link>mailto:news@example.com</link><pubDate>Sun, 19 Jul 2026 00:00:00 GMT</pubDate></item>
    <item><title>Relative</title><link>/news/relative</link><pubDate>Sun, 19 Jul 2026 00:00:00 GMT</pubDate></item>
    <item><title>User only</title><link>https://user@/article</link><pubDate>Sun, 19 Jul 2026 00:00:00 GMT</pubDate></item>
    <item><title>Port only</title><link>https://:443/article</link><pubDate>Sun, 19 Jul 2026 00:00:00 GMT</pubDate></item>
    </channel></rss>"""
    source = FeedConfig(name="OpenAI", url="https://openai.com/feed", tier="official")

    result = collect_news([source], lambda url: feed)

    assert [(item.title, item.canonical_url) for item in result.items] == [
        ("Valid", "https://openai.com/news/valid")
    ]


def test_collect_news_extracts_dated_official_html_articles() -> None:
    html = b"""<main><article><a href='/news/grok-5'>Grok 5</a>
    <time datetime='2026-07-19T08:00:00Z'>July 19</time><p>Agent update</p></article></main>"""
    source = FeedConfig(name="xAI", url="https://x.ai/news", tier="official", kind="html")

    result = collect_news([source], lambda url: html)

    assert result.items[0].canonical_url == "https://x.ai/news/grok-5"
    assert result.items[0].title == "Grok 5"


def test_html_error_page_and_layout_drift_degrade_source() -> None:
    source = FeedConfig(
        name="Official", url="https://example.com/news", tier="official", kind="html"
    )

    error_page = collect_news(
        [source],
        lambda url: b"<html><head><title>500 Server Error</title></head><body>failed</body></html>",
    )
    layout_drift = collect_news(
        [source],
        lambda url: b"<html><body><main><div><a href='/new'>New release</a></div></main></body></html>",
    )

    assert error_page.statuses[0].ok is False
    assert layout_drift.statuses[0].ok is False


def test_structurally_valid_empty_html_page_remains_healthy() -> None:
    source = FeedConfig(
        name="Official", url="https://example.com/news", tier="official", kind="html"
    )

    result = collect_news(
        [source], lambda url: b"<html><body><main></main></body></html>"
    )

    assert result.items == ()
    assert result.statuses[0].ok is True


def test_collect_news_maps_github_release_fields() -> None:
    payload = json.dumps(
        [
            {
                "tag_name": "v2.1.215",
                "name": "Claude Code 2.1.215",
                "html_url": "https://github.com/anthropics/claude-code/releases/tag/v2.1.215",
                "published_at": "2026-07-19T20:00:00Z",
                "body": "Release notes",
            },
            {
                "tag_name": "v2.1.214",
                "name": "",
                "html_url": "https://github.com/anthropics/claude-code/releases/tag/v2.1.214",
                "published_at": "2026-07-18T20:00:00Z",
                "body": None,
            },
        ]
    ).encode()
    source = FeedConfig(
        name="Anthropic Releases",
        url="https://api.github.com/repos/anthropics/claude-code/releases?per_page=30",
        tier="official",
        kind="github_releases",
    )

    result = collect_news([source], lambda url: payload)

    assert [(item.title, item.summary) for item in result.items] == [
        ("v2.1.214", ""),
        ("Claude Code 2.1.215", "Release notes"),
    ]
    assert result.items[1].canonical_url.endswith("/releases/tag/v2.1.215")
    assert result.items[1].published_at.isoformat() == "2026-07-19T20:00:00+00:00"
    assert result.statuses[0].ok is True
    assert result.statuses[0].item_count == 2


def test_collect_news_degrades_invalid_github_release_payload_to_source_failure() -> None:
    source = FeedConfig(
        name="xAI Releases",
        url="https://api.github.com/repos/xai-org/xai-sdk-python/releases",
        tier="official",
        kind="github_releases",
    )

    result = collect_news([source], lambda url: b'{"message":"rate limited"}')

    assert result.items == ()
    assert result.statuses[0].ok is False
    assert result.statuses[0].error == "ValueError"


def test_collect_news_treats_nonempty_release_array_without_valid_records_as_failure() -> None:
    source = FeedConfig(
        name="Kimi Releases",
        url="https://api.github.com/repos/MoonshotAI/kimi-cli/releases",
        tier="official",
        kind="github_releases",
    )
    payload = json.dumps(
        [
            {"tag_name": "missing URL and date"},
            "not an object",
        ]
    ).encode()

    result = collect_news([source], lambda url: payload)

    assert result.items == ()
    assert result.statuses[0].ok is False
    assert result.statuses[0].error == "ValueError"


def test_collect_news_allows_empty_github_release_array() -> None:
    source = FeedConfig(
        name="Kimi Releases",
        url="https://api.github.com/repos/MoonshotAI/kimi-cli/releases",
        tier="official",
        kind="github_releases",
    )

    result = collect_news([source], lambda url: b"[]")

    assert result.items == ()
    assert result.statuses[0].ok is True
    assert result.statuses[0].item_count == 0


def test_collect_news_keeps_only_http_links_on_configured_html_domain() -> None:
    html = b"""<main>
    <article><a href='https://x.ai/news/valid'>Valid</a><time datetime='2026-07-19T08:00:00Z'>July 19</time></article>
    <article><a href='https://evil.example/news'>External</a><time datetime='2026-07-19T08:00:00Z'>July 19</time></article>
    <article><a href='javascript:alert(1)'>Script</a><time datetime='2026-07-19T08:00:00Z'>July 19</time></article>
    </main>"""
    source = FeedConfig(name="xAI", url="https://X.AI/news", tier="official", kind="html")

    result = collect_news([source], lambda url: html)

    assert [item.title for item in result.items] == ["Valid"]


def test_collect_news_merges_same_event_and_keeps_official_primary() -> None:
    official = b"""<rss><channel><item><title>Claude Code launches agent teams</title>
    <link>https://anthropic.com/news/agent-teams</link><pubDate>Sun, 19 Jul 2026 00:00:00 GMT</pubDate>
    </item></channel></rss>"""
    trusted = official.replace(
        b"https://anthropic.com/news/agent-teams", b"https://example.com/claude-agent-teams"
    )
    sources = [
        FeedConfig(name="Anthropic", url="https://official", tier="official"),
        FeedConfig(name="Industry", url="https://trusted", tier="trusted"),
    ]

    result = collect_news(sources, lambda url: official if url.endswith("official") else trusted)

    assert len(result.items) == 1
    assert result.items[0].source == "Anthropic"
    assert result.items[0].related_urls == ("https://example.com/claude-agent-teams",)


def test_collect_news_does_not_merge_events_more_than_two_days_apart() -> None:
    official = b"""<rss><channel><item><title>Agent teams launch</title>
    <link>https://anthropic.com/news/agent-teams</link><pubDate>Sun, 19 Jul 2026 00:00:00 GMT</pubDate>
    </item></channel></rss>"""
    trusted = official.replace(
        b"https://anthropic.com/news/agent-teams</link><pubDate>Sun, 19 Jul 2026 00:00:00 GMT",
        b"https://example.com/agent-teams</link><pubDate>Tue, 21 Jul 2026 23:59:00 GMT",
    )
    sources = [
        FeedConfig(name="Anthropic", url="https://official", tier="official"),
        FeedConfig(name="Industry", url="https://trusted", tier="trusted"),
    ]

    result = collect_news(sources, lambda url: official if url.endswith("official") else trusted)

    assert len(result.items) == 2


def test_collect_news_merges_events_exactly_two_days_apart() -> None:
    official = b"""<rss><channel><item><title>Agent teams launch</title>
    <link>https://anthropic.com/news/agent-teams</link><pubDate>Sun, 19 Jul 2026 00:00:00 GMT</pubDate>
    </item></channel></rss>"""
    trusted = official.replace(
        b"https://anthropic.com/news/agent-teams</link><pubDate>Sun, 19 Jul 2026 00:00:00 GMT",
        b"https://example.com/agent-teams</link><pubDate>Tue, 21 Jul 2026 00:00:00 GMT",
    )
    sources = [
        FeedConfig(name="Anthropic", url="https://official", tier="official"),
        FeedConfig(name="Industry", url="https://trusted", tier="trusted"),
    ]

    result = collect_news(sources, lambda url: official if url.endswith("official") else trusted)

    assert len(result.items) == 1
    assert result.items[0].related_urls == ("https://example.com/agent-teams",)
