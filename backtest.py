#!/usr/bin/env python3
"""
Backtest harness for the screener's factors. This is the part that earns (or
denies) the word "edge": it measures whether the signals actually carry
information about FORWARD returns, out-of-sample, after costs.

Methodology (designed to avoid the usual lies):
  - POINT-IN-TIME factors: every factor at date t uses only price data <= t.
  - Forward-return labels: 21d and 63d returns measured t -> t+h (the future,
    relative to t, but realized within the historical window).
  - Rank Information Coefficient (Spearman) per rebalance date, per factor and
    for the composite. Reported as mean IC, IC t-stat, and IC IR (annualized).
  - Quintile spread: top-20% vs bottom-20% mean forward return.
  - Long top-quintile equity curve vs the SMH benchmark, with turnover-based
    transaction costs (configurable bps/side).
  - WALK-FORWARD weights: weights = trailing-24mo positive IC per factor,
    applied to the NEXT month only. The OOS composite IC is the honest number.

KNOWN LIMITATIONS (stated, not hidden):
  - SURVIVORSHIP BIAS: the universe is current listings only. Delisted/acquired
    losers (e.g. Infinera) are absent, which biases results optimistic. A
    bias-free study needs Sharadar/Norgate/CRSP. This harness validates the
    PRICE/TECHNICAL factors only; fundamental factors (rev_accel, eps_rev)
    cannot be backtested point-in-time on free yfinance data (restated values),
    so they are deliberately excluded here.
  - Free EOD data; no intraday, no borrow/short constraints modeled.

Usage:
    pip install yfinance pandas numpy
    python backtest.py
    python backtest.py --start 2016-01-01 --cost-bps 10 --horizon 21
"""
from __future__ import annotations

import argparse
import sys
import warnings

import numpy as np
import pandas as pd

warnings.simplefilter("ignore")
try:
    import yfinance as yf
except ImportError:
    sys.exit("pip install yfinance pandas numpy")

BENCH = "SMH"

# Broad CURRENT US tech-hardware universe (semis, equipment, optical, components,
# servers). Survivorship-biased by construction — see header.
UNIVERSE = [
    "NVDA", "AMD", "AVGO", "TSM", "INTC", "QCOM", "TXN", "MU", "ADI", "MCHP",
    "MRVL", "ON", "NXPI", "STM", "SWKS", "QRVO", "LSCC", "RMBS", "MPWR", "POWI",
    "DIOD", "AOSL", "SITM", "MTSI", "ALGM", "SLAB", "MXL", "SIMO", "AMBA", "CRUS",
    "ASML", "AMAT", "LRCX", "KLAC", "TER", "ENTG", "MKSI", "ONTO", "NVMI", "CAMT",
    "ACMR", "AEHR", "COHU", "FORM", "KLIC", "UCTT", "ICHR", "PLAB", "AZTA",
    "COHR", "LITE", "CIEN", "ANET", "CRDO", "NTGR", "EXTR", "CALX", "HLIT", "DGII",
    "APH", "TEL", "GLW", "JBL", "FLEX", "SANM", "PLXS", "BHE", "VICR", "VSH",
    "OSIS", "SMCI", "DELL", "HPQ", "WDC", "STX", "KEYS", "NOVT", "KN", "CRUS",
    "QCOM", "INTC",  # dups harmless; deduped below
]


def wide_download(start: str) -> tuple[dict, pd.Series]:
    syms = sorted(set(UNIVERSE))
    raw = yf.download(syms + [BENCH], start=start, auto_adjust=True,
                      group_by="ticker", threads=True, progress=False)
    closes, highs, lows, vols = {}, {}, {}, {}
    for s in syms:
        try:
            d = raw[s].dropna(how="all")
            if len(d) < 300:
                continue
            closes[s], highs[s], lows[s], vols[s] = d["Close"], d["High"], d["Low"], d["Volume"]
        except Exception:
            continue
    close = pd.DataFrame(closes)
    high = pd.DataFrame(highs)
    low = pd.DataFrame(lows)
    bench = raw[BENCH]["Close"].reindex(close.index).ffill()
    return {"close": close, "high": high, "low": low}, bench


