#!/bin/bash
cd /vercel/Ouo
export PYTHONPATH=/vercel/.local/lib/python3.14/site-packages:$PYTHONPATH
exec python3 main.py "$@"