#!/usr/bin/env python3
"""
SEC EDGAR point-in-time fundamentals — FREE and genuinely point-in-time.

Every figure from EDGAR carries its actual filing date ('filed') and the
as-reported value, with full history. So at any past rebalance date we can use
ONLY the quarters that were publicly filed before that date — no look-ahead, no
restatement leakage, no payment. This is what makes a fundamental backtest honest.

Builds two factors per (ticker, as-of date):
  - f_rev_accel : YoY revenue growth acceleration (latest YoY minus prior YoY)
  - f_margin    : gross-margin expansion (latest gross margin minus year-ago)

NOT covered (needs paid data): analyst estimate revisions — EDGAR has no estimates.
Foreign filers (20-F annual, e.g. TSM/ASML) have no quarterly facts -> NaN (neutral).
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import date
from pathlib import Path

CACHE = Path(__file__).parent / ".cache" / "edgar"
UA = "market-screener-research admin@example.com"   # SEC requires a UA with contact
REV_TAGS = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"]
GP_TAGS = ["GrossProfit"]


def _get(url: str) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate"})
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            return json.loads(raw)
    except Exception:
        return None


def cik_map() -> dict:
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / "tickers.json"
    if p.exists():
        data = json.loads(p.read_text())
    else:
        data = _get("https://www.sec.gov/files/company_tickers.json") or {}
        if data:
            p.write_text(json.dumps(data))
    out = {}
    for row in (data.values() if isinstance(data, dict) else []):
        out[row["ticker"].upper()] = str(row["cik_str"]).zfill(10)
    return out


def _companyfacts(cik: str) -> dict | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f"CIK{cik}.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    data = _get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
    time.sleep(0.12)   # SEC: <10 req/s
    if data:
        p.write_text(json.dumps(data))
    return data


def _quarterly(facts: dict, tags: list) -> list:
    """Return [(end, filed, val)] for ~3-month periods, as-reported (earliest filing)."""
    g = (facts or {}).get("facts", {}).get("us-gaap", {})
    items = []
    for tag in tags:
        unit = g.get(tag, {}).get("units", {}).get("USD")
        if not unit:
            continue
        for it in unit:
            s, e, v, f = it.get("start"), it.get("end"), it.get("val"), it.get("filed")
            if not (s and e and v is not None and f):
                continue
            days = (date.fromisoformat(e) - date.fromisoformat(s)).days
            if 80 <= days <= 100:       # quarterly only
                items.append((e, f, float(v)))
        if items:
            break
    best = {}
    for e, f, v in items:               # dedup by period-end, keep earliest filing (as-reported)
        if e not in best or f < best[e][0]:
            best[e] = (f, v)
    return sorted((e, best[e][0], best[e][1]) for e in best)


class PIT:
    """Point-in-time fundamentals for a set of tickers."""

    def __init__(self, tickers, quiet=True):
        cm = cik_map()
        self.rev, self.gp = {}, {}
        miss = []
        for t in tickers:
            cik = cm.get(t.upper())
            if not cik:
                miss.append(t); continue
            facts = _companyfacts(cik)
            if not facts:
                miss.append(t); continue
            self.rev[t] = _quarterly(facts, REV_TAGS)
            self.gp[t] = _quarterly(facts, GP_TAGS)
        if not quiet:
            have = sum(1 for t in tickers if len(self.rev.get(t, [])) >= 6)
            print(f"EDGAR: {have}/{len(tickers)} tickers with >=6 quarters; {len(miss)} no SEC quarterly data")

    def factors_asof(self, ticker: str, asof: str) -> dict:
        """asof = 'YYYY-MM-DD'. Uses only quarters FILED on/before asof."""
        out = {"f_rev_accel": None, "f_margin": None}
        rev = [(e, v) for (e, f, v) in self.rev.get(ticker, []) if f <= asof]
        if len(rev) >= 6:
            r = [v for _, v in rev]
            if r[-5] and r[-6]:
                yoy_now = r[-1] / r[-5] - 1
                yoy_prev = r[-2] / r[-6] - 1
                out["f_rev_accel"] = yoy_now - yoy_prev          # acceleration
        gp = {e: v for (e, f, v) in self.gp.get(ticker, []) if f <= asof}
        revd = {e: v for (e, f, v) in self.rev.get(ticker, []) if f <= asof}
        ends = sorted(set(gp) & set(revd))
        if len(ends) >= 5:
            gm_now = gp[ends[-1]] / revd[ends[-1]] if revd[ends[-1]] else None
            gm_yago = gp[ends[-5]] / revd[ends[-5]] if revd[ends[-5]] else None
            if gm_now is not None and gm_yago is not None:
                out["f_margin"] = gm_now - gm_yago               # margin expansion
        return out


if __name__ == "__main__":
    import sys
    syms = [s.upper() for s in sys.argv[1:]] or ["NVDA", "MU", "AAPL", "TSM"]
    pit = PIT(syms, quiet=False)
    for s in syms:
        print(s, pit.factors_asof(s, "2026-05-29"))
