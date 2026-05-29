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
mkdir -p results
DATE=$(date +%Y-%m-%d)
OUT="results/$DATE.md"

{
  echo "# Daily Sector Report — $DATE"
  echo "_Generated $(date '+%Y-%m-%d %H:%M %Z'). Not investment advice._"
  echo

  echo "## 1. Screener — all themes (picks + EARNINGS WATCH at top)"
  echo '```'
  "$PY" screener.py --all-themes --min-score 55
  SC=$?
  echo '```'
  if [ $SC -ne 0 ]; then
    echo
    echo "> ⚠ **screener exited $SC** — data source degraded; picks above may be aborted/incomplete. Do not trust today's rankings."
  fi
  echo

  echo "## 2. Position monitor — exit signals on tracked names"
  echo '```'
  "$PY" monitor.py --file holdings.txt
  echo '```'
  echo

  echo "## 3. Market regime — risk context"
  echo '```'
  "$PY" regime.py
  echo '```'
  echo

  echo "## 4. Macro thesis (standing reference)"
  echo "_Static thesis compiled 2026-05-28. Whether it still holds is tracked daily"
  echo "by the cloud catalyst monitor (claude.ai routines), separate from this file._"
  echo
  if [ -f MARKET_OUTLOOK_2026_2027.md ]; then
    # skip the file's own H1 so heading levels stay consistent under section 4
    tail -n +2 MARKET_OUTLOOK_2026_2027.md
  else
    echo "_MARKET_OUTLOOK_2026_2027.md not found._"
  fi
} > "$OUT" 2>&1

echo "Wrote $OUT"
