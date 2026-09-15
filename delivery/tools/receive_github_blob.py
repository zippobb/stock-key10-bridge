"""Receive exact connector UTF-8 bytes on stdin, without waiting for EOF.

Usage: python receive_github_blob.py OUTPUT BYTE_COUNT GIT_BLOB_SHA SHA256
Send github_fetch_blob's original text via write_stdin, never a shell argument.
Does not access credentials or the network. Does not overwrite existing files.
"""
import hashlib
import os
from pathlib import Path
import re
import select
import sys
import tempfile
import time


def receive(output, size, blob_sha, sha256, timeout=30):
    if size <= 0 or not re.fullmatch(r"[0-9a-f]{40}", blob_sha) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("invalid size or digest")
    target = Path(output)
    if target.exists():
        raise FileExistsError("output already exists; use a fresh temporary path")
    git_hash = hashlib.sha1(f"blob {size}\0".encode())
    data_hash = hashlib.sha256()
    deadline = time.monotonic() + timeout
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".blob-")
    try:
        with os.fdopen(fd, "wb") as stream:
            remaining = size
            while remaining:
                wait = deadline - time.monotonic()
                if wait <= 0 or not select.select([sys.stdin.buffer], [], [], wait)[0]:
                    raise TimeoutError("stdin transport timeout")
                block = os.read(sys.stdin.fileno(), min(65536, remaining))
                if not block:
                    raise ValueError("truncated stdin transport")
                stream.write(block)
                git_hash.update(block)
                data_hash.update(block)
                remaining -= len(block)
            stream.flush()
            os.fsync(stream.fileno())
        if git_hash.hexdigest() != blob_sha or data_hash.hexdigest() != sha256:
            raise ValueError("raw byte hash mismatch")
        # Validate the actual on-disk file again; preserve all original bytes.
        with open(temporary, "rb") as stream:
            disk_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        if disk_hash != sha256:
            raise ValueError("disk hash mismatch")
        os.link(temporary, target)  # atomic publish, refuses an existing target
        print(disk_hash, flush=True)
    finally:
        os.unlink(temporary)


if __name__ == "__main__":
    try:
        receive(sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4])
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
