import json

import httpx
import pytest

from ai_agent_radar.publish import IssuePublisher


def _raw_publisher(handler) -> IssuePublisher:
    return IssuePublisher(
        "token", "o/r", httpx.Client(transport=httpx.MockTransport(handler))
    )


def _publisher(handler) -> IssuePublisher:
    def label_aware_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/labels/" in request.url.path:
            return httpx.Response(200, json={"name": request.url.path.rsplit("/", 1)[-1]})
        return handler(request)

    return _raw_publisher(label_aware_handler)


def test_upsert_reuses_existing_label_before_searching_issues() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and "/labels/" in request.url.path:
            return httpx.Response(200, json={"name": "radar-daily"})
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(201, json={"html_url": "https://github.com/o/r/issues/1"})

    _raw_publisher(handler).upsert("Daily", "body", "radar-daily")

    assert requests[0].url.path == "/repos/o/r/labels/radar-daily"
    assert not any(request.url.path == "/repos/o/r/labels" for request in requests)


def test_upsert_creates_missing_label_before_searching_issues() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and "/labels/" in request.url.path:
            return httpx.Response(404, json={"message": "Not Found"})
        if request.method == "POST" and request.url.path.endswith("/labels"):
            return httpx.Response(201, json={"name": "radar-weekly"})
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(201, json={"html_url": "https://github.com/o/r/issues/1"})

    _raw_publisher(handler).upsert("Weekly", "body", "radar-weekly")

    create = next(request for request in requests if request.url.path.endswith("/labels"))
    assert json.loads(create.content) == {
        "name": "radar-weekly",
        "color": "1d76db",
        "description": "AI Agent Radar automated reports",
    }
    issue_search_index = next(
        index
        for index, request in enumerate(requests)
        if request.method == "GET" and request.url.path.endswith("/issues")
    )
    assert requests.index(create) < issue_search_index


def test_upsert_stops_when_label_lookup_fails() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500, json={"message": "failure"})

    with pytest.raises(httpx.HTTPStatusError):
        _raw_publisher(handler).upsert("Daily", "body", "radar-daily")

    assert len(requests) == 1
    assert requests[0].url.path == "/repos/o/r/labels/radar-daily"


def test_upsert_recovers_when_concurrent_label_creation_returns_422() -> None:
    requests: list[httpx.Request] = []
    label_lookups = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal label_lookups
        requests.append(request)
        if request.method == "GET" and "/labels/" in request.url.path:
            label_lookups += 1
            return httpx.Response(
                404 if label_lookups == 1 else 200,
                json={"message": "Not Found"} if label_lookups == 1 else {"name": "radar-daily"},
            )
        if request.method == "POST" and request.url.path.endswith("/labels"):
            return httpx.Response(422, json={"message": "Validation Failed"})
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(201, json={"html_url": "https://github.com/o/r/issues/1"})

    url = _raw_publisher(handler).upsert("Daily", "body", "radar-daily")

    assert url.endswith("/1")
    assert label_lookups == 2
    assert any(request.url.path.endswith("/issues") for request in requests)


def test_upsert_fails_when_label_is_still_missing_after_422_race() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and "/labels/" in request.url.path:
            return httpx.Response(404, json={"message": "Not Found"})
        if request.method == "POST" and request.url.path.endswith("/labels"):
            return httpx.Response(422, json={"message": "Validation Failed"})
        raise AssertionError("issue search must not run without the label")

    with pytest.raises(httpx.HTTPStatusError):
        _raw_publisher(handler).upsert("Daily", "body", "radar-daily")

    assert [request.method for request in requests] == ["GET", "POST", "GET"]
    assert all("/issues" not in request.url.path for request in requests)


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


def test_upsert_ignores_pull_request_with_exact_title() -> None:
    requests: list[httpx.Request] = []
    title = "AI Agent Radar 日报 · 2026-07-20"

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 10,
                        "title": title,
                        "pull_request": {"url": "https://api.github.com/repos/o/r/pulls/10"},
                    },
                    {"number": 11, "title": title},
                ],
            )
        return httpx.Response(200, json={"html_url": "https://github.com/o/r/issues/11"})

    url = _publisher(handler).upsert(title, "body", "radar-daily")

    assert url.endswith("/11")
    assert any(
        request.method == "PATCH" and request.url.path.endswith("/issues/11")
        for request in requests
    )
    assert not any(request.url.path.endswith("/issues/10") for request in requests)


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
