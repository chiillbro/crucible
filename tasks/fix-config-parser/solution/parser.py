"""Parse INI-style text into nested dictionaries.

Grammar (one entry per line):

    [section]            start a section; keys before any header go in section ""
    key = value          value may be bare or wrapped in double quotes
    # comment            full-line comments start with # or ;
    key = value ; note   inline comments are stripped

Quoted values have their quotes removed and keep inner whitespace. Escape sequences
are not supported.
"""

from __future__ import annotations

from pathlib import Path

COMMENT_PREFIXES = ("#", ";")
QUOTE = '"'

Config = dict[str, dict[str, str]]


class ParseError(ValueError):
    """Raised for malformed input; ``lineno`` is 1-based."""

    def __init__(self, lineno: int, message: str) -> None:
        super().__init__(f"line {lineno}: {message}")
        self.lineno = lineno


def load(path: str | Path) -> Config:
    """Parse the file at ``path``."""
    return loads(Path(path).read_text(encoding="utf-8"))


def loads(text: str) -> Config:
    """Parse ``text`` into ``{section: {key: value}}``."""
    sections: Config = {"": {}}
    current = ""
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = _strip_comment(raw).strip()
        if not line:
            continue
        if line.startswith("["):
            current = _parse_header(lineno, line)
            sections.setdefault(current, {})
            continue
        if "=" not in line:
            raise ParseError(lineno, "expected 'key = value'")
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not key:
            raise ParseError(lineno, "empty key")
        sections[current][key] = _unquote(value)
    return sections


def _parse_header(lineno: int, line: str) -> str:
    if not line.endswith("]"):
        raise ParseError(lineno, "unterminated section header")
    name = line[1:-1].strip()
    if not name:
        raise ParseError(lineno, "empty section name")
    return name


def _strip_comment(line: str) -> str:
    """Cut the line at the first comment prefix that is not inside double quotes."""
    in_quotes = False
    for index, char in enumerate(line):
        if char == QUOTE:
            in_quotes = not in_quotes
        elif char in COMMENT_PREFIXES and not in_quotes:
            return line[:index]
    return line


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == QUOTE:
        return value[1:-1]
    return value
