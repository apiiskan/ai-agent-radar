import json

import httpx

from ai_agent_radar.publish import IssuePublisher


def _publisher(handler) -> IssuePublisher:
    return IssuePublisher(
        "token", "o/r", httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_upsert_does_not_match_issue_with_missing_title() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=[{"number": 12}])
        return httpx.Response(201, json={"html_url": "https://github.com/o/r/issues/13"})

    url = _publisher(handler).upsert(
        "AI Agent Radar 日报 · 2026-07-20", "body", "radar-daily"
    )

    assert url.endswith("/13")
    assert any(request.method == "POST" for request in requests)
    assert not any(request.method == "PATCH" for request in requests)


def test_upsert_updates_exact_closed_match_instead_of_creating() -> None:
    requests: list[httpx.Request] = []
    title = "AI Agent Radar 日报 · 2026-07-20"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200, json=[{"number": 12, "title": title, "state": "closed"}]
            )
        return httpx.Response(200, json={"html_url": "https://github.com/o/r/issues/12"})

    url = _publisher(handler).upsert(title, "replacement", "radar-daily")

    assert url.endswith("/12")
    listing = requests[0]
    assert listing.url.params["state"] == "all"
    assert listing.url.params["labels"] == "radar-daily"
    assert listing.url.params["per_page"] == "100"
    patch = next(request for request in requests if request.method == "PATCH")
    assert patch.url.path == "/repos/o/r/issues/12"
    assert json.loads(patch.content) == {"body": "replacement"}
    assert not any(request.method == "POST" for request in requests)


def test_upsert_updates_exact_match_on_second_page() -> None:
    requests: list[httpx.Request] = []
    title = "AI Agent Radar 周榜 · 2026-W30"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.params.get("page") == "1":
            return httpx.Response(
                200,
                json=[{"number": 1, "title": "other"}],
                headers={
                    "Link": '<https://api.github.com/repos/o/r/issues?page=2>; rel="next"'
                },
            )
        if request.method == "GET":
            return httpx.Response(200, json=[{"number": 22, "title": title}])
        return httpx.Response(200, json={"html_url": "https://github.com/o/r/issues/22"})

    url = _publisher(handler).upsert(title, "body", "radar-weekly")

    assert url.endswith("/22")
    assert sum(request.method == "GET" for request in requests) == 2
    assert any(
        request.method == "PATCH" and request.url.path.endswith("/issues/22")
        for request in requests
    )
    assert not any(request.method == "POST" for request in requests)


def test_upsert_creates_issue_when_no_title_matches() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(201, json={"html_url": "https://github.com/o/r/issues/13"})

    url = _publisher(handler).upsert(
        "AI Agent Radar 周榜 · 2026-W30", "body", "radar-weekly"
    )

    assert url.endswith("/13")
    post = next(request for request in requests if request.method == "POST")
    assert post.url.path == "/repos/o/r/issues"
    assert json.loads(post.content) == {
        "title": "AI Agent Radar 周榜 · 2026-W30",
        "body": "body",
        "labels": ["radar-weekly"],
    }
