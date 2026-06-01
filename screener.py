#!/usr/bin/env python3
"""
Pre-breakout screener for AI / memory / semiconductor / photonics names.

DISCOVERY-FIRST: by default it scans the *entire* US tech-hardware universe
live via Yahoo's server-side screener (semis, semicap equipment, optical/comms
gear, electronic components, instruments), then scores every survivor. It does
NOT rely on a hand-picked list — names you've never heard of can surface.

Funnel:
  1. DISCOVER (cheap, server-side): Yahoo screener filters the whole market by
     industry + market-cap + volume [+ optional revenue-growth]. Returns ~150-250.
  2. SCORE (detailed, per-ticker): on the top-N by market cap, stack three layers:
       - FUNDAMENTAL INFLECTION (leading): revenue-growth acceleration,
         gross-margin expansion, forward-EPS revision momentum, last surprise.
       - TECHNICAL PRE-BREAKOUT (timing): proximity to 52w high, trend (50>200),
         relative strength vs SOX, volatility contraction, volume dry-up.
       - LIQUIDITY/JUNK gate: price, dollar-volume.
  3. Split into "early" (small/mid) and "core" (large-cap), ranked, with a
     plain-English verdict per name.

Usage:
    pip install yfinance pandas numpy
    python screener.py                      # discover + score US tech-hardware
    python screener.py --growth 15          # server-side: only rev-growth >15%
    python screener.py --limit 120          # score more survivors (slower)
    python screener.py --all-tech           # widen to ALL tech (incl. software)
    python screener.py --watchlist          # score the small curated list instead
    python screener.py NVDA MU CRDO         # score exactly these tickers
    python screener.py --min-score 60       # only show setups scoring >=60
"""
from __future__ import annotations

import argparse
import sys
import warnings

import numpy as np
import pandas as pd

warnings.simplefilter("ignore")

from datasource import YFinanceSource, DataError  # swappable, cached, fails loud

LARGE_CAP_FLOOR = 10_000_000_000  # >= this => "core", below => "early"
BENCHMARK = "SOXX"

# Selectable themes -> Yahoo industry tags. Memory lives inside "Semiconductors".
THEME_INDUSTRIES = {
    "semis": ["Semiconductors"],                         # incl AVGO, INTC, MU, NOK-adjacent, LAES
    "equipment": ["Semiconductor Equipment & Materials"],
    "photonics": ["Communication Equipment", "Scientific & Technical Instruments"],
    "hardware": ["Computer Hardware", "Electronic Components"],  # incl quantum (Computer Hardware)
    "solar": ["Solar"],
}
# The AI/memory/semis/photonics complex (the report's scope) is the default.
DEFAULT_THEMES = ["semis", "equipment", "photonics", "hardware"]

# Quantum pure-plays have no clean industry tag (split across Computer Hardware /
# Software / Semiconductors) and are often pre-revenue, so seed them explicitly.
QUANTUM_SEEDS = ["IONQ", "RGTI", "QBTS", "QUBT", "ARQQ", "INFQ", "LAES", "QMCO"]

# Top ETFs to track the tape/regime (technical-only — ETFs have no fundamentals).
ETFS = {
    "SPY": "S&P 500", "QQQ": "Nasdaq 100", "SMH": "Semis", "SOXX": "Semis",
    "XLK": "Tech sector", "IGV": "Software", "DRAM": "Memory", "XLF": "Financials",
    "AIQ": "AI/thematic", "QTUM": "Quantum", "CIBR": "Cybersecurity",
    "DTCR": "Data centers", "WCLD": "Cloud SW", "BOTZ": "Robotics/AI", "IPO": "IPOs",
    # (no dedicated photonics ETF exists; photonics lives inside SMH/SOXX + COHR/LITE)
}

