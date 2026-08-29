#!/usr/bin/env python3
"""Shared, side-effect-free helpers for the V2.0 pipeline."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


TZ = ZoneInfo("Asia/Shanghai")


def now_bj() -> datetime:
    return datetime.now(TZ)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sanitize(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if not isinstance(value, (str, bytes)):
        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
    return value


def json_bytes(value: object) -> bytes:
    return (
        json.dumps(sanitize(value), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def write_json_pair(latest: Path, dated: Path, value: object) -> None:
    payload = json_bytes(value)
    atomic_write(latest, payload)
    atomic_write(dated, payload)
    if latest.read_bytes() != dated.read_bytes():
        raise RuntimeError("latest与日期版写入结果不一致")


def safe_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    for key in ("TUSHARE_TOKEN", "ALIBABA_CLOUD_ACCESS_KEY_ID", "ALIBABA_CLOUD_ACCESS_KEY_SECRET"):
        value = os.environ.get(key)
        if value:
            text = text.replace(value, "***")
    return text[:1200]


def load_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON根节点必须为对象: {path}")
    return value


def forbidden_keys(value: object, forbidden: set[str]) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key) in forbidden:
                found.add(str(key))
            found.update(forbidden_keys(item, forbidden))
    elif isinstance(value, list):
        for item in value:
            found.update(forbidden_keys(item, forbidden))
    return found
