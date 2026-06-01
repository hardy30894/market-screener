#!/usr/bin/env python3
"""
Daily briefing renderer. Separates compute (screener/monitor/regime, unchanged)
from presentation. Produces a clean, easy-to-read report:

  TL;DR  ->  summary tables  ->  FULL detail tables  ->  full macro thesis

Nothing is thrown away: the clean briefing leads, the complete data and the
full thesis follow as the deep-dive read. Script noise (errors, progress,
data-health internals) is kept OUT of the report (it goes to stdout/logs).

Writes results/<date>.md and results/latest.md.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from datasource import YFinanceSource, DataError
from screener import (THEME_INDUSTRIES, build_universe, score_universe, find_market_primed, ETFS)
from monitor import monitor_holdings
from regime import compute_regime

HERE = Path(__file__).parent
EARN_WINDOW = 14
EARN_WATCH_SCORE = 65.0
GOOD = {"primed", "PRIMED *", "early (fundies)"}

FULL_COLS = ["ticker", "signal", "theme", "cap", "verdict", "deal", "flag", "setup", "score", "ext", "earn_in", "next_earn",
             "to_resist", "to_support", "cov", "mcap_$B", "price", "rev_accel", "margin_exp",
             "eps_rev", "surprise", "near_high", "mom", "trend", "rel_str", "squeeze", "vol_dry"]
CLEAN_COLS = ["ticker", "signal", "theme", "cap", "verdict", "setup", "score", "ext", "near_high", "mom", "eps_rev", "earn_in"]


def fenced(df: pd.DataFrame, cols: list) -> str:
    cols = [c for c in cols if c in df.columns]
    return "```\n" + df[cols].to_string(index=False) + "\n```"


def round_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for c in df.select_dtypes("float").columns:
        df[c] = df[c].round(2)
    if "earn_in" in df:
        df["earn_in"] = df["earn_in"].apply(lambda x: f"{int(x)}d" if pd.notna(x) else "-")
    return df


def main() -> None:
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    src = YFinanceSource()
    L = []  # markdown lines

    # ---- compute: screener ----
    screen_err = None
    df = pd.DataFrame()
    try:
        themes = list(THEME_INDUSTRIES)  # semis, equipment, photonics, hardware, solar
        cands = build_universe(src, themes, with_quantum=True)
        df = score_universe(src, cands, EARN_WINDOW, min_score=0.0)
    except DataError as e:
        screen_err = str(e)

    # ---- compute: monitor (shared src so cache + health carry over) ----
    holdings = []
    hf = HERE / "holdings.txt"
    if hf.exists():
        holdings = [ln.strip().upper() for ln in hf.read_text().splitlines()
                    if ln.strip() and not ln.startswith("#")]
    mon = monitor_holdings(src, holdings) if holdings else []

    # ---- compute: cross-sector PRIMED* hunt (the rare bullseye, market-wide) ----
    try:
        mktp = find_market_primed(src)
    except Exception:
        mktp = pd.DataFrame()

    # ---- compute: ETF tape (technical-only) ----
    try:
        edf = score_universe(src, [{"symbol": e, "mcap": 0, "theme": "etf"} for e in ETFS], min_score=0)
    except Exception:
        edf = pd.DataFrame()

    # ---- health check (shared screener+monitor) ----
    health_warn = None
    try:
        src.assert_healthy()
    except DataError as e:
        health_warn = str(e)

    # ---- compute: regime (independent data) ----
    try:
        reg = compute_regime()
    except Exception as e:  # noqa: BLE001
        reg = {"verdict": "UNKNOWN", "composite": 0, "n": 0, "gauges": [],
               "error": repr(e)[:120]}

    # ---- derive TL;DR pieces ----
    picks = stars = exits = earn_soon = []
    if not df.empty:
        picks = df[df["verdict"].isin(GOOD)]["ticker"].head(5).tolist()
        stars = df[df["verdict"] == "PRIMED *"]["ticker"].tolist()
        es = df[(df["earn_in"].notna()) & (df["earn_in"] >= 0)
                & (df["earn_in"] <= EARN_WINDOW) & (df["score"] >= EARN_WATCH_SCORE)]
        earn_soon = [f"{r.ticker} ({int(r.earn_in)}d)" for r in es.itertuples()]
    exits = [f"{r['ticker']} ({r['action']})" for r in mon
             if r.get("action") in ("EXIT", "TRIM")]

    # ================= RENDER: briefing =================
    L.append(f"# Daily Sector Report — {date}")
    L.append(f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. "
             f"Regime: **{reg['verdict']}** ({reg['composite']:+d}/{reg['n']}). Not investment advice._")
    if health_warn:
        L.append(f"\n> ⚠ **DATA DEGRADED** — {health_warn} Rankings below may be incomplete.")
    if screen_err:
        L.append(f"\n> ⚠ **SCREEN FAILED** — {screen_err}")
    L.append("")

    L.append("## TL;DR")
    L.append(f"- **Top setups:** {', '.join(picks) if picks else 'none'}")
    L.append(f"- 🎯 **PRIMED\\*** (coiled near highs): {', '.join(stars) if stars else 'none today'}")
    L.append(f"- ⚠ **Exit signals:** {', '.join(exits) if exits else 'none — holdings clean'}")
    L.append(f"- 📅 **Earnings ≤{EARN_WINDOW}d (strong names):** {', '.join(earn_soon) if earn_soon else 'none'}")
    L.append("")

    if not df.empty:
        rdf = round_df(df)
        core = rdf[rdf["_mcap"] >= 10e9] if "_mcap" in rdf else rdf
        L.append("## Top setups (large-cap)")
        L.append(fenced(core.head(12), CLEAN_COLS))
        early = rdf[(rdf["_mcap"] > 0) & (rdf["_mcap"] < 10e9)] if "_mcap" in rdf else pd.DataFrame()
        if len(early):
            L.append("\n## Top setups (small/mid)")
            L.append(fenced(early.head(10), CLEAN_COLS))

        ew = rdf[(df["earn_in"].notna()) & (df["earn_in"] >= 0)
                 & (df["earn_in"] <= EARN_WINDOW) & (df["score"] >= EARN_WATCH_SCORE)]
        L.append("\n## 📅 Earnings watch")
        L.append(fenced(ew.sort_values("score", ascending=False),
                        ["ticker", "theme", "verdict", "score", "earn_in", "next_earn", "eps_rev"])
                 if len(ew) else "_No strong names reporting in the window._")

        cw = rdf[df["squeeze"].notna() & (df["squeeze"] >= 0.5)]
        L.append("\n## 🎯 Coiled watch (volatility squeeze; PRIMED\\* = bullseye)")
        L.append(fenced(cw, ["ticker", "theme", "verdict", "score", "squeeze", "near_high", "eps_rev"])
                 if len(cw) else "_Nothing coiling — volatility expanding (momentum tape)._")

    # market-wide PRIMED* (all sectors, the rare bullseye)
    L.append("\n## 🌐 Market-wide PRIMED★ (all sectors)")
    if len(mktp):
        L.append(fenced(round_df(mktp),
                        ["ticker", "cap", "score", "near_high", "mom", "eps_rev", "squeeze", "earn_in"]))
        L.append("\n_Coiled near their highs with rising estimates, scanned across the whole US market "
                 "(not just tech). A setup, not a buy; verify each name._")
    else:
        L.append("_No PRIMED★ across the market today._")

    if mon:
        flagged = [r for r in mon if r.get("action") in ("EXIT", "TRIM")]
        L.append("\n## ⚠ Exit signals (your holdings)")
        if flagged:
            mdf = pd.DataFrame(flagged)[["ticker", "action", "price", "stop", "to_stop_%", "signals"]]
            L.append(fenced(mdf, list(mdf.columns)))
        else:
            L.append("_All monitored holdings clean (HOLD)._")

    # regime compact
    L.append("\n## Market regime")
    if reg["gauges"]:
        L.append("```")
        for name, reading, state, _, _ in reg["gauges"]:
            L.append(f"  {name:34}{reading:>14}  {state}")
        L.append("```")

    # ================= RENDER: full detail (nothing discarded) =================
    L.append("\n---\n")
    L.append("## Full detail")
    L.append("_Everything below is the complete data behind the summary above._\n")

    if not df.empty:
        rdf = round_df(df)
        core = rdf[rdf["_mcap"] >= 10e9] if "_mcap" in rdf else rdf
        early = rdf[(rdf["_mcap"] > 0) & (rdf["_mcap"] < 10e9)] if "_mcap" in rdf else pd.DataFrame()
        L.append("### Full screen — CORE (large-cap)")
        L.append(fenced(core, FULL_COLS))
        if len(early):
            L.append("\n### Full screen — EARLY (small/mid)")
            L.append(fenced(early, FULL_COLS))

    if mon:
        L.append("\n### Full position monitor")
        mdf = pd.DataFrame(mon)
        L.append(fenced(mdf, [c for c in ["ticker", "action", "price", "stop", "to_stop_%", "signals"] if c in mdf]))

    if reg["gauges"]:
        L.append("\n### Regime detail")
        L.append("```")
        for name, reading, state, score, cav in reg["gauges"]:
            L.append(f"  {name:34}{reading:>14}  {state:>9}  ({score:+d})")
        L.append("\n  caveats:")
        for name, _, _, _, cav in reg["gauges"]:
            L.append(f"   - {name.split(' (')[0]}: {cav}")
        L.append("```")
        L.append("\nRISK-OFF = 'environment fragile, size down', NOT 'sell everything'. "
                 "High false-positive rates, long lead times. Context, not timing.")

    # full macro thesis appended
    thesis = HERE / "MARKET_OUTLOOK_2026_2027.md"
    if thesis.exists():
        L.append("\n---\n")
        L.append("## Macro thesis (full standing reference)")
        L.append("_Static thesis compiled 2026-05-28; treat as standing context, not live._\n")
        L.append("\n".join(thesis.read_text().splitlines()[1:]))  # skip its H1

    out = HERE / "results" / f"{date}.md"
    out.parent.mkdir(exist_ok=True)
    text = "\n".join(L) + "\n"
    out.write_text(text)
    (HERE / "results" / "latest.md").write_text(text)
    print(f"Wrote {out}")

    # ---- dashboard: structured data injected into the HTML template ----
    def num(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        return float(v) if isinstance(v, float) else v

    # ---- streaks: how many consecutive days each name has held the setup list ----
    ddir0 = HERE / "docs" / "data"
    idxp = ddir0 / "index.json"
    prior_dates = sorted(json.loads(idxp.read_text())) if idxp.exists() else []
    prior_good = {}
    for dt in prior_dates:
        if dt == date:
            continue
        p = ddir0 / f"{dt}.json"
        if p.exists():
            try:
                pd_ = json.loads(p.read_text())
                prior_good[dt] = {r["ticker"] for r in pd_.get("screen", []) if r.get("verdict") in GOOD}
            except Exception:
                pass
    today_good = set(df[df["verdict"].isin(GOOD)]["ticker"]) if not df.empty else set()
    order = [d for d in sorted(prior_good) if d < date] + [date]

    def streak_of(tk):
        s = 0
        for dt in reversed(order):
            member = (tk in today_good) if dt == date else (tk in prior_good.get(dt, set()))
            if member:
                s += 1
            else:
                break
        return s
    yest = order[-2] if len(order) >= 2 else None

    screen = []
    if not df.empty:
        for r in df.to_dict("records"):
            row = {k: num(v) for k, v in r.items() if not k.startswith("_")}
            row["earn_in"] = int(r["earn_in"]) if pd.notna(r.get("earn_in")) else None
            row["next_earn"] = r.get("next_earn") if isinstance(r.get("next_earn"), str) else None
            tk = r["ticker"]
            row["streak"] = streak_of(tk) if row["verdict"] in GOOD else 0
            row["new"] = bool(yest and row["verdict"] in GOOD and tk not in prior_good.get(yest, set()))
            screen.append(row)

    # ---- track record: how the earliest cohort's picks have done vs SPY ----
    trackrec = None
    cohorts = [d for d in sorted(prior_good) if d < date]
    if cohorts:
        cdate = cohorts[0]
        cd = json.loads((ddir0 / f"{cdate}.json").read_text())
        cps = sorted([r for r in cd.get("screen", []) if r.get("verdict") in GOOD and r.get("price")],
                     key=lambda r: r.get("score", 0), reverse=True)[:15]
        cur = {r["ticker"]: r["price"] for r in (df.to_dict("records") if not df.empty else [])}
        rets = []
        for r in cps:
            now = cur.get(r["ticker"])
            if now is None:
                b = src.bundle(r["ticker"])
                now = b["price"] if b else None
            if now:
                rets.append(now / r["price"] - 1)
        bret = None
        try:
            sp = src.bundle("SPY")
            h = sp["hist"]["Close"]
            then = h[h.index.strftime("%Y-%m-%d") <= cdate]
            if len(then):
                bret = float(h.iloc[-1] / then.iloc[-1] - 1)
        except Exception:
            pass
        if rets:
            avg = sum(rets) / len(rets) * 100
            days = (datetime.now(timezone.utc).date() - datetime.fromisoformat(cdate + "T00:00:00").date()).days
            trackrec = {"date": cdate, "days": days, "n": len(rets),
                        "avg_ret": round(avg, 2),
                        "win": round(100 * sum(1 for x in rets if x > 0) / len(rets)),
                        "bench": round(bret * 100, 2) if bret is not None else None,
                        "spread": round(avg - bret * 100, 2) if bret is not None else None}
    earn_rows, coiled_rows = [], []
    if not df.empty:
        es2 = df[(df["earn_in"].notna()) & (df["earn_in"] >= 0)
                 & (df["earn_in"] <= EARN_WINDOW) & (df["score"] >= EARN_WATCH_SCORE)].sort_values("score", ascending=False)
        earn_rows = [{"ticker": r.ticker, "verdict": r.verdict, "signal": getattr(r, "signal", ""),
                      "eps_rev": num(getattr(r, "eps_rev", None)), "ext": num(getattr(r, "ext", None)),
                      "earn_in": int(r.earn_in),
                      "next_earn": r.next_earn if isinstance(r.next_earn, str) else None} for r in es2.itertuples()]
        cw2 = df[df["squeeze"].notna() & (df["squeeze"] >= 0.5)].sort_values("squeeze", ascending=False)
        coiled_rows = [{"ticker": r.ticker, "verdict": r.verdict, "squeeze": num(r.squeeze),
                        "near_high": num(r.near_high)} for r in cw2.itertuples()]
    mon_rows = [{"ticker": r["ticker"], "verdict": r.get("verdict", "?"), "action": r["action"],
                 "price": num(r.get("price")), "stop": num(r.get("stop")),
                 "to_stop_pct": num(r.get("to_stop_%")), "signals": r.get("signals", "")} for r in mon]
    gauges = [{"name": g[0].split(" (")[0], "reading": g[1], "state": g[2], "score": g[3]}
              for g in reg["gauges"]]
    mkt_rows = []
    if not mktp.empty:
        for r in mktp.to_dict("records"):
            mkt_rows.append({"ticker": r["ticker"], "cap": r.get("cap"), "signal": "BUY",
                             "score": num(r["score"]),
                             "near_high": num(r.get("near_high")), "mom": num(r.get("mom")),
                             "eps_rev": num(r.get("eps_rev")), "squeeze": num(r.get("squeeze")),
                             "earn_in": int(r["earn_in"]) if pd.notna(r.get("earn_in")) else None})

    etf_rows = []
    if not edf.empty:
        for r in edf.to_dict("records"):
            r1m = r1y = None
            b = src.bundle(r["ticker"])               # cached from the scan
            if b is not None:
                cl = b["hist"]["Close"]
                if len(cl) >= 21:
                    r1m = round((cl.iloc[-1] / cl.iloc[-21] - 1) * 100, 1)
                if len(cl) >= 252:
                    r1y = round((cl.iloc[-1] / cl.iloc[-252] - 1) * 100, 1)
            etf_rows.append({"ticker": r["ticker"], "name": ETFS.get(r["ticker"], ""),
                             "verdict": r["verdict"], "trend": num(r.get("trend")),
                             "near_high": num(r.get("near_high")), "mom": num(r.get("mom")),
                             "st_mom": num(r.get("st_mom")), "setup": r.get("setup", ""),
                             "ret_1m": r1m, "ret_1y": r1y})

    data = {
        "date": date,
        "generated": f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M}",
        "regime": {"verdict": reg["verdict"], "composite": reg["composite"],
                   "n": reg["n"], "gauges": gauges},
        "tldr": {"picks": picks, "stars": stars, "exits": exits, "earnings": earn_soon},
        "screen": screen, "earnings": earn_rows, "coiled": coiled_rows, "monitor": mon_rows,
        "market_primed": mkt_rows, "trackrec": trackrec, "etfs": etf_rows,
        "health": (health_warn or screen_err or "").strip(),
    }
    tmpl = HERE / "dashboard_template.html"
    if tmpl.exists():
        docs = HERE / "docs"
        ddir = docs / "data"
        ddir.mkdir(parents=True, exist_ok=True)
        # save this day's data + maintain the date index (newest first)
        (ddir / f"{date}.json").write_text(json.dumps(data))
        idx_path = ddir / "index.json"
        try:
            idx = json.loads(idx_path.read_text()) if idx_path.exists() else []
        except Exception:
            idx = []
        idx = sorted(set(idx) | {date}, reverse=True)
        idx_path.write_text(json.dumps(idx))
        # dashboard: inline latest data + the index, history loads on click
        html = (tmpl.read_text().replace("__DATA__", json.dumps(data))
                .replace("__INDEX__", json.dumps(idx)))
        (docs / "index.html").write_text(html)   # served by GitHub Pages at the site root
        (docs / ".nojekyll").write_text("")        # tell Pages not to run Jekyll
        print(f"Wrote docs/index.html ({len(idx)} days in history)")


if __name__ == "__main__":
    main()