# Optional convenience list for --watchlist (NOT used for discovery).
WATCHLIST = {
    "NVDA": "ai-compute", "AMD": "ai-compute", "AVGO": "ai-compute",
    "TSM": "foundry", "MRVL": "ai-compute/optical", "MU": "memory",
    "ASML": "semicap", "AMAT": "semicap", "LRCX": "semicap", "KLAC": "semicap",
    "COHR": "photonics", "LITE": "photonics", "CIEN": "optical", "ANET": "networking",
    "CRDO": "connectivity", "ALAB": "connectivity", "VRT": "dc-power-cooling",
    "AEHR": "test", "ONTO": "metrology", "CAMT": "metrology", "SITM": "timing",
}

# Weights: technical block calibrated to measured 63d rank-IC (see backtest.py)
# -- trend/near_high/mom tested best; squeeze/vol_dryup weak, so down-weighted.
# Fundamental block is theory-backed (estimate-revision momentum) but CANNOT be
# point-in-time backtested on free data, so treat as an untested overlay.
WEIGHTS = {
    "eps_rev": 18, "rev_accel": 10, "margin_exp": 6, "surprise": 6,   # fundamentals (untested)
    "trend": 12, "near_high": 11, "mom": 11, "rel_strength": 5,        # technical (validated, weak edge)
    "vol_squeeze": 4, "vol_dryup": 3,
}


def pct_rank(series: pd.Series, value: float) -> float:
    s = series.dropna()
    if len(s) < 10:
        return np.nan
    return float((s < value).mean())


