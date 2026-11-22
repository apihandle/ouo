#!/bin/bash
# Run from this script's own directory, with whatever python3 is available.
# The previous version hard-coded /vercel/Ouo and python3.14, so it failed on
# every other machine.
cd "$(dirname "$0")" || exit 1
exec python3 main.py "$@"
