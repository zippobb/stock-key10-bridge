#!/usr/bin/env python3
"""Read-only verifier for a fixed-commit candidate-assets staging directory."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def fail(message: str) -> "NoReturn":
    print(f"VERIFY_FAILED: {message}")
    raise SystemExit(2)


def main() -> int:
    if len(sys.argv) != 3:
        fail("usage: verify_stage.py MANIFEST EXPECTED_COMMIT")

    manifest_path = Path(sys.argv[1]).resolve()
    expected_commit = sys.argv[2]
    if not manifest_path.is_file():
        fail("manifest_missing")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        fail(f"manifest_invalid={exc.__class__.__name__}")

    if manifest.get("repository") != "zippobb/stock-key10-bridge":
        fail("repository_mismatch")
    if manifest.get("ref") != "main":
        fail("ref_mismatch")
    if manifest.get("commit_sha") != expected_commit:
        fail("commit_mismatch")

    root = manifest_path.parent
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        fail("files_missing")

    for entry in entries:
        if not isinstance(entry, dict):
            fail("file_entry_invalid")
        relative = entry.get("path")
        if not isinstance(relative, str):
            fail("file_path_invalid")
        rel_path = Path(relative)
        if rel_path.is_absolute() or ".." in rel_path.parts:
            fail(f"path_escape={relative!r}")
        path = (root / rel_path).resolve()
        if root not in path.parents:
            fail(f"path_escape={relative!r}")
        if not path.is_file():
            fail(f"file_missing={relative}")
        data = path.read_bytes()
        actual_sha = hashlib.sha256(data).hexdigest()
        if len(data) != entry.get("bytes"):
            fail(f"size_mismatch={relative}")
        if actual_sha != entry.get("sha256"):
            fail(f"sha256_mismatch={relative}")
        print(f"verified {relative} bytes={len(data)} sha256={actual_sha}")

    print(
        "VERIFY_PASSED"
        f" repository={manifest['repository']}"
        f" ref={manifest['ref']}"
        f" commit_sha={manifest['commit_sha']}"
        f" files={len(entries)}"
    )
    return 0


if __name__ == "__main__":
    main()
