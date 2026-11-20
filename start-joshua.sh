#!/bin/sh
# Starts Joshua. Everything he needs lives in this folder.
DIR="$(dirname "$(readlink -f "$0")")"
systemctl --user is-active --quiet joshua-brain || systemctl --user start joshua-brain
exec "$DIR/.venv/bin/python" "$DIR/joshua.py" "$@"
