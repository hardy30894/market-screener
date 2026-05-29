#!/usr/bin/env python3
"""
Market-regime / risk dashboard. Reads widely-watched RISK GAUGES and rolls them
into a RISK-ON / NEUTRAL / RISK-OFF context read.

READ THIS, IT MATTERS: this does NOT predict crashes. Nobody can. These gauges
read the *ambient risk environment* — they have HIGH false-positive rates and
LONG, VARIABLE lead times (the yield curve can invert a year+ before anything;
breadth is weak in plenty of ongoing bull markets). Use this as a position-
sizing / risk-budget input ("environment fragile -> tighten up"), NOT as a
sell-everything trigger.

Gauges (all from free Yahoo data):
  - SPX trend:    SPY vs its 200-day  (below = risk-off)
  - Breadth:      RSP/SPY 50d slope   (equal-weight lagging cap-weight = narrowing)
  - Credit:       HYG/IEF 50d slope   (falling = high-yield spreads widening = stress)
  - Volatility:   ^VIX level          (>20 elevated, >27 high)
  - Yield curve:  10y - 3mo (^TNX-^IRX) (inverted = recession-risk, long lead)
  - Semis lead:   SOXX vs SPY 63d      (semis lead the tape both ways)

Usage: python regime.py
NOT investment advice.
"""
from __future__ import annotations

import sys
import time

import numpy as np
import pandas as pd

try:
    import yfinance as yf
except ImportError:
    sys.exit("pip install yfinance pandas numpy")

TICKERS = ["SPY", "RSP", "HYG", "IEF", "SOXX", "^VIX", "^TNX", "^IRX"]


def load() -> pd.DataFrame:
    for attempt in range(3):
        try:
            raw = yf.download(TICKERS, period="2y", auto_adjust=True,
                              progress=False, threads=True)
            close = raw["Close"] if "Close" in raw.columns.get_level_values(0) else raw
            if close.dropna(how="all").shape[0] > 220:
                return close
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    raise SystemExit("regime: data source unavailable (Yahoo). Try again later.")


def slope50(s: pd.Series) -> float:
    s = s.dropna()
    return float(s.iloc[-1] / s.iloc[-50] - 1) if len(s) > 50 else np.nan


def compute_regime() -> dict:
    """Compute regime gauges -> {verdict, composite, n, gauges}. Shared by CLI and report.py."""
    px = load()

    def col(t):
        return px[t].dropna() if t in px.columns else pd.Series(dtype=float)

    gauges = []  # (name, reading, state, score, caveat)

    spy = col("SPY")
    ma200 = spy.rolling(200).mean().iloc[-1]
    above = spy.iloc[-1] > ma200
    gauges.append(("SPX trend", f"SPY {'above' if above else 'BELOW'} 200d "
                   f"({spy.iloc[-1]/ma200-1:+.1%})", "risk-on" if above else "risk-off",
                   1 if above else -1, "below 200d historically precedes most drawdowns, but whipsaws"))

    br = slope50(col("RSP")) - slope50(col("SPY"))
    st = "risk-on" if br > 0.005 else "risk-off" if br < -0.02 else "neutral"
    gauges.append(("Breadth (RSP-SPY 50d)", f"{br:+.1%}", st,
                   1 if st == "risk-on" else -1 if st == "risk-off" else 0,
                   "narrow breadth can persist for a long time in bull markets"))

    cr = slope50(col("HYG") / col("IEF"))
    st = "risk-on" if cr > 0 else "risk-off" if cr < -0.02 else "neutral"
    gauges.append(("Credit (HYG/IEF 50d)", f"{cr:+.1%}", st,
                   1 if st == "risk-on" else -1 if st == "risk-off" else 0,
                   "credit usually leads equities into stress — weight this one"))

    vix = col("^VIX").iloc[-1]
    st = "risk-off" if vix > 27 else "neutral" if vix > 20 else "risk-on"
    gauges.append(("Volatility (VIX)", f"{vix:.1f}", st,
                   -1 if vix > 27 else 0 if vix > 20 else 1,
                   "VIX is coincident, not leading — spikes confirm, don't predict"))

    tnx, irx = col("^TNX"), col("^IRX")
    if len(tnx) and len(irx):
        spread = float(tnx.iloc[-1] - irx.iloc[-1])
        inv = spread < 0
        gauges.append(("Yield curve (10y-3mo)", f"{spread:+.2f}pp", "risk-off" if inv else "risk-on",
                       -1 if inv else 1,
                       "inversion leads recessions by 6-18mo — very long, noisy lead"))

    soxx = slope50(col("SOXX")) - slope50(col("SPY"))
    # 63d relative
    s63 = (col("SOXX").iloc[-1] / col("SOXX").iloc[-63] - 1) - (spy.iloc[-1] / spy.iloc[-63] - 1)
    st = "risk-on" if s63 > 0 else "risk-off"
    gauges.append(("Semis leadership (SOXX-SPY 63d)", f"{s63:+.1%}", st,
                   1 if s63 > 0 else -1, "semis lead the tape both ways — key for THIS basket"))

    composite = sum(g[3] for g in gauges)
    n = len(gauges)
    verdict = "RISK-ON" if composite >= 2 else "RISK-OFF" if composite <= -2 else "NEUTRAL"
    return {"verdict": verdict, "composite": composite, "n": n, "gauges": gauges}


def main() -> None:
    r = compute_regime()
    verdict, composite, n, gauges = r["verdict"], r["composite"], r["n"], r["gauges"]

    print("\n" + "=" * 72)
    print(f"MARKET REGIME DASHBOARD   ->   {verdict}   (score {composite:+d} / range ±{n})")
    print("=" * 72)
    print(f"  {'GAUGE':32}{'READING':>14}  {'STATE':>9}")
    print("  " + "-" * 60)
    for name, reading, state, score, _ in gauges:
        print(f"  {name:32}{reading:>14}  {state:>9}")
    print("\n  caveats:")
    for name, _, _, _, cav in gauges:
        print(f"   - {name.split(' (')[0]}: {cav}")
    print("\nRISK-OFF here means 'environment fragile, size down / tighten stops',")
    print("NOT 'sell everything'. These gauges have high false-positive rates and")
    print("long, variable lead times. This is CONTEXT, not a market-timing call.")
    print("The most actionable early warning for THIS basket is sector-internal:")
    print("memory pricing rolling over + hyperscaler capex guide-downs (see the")
    print("daily catalyst monitor). NOT investment advice.")


if __name__ == "__main__":
    main()
