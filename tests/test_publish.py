import json

import httpx

from ai_agent_radar.publish import IssuePublisher


def test_upsert_updates_matching_issue_instead_of_creating() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 12,
                        "html_url": "https://github.com/o/r/issues/12",
                    }
                ],
            )
        return httpx.Response(200, json={"html_url": "https://github.com/o/r/issues/12"})

    publisher = IssuePublisher(
        "token", "o/r", httpx.Client(transport=httpx.MockTransport(handler))
    )

    url = publisher.upsert("AI Agent Radar 日报 · 2026-07-20", "body", "radar-daily")

    assert url.endswith("/12")
    assert ("PATCH", "/repos/o/r/issues/12") in calls
    assert not any(method == "POST" and path.endswith("/issues") for method, path in calls)


def test_upsert_creates_issue_when_no_title_matches() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(201, json={"html_url": "https://github.com/o/r/issues/13"})

    publisher = IssuePublisher(
        "token", "o/r", httpx.Client(transport=httpx.MockTransport(handler))
    )

    url = publisher.upsert("AI Agent Radar 周榜 · 2026-W30", "body", "radar-weekly")

    assert url.endswith("/13")
    post = next(request for request in requests if request.method == "POST")
    assert post.url.path == "/repos/o/r/issues"
    assert json.loads(post.content) == {
        "title": "AI Agent Radar 周榜 · 2026-W30",
        "body": "body",
        "labels": ["radar-weekly"],
    }
