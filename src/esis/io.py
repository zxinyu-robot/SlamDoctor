from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TextIO

from .models import Observation

STANDARD_FIELDS = {
    "sensor_id",
    "sensor_type",
    "timestamp",
    "receive_timestamp",
    "sequence",
    "payload_size",
    "values",
    "metadata",
}


def _coerce(value: str) -> object:
    if value == "":
        return None
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return float(value) if any(char in value for char in ".eE") else int(value)
    except ValueError:
        return value


def read_jsonl(handle: TextIO) -> Iterator[Observation]:
    for line_number, line in enumerate(handle, start=1):
        if not line.strip():
            continue
        try:
            yield Observation.model_validate_json(line)
        except Exception as exc:
            raise ValueError(f"第 {line_number} 行不是有效观测数据: {exc}") from exc


def read_csv(handle: TextIO) -> Iterator[Observation]:
    for line_number, row in enumerate(csv.DictReader(handle), start=2):
        try:
            values = json.loads(row.get("values") or "{}")
            metadata = json.loads(row.get("metadata") or "{}")
            for key, value in row.items():
                if key not in STANDARD_FIELDS and value not in (None, ""):
                    values[key] = _coerce(value)
            data = {key: _coerce(value) for key, value in row.items() if key in STANDARD_FIELDS}
            data["values"] = values
            data["metadata"] = metadata
            yield Observation.model_validate(data)
        except Exception as exc:
            raise ValueError(f"第 {line_number} 行不是有效观测数据: {exc}") from exc


def read_observations(path: Path | None) -> Iterator[Observation]:
    if path is None:
        import sys

        yield from read_jsonl(sys.stdin)
        return

    with path.open("r", encoding="utf-8", newline="") as handle:
        if path.suffix.lower() == ".csv":
            yield from read_csv(handle)
        else:
            yield from read_jsonl(handle)


def write_jsonl(observations: Iterable[Observation], handle: TextIO) -> None:
    for observation in observations:
        handle.write(observation.model_dump_json(exclude_none=True) + "\n")
