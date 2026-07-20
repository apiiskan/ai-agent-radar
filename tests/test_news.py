from ai_agent_radar.config import FeedConfig
from ai_agent_radar.news import collect_news
from ai_agent_radar.normalize import canonicalize_url


def test_canonicalize_url_removes_tracking_and_fragment() -> None:
    url = "https://OpenAI.com/news/codex/?utm_source=x&ref=home#top"

    assert canonicalize_url(url) == "https://openai.com/news/codex"


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


def test_collect_news_extracts_dated_official_html_articles() -> None:
    html = b"""<main><article><a href='/news/grok-5'>Grok 5</a>
    <time datetime='2026-07-19T08:00:00Z'>July 19</time><p>Agent update</p></article></main>"""
    source = FeedConfig(name="xAI", url="https://x.ai/news", tier="official", kind="html")

    result = collect_news([source], lambda url: html)

    assert result.items[0].canonical_url == "https://x.ai/news/grok-5"
    assert result.items[0].title == "Grok 5"


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
