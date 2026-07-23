import json

import httpx
import pytest

import ai_agent_radar.telegram as telegram
from ai_agent_radar.telegram import TelegramError, TelegramPublisher, mask_chat_id


def telegram_response(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/getUpdates"):
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": [
                    {
                        "update_id": 1,
                        "message": {
                            "text": "/start",
                            "chat": {"id": 123456789, "type": "private"},
                        },
                    },
                    {
                        "update_id": 2,
                        "message": {
                            "text": "ignored",
                            "chat": {"id": 999, "type": "private"},
                        },
                    },
                    {
                        "update_id": 3,
                        "message": {
                            "text": "/start",
                            "chat": {"id": -100123, "type": "group"},
                        },
                    },
                ],
            },
        )
    if request.url.path.endswith("/sendMessage"):
        payload = json.loads(request.content)
        assert payload["chat_id"] == "123456789"
        assert "TELEGRAM_CHAT_ID: 123456789" in payload["text"]
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})
    raise AssertionError(f"unexpected Telegram method: {request.url.path.rsplit('/', 1)[-1]}")


def test_bootstrap_discovers_one_private_start_and_sends_chat_id_to_that_chat() -> None:
    with httpx.Client(transport=httpx.MockTransport(telegram_response)) as client:
        publisher = TelegramPublisher("bot-token", None, client)

        chat_id = publisher.discover_private_start_chat()
        message_id = publisher.send_bootstrap_test(chat_id)

    assert chat_id == "123456789"
    assert message_id == 77


@pytest.mark.parametrize(
    "updates",
    [
        [],
        [
            {"message": {"text": "/start", "chat": {"id": 1111, "type": "private"}}},
            {"message": {"text": "/start", "chat": {"id": 2222, "type": "private"}}},
        ],
    ],
)
def test_bootstrap_refuses_zero_or_ambiguous_private_start_chats(updates) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": updates})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        publisher = TelegramPublisher("super-secret-token", None, client)
        with pytest.raises(TelegramError) as error:
            publisher.discover_private_start_chat()

    message = str(error.value)
    assert "super-secret-token" not in message
    assert "1111" not in message
    assert "2222" not in message


def test_telegram_api_failure_is_sanitized() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "ok": False,
                "description": "bad bot-token for chat 123456789",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        publisher = TelegramPublisher("bot-token", None, client)
        with pytest.raises(TelegramError) as error:
            publisher.discover_private_start_chat()

    assert str(error.value) == "Telegram API request failed (HTTP 401)"


@pytest.mark.parametrize(
    ("chat_id", "masked"),
    [
        ("123456789", "***6789"),
        ("-100123456789", "-***6789"),
        ("12", "***"),
    ],
)
def test_chat_id_masking_never_returns_the_full_identifier(chat_id, masked) -> None:
    assert mask_chat_id(chat_id) == masked
    assert mask_chat_id(chat_id) != chat_id


def test_send_document_uploads_markdown_bytes_and_expected_fields(tmp_path) -> None:
    report = tmp_path / "2026-07-23.md"
    report_bytes = "# 日报\n\n完整内容".encode()
    report.write_bytes(report_bytes)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/sendDocument")
        content_type = request.headers["content-type"]
        assert content_type.startswith("multipart/form-data; boundary=")
        body = request.content
        assert b'name="chat_id"' in body
        assert b"123456789" in body
        assert b'name="caption"' in body
        assert "日报摘要".encode() in body
        assert b'name="document"; filename="2026-07-23.md"' in body
        assert b"Content-Type: text/markdown" in body
        assert report_bytes in body
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 88}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        publisher = TelegramPublisher("bot-token", "123456789", client)
        message_id = publisher.send_document(report, "日报摘要")

    assert message_id == 88


def test_send_document_rejects_oversized_file_before_network(
    tmp_path, monkeypatch
) -> None:
    report = tmp_path / "2026-07-23.md"
    report.write_bytes(b"12345")
    monkeypatch.setattr(telegram, "MAX_DOCUMENT_BYTES", 4)

    def unexpected_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError("oversized report must not contact Telegram")

    with httpx.Client(transport=httpx.MockTransport(unexpected_request)) as client:
        publisher = TelegramPublisher("bot-token", "123456789", client)
        with pytest.raises(TelegramError, match="50 MB"):
            publisher.send_document(report, "caption")


@pytest.mark.parametrize(
    ("method", "text", "message"),
    [
        ("send_document", "x" * 1025, "caption exceeds 1024"),
        ("send_alert", "x" * 4097, "alert exceeds 4096"),
    ],
)
def test_telegram_text_limits_are_validated_locally(
    method, text, message, tmp_path
) -> None:
    report = tmp_path / "2026-07-23.md"
    report.write_text("# report", encoding="utf-8")

    def unexpected_request(request: httpx.Request) -> httpx.Response:
        raise AssertionError("invalid text must not contact Telegram")

    with httpx.Client(transport=httpx.MockTransport(unexpected_request)) as client:
        publisher = TelegramPublisher("bot-token", "123456789", client)
        with pytest.raises(TelegramError, match=message):
            if method == "send_document":
                publisher.send_document(report, text)
            else:
                publisher.send_alert(text)


def test_telegram_retries_429_using_bounded_retry_after() -> None:
    attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                429,
                json={
                    "ok": False,
                    "parameters": {"retry_after": 1000},
                },
            )
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 5}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        publisher = TelegramPublisher(
            "bot-token", "123456789", client, sleep=sleeps.append
        )
        assert publisher.send_alert("retry") == 5

    assert attempts == 2
    assert sleeps == [60]


@pytest.mark.parametrize("status_code", [500, 502, 503, 504])
def test_telegram_retries_retryable_server_errors(status_code) -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(status_code, json={"ok": False})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 6}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        publisher = TelegramPublisher(
            "bot-token", "123456789", client, sleep=lambda _: None
        )
        assert publisher.send_alert("retry") == 6

    assert attempts == 3


def test_telegram_retries_timeouts_at_most_three_times() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("contained bot-token and 123456789")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        publisher = TelegramPublisher(
            "bot-token", "123456789", client, sleep=lambda _: None
        )
        with pytest.raises(TelegramError) as error:
            publisher.send_alert("retry")

    assert attempts == 3
    assert str(error.value) == "Telegram API request failed after 3 attempts"


def test_telegram_does_not_retry_non_retryable_client_error() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            400,
            json={"ok": False, "description": "bot-token 123456789 report body"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        publisher = TelegramPublisher(
            "bot-token", "123456789", client, sleep=lambda _: None
        )
        with pytest.raises(TelegramError) as error:
            publisher.send_alert("report body")

    assert attempts == 1
    assert str(error.value) == "Telegram API request failed (HTTP 400)"


def test_telegram_rejects_ok_false_response_without_leaking_description() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ok": False,
                "description": "bot-token 123456789 report body",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        publisher = TelegramPublisher("bot-token", "123456789", client)
        with pytest.raises(TelegramError) as error:
            publisher.send_alert("report body")

    assert str(error.value) == "Telegram API rejected the request"


def test_get_bot_username_validates_token_without_returning_other_profile_data() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/getMe")
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": {
                    "id": 123,
                    "is_bot": True,
                    "first_name": "Radar",
                    "username": "agent_radar_bot",
                },
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        publisher = TelegramPublisher("bot-token", None, client)
        assert publisher.get_bot_username() == "agent_radar_bot"
