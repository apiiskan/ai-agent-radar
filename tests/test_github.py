import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ai_agent_radar.github import GitHubClient


def test_search_normalizes_repository_and_categories() -> None:
    fixture = Path(__file__).with_name("fixtures") / "github_search.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    client = GitHubClient("token", httpx.Client(transport=transport), now=lambda: datetime.now(timezone.utc))

    repos = client.search("codex agent skill", "codex", per_page=10)

    assert repos[0].repository_id == 42
    assert repos[0].matched_categories == ("codex",)
    assert repos[0].stars == 120


def test_search_turns_rate_limit_into_source_status() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(403, headers={"x-ratelimit-remaining": "0"})
    )
    client = GitHubClient("token", httpx.Client(transport=transport))

    repos, status = client.safe_search("codex", "codex", per_page=10)

    assert repos == []
    assert status.ok is False
    assert "rate limit" in (status.error or "").lower()


def test_collect_continues_after_invalid_json_response(radar_config) -> None:
    fixture = Path(__file__).with_name("fixtures") / "github_search.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    search_queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search/repositories":
            query = request.url.params["q"]
            search_queries.append(query)
            if query == "invalid":
                return httpx.Response(200, content=b"not json")
            return httpx.Response(200, json=payload)
        return httpx.Response(404)

    client = GitHubClient("token", httpx.Client(transport=httpx.MockTransport(handler)))
    config = radar_config.model_copy(update={"queries": {"first": ["invalid"], "later": ["valid"]}})

    collection = client.collect(config)

    assert search_queries == ["invalid", "valid"]
    assert [status.ok for status in collection.statuses] == [False, True]
    assert collection.repositories[0].repository_id == 42
    assert collection.complete is False
    assert collection.queries_total == 2
    assert collection.queries_succeeded == 1


def test_search_turns_malformed_item_into_failed_source_status() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"items": [{"id": 42}]}))
    client = GitHubClient("token", httpx.Client(transport=transport))

    repos, status = client.safe_search("codex", "codex", per_page=10)

    assert repos == []
    assert status.ok is False
    assert status.error == "KeyError"


def test_collect_stops_after_429_rate_limit(radar_config) -> None:
    search_queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        search_queries.append(request.url.params["q"])
        return httpx.Response(429)

    client = GitHubClient("token", httpx.Client(transport=httpx.MockTransport(handler)))
    config = radar_config.model_copy(update={"queries": {"general": ["first", "later"]}})

    collection = client.collect(config)

    assert search_queries == ["first"]
    assert collection.statuses[0].ok is False
    assert client.limited is True
    assert collection.complete is False
    assert collection.queries_total == 2
    assert collection.queries_succeeded == 0


def test_collect_stops_after_403_with_retry_after(radar_config) -> None:
    search_queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        search_queries.append(request.url.params["q"])
        return httpx.Response(403, headers={"Retry-After": "60"})

    client = GitHubClient("token", httpx.Client(transport=httpx.MockTransport(handler)))
    config = radar_config.model_copy(update={"queries": {"general": ["first", "later"]}})

    collection = client.collect(config)

    assert search_queries == ["first"]
    assert collection.statuses[0].ok is False
    assert client.limited is True


def test_collect_keeps_repository_when_detail_payloads_are_malformed(radar_config) -> None:
    fixture = Path(__file__).with_name("fixtures") / "github_search.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search/repositories":
            return httpx.Response(200, json=payload)
        if request.url.path.endswith("/readme"):
            return httpx.Response(404)
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(200, json={"tag_name": 123})
        if request.url.path.endswith("/contents"):
            return httpx.Response(200, json=["not-a-mapping", {"name": None}, {"name": "SKILL.md"}])
        raise AssertionError(request.url.path)

    client = GitHubClient("token", httpx.Client(transport=httpx.MockTransport(handler)))

    collection = client.collect(radar_config)

    assert len(collection.repositories) == 1
    assert collection.repositories[0].latest_release is None
    assert collection.repositories[0].release_detail_valid is False
    assert collection.repositories[0].has_skill_md is True
    assert collection.complete is False
    assert collection.statuses[-1].name == "github:optional-details"
    assert collection.statuses[-1].ok is False


