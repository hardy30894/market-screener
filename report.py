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

import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from datasource import YFinanceSource, DataError
from screener import (THEME_INDUSTRIES, build_universe, score_universe)
from monitor import monitor_holdings
from regime import compute_regime

HERE = Path(__file__).parent
EARN_WINDOW = 14
EARN_WATCH_SCORE = 65.0
GOOD = {"primed", "PRIMED *", "early (fundies)"}

FULL_COLS = ["ticker", "theme", "verdict", "score", "earn_in", "next_earn", "cov",
             "mcap_$B", "price", "rev_accel", "margin_exp", "eps_rev", "surprise",
             "near_high", "mom", "trend", "rel_str", "squeeze", "vol_dry"]
CLEAN_COLS = ["ticker", "theme", "verdict", "score", "near_high", "mom", "eps_rev", "earn_in"]


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
        L.append("_Static thesis compiled 2026-05-28; the cloud catalyst monitor tracks "
                 "whether it still holds._\n")
        L.append("\n".join(thesis.read_text().splitlines()[1:]))  # skip its H1

    out = HERE / "results" / f"{date}.md"
    out.parent.mkdir(exist_ok=True)
    text = "\n".join(L) + "\n"
    out.write_text(text)
    (HERE / "results" / "latest.md").write_text(text)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
