"""Content digest and image tag tests."""

from __future__ import annotations

from pathlib import Path

from crucible.images import content_digest, image_tag


def test_digest_is_stable_and_sensitive_to_content_names_and_mode(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.sh").write_text("b")
    first = content_digest(tmp_path)
    assert first == content_digest(tmp_path)
    assert len(first) == 12

    (tmp_path / "sub" / "b.sh").chmod(0o755)
    with_exec = content_digest(tmp_path)
    assert with_exec != first

    (tmp_path / "a.txt").write_text("changed")
    assert content_digest(tmp_path) != with_exec

    (tmp_path / "a.txt").rename(tmp_path / "c.txt")
    renamed = content_digest(tmp_path)
    (tmp_path / "c.txt").rename(tmp_path / "a.txt")
    assert renamed != content_digest(tmp_path)


def test_extra_salt_changes_digest(tmp_path: Path) -> None:
    (tmp_path / "f").write_text("x")
    assert content_digest(tmp_path) != content_digest(tmp_path, extra="sha256:base")


def test_image_tag_format() -> None:
    assert image_tag("fix-parser", "env", "abc123") == "crucible/fix-parser-env:abc123"
