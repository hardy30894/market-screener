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
  - BLOW-OFF: volume-climax spike far above the 50-day. This does NOT trim — you
    never cut a runner on strength (that's how a 10x becomes a 2x). It TIGHTENS
    the trailing stop (2*ATR instead of 3*ATR) so a real top banks you near the
    high, while the position keeps riding as long as it keeps climbing.

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
from screener import technical_signals, fundamental_signals, verdict, extreme_flag, MIN_EPS_REV

BENCH = "SPY"   # holdings are cross-sector, so benchmark relative strength vs the broad market


def atr(hist: pd.DataFrame, n: int = 22) -> pd.Series:
    h, l, c = hist["High"], hist["Low"], hist["Close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def exit_signals(b: dict, bench: pd.Series, blowoff: bool = False) -> tuple[list, dict]:
    hist = b["hist"]
    c = hist["Close"]
    px = float(c.iloc[-1])
    sigs, severe = [], False

    ma50 = c.rolling(50).mean().iloc[-1]
    ma200 = c.rolling(200).mean().iloc[-1]
    below50 = px < ma50
    below200 = bool(pd.notna(ma200) and px < ma200)
    st = (px / float(c.iloc[-21]) - 1) if len(c) >= 22 else 0.0
    recovering = (not below50) and below200 and st > 0.07   # reclaimed 50d, below 200d, strong 1mo
    if below50 and below200:
        sigs.append("downtrend (below 50d & 200d)")
        severe = True
    elif below200 and not recovering:
        sigs.append("below 200d")
    elif below50:
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
        if dn > up and (up + dn) >= MIN_EPS_REV:   # ignore 1-2 analyst noise on thin coverage
            sigs.append("estimates being cut")
    except Exception:
        pass

    if bench is not None and len(bench) > 70:
        rs = (px / float(c.iloc[-63]) - 1) - (float(bench.iloc[-1]) / float(bench.iloc[-63]) - 1)
        if rs < 0:
            sigs.append("lagging market")

    a = atr(hist).iloc[-1]
    mult = 2.0 if blowoff else 3.0   # blow-off -> tighter stop, bank the spike if it tops
    stop = float(hist["High"].tail(22).max() - mult * a) if pd.notna(a) else np.nan
    if pd.notna(stop) and px < stop:
        sigs.append("below trailing stop")
        if below200:                 # stop breach + broken trend = exit; in an uptrend = trim
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
                  "severe": severe, "recovering": recovering,
                  "note": "blow-off — stop tightened" if blowoff else ""}


def action(sigs: list, severe: bool, recovering: bool = False) -> str:
    if severe:
        return "EXIT"            # downtrend / stop-breach-in-downtrend / earnings gap
    if recovering:
        return "HOLD"            # improving — don't trim a recovery on soft signals
    if "below trailing stop" in sigs:
        return "TRIM"            # stop breach in an intact uptrend -> trim/tighten, not full exit
    if len(sigs) >= 2:
        return "TRIM"            # 2+ soft signals
    return "HOLD"


def monitor_holdings(src, syms, bench=None) -> list[dict]:
    """Score a holdings list into HOLD/TRIM/EXIT rows. Shared by CLI and report.py."""
    if bench is None:
        bench_b = src.bundle(BENCH)
        bench = bench_b["hist"]["Close"] if bench_b else None
    rows = []
    for s in syms:
        b = src.bundle(s)
        if b is None:
            rows.append({"ticker": s, "verdict": "?", "action": "NO DATA", "signals": "fetch failed"})
            continue
        sig = {}
        try:
            sig.update(technical_signals(b["hist"], bench))
            sig.update(fundamental_signals(b))
        except Exception:
            pass
        blowoff = bool(sig and "blow-off" in extreme_flag(sig))   # volume climax
        sigs, m = exit_signals(b, bench, blowoff=blowoff)
        # blow-off is a display caution + a tighter stop, NOT an action trigger:
        # never trim a runner on strength; let the stop do the selling.
        disp = list(sigs) + ([m["note"]] if m.get("note") else [])
        rows.append({"ticker": s, "verdict": verdict(sig) if sig else "?",
                     "action": action(sigs, m["severe"], m["recovering"]),
                     "price": m["px"], "stop": m["stop"], "to_stop_%": m["to_stop_%"],
                     "signals": "; ".join(disp) if disp else "clean"})
    return rows


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
    rows = monitor_holdings(src, syms)

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
    print("\nEXIT = downtrend (below 50d & 200d), stop-breach in a downtrend, or an")
    print("earnings gap-down. TRIM = stop-breach in an intact uptrend, or 2+ soft")
    print("signals. HOLD = clean or recovering. 'to_stop_%' = distance above stop.")
    print("\nExits are risk-management rules, NOT backtested predictions. Not advice.")


if __name__ == "__main__":
    main()