def test_collect_marks_inventory_incomplete_when_all_quality_details_fail(
    radar_config,
) -> None:
    fixture = Path(__file__).with_name("fixtures") / "github_search.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search/repositories":
            return httpx.Response(200, json=payload)
        return httpx.Response(503)

    collection = GitHubClient(
        "token", httpx.Client(transport=httpx.MockTransport(handler))
    ).collect(radar_config)

    assert collection.queries_succeeded == collection.queries_total
    assert collection.complete is False
    assert collection.statuses[-1].name == "github:optional-details"
    assert collection.statuses[-1].ok is False


def test_collect_requires_explicit_results_for_every_optional_detail(
    radar_config,
) -> None:
    fixture = Path(__file__).with_name("fixtures") / "github_search.json"
    payload = json.loads(fixture.read_text(encoding="utf-8"))

    def run_with_failure(failing_suffix: str):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/search/repositories":
                return httpx.Response(200, json=payload)
            if request.url.path.endswith(failing_suffix):
                return httpx.Response(503)
            if request.url.path.endswith("/contents"):
                return httpx.Response(200, json=[{"name": "main.py"}])
            return httpx.Response(404)

        return GitHubClient(
            "token", httpx.Client(transport=httpx.MockTransport(handler))
        ).collect(radar_config)

    for suffix in ("/readme", "/releases/latest", "/contents"):
        collection = run_with_failure(suffix)
        assert collection.complete is False
        assert collection.statuses[-1].name == "github:optional-details"


def test_enrich_reads_readme_release_and_root_capabilities(repo_factory) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/readme"):
            return httpx.Response(
                200, json={"content": "SW5zdGFsbFxuRXhhbXBsZQ==", "encoding": "base64"}
            )
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(200, json={"tag_name": "v1.2.0"})
        if request.url.path.endswith("/contents"):
            return httpx.Response(
                200,
                json=[
                    {"name": "SKILL.md"},
                    {"name": "examples"},
                    {"name": "tests"},
                    {"name": "mcp.json"},
                    {"name": "pyproject.toml", "type": "file"},
                    {"name": "src", "type": "dir"},
                ],
            )
        raise AssertionError(request.url.path)

    client = GitHubClient("token", httpx.Client(transport=httpx.MockTransport(handler)))

    enriched = client.enrich(repo_factory(full_name="acme/agent-skill"))

    assert enriched.readme == "Install\\nExample"
    assert enriched.latest_release == "v1.2.0"
    assert enriched.has_skill_md and enriched.has_mcp and enriched.has_examples and enriched.has_tests
    assert enriched.has_runnable_entrypoint is True
    assert enriched.has_executable_code is True


def test_enrich_distinguishes_no_release_from_failed_release_detail(repo_factory) -> None:
    def no_release_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(404)
        if request.url.path.endswith("/readme"):
            return httpx.Response(404)
        if request.url.path.endswith("/contents"):
            return httpx.Response(200, json=[])
        raise AssertionError(request.url.path)

    no_release = GitHubClient(
        "token", httpx.Client(transport=httpx.MockTransport(no_release_handler))
    ).enrich(repo_factory())

    def failed_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(503)
        if request.url.path.endswith("/readme"):
            return httpx.Response(404)
        if request.url.path.endswith("/contents"):
            return httpx.Response(200, json=[])
        raise AssertionError(request.url.path)

    failed = GitHubClient(
        "token", httpx.Client(transport=httpx.MockTransport(failed_handler))
    ).enrich(repo_factory())

    assert no_release.latest_release is None
    assert no_release.release_detail_valid is True
    assert failed.latest_release is None
    assert failed.release_detail_valid is False


def test_enrich_collects_bounded_fork_independence_evidence(repo_factory) -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/readme"):
            return httpx.Response(404)
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(404)
        if request.url.path.endswith("/contents"):
            return httpx.Response(200, json=[{"name": "main.py", "type": "file"}])
        if request.url.path == "/repos/acme/agent-skill":
            return httpx.Response(
                200,
                json={
                    "default_branch": "main",
                    "parent": {
                        "full_name": "upstream/agent-skill",
                        "default_branch": "main",
                        "pushed_at": "2026-07-01T00:00:00Z",
                    },
                },
            )
        if request.url.path.endswith("/compare/main...acme:main"):
            return httpx.Response(200, json={"ahead_by": 7})
        raise AssertionError(request.url.path)

    enriched = GitHubClient(
        "token", httpx.Client(transport=httpx.MockTransport(handler))
    ).enrich(repo_factory(fork=True))

    assert enriched.fork_ahead_by == 7
    assert enriched.parent_pushed_at.isoformat() == "2026-07-01T00:00:00+00:00"
    assert enriched.has_executable_code is True
    assert len(paths) == 5
