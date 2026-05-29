#!/usr/bin/env python3
"""
Position / exit monitor — the sell side. Feed it the tickers you hold; it reads
the SAME signals the screener uses, in reverse, and returns HOLD / TRIM / EXIT
with the reasons firing.

Exit rules (risk management, not prediction — defensible without a backtest):
  - TREND BREAK: 50-day crosses below 200-day (severe), or price below 50-day.
  - MOMENTUM ROLLOVER: 12-1 momentum turns negative.
  - ESTIMATES CUT: forward-EPS revisions turn net-negative (mirror of the entry).
  - RS BREAKDOWN: stops outperforming SOX (leaders peel off here first).
  - BELOW TRAILING STOP: close under a chandelier stop (22d high - 3*ATR).
  - POST-EARNINGS DROP: just reported and fell hard.

Verdict: EXIT if a severe signal fires (trend fully broken / below stop) or >=3
signals; TRIM if 1-2; HOLD if clean.

Usage:
    python monitor.py NVDA MU COHR          # monitor these holdings
    python monitor.py --file holdings.txt    # one ticker per line
NOT investment advice. Exits are rules to manage risk, not market calls.
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
import pandas as pd

from datasource import YFinanceSource, DataError

BENCH = "SOXX"


def atr(hist: pd.DataFrame, n: int = 22) -> pd.Series:
    h, l, c = hist["High"], hist["Low"], hist["Close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def exit_signals(b: dict, bench: pd.Series) -> tuple[list, dict]:
    hist = b["hist"]
    c = hist["Close"]
    px = float(c.iloc[-1])
    sigs, severe = [], False

    ma50 = c.rolling(50).mean().iloc[-1]
    ma200 = c.rolling(200).mean().iloc[-1]
    if pd.notna(ma200) and ma50 < ma200:
        sigs.append("trend broken (50<200)")
        severe = True
    elif px < ma50:
        sigs.append("below 50d")

    if len(c) >= 252:
        mom = px / float(c.iloc[-252]) - 1
        if mom < 0:
            sigs.append("12m momentum negative")

    er = b.get("eps_rev")
    try:
        row = er.loc["+1y"] if "+1y" in er.index else er.iloc[0]
        up = float(row.get("upLast30days", 0) or 0)
        dn = float(row.get("downLast30days", 0) or 0)
        if dn > up:
            sigs.append("estimates being cut")
    except Exception:
        pass

    if bench is not None and len(bench) > 70:
        rs = (px / float(c.iloc[-63]) - 1) - (float(bench.iloc[-1]) / float(bench.iloc[-63]) - 1)
        if rs < 0:
            sigs.append("lagging SOX")

    a = atr(hist).iloc[-1]
    stop = float(hist["High"].tail(22).max() - 3 * a) if pd.notna(a) else np.nan
    if pd.notna(stop) and px < stop:
        sigs.append("below trailing stop")
        severe = True

    # post-earnings drop: reported within ~5d and last week was sharply down
    ed = b.get("earn_dates")
    try:
        if ed is not None and len(ed):
            now = pd.Timestamp.now(tz=ed.index.tz) if getattr(ed.index, "tz", None) else pd.Timestamp.now()
            recent = ed.index[(ed.index <= now)]
            if len(recent) and (now - recent.max()).days <= 5:
                wk = px / float(c.iloc[-6]) - 1
                if wk < -0.05:
                    sigs.append(f"post-earnings drop ({wk:+.0%})")
                    severe = True
    except Exception:
        pass

    return sigs, {"px": round(px, 2), "stop": round(stop, 2) if pd.notna(stop) else np.nan,
                  "to_stop_%": round(100 * (px - stop) / px, 1) if pd.notna(stop) else np.nan,
                  "severe": severe}


def action(sigs: list, severe: bool) -> str:
    if severe or len(sigs) >= 3:
        return "EXIT"
    if len(sigs) >= 1:
        return "TRIM"
    return "HOLD"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="*")
    ap.add_argument("--file", help="file with one ticker per line")
    args = ap.parse_args()

    syms = [s.upper() for s in args.tickers]
    if args.file:
        with open(args.file) as f:
            syms += [ln.strip().upper() for ln in f if ln.strip() and not ln.startswith("#")]
    syms = list(dict.fromkeys(syms))
    if not syms:
        sys.exit("Usage: python monitor.py NVDA MU COHR   (or --file holdings.txt)")

    src = YFinanceSource()
    bench_b = src.bundle(BENCH)
    bench = bench_b["hist"]["Close"] if bench_b else None

    rows = []
    for s in syms:
        b = src.bundle(s)
        if b is None:
            rows.append({"ticker": s, "action": "NO DATA", "signals": "fetch failed"})
            continue
        sigs, m = exit_signals(b, bench)
        rows.append({"ticker": s, "action": action(sigs, m["severe"]),
                     "price": m["px"], "stop": m["stop"], "to_stop_%": m["to_stop_%"],
                     "signals": "; ".join(sigs) if sigs else "clean"})

    try:
        src.assert_healthy()
    except DataError as e:
        print(f"\n{e}")
        sys.exit(2)

    order = {"EXIT": 0, "TRIM": 1, "HOLD": 2, "NO DATA": 3}
    df = pd.DataFrame(rows).sort_values("action", key=lambda c: c.map(order))
    pd.set_option("display.max_rows", None, "display.width", 200, "display.max_colwidth", 60)
    print("\n" + "=" * 72)
    print("POSITION MONITOR — exit signals on your holdings")
    print("=" * 72)
    print(df.to_string(index=False))
    print("\nEXIT = severe signal (trend fully broken / below stop / earnings gap)")
    print("or >=3 signals. TRIM = 1-2 signals. HOLD = clean. 'to_stop_%' = how far")
    print("price is above the trailing stop (negative = already below it).")
    print("\nExits are risk-management rules, NOT backtested predictions. Not advice.")


if __name__ == "__main__":
    main()
