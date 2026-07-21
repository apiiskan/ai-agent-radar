import json
import os
from datetime import datetime
from pathlib import Path

from .models import SourceStatus


def merge_source_state(
    path: Path, current: tuple[SourceStatus, ...], now: datetime
) -> tuple[SourceStatus, ...]:
    previous = _load_source_state(path)
    merged = tuple(_merge_status(status, previous.get(status.name), now) for status in current)

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps([status.model_dump(mode="json") for status in merged], ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary_path, path)
    return merged


def _load_source_state(path: Path) -> dict[str, SourceStatus]:
    if not path.exists():
        return {}
    statuses = json.loads(path.read_text(encoding="utf-8"))
    return {item["name"]: SourceStatus.model_validate(item) for item in statuses}


def _merge_status(status: SourceStatus, previous: SourceStatus | None, now: datetime) -> SourceStatus:
    if status.ok:
        return status.model_copy(update={"last_success_at": now, "consecutive_failures": 0})
    return status.model_copy(
        update={
            "last_success_at": previous.last_success_at if previous else None,
            "consecutive_failures": (previous.consecutive_failures if previous else 0) + 1,
        }
    )
