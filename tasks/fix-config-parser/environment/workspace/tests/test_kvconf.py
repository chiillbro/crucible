import pytest
from kvconf import ParseError, loads


def test_sections_and_default_section():
    config = loads("name = demo\n[server]\nhost = 127.0.0.1\nport = 8080\n")
    assert config == {"": {"name": "demo"}, "server": {"host": "127.0.0.1", "port": "8080"}}


def test_blank_lines_and_full_line_comments_are_ignored():
    config = loads("\n# comment\n; another\n[a]\n\nx = 1\n")
    assert config == {"": {}, "a": {"x": "1"}}


def test_inline_comments_are_stripped_from_bare_values():
    config = loads("[a]\nx = 1 # one\ny = 2 ; two\n")
    assert config["a"] == {"x": "1", "y": "2"}


def test_quoted_values_keep_inner_whitespace():
    config = loads('[a]\nmotd = "  hello  "\n')
    assert config["a"]["motd"] == "  hello  "


def test_missing_equals_is_a_parse_error():
    with pytest.raises(ParseError, match="line 2: expected 'key = value'"):
        loads("[a]\nnot an entry\n")


def test_unterminated_header_is_a_parse_error():
    with pytest.raises(ParseError, match="line 1: unterminated section header"):
        loads("[a\n")
