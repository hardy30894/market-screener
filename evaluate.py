#!/usr/bin/env python3
"""
Performance tracker — closes the loop on past reports. Reads a historical
day's pick data (docs/data/<date>.json), compares each pick's price THEN to its
price NOW, and measures the picks vs benchmarks (SPY, SMH).

This is honest accounting, not a backtest: it only knows what the reports
actually said on the day. It becomes meaningful only with WEEKS of forward data
— a handful of trading days is noise, and the script says so.

Usage:
    python evaluate.py                 # evaluate the earliest report on record
    python evaluate.py 2026-05-29      # evaluate a specific report date
"""
from __future__ import annotations

import json
import sys
from datetime import date as _date
from pathlib import Path

import pandas as pd
import yfinance as yf

HERE = Path(__file__).parent
GOOD = {"PRIMED *", "primed", "early (fundies)"}
BENCH = ["SPY", "SMH"]


def current_price(sym: str) -> float | None:
    try:
        h = yf.Ticker(sym).history(period="5d", auto_adjust=True)
        return float(h["Close"].iloc[-1]) if len(h) else None
    except Exception:
        return None


def main() -> None:
    ddir = HERE / "docs" / "data"
    dates = sorted(json.loads((ddir / "index.json").read_text())) if (ddir / "index.json").exists() else []
    if not dates:
        sys.exit("No history in docs/data/. Run report.py first.")
    target = sys.argv[1] if len(sys.argv) > 1 else dates[0]
    data = json.loads((ddir / f"{target}.json").read_text())

    today = _date.today().isoformat()
    td = (_date.fromisoformat(today) - _date.fromisoformat(target)).days
    print(f"\nEvaluating report {target}  ->  today {today}  ({td} calendar days)\n")

    # benchmark returns over the span
    bench_ret = {}
    for b in BENCH:
        try:
            h = yf.Ticker(b).history(start=target, auto_adjust=True)["Close"]
            if len(h) >= 2:
                bench_ret[b] = h.iloc[-1] / h.iloc[0] - 1
        except Exception:
            pass

    # top setups from that day (good verdicts), by score
    picks = [r for r in data["screen"] if r.get("verdict") in GOOD and r.get("price")]
    picks = sorted(picks, key=lambda r: r.get("score", 0), reverse=True)[:15]

    rows = []
    for r in picks:
        now = current_price(r["ticker"])
        if now is None:
            continue
        ret = now / r["price"] - 1
        rows.append({"ticker": r["ticker"], "verdict": r["verdict"],
                     "then": round(r["price"], 2), "now": round(now, 2),
                     "ret_%": round(100 * ret, 2)})
    df = pd.DataFrame(rows)

    print("=" * 64)
    print(f"TOP SETUPS from {target} — performance since")
    print("=" * 64)
    if len(df):
        print(df.to_string(index=False))
        avg = df["ret_%"].mean()
        win = (df["ret_%"] > 0).mean() * 100
        print(f"\n  avg pick return: {avg:+.2f}%   |   win rate: {win:.0f}%   |   n={len(df)}")
        for b, br in bench_ret.items():
            print(f"  vs {b}: {br*100:+.2f}%   ->   pick avg minus {b}: {avg - br*100:+.2f}%")
    else:
        print("  (no priced picks found)")

    # market-wide PRIMED* from that day, if present
    mp = data.get("market_primed", [])
    if mp:
        mrows = []
        for r in mp:
            then = next((s["price"] for s in data["screen"] if s["ticker"] == r["ticker"]), None)
            now = current_price(r["ticker"])
            if then and now:
                mrows.append({"ticker": r["ticker"], "cap": r.get("cap"),
                              "then": round(then, 2), "now": round(now, 2),
                              "ret_%": round(100 * (now / then - 1), 2)})
        if mrows:
            mdf = pd.DataFrame(mrows)
            print("\n" + "=" * 64)
            print(f"MARKET-WIDE PRIMED* from {target} — performance since")
            print("=" * 64)
            print(mdf.to_string(index=False))
            print(f"\n  avg: {mdf['ret_%'].mean():+.2f}%   |   win rate: {(mdf['ret_%']>0).mean()*100:.0f}%   |   n={len(mdf)}")

    print("\n" + "-" * 64)
    if td <= 7:
        print("⚠ SAMPLE FAR TOO SMALL. A few days (and weekends count as zero)")
        print("is NOISE, not signal. This is a process check, not a verdict.")
        print("Meaningful read needs WEEKS of forward data and many report dates.")
    print("Prices via yfinance (delayed/approx). NOT investment advice.")


if __name__ == "__main__":
    main()
