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
        issue = self._find_issue(base_url, title, label)
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

    def _find_issue(self, base_url: str, title: str, label: str) -> dict | None:
        url: str | httpx.URL = base_url
        params: dict[str, str | int] | None = {
            "state": "all",
            "labels": label,
            "per_page": 100,
            "page": 1,
        }
        while True:
            response = self.client.get(
                url,
                headers=self.headers,
                params=params,
                timeout=20,
            )
            response.raise_for_status()
            items = response.json()
            if not isinstance(items, list):
                raise ValueError("GitHub issues response must be a JSON array")
            issue = next(
                (
                    item
                    for item in items
                    if isinstance(item, dict) and item.get("title") == title
                ),
                None,
            )
            if issue is not None:
                return issue

            next_url = response.links.get("next", {}).get("url")
            if next_url:
                url = next_url
                params = None
                continue
            if len(items) < 100:
                return None

            current_page = int(response.request.url.params.get("page", "1"))
            url = base_url
            params = {
                "state": "all",
                "labels": label,
                "per_page": 100,
                "page": current_page + 1,
            }
