from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import httpx

MAX_DOCUMENT_BYTES = 50 * 1024 * 1024
MAX_CAPTION_LENGTH = 1024
MAX_ALERT_LENGTH = 4096
MAX_ATTEMPTS = 3
MAX_RETRY_DELAY = 60
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class TelegramError(RuntimeError):
    """A Telegram failure safe to expose in command output."""


def mask_chat_id(chat_id: str) -> str:
    sign = "-" if chat_id.startswith("-") else ""
    digits = chat_id.removeprefix("-")
    if len(digits) <= 4:
        return f"{sign}***"
    return f"{sign}***{digits[-4:]}"


class TelegramPublisher:
    def __init__(
        self,
        token: str,
        chat_id: str | None,
        client: httpx.Client,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._token = token
        self._chat_id = chat_id
        self._client = client
        self._sleep = sleep

    def discover_private_start_chat(self) -> str:
        payload = self._request(
            "getUpdates",
            json={"allowed_updates": ["message"]},
        )
        candidates: set[str] = set()
        for update in payload["result"]:
            message = update.get("message")
            if not isinstance(message, dict):
                continue
            chat = message.get("chat")
            text = message.get("text")
            if (
                not isinstance(chat, dict)
                or chat.get("type") != "private"
                or not isinstance(text, str)
                or not _is_start_command(text)
            ):
                continue
            chat_id = chat.get("id")
            if isinstance(chat_id, int):
                candidates.add(str(chat_id))
        if len(candidates) != 1:
            raise TelegramError(
                "expected exactly one private /start chat; "
                f"found {len(candidates)}"
            )
        return candidates.pop()

    def send_alert(self, text: str, *, chat_id: str | None = None) -> int:
        if len(text) > MAX_ALERT_LENGTH:
            raise TelegramError("Telegram alert exceeds 4096 characters")
        destination = chat_id or self._chat_id
        if not destination:
            raise TelegramError("Telegram chat ID is required")
        payload = self._request(
            "sendMessage",
            json={"chat_id": destination, "text": text},
        )
        return _message_id(payload)

    def send_document(self, path: Path, caption: str) -> int:
        if len(caption) > MAX_CAPTION_LENGTH:
            raise TelegramError("Telegram caption exceeds 1024 characters")
        if not path.is_file():
            raise TelegramError("Telegram report file does not exist")
        if path.stat().st_size > MAX_DOCUMENT_BYTES:
            raise TelegramError("Telegram report exceeds the 50 MB limit")
        if not self._chat_id:
            raise TelegramError("Telegram chat ID is required")
        content = path.read_bytes()
        payload = self._request(
            "sendDocument",
            data={"chat_id": self._chat_id, "caption": caption},
            files={"document": (path.name, content, "text/markdown")},
        )
        return _message_id(payload)

    def send_bootstrap_test(self, chat_id: str) -> int:
        text = (
            "✅ AI Agent Radar 已连接 Telegram。\n\n"
            f"TELEGRAM_CHAT_ID: {chat_id}\n\n"
            "请把上面的数字保存为 GitHub Actions Secret："
            "TELEGRAM_CHAT_ID。"
        )
        return self.send_alert(text, chat_id=chat_id)

    def _request(self, method: str, **kwargs: object) -> dict:
        url = f"https://api.telegram.org/bot{self._token}/{method}"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self._client.post(url, timeout=20, **kwargs)
            except httpx.RequestError as exc:
                if attempt == MAX_ATTEMPTS:
                    raise TelegramError(
                        "Telegram API request failed after 3 attempts"
                    ) from exc
                self._sleep(min(2 ** (attempt - 1), MAX_RETRY_DELAY))
                continue

            payload = _response_payload(response)
            if (
                response.status_code in RETRYABLE_STATUS_CODES
                and attempt < MAX_ATTEMPTS
            ):
                self._sleep(_retry_delay(response.status_code, payload, attempt))
                continue
            if response.status_code >= 400:
                raise TelegramError(
                    f"Telegram API request failed (HTTP {response.status_code})"
                )
            if payload.get("ok") is not True:
                raise TelegramError("Telegram API rejected the request")
            if not isinstance(payload.get("result"), (dict, list)):
                raise TelegramError("Telegram API returned an invalid result")
            return payload
        raise TelegramError("Telegram API request failed after 3 attempts")


def _is_start_command(text: str) -> bool:
    command = text.strip().split(maxsplit=1)[0]
    return command.split("@", maxsplit=1)[0] == "/start"


def _message_id(payload: dict) -> int:
    result = payload["result"]
    if not isinstance(result, dict) or not isinstance(result.get("message_id"), int):
        raise TelegramError("Telegram API returned an invalid message")
    return result["message_id"]


def _response_payload(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError as exc:
        raise TelegramError("Telegram API returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise TelegramError("Telegram API returned invalid JSON")
    return payload


def _retry_delay(status_code: int, payload: dict, attempt: int) -> float:
    if status_code == 429:
        parameters = payload.get("parameters")
        if isinstance(parameters, dict):
            retry_after = parameters.get("retry_after")
            if isinstance(retry_after, (int, float)) and retry_after >= 0:
                return min(float(retry_after), MAX_RETRY_DELAY)
    return min(float(2 ** (attempt - 1)), MAX_RETRY_DELAY)
