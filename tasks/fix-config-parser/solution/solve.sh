#!/bin/sh
# Reference solution. Runs with the task workdir as the current directory.
set -eu
cp "$(dirname "$0")/parser.py" kvconf/parser.py
