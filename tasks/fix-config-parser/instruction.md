# Fix value parsing in kvconf

`kvconf` is a small INI-style configuration parser that lives in `kvconf/` in your
working directory. Users have reported two bugs in `kvconf.loads`:

1. **Values containing `=` are truncated.**
   `url = https://example.com/search?q=1&lang=en` parses as `en` instead of the full
   URL. The key is everything before the *first* `=` on the line and the value is
   everything after it (both trimmed of surrounding whitespace).

2. **Quoted values are cut at comment characters.**
   `greeting = "hello # world"` parses as `"hello` instead of `hello # world`.
   A `#` or `;` must only start a comment when it appears *outside* double quotes.
   Outside quotes the existing rule is unchanged: the first `#` or `;` on the line
   starts a comment, wherever it appears.

Fix both bugs in `kvconf/parser.py`. Keep the public API (`loads`, `load`,
`ParseError`) and all existing behaviour that the tests in `tests/` cover; run them
with `python -m pytest`. Quoted values do not support escape sequences and you do not
need to add them.