def build_factors(px: dict, bench: pd.Series) -> dict:
    c, h, l = px["close"], px["high"], px["low"]
    # ATR(14) per ticker
    pc = c.shift(1)
    tr = pd.concat([(h - l).abs(), (h - pc).abs(), (l - pc).abs()]).groupby(level=0).max()
    tr = pd.DataFrame({col: pd.concat([(h[col] - l[col]),
                                       (h[col] - pc[col]).abs(),
                                       (l[col] - pc[col]).abs()], axis=1).max(axis=1)
                       for col in c.columns})
    atr = tr.rolling(14).mean()

    ma50, ma200 = c.rolling(50).mean(), c.rolling(200).mean()
    factors = {
        "mom_12_1": c.shift(21) / c.shift(252) - 1,            # 12m return, skip last month
        "near_high": c / c.rolling(252).max(),                  # proximity to 52w high
        "trend": c / ma200 - 1,                                 # extension above 200d
        "squeeze": atr.shift(63) / atr,                         # ATR contraction (>1 = coiling)
        "lowvol": -c.pct_change().rolling(63).std(),            # inverse realized vol
    }
    b_ret = bench / bench.shift(63) - 1
    factors["rel_str"] = (c / c.shift(63) - 1).sub(b_ret, axis=0)  # 3m RS vs SMH
    return factors


def xs_rank(wide: pd.DataFrame) -> pd.DataFrame:
    return wide.rank(axis=1, pct=True)


def ic_per_date(factor: pd.DataFrame, fwd: pd.DataFrame, dates) -> pd.Series:
    out = {}
    for d in dates:
        if d not in factor.index or d not in fwd.index:
            continue
        df = pd.concat([factor.loc[d], fwd.loc[d]], axis=1).dropna()
        if len(df) >= 10:
            out[d] = df.iloc[:, 0].corr(df.iloc[:, 1], method="spearman")
    return pd.Series(out)