def atr(hist: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = hist["High"], hist["Low"], hist["Close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def technical_signals(hist: pd.DataFrame, bench: pd.Series) -> dict:
    out = {}
    c = hist["Close"]
    if len(c) < 120:
        return out
    px = float(c.iloc[-1])

    hi52 = float(c.tail(252).max())
    dist = (hi52 - px) / hi52
    out["near_high"] = float(np.clip(1 - dist / 0.15, 0, 1))

    if len(c) >= 252:  # 12-1 momentum (best-tested technical factor)
        m = float(c.iloc[-21] / c.iloc[-252] - 1)
        out["mom"] = float(np.clip(0.5 + m, 0, 1))

    ma50, ma200 = c.rolling(50).mean().iloc[-1], c.rolling(200).mean().iloc[-1]
    if px > ma50 and ma50 > ma200:
        out["trend"] = 1.0          # confirmed uptrend
    elif px > ma200:
        out["trend"] = 0.7          # above 200d, 50<200 (choppy / early)
    elif px > ma50:
        out["trend"] = 0.4          # below 200d but reclaimed 50d -> recovering zone
    else:
        out["trend"] = 0.0          # below both -> truly broken

    if len(c) >= 22:                # short-term (1-month) momentum, for V-reversal detection
        st = float(px / c.iloc[-21] - 1)
        out["st_mom"] = float(np.clip(0.5 + st * 3, 0, 1))  # +17%/mo -> 1.0

    if bench is not None and len(bench) > 70:
        r_t = px / float(c.iloc[-63]) - 1
        r_b = float(bench.iloc[-1]) / float(bench.iloc[-63]) - 1
        out["rel_strength"] = float(np.clip(0.5 + (r_t - r_b) * 2.5, 0, 1))

    a = atr(hist)
    if a.notna().sum() > 130:
        cur, ref = a.iloc[-1], a.iloc[-63]
        contraction = 1 - cur / ref if ref else 0
        out["vol_squeeze"] = float(np.clip(0.5 + contraction * 2, 0, 1))

    v = hist["Volume"]
    if len(v) > 60:
        ratio = v.tail(10).mean() / v.tail(60).mean()
        out["vol_dryup"] = 1.0 if ratio < 0.9 else (0.9 if ratio > 1.6 else 0.4)

    # support / resistance + pre-breakout (pivot) detection
    if len(hist) > 60:
        hh, ll = hist["High"], hist["Low"]
        resist = float(hh.iloc[-60:-1].max())          # ~3mo prior high = level to break
        support = float(ll.iloc[-60:].min())            # ~3mo low = support
        out["dist_resist"] = (resist - px) / px         # raw % to resistance (display)
        out["dist_support"] = (px - support) / px       # raw % above support (risk)
        out["near_resist"] = float(np.clip(1 - max(0.0, (resist - px) / px) / 0.08, 0, 1))
        rng = (float(hh.tail(20).max()) - float(ll.tail(20).min())) / px
        out["base_tight"] = float(np.clip(1 - rng / 0.15, 0, 1))  # tight 20d base -> 1.0
        vexp = float(v.tail(3).mean() / v.tail(50).mean()) if len(v) > 50 else 0.0
        out["breakout"] = 1.0 if (px >= resist * 0.999 and vexp >= 1.3) else 0.0
    return out


def fundamental_signals(b: dict) -> dict:
    out = {}
    try:
        q = b.get("income")
        rev = q.loc["Total Revenue"].dropna().iloc[::-1]  # oldest -> newest
        # yfinance usually gives only ~5 quarters, so degrade by what's available:
        if len(rev) >= 6:                                  # true YoY acceleration
            yoy_now = rev.iloc[-1] / rev.iloc[-5] - 1
            yoy_prev = rev.iloc[-2] / rev.iloc[-6] - 1
            out["rev_accel"] = 1.0 if yoy_now > yoy_prev and yoy_now > 0 else (
                0.5 if yoy_now > 0 else 0.0)
        elif len(rev) >= 5:                                # YoY level (no prior to compare)
            yoy = rev.iloc[-1] / rev.iloc[-5] - 1
            out["rev_accel"] = 1.0 if yoy > 0.20 else (0.5 if yoy > 0 else 0.0)
        elif len(rev) >= 3:                                # QoQ-growth acceleration
            g = rev.pct_change().dropna()
            out["rev_accel"] = 1.0 if g.iloc[-1] > g.iloc[-2] and g.iloc[-1] > 0 else (
                0.5 if g.iloc[-1] > 0 else 0.0)
        gp = q.loc["Gross Profit"].dropna().iloc[::-1]
        if len(gp) >= 5 and len(rev) >= 5:
            gm_now, gm_yago = gp.iloc[-1] / rev.iloc[-1], gp.iloc[-5] / rev.iloc[-5]
            out["margin_exp"] = 1.0 if gm_now > gm_yago else 0.3
    except Exception:
        pass

    try:
        er = b.get("eps_rev")
        row = er.loc["+1y"] if "+1y" in er.index else er.iloc[0]
        up = float(row.get("upLast30days", 0) or 0)
        dn = float(row.get("downLast30days", 0) or 0)
        if up + dn > 0:
            out["eps_rev"] = float(np.clip((up - dn) / (up + dn) * 0.5 + 0.5, 0, 1))
        elif up > 0:
            out["eps_rev"] = 1.0
    except Exception:
        pass

    try:
        sp = b.get("earnings")["surprisePercent"].dropna()
        if len(sp):
            out["surprise"] = float(np.clip(0.5 + float(sp.iloc[-1]) * 5, 0, 1))
    except Exception:
        pass
    return out


def verdict(sig: dict) -> str:
    lead = [sig[k] for k in ("rev_accel", "eps_rev", "margin_exp", "surprise") if k in sig]
    fund = sum(lead) / len(lead) if lead else 0.0
    fund_strong = fund >= 0.6 and sig.get("eps_rev", 0) >= 0.5
    near = sig.get("near_high", 0)
    coiled = sig.get("vol_squeeze", 0) >= 0.6
    trend = sig.get("trend", 1)
    st = sig.get("st_mom", 0.5)
    if trend < 0.6:                          # below the 200-day = not a confirmed uptrend
        if trend >= 0.4 and st >= 0.7:       # reclaimed the 50d AND strong 1-month move
            return "recovering"
        return "broken trend"
    if fund_strong and near >= 0.6 and coiled:
        return "PRIMED *"
    if fund_strong and near >= 0.6:
        return "primed"
    if fund_strong and near < 0.4:
        return "extended"
    if fund_strong:
        return "early (fundies)"
    if coiled and near >= 0.6:
        return "coiling (no fund)"
    return "watch"


def earnings_info(b: dict) -> dict:
    """Days to next earnings + days since last + last surprise, from the bundle."""
    df = b.get("earn_dates")
    if df is None or len(df) == 0:
        return {}
    idx = df.index
    try:
        now = pd.Timestamp.now(tz=idx.tz) if getattr(idx, "tz", None) else pd.Timestamp.now()
    except Exception:
        now = pd.Timestamp.now()
    out = {}
    future = idx[idx > now]
    past = idx[idx <= now]
    if len(future):
        out["days_to_earn"] = int((future.min() - now).days)
        out["next_earn"] = future.min().date().isoformat()
    if len(past):
        last = past.max()
        out["days_since_earn"] = int((now - last).days)
        for col in ("Surprise(%)", "Surprise (%)"):
            if col in df.columns:
                try:
                    out["last_surprise"] = float(df.loc[last, col])
                except Exception:
                    pass
                break
    return out


def earnings_tag(ei: dict, window: int) -> str:
    d, ds = ei.get("days_to_earn"), ei.get("days_since_earn")
    if d is not None and 0 <= d <= window:
        return f"PRE-EARN {d}d"
    if ds is not None and ds <= 5 and (ei.get("last_surprise") or 0) > 0:
        return f"post-beat {ds}d"
    return ""


def pivot_state(sig: dict) -> str:
    """Pre-breakout / breakout detection from support-resistance + base tightness.
    'pivot' = coiled just under resistance with a tight base (catch BEFORE the move).
    'BREAKOUT' = clearing resistance on volume expansion (the trigger, happening now)."""
    nr = sig.get("near_resist", 0)
    bt = sig.get("base_tight", 0)
    bo = sig.get("breakout", 0)
    trend = sig.get("trend", 1)
    if trend < 0.4:                       # truly broken trends don't get pivot/breakout tags
        return ""
    if bo:
        return "BREAKOUT"                 # above resistance + volume expansion
    if nr >= 0.6 and bt >= 0.5:
        return "pivot"                    # within ~3% of resistance, tight base
    return ""


def buy_signal(verdict: str, setup: str) -> str:
    """Buy-side action, symmetric with the monitor's HOLD/TRIM/EXIT.
    BUY = trigger firing / bullseye. WATCH = setup forming, await confirmation. PASS = no setup."""
    if setup == "BREAKOUT" or verdict == "PRIMED *":
        return "BUY"
    if verdict in ("primed", "early (fundies)", "recovering") or setup == "pivot":
        return "WATCH"
    return "PASS"


def cap_band(mcap: float) -> str:
    if not mcap:
        return "?"
    if mcap >= 200e9:
        return "mega"
    if mcap >= 10e9:
        return "large"
    if mcap >= 2e9:
        return "mid"
    if mcap >= 3e8:
        return "small"
    return "micro"


def score_ticker(src, sym: str, theme: str, bench: pd.Series, earn_window: int = 14) -> dict | None:
    b = src.bundle(sym)  # cached; None if fetch failed (tracked for loud-fail health)
    if b is None:
        return None
    hist, px, mcap = b["hist"], b["price"], b["mcap"]
    dollar_vol = px * float(hist["Volume"].tail(30).mean())

    if px < 5 or dollar_vol < 3_000_000:
        return None

    sig = {}
    sig.update(technical_signals(hist, bench))
    sig.update(fundamental_signals(b))
    if not sig:
        return None

    avail = sum(WEIGHTS[k] for k in sig if k in WEIGHTS)
    got = sum(WEIGHTS[k] * v for k, v in sig.items() if k in WEIGHTS)
    score = round(100 * got / avail, 1) if avail else 0.0

    ei = earnings_info(b)
    vd = verdict(sig)
    stp = pivot_state(sig)

    return {
        "ticker": sym, "theme": theme[:14], "cap": cap_band(mcap), "verdict": vd,
        "signal": buy_signal(vd, stp), "setup": stp,
        "to_resist": round(sig["dist_resist"] * 100, 1) if "dist_resist" in sig else None,
        "to_support": round(sig["dist_support"] * 100, 1) if "dist_support" in sig else None,
        "score": score, "cov": round(avail / sum(WEIGHTS.values()), 2),
        "earn_in": ei.get("days_to_earn"), "next_earn": ei.get("next_earn"),
        "earn_flag": earnings_tag(ei, earn_window),
        "mcap_$B": round(mcap / 1e9, 1) if mcap else np.nan, "price": round(px, 2),
        "rev_accel": sig.get("rev_accel"), "margin_exp": sig.get("margin_exp"),
        "eps_rev": sig.get("eps_rev"), "surprise": sig.get("surprise"),
        "near_high": round(sig.get("near_high", np.nan), 2) if "near_high" in sig else np.nan,
        "mom": round(sig.get("mom", np.nan), 2) if "mom" in sig else np.nan,
        "st_mom": round(sig.get("st_mom", np.nan), 2) if "st_mom" in sig else np.nan,
        "trend": sig.get("trend"),
        "rel_str": round(sig.get("rel_strength", np.nan), 2) if "rel_strength" in sig else np.nan,
        "squeeze": round(sig.get("vol_squeeze", np.nan), 2) if "vol_squeeze" in sig else np.nan,
        "vol_dry": sig.get("vol_dryup"), "_mcap": mcap,
    }


def build_universe(src, themes, *, growth=None, cap_min=300e6, vol_min=200_000,
                   all_tech=False, limit=40, extra_seeds=(), with_quantum=False) -> list[dict]:
    """Discover per sub-sector (so theme labels are real), split by cap band, add seeds."""
    if all_tech:
        cands = src.discover(["__all_tech__"], cap_min, vol_min, growth, True, max_pull=600)
    else:
        seen, cands = set(), []
        for t in themes:
            for c in src.discover(THEME_INDUSTRIES[t], cap_min, vol_min, growth, False, max_pull=600):
                if c["symbol"] not in seen:
                    seen.add(c["symbol"])
                    c["theme"] = t
                    cands.append(c)
    core_c = sorted([c for c in cands if c["mcap"] >= LARGE_CAP_FLOOR],
                    key=lambda c: c["mcap"], reverse=True)[:limit]
    early_c = sorted([c for c in cands if 0 < c["mcap"] < LARGE_CAP_FLOOR],
                     key=lambda c: c["mcap"], reverse=True)[:limit]
    seeds = list(QUANTUM_SEEDS) if with_quantum else []
    seeds += [s.strip().upper() for s in extra_seeds if s and s.strip()]
    have = {c["symbol"] for c in core_c + early_c}
    seed_c = [{"symbol": s, "mcap": 0, "theme": "quantum" if s in QUANTUM_SEEDS else "seed"}
              for s in dict.fromkeys(seeds) if s not in have]
    return core_c + early_c + seed_c


def score_universe(src, cands, earn_window=14, min_score=0.0, progress=False) -> pd.DataFrame:
    """Score candidates into a sorted DataFrame. Shared by the CLI and report.py."""
    bench_b = src.bundle(BENCHMARK)
    bench = bench_b["hist"]["Close"] if bench_b else None
    rows = []
    for i, c in enumerate(cands, 1):
        r = score_ticker(src, c["symbol"], c["theme"], bench, earn_window)
        if progress:
            print(f"  [{i}/{len(cands)}] {c['symbol']:6} {'ok ' if r else 'skip'}", end="\r")
        if r and r["score"] >= min_score:
            rows.append(r)
    if progress:
        print(" " * 50, end="\r")
    return pd.DataFrame(rows).sort_values("score", ascending=False) if rows else pd.DataFrame()


def find_market_primed(src, per_band=45, vol_min=400_000) -> pd.DataFrame:
    """Cross-sector hunt for PRIMED* setups (coiled near highs + rising estimates).
    Scans the whole US market by cap band, returns only the PRIMED* rows."""
    mkt = src.discover_market(3e8, vol_min, max_pull=2500)
    bands = [
        sorted([c for c in mkt if c["mcap"] >= 10e9], key=lambda c: c["mcap"], reverse=True)[:per_band],
        sorted([c for c in mkt if 2e9 <= c["mcap"] < 10e9], key=lambda c: c["mcap"], reverse=True)[:per_band],
        sorted([c for c in mkt if 3e8 <= c["mcap"] < 2e9], key=lambda c: c["mcap"], reverse=True)[:per_band],
    ]
    seen, cands = set(), []
    for b in bands:
        for c in b:
            if c["symbol"] not in seen:
                seen.add(c["symbol"])
                cands.append(c)
    df = score_universe(src, cands, min_score=0.0)
    if df.empty:
        return df
    return df[df["verdict"] == "PRIMED *"].sort_values("score", ascending=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="*", help="score exactly these (skips discovery)")
    ap.add_argument("--min-score", type=float, default=0.0)
    ap.add_argument("--limit", type=int, default=40, help="max names to deep-score PER cap band")
    ap.add_argument("--growth", type=float, default=None, help="server-side min rev-growth %% (e.g. 15)")
    ap.add_argument("--cap-min", type=float, default=300e6, help="min market cap")
    ap.add_argument("--vol-min", type=float, default=200_000, help="min daily volume")
    ap.add_argument("--all-tech", action="store_true", help="all Technology, not just selected themes")
    ap.add_argument("--themes", default=",".join(DEFAULT_THEMES),
                    help=f"comma list from {list(THEME_INDUSTRIES)} (default: AI complex)")
    ap.add_argument("--quantum", action="store_true", help="add quantum pure-plays (seeded)")
    ap.add_argument("--solar", action="store_true", help="add solar industry")
    ap.add_argument("--all-themes", action="store_true", help="every theme incl solar + quantum")
    ap.add_argument("--seeds", default="", help="force-include these tickers, e.g. NOK,FSLR")
    ap.add_argument("--earn-days", type=int, default=14, help="earnings-watch window (days)")
    ap.add_argument("--earn-watch-score", type=float, default=65.0,
                    help="min score to appear in the EARNINGS WATCH block")
    ap.add_argument("--watchlist", action="store_true", help="score curated list, no discovery")
    args = ap.parse_args()

    src = YFinanceSource()

    if args.tickers:
        cands = [{"symbol": s.upper(), "mcap": 0, "theme": WATCHLIST.get(s.upper(), "custom")}
                 for s in args.tickers]
    elif args.watchlist:
        cands = [{"symbol": s, "mcap": 0, "theme": th} for s, th in WATCHLIST.items()]
    else:
        themes = list(THEME_INDUSTRIES) if args.all_themes else \
            [t.strip() for t in args.themes.split(",") if t.strip() in THEME_INDUSTRIES]
        if args.solar and "solar" not in themes:
            themes.append("solar")
        print(f"Themes: {themes}")
        try:
            cands = build_universe(src, themes, growth=args.growth, cap_min=args.cap_min,
                                   vol_min=args.vol_min, all_tech=args.all_tech, limit=args.limit,
                                   extra_seeds=args.seeds.split(","),
                                   with_quantum=(args.quantum or args.all_themes))
        except DataError as e:
            print(f"\n{e}")
            sys.exit(2)

    print(f"\nScoring {len(cands)} tickers...\n")
    df = score_universe(src, cands, args.earn_days, args.min_score, progress=True)

    # LOUD FAILURE: refuse to print rankings if the data source is too degraded
    try:
        src.assert_healthy()
    except DataError as e:
        print(f"\n{e}")
        sys.exit(2)

    if df.empty:
        print("No candidates passed the gates.")
        return
    cols = ["ticker", "signal", "theme", "cap", "verdict", "setup", "score", "earn_in",
            "to_resist", "to_support", "cov", "mcap_$B", "price", "rev_accel", "margin_exp",
            "eps_rev", "surprise", "near_high", "mom", "trend", "rel_str", "squeeze", "vol_dry"]

    # EARNINGS WATCH: strong picks reporting within the window — surfaced FIRST
    pd.set_option("display.max_rows", None, "display.width", 240)
    ew = df[(df["earn_in"].notna()) & (df["earn_in"] >= 0)
            & (df["earn_in"] <= args.earn_days) & (df["score"] >= args.earn_watch_score)]
    ew = ew.sort_values("earn_in")
    print("\n" + "#" * 72)
    print(f"# EARNINGS WATCH — score>={args.earn_watch_score:.0f} reporting in next {args.earn_days}d")
    print("#" * 72)
    if len(ew):
        ewc = ["ticker", "theme", "verdict", "score", "earn_in", "next_earn",
               "eps_rev", "near_high", "mcap_$B"]
        print(ew[ewc].to_string(index=False))
        print("\n^ Strong setups with earnings imminent. PRE-EARN = event risk both")
        print("ways (size small); a beat-and-raise on these can gap and drift. The")
        print("higher eps_rev is, the better the pre-print odds. Not advice.")
    else:
        print("  (no strong picks reporting in this window)")

    # COILED WATCH: volatility-squeeze setups (the PRIMED* bullseye is rare,
    # so surface anything contracting before it gets there)
    cw = df[df["squeeze"].notna() & (df["squeeze"] >= 0.5)].sort_values("squeeze", ascending=False)
    stars = df[df["verdict"] == "PRIMED *"]
    print("\n" + "#" * 72)
    print("# COILED WATCH — volatility contracting (squeeze>=0.5); PRIMED* = bullseye")
    print("#" * 72)
    if len(cw):
        cwc = ["ticker", "theme", "verdict", "score", "squeeze", "near_high", "eps_rev", "earn_in"]
        print(cw[cwc].to_string(index=False))
        if len(stars):
            print(f"\n*** {len(stars)} PRIMED* (coiled + strong fundamentals near high): "
                  f"{', '.join(stars['ticker'])} ***")
        else:
            print("\nNo PRIMED* yet — these are coiling but not all three conditions align.")
    else:
        print("  (nothing coiling — volatility expanding across the board, momentum tape)")

    core = df[df["_mcap"] >= LARGE_CAP_FLOOR]
    early = df[(df["_mcap"] > 0) & (df["_mcap"] < LARGE_CAP_FLOOR)]
    unknown = df[df["_mcap"] <= 0]

    pd.set_option("display.max_rows", None, "display.width", 220)
    print("\n" + "=" * 72)
    print("CORE  — large-cap (>= $10B)  — lower risk, smaller moves")
    print("=" * 72)
    print(core[cols].to_string(index=False) if len(core) else "  (none)")
    print("\n" + "=" * 72)
    print("EARLY — small/mid (< $10B)  — bigger moves, more false signals")
    print("=" * 72)
    print(early[cols].to_string(index=False) if len(early) else "  (none)")
    if len(unknown):
        print("\n(market cap unavailable):")
        print(unknown[cols].to_string(index=False))

    print("\nverdict: PRIMED* = fundies + coiled near high | primed = ready, no")
    print("squeeze | early(fundies) = fundamentals leading, base-building |")
    print("extended = move already underway | coiling(no fund) = technical only |")
    print("watch = mixed | recovering = below 200d but reclaimed 50d on strong 1mo |")
    print("broken trend = below 200d & falling, avoid.")
    print("\nSignal cols are 0-1 (higher=better). 'cov' = fraction of signals with")
    print("data; treat low-cov scores as low-confidence. Leading signals")
    print("(rev_accel, eps_rev) matter most for 'before it breaks'.")
    print("\nNOT investment advice. A screen surfaces candidates to research, not")
    print("buys. Verify every name's catalyst and risk before acting.")


if __name__ == "__main__":
    main()
