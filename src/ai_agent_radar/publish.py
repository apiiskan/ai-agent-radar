from __future__ import annotations

import httpx


class IssuePublisher:
    def __init__(
        self,
        token: str,
        repository: str,
        client: httpx.Client | None = None,
    ) -> None:
        self.repository = repository
        self.client = client or httpx.Client()
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def upsert(self, title: str, body: str, label: str) -> str:
        base_url = f"https://api.github.com/repos/{self.repository}/issues"
        response = self.client.get(
            base_url,
            headers=self.headers,
            params={"state": "open", "labels": label, "per_page": 100},
            timeout=20,
        )
        response.raise_for_status()
        issue = next(
            (item for item in response.json() if item.get("title", title) == title),
            None,
        )
        if issue:
            result = self.client.patch(
                f"{base_url}/{issue['number']}",
                headers=self.headers,
                json={"body": body},
                timeout=20,
            )
        else:
            result = self.client.post(
                base_url,
                headers=self.headers,
                json={"title": title, "body": body, "labels": [label]},
                timeout=20,
            )
        result.raise_for_status()
        return result.json()["html_url"]
