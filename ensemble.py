#!/usr/bin/env python3
"""
Stage 1 ensemble research engine — does combining our factors, fitted out-of-
sample, actually beat the benchmark and survive a multiple-testing penalty?

Method (built to NOT fool ourselves):
  - Cross-sectional rank features per rebalance (rank each factor across names).
  - Non-overlapping rebalances at the forward horizon (no overlap autocorrelation).
  - WALK-FORWARD fit: at each test period, fit factor weights via ridge on an
    expanding history, with a 1-period EMBARGO so the fit never touches the test
    window. Predict the test period out-of-sample.
  - Report OOS rank-IC for: each single factor, equal-weight ensemble, and the
    fitted ensemble. Then a long-short portfolio Sharpe.
  - DEFLATED SHARPE RATIO (Bailey & Lopez de Prado): the probability the Sharpe
    is real *after* accounting for the number of strategies tried and non-normal
    returns. DSR > 0.95 = survives multiple testing. This is the honesty gate.

Reuses data + factor construction from backtest.py (no duplication).

Usage:
    python ensemble.py --start 2015-01-01 --horizon 21
"""
from __future__ import annotations

import argparse
import math

import numpy as np
import pandas as pd

from backtest import wide_download, build_factors, xs_rank


def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def norm_ppf(p: float) -> float:
    # Acklam's rational approximation to the inverse normal CDF.
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def spearman(a: pd.Series, b: pd.Series) -> float:
    df = pd.concat([a, b], axis=1).dropna()
    return df.iloc[:, 0].corr(df.iloc[:, 1], method="spearman") if len(df) >= 10 else np.nan


def ridge(X: np.ndarray, y: np.ndarray, lam: float = 1.0) -> np.ndarray:
    Xc = X - X.mean(0)
    yc = y - y.mean()
    p = Xc.shape[1]
    return np.linalg.solve(Xc.T @ Xc + lam * np.eye(p), Xc.T @ yc)


