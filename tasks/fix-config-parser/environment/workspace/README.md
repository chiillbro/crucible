# kvconf

A tiny INI-style configuration parser.

```ini
# full-line comments start with # or ;
name = demo

[server]
host = 127.0.0.1   ; inline comment
port = 8080
motd = "quoted values keep their spaces"
```

```python
import kvconf

config = kvconf.loads(text)
config["server"]["port"]  # "8080"
config[""]["name"]  # keys before any section live in the "" section
```

Run the tests with `python -m pytest`.
