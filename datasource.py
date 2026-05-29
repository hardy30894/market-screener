"""Swappable market-data layer with caching, retry, and LOUD failure.

The screener talks only to the `DataSource` interface, so swapping yfinance for
a paid API later (FMP / Tiingo / Polygon) is a one-class change, not a rewrite.

yfinance is an unofficial Yahoo scraper with no SLA. This layer wraps it with:
  - disk caching (same-day reruns don't re-hammer Yahoo -> fewer rate-limits)
  - retry with exponential backoff on transient errors
  - staleness detection (is the latest price bar actually recent?)
  - LOUD FAILURE: if too much of the universe fails or is stale, the run ABORTS
    with a clear message instead of silently scoring on nulls. Silent-wrong is
    the failure mode that hurts; this turns it into a visible, honest stop.
"""
from __future__ import annotations

import hashlib
import pickle
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

CACHE_DIR = Path(__file__).parent / ".cache"
CACHE_TTL_SEC = 6 * 3600          # intraday: reruns within 6h hit cache
MAX_FAIL_RATE = 0.25              # abort if >25% of fetches fail
MAX_STALE_RATE = 0.40             # abort if >40% of data is stale
STALE_AFTER_DAYS = 6              # a price bar older than this = stale


class DataError(Exception):
    """Raised when the data source is too degraded to trust the output."""


def _key_path(key: str) -> Path:
    return CACHE_DIR / (hashlib.md5(key.encode()).hexdigest()[:16] + ".pkl")


def _cache_get(key: str, ttl: float = CACHE_TTL_SEC):
    p = _key_path(key)
    try:
        if p.exists() and (time.time() - p.stat().st_mtime) < ttl:
            return pickle.loads(p.read_bytes())
    except Exception:
        return None
    return None


def _cache_put(key: str, val) -> None:
    try:
        CACHE_DIR.mkdir(exist_ok=True)
        _key_path(key).write_bytes(pickle.dumps(val))
    except Exception:
        pass


def _retry(fn, tries: int = 3, base: float = 1.0):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - transient network/parse errors
            last = e
            time.sleep(base * (2 ** i))
    raise last


class DataSource:
    """Interface the screener depends on. Implement these to swap providers."""

    def discover(self, industries, cap_min, vol_min, growth, all_tech, max_pull) -> list[dict]:
        raise NotImplementedError

    def bundle(self, symbol: str, period: str = "15mo") -> dict | None:
        """All per-ticker data in one cached unit, or None if the fetch failed."""
        raise NotImplementedError

    def assert_healthy(self) -> None:
        raise NotImplementedError


class YFinanceSource(DataSource):
    def __init__(self) -> None:
        import yfinance as yf
        from yfinance import EquityQuery
        self.yf = yf
        self.EQ = EquityQuery
        self.attempts = 0
        self.failures = 0
        self.stale = 0
        self.cache_hits = 0

    def discover(self, industries, cap_min, vol_min, growth, all_tech, max_pull) -> list[dict]:
        EQ = self.EQ
        crit = [EQ("eq", ["region", "us"]),
                EQ("gt", ["intradaymarketcap", cap_min]),
                EQ("gt", ["dayvolume", vol_min])]
        crit.append(EQ("eq", ["sector", "Technology"]) if all_tech
                    else EQ("is-in", ["industry", *industries]))
        if growth is not None:
            crit.append(EQ("gt", ["quarterlyrevenuegrowth.quarterly", growth]))
        q = EQ("and", crit)

        out, total = [], None
        for off in range(0, max_pull, 100):
            try:
                r = _retry(lambda off=off: self.yf.screen(
                    q, size=100, offset=off, sortField="intradaymarketcap", sortAsc=False))
            except Exception as e:
                raise DataError(f"discovery failed at offset {off}: {repr(e)[:120]}")
            total = r.get("total")
            quotes = r.get("quotes", [])
            for x in quotes:
                ind = (x.get("industry") or "tech").lower().replace(
                    "semiconductor equipment & materials", "semicap").replace(
                    "scientific & technical instruments", "instruments")
                out.append({"symbol": x.get("symbol"), "mcap": x.get("marketCap") or 0,
                            "theme": ind})
            if len(quotes) < 100:
                break
        if not out:
            raise DataError("discovery returned zero names — source likely blocked/changed.")
        print(f"Discovered {len(out)} names (universe total: {total}).")
        return out

    _BUNDLE_V = "v2"  # bump when the bundle schema changes -> auto-invalidates cache

    def bundle(self, symbol: str, period: str = "15mo") -> dict | None:
        key = f"bundle:{self._BUNDLE_V}:{symbol}:{period}"
        cached = _cache_get(key)
        if cached is not None:
            self.cache_hits += 1
            self._account(cached)
            return cached

        self.attempts += 1

        def fetch() -> dict:
            t = self.yf.Ticker(symbol)
            hist = t.history(period=period, auto_adjust=True)
            if hist.empty or len(hist) < 120:
                raise DataError(f"{symbol}: insufficient price history")
            try:
                fi = t.fast_info
                price = float(fi.get("lastPrice") or hist["Close"].iloc[-1])
                mcap = float(fi.get("marketCap") or 0)
            except Exception:
                price, mcap = float(hist["Close"].iloc[-1]), 0.0
            asof = hist.index[-1]
            asof = asof.to_pydatetime().replace(tzinfo=None) if hasattr(asof, "to_pydatetime") else None

            def _safe(getter):
                try:
                    return getter()
                except Exception:
                    return None
            return {
                "hist": hist, "price": price, "mcap": mcap, "asof": asof,
                "income": _safe(lambda: t.quarterly_income_stmt),
                "eps_rev": _safe(lambda: t.eps_revisions),
                "earnings": _safe(lambda: t.earnings_history),
                "earn_dates": _safe(lambda: t.get_earnings_dates(limit=8)),  # future + past
            }

        try:
            b = _retry(fetch)
        except Exception:
            self.failures += 1
            return None
        _cache_put(key, b)
        self._account(b)
        return b

    def _account(self, b: dict) -> None:
        asof = b.get("asof")
        if asof is None:
            return
        age = (datetime.now() - asof).days
        if age > STALE_AFTER_DAYS:
            self.stale += 1

    def assert_healthy(self) -> None:
        # measure failures against the WHOLE universe (cached successes count),
        # not just fresh fetches — else one failed fetch on a warm cache aborts.
        total = self.attempts + self.cache_hits
        if total == 0:
            print("Data health: nothing processed.")
            return
        fail_rate = self.failures / total
        stale_rate = self.stale / total
        print(f"Data health: {total} names ({self.cache_hits} cached, {self.attempts} fetched), "
              f"{self.failures} failed ({fail_rate:.0%}), {self.stale} stale ({stale_rate:.0%}).")
        if fail_rate > MAX_FAIL_RATE:
            raise DataError(
                f"ABORT: {fail_rate:.0%} of the universe failed to load (>{MAX_FAIL_RATE:.0%}). "
                f"Data source is degraded — refusing to print rankings built on "
                f"unreliable data. Try again later or switch providers.")
        if stale_rate > MAX_STALE_RATE:
            raise DataError(
                f"ABORT: {stale_rate:.0%} of data is stale (latest bar >{STALE_AFTER_DAYS}d old). "
                f"Refusing to score on stale data.")