def stats(ic: pd.Series, periods_per_year: float) -> dict:
    n = ic.notna().sum()
    m, s = ic.mean(), ic.std()
    return {"mean_IC": round(m, 4), "hit_%": round(100 * (ic > 0).mean(), 1),
            "t_stat": round(m / s * np.sqrt(n), 2) if s else np.nan,
            "IC_IR_ann": round(m / s * np.sqrt(periods_per_year), 2) if s else np.nan,
            "n": int(n)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--horizon", type=int, default=21, help="forward-return days")
    ap.add_argument("--cost-bps", type=float, default=10.0, help="cost per side")
    args = ap.parse_args()

    print(f"Downloading {len(set(UNIVERSE))} tickers from {args.start} ...")
    px, bench = wide_download(args.start)
    c = px["close"]
    print(f"Usable tickers: {c.shape[1]}, trading days: {c.shape[0]}\n")

    factors = build_factors(px, bench)
    h = args.horizon
    fwd = c.shift(-h) / c - 1                       # forward return per ticker
    fwd_xs = fwd.sub(fwd.mean(axis=1), axis=0)       # market-neutral (isolates selection)

    # monthly rebalance dates = first trading day of each month
    months = c.index.to_period("M")
    rebal = c.index[~months.duplicated(keep="first")]
    rebal = rebal[(rebal >= c.index[252]) & (rebal <= c.index[-h - 1])]
    ppy = 12.0

    # --- per-factor IC ---
    print("=" * 74)
    print(f"PER-FACTOR RANK-IC  (forward {h}d, market-neutral, {len(rebal)} rebalances)")
    print("=" * 74)
    rows = []
    ic_store = {}
    for name, fac in factors.items():
        ic = ic_per_date(fac, fwd_xs, rebal)
        ic_store[name] = ic
        rows.append({"factor": name, **stats(ic, ppy)})
    icdf = pd.DataFrame(rows).set_index("factor")
    print(icdf.to_string())
    print("\nIC_IR_ann > ~0.5 is decent, > 1.0 is strong. t_stat > 2 = significant.")

    # --- equal-weight composite ---
    ranked = {k: xs_rank(v) for k, v in factors.items()}
    comp_ew = sum(ranked.values()) / len(ranked)
    ic_ew = ic_per_date(comp_ew, fwd_xs, rebal)
    print("\n" + "=" * 74)
    print("EQUAL-WEIGHT COMPOSITE")
    print("=" * 74)
    print(pd.Series(stats(ic_ew, ppy)).to_string())

    # --- quintile spread (composite) ---
    print("\n" + "=" * 74)
    print("QUINTILE FORWARD RETURNS (composite, market-neutral, mean per rebalance)")
    print("=" * 74)
    qret = {q: [] for q in range(1, 6)}
    for d in rebal:
        s = comp_ew.loc[d].dropna()
        r = fwd_xs.loc[d].reindex(s.index)
        valid = pd.concat([s, r], axis=1).dropna()
        if len(valid) < 15:
            continue
        q = pd.qcut(valid.iloc[:, 0], 5, labels=False, duplicates="drop")
        for qi in range(5):
            sel = valid.iloc[:, 1][q == qi]
            if len(sel):
                qret[qi + 1].append(sel.mean())
    qsummary = {f"Q{q} ({'low' if q==1 else 'high' if q==5 else ''})":
                round(100 * np.mean(v), 3) for q, v in qret.items() if v}
    for k, v in qsummary.items():
        print(f"  {k:12} mean fwd {h}d (vs universe): {v:+.3f}%")
    spread = qsummary.get("Q5 (high)", 0) - qsummary.get("Q1 (low)", 0)
    print(f"  Q5 - Q1 spread: {spread:+.3f}%  (monotonic increase = real signal)")

    # --- walk-forward weighted composite (OOS) ---
    print("\n" + "=" * 74)
    print("WALK-FORWARD COMPOSITE (weights = trailing-24mo positive IC, applied OOS)")
    print("=" * 74)
    ic_hist = pd.DataFrame(ic_store).reindex(rebal)
    oos_ic = {}
    for i, d in enumerate(rebal):
        if i < 24:
            continue
        w = ic_hist.iloc[i - 24:i].mean().clip(lower=0)
        if w.sum() == 0:
            continue
        w = w / w.sum()
        comp = sum(w[k] * ranked[k].loc[d] for k in ranked)
        r = fwd_xs.loc[d]
        df = pd.concat([comp, r], axis=1).dropna()
        if len(df) >= 10:
            oos_ic[d] = df.iloc[:, 0].corr(df.iloc[:, 1], method="spearman")
    oos = pd.Series(oos_ic)
    print(pd.Series(stats(oos, ppy)).to_string())
    print("\nThis OOS number is the honest one: weights never saw the future.")
    print("If OOS mean_IC <= 0, the composite has no demonstrated edge on this")
    print("universe/window and should NOT be trusted regardless of how good the")
    print("live screen looks.")

    # --- long top-quintile equity curve vs benchmark, after costs ---
    print("\n" + "=" * 74)
    print(f"LONG TOP-QUINTILE PORTFOLIO vs {BENCH} (after {args.cost_bps}bps/side)")
    print("=" * 74)
    fwd_abs = c.shift(-h) / c - 1   # absolute (not neutralized) for P&L
    eq, bench_eq = [1.0], [1.0]
    prev_top = set()
    cost = args.cost_bps / 10000.0
    for d in rebal:
        s = comp_ew.loc[d].dropna()
        if len(s) < 15:
            continue
        top = set(s.sort_values(ascending=False).head(max(5, len(s) // 5)).index)
        r = fwd_abs.loc[d].reindex(top).dropna()
        if r.empty:
            continue
        turn = 1 - len(prev_top & top) / len(top) if prev_top else 1.0
        port_r = r.mean() - 2 * cost * turn
        eq.append(eq[-1] * (1 + port_r))
        b = bench.shift(-h).loc[d] / bench.loc[d] - 1 if d in bench.index else 0
        bench_eq.append(bench_eq[-1] * (1 + (b if pd.notna(b) else 0)))
        prev_top = top

    def perf(curve, n_per_year):
        curve = np.array(curve)
        rets = curve[1:] / curve[:-1] - 1
        yrs = len(rets) / n_per_year
        cagr = curve[-1] ** (1 / yrs) - 1 if yrs > 0 else np.nan
        sharpe = rets.mean() / rets.std() * np.sqrt(n_per_year) if rets.std() else np.nan
        dd = (np.maximum.accumulate(curve) - curve) / np.maximum.accumulate(curve)
        return cagr, sharpe, dd.max()

    pc, ps, pdd = perf(eq, ppy)
    bc, bs, bdd = perf(bench_eq, ppy)
    print(f"  {'':14}{'CAGR':>9}{'Sharpe':>9}{'MaxDD':>9}{'TotRet':>10}")
    print(f"  {'Top-quintile':14}{pc*100:>8.1f}%{ps:>9.2f}{pdd*100:>8.1f}%{(eq[-1]-1)*100:>9.0f}%")
    print(f"  {BENCH+' (bench)':14}{bc*100:>8.1f}%{bs:>9.2f}{bdd*100:>8.1f}%{(bench_eq[-1]-1)*100:>9.0f}%")
    print("\nSurvivorship bias inflates BOTH lines; the meaningful read is the")
    print("SPREAD (top-quintile minus benchmark), not the absolute returns.")
    print("Rebalanced monthly; horizon-overlap means this is illustrative, not")
    print("a tradeable track record. NOT investment advice.")


if __name__ == "__main__":
    main()
