"""Content-addressed image tags.

A tag embeds a digest of the build context, so editing a task yields a new tag and
``results.json`` records exactly which bytes were evaluated.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

IMAGE_NAMESPACE = "crucible"
DIGEST_LENGTH = 12


def content_digest(root: Path, *, extra: str = "") -> str:
    """Hex digest over every file under ``root``: relative path, executable bit, bytes."""
    hasher = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        executable = "x" if path.stat().st_mode & 0o100 else "-"
        hasher.update(f"{relative}\0{executable}\0".encode())
        hasher.update(path.read_bytes())
        hasher.update(b"\0")
    hasher.update(extra.encode())
    return hasher.hexdigest()[:DIGEST_LENGTH]


def digest_of(*parts: str) -> str:
    """Hex digest of a sequence of strings, for images built from generated contexts."""
    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(part.encode())
        hasher.update(b"\0")
    return hasher.hexdigest()[:DIGEST_LENGTH]


def image_tag(task_name: str, role: str, digest: str) -> str:
    return f"{IMAGE_NAMESPACE}/{task_name}-{role}:{digest}"