def deflated_sharpe(rets: np.ndarray, n_trials: int, sr_variance: float) -> tuple[float, float]:
    """Returns (annualized-ish per-period SR, DSR probability)."""
    rets = rets[~np.isnan(rets)]
    T = len(rets)
    if T < 8 or rets.std() == 0:
        return np.nan, np.nan
    sr = rets.mean() / rets.std(ddof=1)
    # skew/kurtosis of returns
    z = (rets - rets.mean()) / rets.std(ddof=1)
    skew = (z**3).mean()
    kurt = (z**4).mean()  # raw (normal = 3)
    gamma = 0.5772156649
    e = math.e
    # expected max Sharpe under the null from n_trials independent strategies
    sr0 = math.sqrt(sr_variance) * ((1 - gamma) * norm_ppf(1 - 1.0 / n_trials)
                                    + gamma * norm_ppf(1 - 1.0 / (n_trials * e)))
    denom = math.sqrt(max(1e-9, 1 - skew * sr + (kurt - 1) / 4 * sr**2))
    dsr = norm_cdf((sr - sr0) * math.sqrt(T - 1) / denom)
    return sr, dsr


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2015-01-01")
    ap.add_argument("--horizon", type=int, default=21)
    ap.add_argument("--warmup", type=int, default=24, help="train periods before first OOS test")
    ap.add_argument("--fundamentals", action="store_true", help="add EDGAR point-in-time fundamental factors")
    args = ap.parse_args()
    h = args.horizon

    print(f"Downloading + building factors ({args.start}, horizon {h}d)...")
    px, bench = wide_download(args.start)
    c = px["close"]
    factors = build_factors(px, bench)

    fwd = c.shift(-h) / c - 1
    fwd_xs = fwd.sub(fwd.mean(axis=1), axis=0)   # market-neutral target

    # NON-overlapping rebalances at the horizon
    idx = c.index
    rebal = list(idx[252:len(idx) - h:h])
    ppy = 252.0 / h

    # optional: EDGAR point-in-time fundamental factors (free, no look-ahead)
    if args.fundamentals:
        from edgar import PIT
        tickers = list(c.columns)
        pit = PIT(tickers, quiet=False)
        fr = pd.DataFrame(index=pd.DatetimeIndex(rebal), columns=tickers, dtype=float)
        fm = fr.copy()
        for d in rebal:
            ds = pd.Timestamp(d).strftime("%Y-%m-%d")
            for t in tickers:
                f = pit.factors_asof(t, ds)
                fr.at[d, t] = f["f_rev_accel"]
                fm.at[d, t] = f["f_margin"]
        factors["f_rev_accel"] = fr
        factors["f_margin"] = fm

    fnames = list(factors)
    ranked = {k: xs_rank(v) for k, v in factors.items()}
    for k in ("f_rev_accel", "f_margin"):   # neutral rank for names without SEC data
        if k in ranked:
            ranked[k] = ranked[k].fillna(0.5)

    # per-date panel: (tickers, X rank-matrix, y market-neutral fwd ret)
    panel = {}
    for d in rebal:
        cols = {f: ranked[f].loc[d] for f in fnames if d in ranked[f].index}
        y = fwd_xs.loc[d] if d in fwd_xs.index else None
        if y is None or len(cols) < len(fnames):
            continue
        X = pd.DataFrame(cols)
        df = pd.concat([X, y.rename("y"), fwd.loc[d].rename("abs")], axis=1).dropna()
        if len(df) >= 15:
            panel[d] = df
    dates = [d for d in rebal if d in panel]
    print(f"{c.shape[1]} names, {len(dates)} non-overlapping periods, {len(fnames)} factors\n")

    # ---- OOS: single factors, equal-weight, fitted ensemble ----
    ic_single = {f: [] for f in fnames}
    ic_eq, ic_fit = [], []
    ret_eq_ls, ret_fit_ls = [], []          # long-short portfolio returns (fitted/eq)
    ret_single_ls = {f: [] for f in fnames}  # per-factor L/S returns (for SR variance)

    for i, d in enumerate(dates):
        df = panel[d]
        # single-factor OOS IC + L/S
        for f in fnames:
            ic_single[f].append(spearman(df[f], df["y"]))
            q = pd.qcut(df[f], 5, labels=False, duplicates="drop")
            ret_single_ls[f].append(df["abs"][q == q.max()].mean() - df["abs"][q == 0].mean())
        # equal-weight ensemble
        eq = df[fnames].mean(axis=1)
        ic_eq.append(spearman(eq, df["y"]))
        qe = pd.qcut(eq, 5, labels=False, duplicates="drop")
        ret_eq_ls.append(df["abs"][qe == qe.max()].mean() - df["abs"][qe == 0].mean())
        # fitted ensemble (walk-forward, embargo 1 period)
        train = [dates[j] for j in range(0, i - 1) if j >= 0]  # up to i-2 => 1-period embargo
        if len(train) >= args.warmup:
            Xtr = np.vstack([panel[t][fnames].values for t in train])
            ytr = np.concatenate([panel[t]["y"].values for t in train])
            w = ridge(Xtr, ytr, lam=1.0)
            pred = (df[fnames].values - df[fnames].values.mean(0)) @ w
            pred = pd.Series(pred, index=df.index)
            ic_fit.append(spearman(pred, df["y"]))
            qf = pd.qcut(pred, 5, labels=False, duplicates="drop")
            ret_fit_ls.append(df["abs"][qf == qf.max()].mean() - df["abs"][qf == 0].mean())

    def summ(ic):
        ic = pd.Series(ic, dtype=float).dropna()
        n, m, s = len(ic), ic.mean(), ic.std()
        return m, (m / s * math.sqrt(n) if s else np.nan), (m / s * math.sqrt(ppy) if s else np.nan), n

    print("=" * 70)
    print(f"OUT-OF-SAMPLE RANK-IC (horizon {h}d, market-neutral)")
    print("=" * 70)
    print(f"  {'signal':22}{'mean_IC':>9}{'t_stat':>8}{'IC_IR':>8}{'n':>5}")
    for f in fnames:
        m, t, ir, n = summ(ic_single[f])
        print(f"  {f:22}{m:>9.4f}{t:>8.2f}{ir:>8.2f}{n:>5}")
    me, te, ire, ne = summ(ic_eq)
    mf, tf, irf, nf = summ(ic_fit)
    print(f"  {'equal-weight ensemble':22}{me:>9.4f}{te:>8.2f}{ire:>8.2f}{ne:>5}")
    print(f"  {'FITTED ensemble (OOS)':22}{mf:>9.4f}{tf:>8.2f}{irf:>8.2f}{nf:>5}")

    # ---- Deflated Sharpe on the fitted long-short portfolio ----
    # SR variance across all trials (single factors + 2 ensembles)
    trial_srs = []
    for f in fnames:
        r = pd.Series(ret_single_ls[f], dtype=float).dropna()
        if r.std():
            trial_srs.append(r.mean() / r.std(ddof=1))
    for r in (ret_eq_ls, ret_fit_ls):
        rr = pd.Series(r, dtype=float).dropna()
        if rr.std():
            trial_srs.append(rr.mean() / rr.std(ddof=1))
    sr_var = float(np.var(trial_srs, ddof=1)) if len(trial_srs) > 1 else 0.04
    n_trials = len(fnames) + 2

    fit_ls = np.array(pd.Series(ret_fit_ls, dtype=float).dropna())
    sr, dsr = deflated_sharpe(fit_ls, n_trials, sr_var)
    ann_sr = sr * math.sqrt(ppy) if not np.isnan(sr) else np.nan

    print("\n" + "=" * 70)
    print("FITTED ENSEMBLE — long/short portfolio, honesty gate")
    print("=" * 70)
    print(f"  periods: {len(fit_ls)}  | trials penalized: {n_trials}")
    print(f"  per-period Sharpe:     {sr:>7.3f}")
    print(f"  annualized Sharpe:     {ann_sr:>7.3f}")
    print(f"  DEFLATED Sharpe (DSR): {dsr:>7.3f}   <-- prob the edge is real after multiple-testing")
    print("\n  DSR > 0.95 = survives; 0.5–0.95 = weak/unproven; < 0.5 = likely noise.")

    # ---- verdict ----
    print("\n" + "=" * 70)
    print("VERDICT")
    print("=" * 70)
    if not np.isnan(dsr) and dsr > 0.95 and mf > 0:
        print("  Fitted ensemble shows a statistically real edge after the multiple-")
        print("  testing penalty. Stage 2 (paid point-in-time fundamentals) is warranted.")
    elif not np.isnan(mf) and mf > 0 and tf > 1.5:
        print("  Weak-but-positive OOS signal; DSR not conclusive. There's a flicker,")
        print("  not a proven edge. Stage 2 fundamentals could tip it; price-only is thin.")
    else:
        print("  No demonstrated edge from price-only factors out-of-sample. Honest")
        print("  result: this is beta. The likely edge is in fundamentals (estimate")
        print("  revisions) which need paid point-in-time data to test. Do NOT trade")
        print("  this as alpha.")
    print("\nNOT investment advice. Free survivorship-biased data; price-only factors.")


if __name__ == "__main__":
    main()
