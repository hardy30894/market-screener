#!/bin/bash
# Daily sector report: screener (all themes) + earnings watch + exit monitor +
# market regime, into one dated markdown file. Wired to launchd at 9am ET.
# Fails loud: if the screener aborts (exit 2 = degraded data), that's noted in
# the report instead of silently writing a broken run.

# Portable: works on macOS (launchd) and Linux (GitHub Actions).
DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -z "$PY" ]; then
  if [ -x "/Users/hardy30894/.pyenv/versions/3.12.7/bin/python3" ]; then
    PY="/Users/hardy30894/.pyenv/versions/3.12.7/bin/python3"  # local Mac (launchd)
  else
    PY="python3"  # CI / anywhere on PATH
  fi
fi
cd "$DIR" || exit 1
# report.py builds the clean briefing (TL;DR -> summary -> full detail -> thesis)
# and writes results/<date>.md + results/latest.md. Script chatter stays on
# stdout (here), out of the report itself.
"$PY" report.py
