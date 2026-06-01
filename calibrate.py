#!/usr/bin/env python3
"""
Closes the loop the rest of the harness leaves open: take the walk-forward
out-of-sample rank-IC for each TECHNICAL factor, gate each one (keep only the
factors that carry real forward-return information), re-fit the technical
weights from what survives, and write weights.json. The live screener
(screener.py) loads weights.json if present and scores with the fitted weights
instead of the hand-set defaults.

What this does and does NOT claim:
  - It fits ONLY the technical block (trend, near_high, mom, rel_strength,
    vol_squeeze). Those map to factors the backtest can measure point-in-time.
  - The fundamental block (eps_rev, rev_accel, margin_exp, surprise) is NOT
    fitted. It cannot be backtested point-in-time on free yfinance data
    (restated values = look-ahead). It stays a FIXED, clearly-untested overlay
    at its current weights. Calibrating it honestly needs paid PIT data.
  - vol_dryup has no clean backtest equivalent here, so it is not measurable and
    gets dropped (weight 0). Dropping > pretending.

Gate (per technical factor) — EFFECT-SIZE, not significance:
  With only ~41 non-overlapping quarters of free survivorship-biased data we do
  NOT have the statistical power to demand significance (an IC_IR of 0.2 is a
  genuinely useful factor yet cannot clear t>=1.5 on n~41). Demanding it just
  drops everything and leaves the score resting on the untested fundamentals,
  which is not an honest improvement. So the gate keeps factors the evidence
  says are positive predictors and drops the ones it says are noise:
      survive if OOS mean rank-IC > 0 AND IC_IR >= MIN_IR (default 0.10).
  This drops wrong-signed factors (e.g. a negative-IC squeeze) and no-info
  factors (IC_IR ~ 0), keeps weak-positive ones (momentum/trend/rel-strength).
  Surviving weights are proportional to OOS IC_IR, scaled to the existing
  technical budget so the fundamental/technical balance is unchanged.
  The per-factor t-stats and the composite DSR are RECORDED so the engine never
  claims a proven edge — these weights are a weak tilt, not validated alpha.

Overall honesty gate: the Deflated Sharpe Ratio of the survivor-weighted
long/short composite (penalized for the number of factors tried). DSR < 0.95
means "no proven edge" even if individual factors look positive — recorded in
weights.json so the dashboard/README can stay honest.

Usage:
    python calibrate.py                  # 63d horizon (the screener's swing horizon)
    python calibrate.py --horizon 21 --min-t 2.0
    python calibrate.py --start 2014-01-01
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import wide_download, build_factors, xs_rank
from ensemble import spearman, deflated_sharpe

# backtest factor name -> screener WEIGHTS key. Only factors present in BOTH
# (same definition) are calibrated. lowvol is measured but has no screener key,
# so it is reported for information and not mapped.
FACTOR_MAP = {
    "mom_12_1": "mom",
    "near_high": "near_high",
    "trend": "trend",
    "rel_str": "rel_strength",
    "squeeze": "vol_squeeze",
}
# fixed, untested overlay — copied from screener defaults, never fitted here.
FUNDAMENTAL_WEIGHTS = {"eps_rev": 18, "rev_accel": 10, "margin_exp": 6, "surprise": 6}
# total technical budget to redistribute across survivors (preserves the
# fund/tech balance the operator chose). = sum of current technical defaults.
TECH_BUDGET = 46
# untested technical factor with no backtest equivalent -> dropped.
DROPPED_UNTESTED = ["vol_dryup"]


def oos_ic_series(ranked: dict, fwd_xs: pd.DataFrame, rebal: list) -> dict:
    """Per-factor out-of-sample rank-IC, one value per non-overlapping rebalance.
    A single factor's cross-sectional rank IS its OOS prediction (no params fit),
    so these ICs are genuinely out-of-sample."""
    out = {f: [] for f in ranked}
    for d in rebal:
        if d not in fwd_xs.index:
            continue
        y = fwd_xs.loc[d]
        for f in ranked:
            if d in ranked[f].index:
                out[f].append(spearman(ranked[f].loc[d], y))
    return out


def summ(ic: list, ppy: float) -> dict:
    s = pd.Series(ic, dtype=float).dropna()
    n, m, sd = len(s), s.mean(), s.std()
    t = (m / sd * math.sqrt(n)) if sd else float("nan")
    ir = (m / sd * math.sqrt(ppy)) if sd else float("nan")
    return {"mean_IC": round(float(m), 4), "t_stat": round(float(t), 2),
            "IC_IR": round(float(ir), 2), "n": int(n)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2015-01-01")
    ap.add_argument("--horizon", type=int, default=63, help="forward-return days (63 = the screener's swing horizon)")
    ap.add_argument("--min-ir", type=float, default=0.10, help="IC_IR (effect-size) gate per factor")
    ap.add_argument("--min-t", type=float, default=0.0, help="optional stricter significance gate (0 = off, recorded only)")
    ap.add_argument("--dry-run", action="store_true", help="print the verdict, do not write weights.json")
    args = ap.parse_args()
    h = args.horizon

    print(f"Downloading universe + building factors (start {args.start}, horizon {h}d)...")
    px, bench = wide_download(args.start)
    c = px["close"]
    factors = build_factors(px, bench)
    ranked = {k: xs_rank(v) for k, v in factors.items()}

    fwd = c.shift(-h) / c - 1
    fwd_xs = fwd.sub(fwd.mean(axis=1), axis=0)         # market-neutral target
    idx = c.index
    rebal = list(idx[252:len(idx) - h:h])              # NON-overlapping at horizon
    ppy = 252.0 / h
    print(f"{c.shape[1]} usable names, {len(rebal)} non-overlapping {h}d periods.\n")

    ic = oos_ic_series(ranked, fwd_xs, rebal)
    stats = {f: summ(ic[f], ppy) for f in factors}

    print("=" * 74)
    print(f"PER-FACTOR OUT-OF-SAMPLE RANK-IC (horizon {h}d, market-neutral)")
    print("=" * 74)
    print(f"  {'factor':12}{'screener key':16}{'mean_IC':>9}{'t_stat':>8}{'IC_IR':>8}{'n':>5}  verdict")
    survivors = {}
    for f in factors:
        st = stats[f]
        key = FACTOR_MAP.get(f, "—")
        passes_ir = st["mean_IC"] > 0 and st["IC_IR"] >= args.min_ir
        passes_t = args.min_t <= 0 or abs(st["t_stat"]) >= args.min_t
        if f not in FACTOR_MAP:
            verdict = "report-only (no screener key)"
        elif passes_ir and passes_t:
            verdict = "KEEP (weak)" if abs(st["t_stat"]) < 1.5 else "KEEP"
            survivors[f] = max(0.0, st["IC_IR"])
        elif st["mean_IC"] <= 0:
            verdict = "DROP (wrong-signed)"
        else:
            verdict = "DROP (no info)"
        print(f"  {f:12}{key:16}{st['mean_IC']:>9.4f}{st['t_stat']:>8.2f}{st['IC_IR']:>8.2f}{st['n']:>5}  {verdict}")

    # ---- DSR on the survivor-weighted long/short composite ----
    fwd_abs = c.shift(-h) / c - 1
    ls_rets, trial_srs = [], []
    # per-factor L/S returns for the SR-variance (multiple-testing) estimate
    per_factor_ls = {f: [] for f in FACTOR_MAP}
    for d in rebal:
        if d not in fwd_abs.index:
            continue
        for f in FACTOR_MAP:
            if d in ranked[f].index:
                s = ranked[f].loc[d].dropna()
                r = fwd_abs.loc[d].reindex(s.index)
                vv = pd.concat([s, r], axis=1).dropna()
                if len(vv) >= 15:
                    q = pd.qcut(vv.iloc[:, 0], 5, labels=False, duplicates="drop")
                    per_factor_ls[f].append(vv.iloc[:, 1][q == q.max()].mean() - vv.iloc[:, 1][q == 0].mean())
        if survivors:
            wsum = sum(survivors.values()) or 1.0
            comp = sum((survivors[f] / wsum) * ranked[f].loc[d] for f in survivors if d in ranked[f].index)
            r = fwd_abs.loc[d].reindex(comp.index)
            vv = pd.concat([comp, r], axis=1).dropna()
            if len(vv) >= 15:
                q = pd.qcut(vv.iloc[:, 0], 5, labels=False, duplicates="drop")
                ls_rets.append(vv.iloc[:, 1][q == q.max()].mean() - vv.iloc[:, 1][q == 0].mean())
    for f in FACTOR_MAP:
        rr = pd.Series(per_factor_ls[f], dtype=float).dropna()
        if rr.std():
            trial_srs.append(rr.mean() / rr.std(ddof=1))
    sr_var = float(np.var(trial_srs, ddof=1)) if len(trial_srs) > 1 else 0.04
    n_trials = len(FACTOR_MAP) + 1
    sr, dsr = deflated_sharpe(np.array(pd.Series(ls_rets, dtype=float).dropna()), n_trials, sr_var)

    print("\n" + "=" * 74)
    print("OVERALL HONESTY GATE — survivor composite, deflated Sharpe")
    print("=" * 74)
    print(f"  surviving factors: {', '.join(FACTOR_MAP[f] for f in survivors) or '(none)'}")
    print(f"  per-period Sharpe: {sr:>7.3f}   DSR: {dsr:>7.3f}   (>0.95 = real after multiple testing)")

    # ---- re-fit weights ----
    weights = dict(FUNDAMENTAL_WEIGHTS)
    if survivors:
        wsum = sum(survivors.values())
        for f, w in survivors.items():
            weights[FACTOR_MAP[f]] = round(TECH_BUDGET * w / wsum)
    # dropped factors are simply absent (weight 0) — screener won't score them.
    for k in list(weights):
        if weights[k] == 0:
            del weights[k]

    print("\n" + "=" * 74)
    print("RE-FITTED WEIGHTS (fundamentals fixed/untested; technical from survivors)")
    print("=" * 74)
    for k, v in sorted(weights.items(), key=lambda kv: -kv[1]):
        tag = "untested overlay" if k in FUNDAMENTAL_WEIGHTS else "fitted"
        print(f"  {k:14}{v:>4}   ({tag})")
    dropped = [FACTOR_MAP[f] for f in FACTOR_MAP if f not in survivors] + DROPPED_UNTESTED
    print(f"  dropped (no scored weight): {', '.join(dropped) or '(none)'}")

    if not survivors:
        print("\n  WARNING: no technical factor passed the gate on this window. The")
        print("  scored signal is fundamentals-only (an untested overlay). Honest read:")
        print("  no demonstrated price-factor edge. Do NOT treat the score as alpha.")

    out = {
        "generated": _dt.date.today().isoformat(),
        "method": f"walk-forward OOS rank-IC, horizon {h}d, effect-size gate IC_IR>={args.min_ir} "
                  f"(drops wrong-signed/no-info factors; keeps weak-positive), weights prop. IC_IR; "
                  f"fundamentals fixed untested overlay. t-stats/DSR recorded — weak tilt, not proven alpha",
        "horizon_days": h,
        "start": args.start,
        "min_t": args.min_t,
        "dsr": None if (dsr is None or (isinstance(dsr, float) and math.isnan(dsr))) else round(float(dsr), 3),
        "edge_verdict": ("real edge (DSR>0.95)" if (dsr and dsr > 0.95)
                         else "weak/unproven (price factors ~beta)"),
        "oos_stats": {FACTOR_MAP.get(f, f): {**stats[f], "kept": f in survivors,
                                             "scored": f in FACTOR_MAP} for f in factors},
        "weights": weights,
    }
    if args.dry_run:
        print("\n[dry-run] weights.json NOT written.")
        return
    p = Path(__file__).with_name("weights.json")
    p.write_text(json.dumps(out, indent=2) + "\n")
    print(f"\nWrote {p.name}. screener.py will load it on the next run.")
    print("NOT investment advice. Free survivorship-biased data; technical factors only.")


if __name__ == "__main__":
    main()
