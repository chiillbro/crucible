"""Hidden tests: the reported bugs plus guards for existing behaviour."""

import pytest
from kvconf import ParseError, loads

# --- bug 1: '=' inside values -----------------------------------------------------


def test_value_may_contain_equals():
    config = loads("[a]\nurl = https://example.com/search?q=1&lang=en\n")
    assert config["a"]["url"] == "https://example.com/search?q=1&lang=en"


def test_key_is_everything_before_first_equals():
    config = loads("expr = a = b = c\n")
    assert config[""] == {"expr": "a = b = c"}


def test_empty_value_is_allowed():
    assert loads("x =\n")[""] == {"x": ""}


def test_equals_inside_quotes():
    config = loads('[a]\nsql = "SELECT 1 WHERE x = 2"\n')
    assert config["a"]["sql"] == "SELECT 1 WHERE x = 2"


# --- bug 2: comment markers inside quotes -----------------------------------------


def test_hash_inside_quotes_is_not_a_comment():
    assert loads('greeting = "hello # world"\n')[""]["greeting"] == "hello # world"


def test_semicolon_inside_quotes_is_not_a_comment():
    assert loads('sep = "a;b;c"\n')[""]["sep"] == "a;b;c"


def test_comment_after_quoted_value_is_still_stripped():
    config = loads('motd = "x # y"   # trailing comment\n')
    assert config[""]["motd"] == "x # y"


def test_both_bugs_together():
    config = loads('[db]\ndsn = "postgres://u:p@h/db?sslmode=require#frag" ; note\n')
    assert config["db"]["dsn"] == "postgres://u:p@h/db?sslmode=require#frag"


# --- existing behaviour must be preserved -----------------------------------------


def test_sections_and_default_section():
    config = loads("name = demo\n[server]\nhost = 127.0.0.1\nport = 8080\n")
    assert config == {"": {"name": "demo"}, "server": {"host": "127.0.0.1", "port": "8080"}}


def test_blank_lines_and_full_line_comments_are_ignored():
    assert loads("\n# comment\n; another\n[a]\n\nx = 1\n") == {"": {}, "a": {"x": "1"}}


def test_inline_comments_are_stripped_from_bare_values():
    assert loads("[a]\nx = 1 # one\ny = 2 ; two\n")["a"] == {"x": "1", "y": "2"}


def test_bare_value_is_cut_at_first_comment_prefix_anywhere():
    assert loads("path = /tmp/a#b\n")[""]["path"] == "/tmp/a"


def test_quoted_values_keep_inner_whitespace():
    assert loads('[a]\nmotd = "  hello  "\n')["a"]["motd"] == "  hello  "


def test_header_with_trailing_comment():
    assert loads("[a] # section a\nx = 1\n")["a"] == {"x": "1"}


def test_repeated_section_merges():
    assert loads("[a]\nx = 1\n[b]\n[a]\ny = 2\n")["a"] == {"x": "1", "y": "2"}


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[a]\nnot an entry\n", "line 2: expected 'key = value'"),
        ("[a\n", "line 1: unterminated section header"),
        ("[ ]\n", "line 1: empty section name"),
        ("\n\n = 3\n", "line 3: empty key"),
    ],
)
def test_parse_errors_report_line_numbers(text, message):
    with pytest.raises(ParseError, match=message):
        loads(text)
