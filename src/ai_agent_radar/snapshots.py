import gzip
import json
import os
from collections import defaultdict
from datetime import date
from pathlib import Path

from .models import RepoSnapshot


def load_snapshot(path: Path) -> list[RepoSnapshot]:
    if not path.exists():
        return []

    payload = _read_snapshot_payload(path)
    if isinstance(payload, dict):
        return [
            RepoSnapshot.model_validate(item)
            for daily_snapshots in payload.values()
            for item in daily_snapshots
        ]
    return [RepoSnapshot.model_validate(item) for item in payload]


def write_snapshot_atomic(path: Path, snapshots: list[RepoSnapshot]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    serialized = json.dumps(
        [snapshot.model_dump(mode="json") for snapshot in snapshots],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    temporary_path.write_text(serialized, encoding="utf-8")
    os.replace(temporary_path, path)


def compact_old_snapshots(directory: Path, cutoff: date) -> list[Path]:
    paths_by_month: dict[str, list[Path]] = defaultdict(list)
    for path in directory.glob("????-??-??.json"):
        try:
            snapshot_date = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if snapshot_date < cutoff:
            paths_by_month[path.stem[:7]].append(path)

    archives: list[Path] = []
    for month, paths in sorted(paths_by_month.items()):
        archive = directory / f"{month}.json.gz"
        archive_payload = _read_archive_payload(archive)
        archive_payload.update(
            {path.stem: _read_snapshot_payload(path) for path in sorted(paths)}
        )
        temporary_path = archive.with_suffix(archive.suffix + ".tmp")
        with gzip.open(temporary_path, "wt", encoding="utf-8") as handle:
            json.dump(archive_payload, handle, ensure_ascii=False, separators=(",", ":"))
        os.replace(temporary_path, archive)
        for path in paths:
            path.unlink()
        archives.append(archive)

    return archives


def _read_snapshot_payload(path: Path) -> list[object] | dict[str, list[object]]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    return json.loads(path.read_text(encoding="utf-8"))


def _read_archive_payload(path: Path) -> dict[str, list[object]]:
    if not path.exists():
        return {}
    payload = _read_snapshot_payload(path)
    if not isinstance(payload, dict):
        raise ValueError(f"snapshot archive must contain a monthly mapping: {path}")
    return payload
