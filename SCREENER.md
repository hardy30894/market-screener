# Pre-Breakout Screener — AI / Memory / Semis / Photonics

Goal: catch names in the window between **fundamentals inflecting** and
**price discovering it** — i.e. *before* the obvious breakout. Edge comes from
stacking three layers, not from any single filter.

> Not investment advice. A screen produces a *research queue*, not buys. The
> leading signals (estimate revisions, revenue acceleration) tell you *what*;
> the technicals tell you *when*; the liquidity gate keeps you out of garbage.

---

## The three layers (platform-agnostic)

| Layer | Filter | Threshold | Why |
|---|---|---|---|
| **1. Theme gate** | Industry / sub-sector | Semis, semicap equipment, comms-equipment (optical), analog/power, AI-server, DC power+cooling | Where the secular wave is |
| **2. Fundamental inflection** (leading) | Fwd-EPS estimate revisions | More upward than downward, last 30–90d | **Best single predictor** of forward returns; most retail screens skip it |
| | Revenue growth *accelerating* | latest YoY > prior-qtr YoY, and > 0 | Inflection beats high-but-decelerating |
| | Gross-margin expansion | latest GM > year-ago GM | Pricing power / mix improving |
| | Last earnings surprise | positive | Confirms estimates were too low |
| **3. Technical pre-breakout** (timing) | Distance to 52w high | within 0–15% | Coiled, not extended, not broken |
| | Trend | price > 50d MA > 200d MA | Don't fight the tape |
| | Relative strength vs SOX/SPX | rising | Leadership, not laggard |
| | Volatility contraction | ATR / Bollinger width compressing | The "spring" before the move |
| | Volume in the base | drying up (<0.9× the 60d avg) | Accumulation; sellers exhausted |
| **4. Liquidity / junk gate** | Price | > $5 | |
| | $-volume | > $3M/day (core), > $1M (early) | Tradable, not a trap |
| | Market cap | split: ≥ $10B = *core*, < $10B = *early* | Two risk bands |

The **trigger** to actually act (vs. just watchlist) is layer 3 flipping:
volume *expands* (>1.5× avg) as price clears the base while layers 1–2 are
still intact. That's the breakout; the screen's job is to have it on your list
the day *before*.

---

## A) Finviz quick-start

Free tier covers most of this; a few rows (relative volume, EPS-revision)
need Elite. Build it under the **Screener → All filters** tab.

### Shared filters (both lists)
- **Sector:** Technology
- **Industry:** Semiconductors *(also run: Semiconductor Equipment & Materials; Communication Equipment for optical; Scientific & Technical Instruments for metrology)*
- **Price:** Over $5
- **52-Week High:** 0-10% below High *(or "5% below High" for tighter)*
- **200-Day SMA:** Price above SMA200
- **50-Day SMA:** SMA50 above SMA200
- **20-Day SMA:** Price above SMA20 *(reclaiming, momentum on)*
- **EPS growth qtr over qtr:** Over 20%
- **Sales growth qtr over qtr:** Over 15%
- **EPS growth next year:** Positive (>0%)
- *(Elite)* **EPS Revision:** Positive
- *(Elite)* **Volatility:** Week - Low *(for the squeeze)*

### "CORE" list — large-cap, lower risk
- **Market Cap:** +Large (over $10bln) — or +Mid for $2–10B
- **Average Volume:** Over 1M

### "EARLY" list — small/mid, bigger moves
- **Market Cap:** Small ($300mln to $2bln) and/or Mid ($2bln to $10bln)
- **Average Volume:** Over 300K
- Expect more false positives — lean harder on the fundamental layer here.

### Best-effort URL presets (verify the params after pasting)
Finviz occasionally renames filter codes; treat these as a starting point and
confirm the chips match the recipe above.

- CORE:
  `https://finviz.com/screener.ashx?v=171&f=cap_largeover,fa_epsqoq_o20,fa_salesqoq_o15,fa_epsyoy1_pos,sec_technology,sh_avgvol_o1000,sh_price_o5,ta_highlow52w_b0to10h,ta_sma20_pa,ta_sma200_pa50&ft=4`
- EARLY:
  `https://finviz.com/screener.ashx?v=171&f=cap_smallmid,fa_epsqoq_o20,fa_salesqoq_o15,fa_epsyoy1_pos,sec_technology,sh_avgvol_o300,sh_price_o5,ta_highlow52w_b0to10h,ta_sma20_pa,ta_sma200_pa50&ft=4`

Tip: save each as a Finviz preset and check it daily. The names that *graduate*
from EARLY toward CORE over weeks are the supercycle winners compounding.

---

## B) Python scorer (`screener.py`) — discovery-first

This is a real screener, not a watchlist scorer. By default it **scans the
entire US tech-hardware universe live** via Yahoo's server-side screener
(semis, semicap equipment, optical/comms gear, electronic components,
instruments), then deep-scores the survivors 0–100 by the same three-layer
logic. Names you've never heard of surface on their own.

Funnel: **(1) discover** — Yahoo filters the whole market server-side by
industry + cap + volume [+ optional revenue growth], returning ~150–250 names;
**(2) score** — the detailed per-ticker scorer runs on the top names *in each
cap band* (so small/mid aren't starved by the mega-caps); **(3) split** into
CORE (≥$10B) and EARLY (<$10B) with a verdict each.

```bash
pip install yfinance pandas numpy
python screener.py                 # discover + score US tech-hardware (default)
python screener.py --growth 15     # server-side prefilter: rev-growth >15% only
python screener.py --limit 60      # deep-score more per band (slower)
python screener.py --all-tech      # widen to ALL Technology (incl. software)
python screener.py --min-score 55  # only show setups scoring >=55
python screener.py --watchlist     # score the small curated list (no discovery)
python screener.py NVDA MU CRDO    # score exactly these tickers
```

**Themes / coverage.** Default discovery scans the AI/memory/semis/photonics
complex (`semis`, `equipment`, `photonics`, `hardware` — and `hardware` includes
the quantum pure-plays that sit under "Computer Hardware"). To widen:

```bash
python screener.py --all-themes              # everything incl. solar + quantum
python screener.py --themes semis,photonics,solar   # pick a mix
python screener.py --quantum                 # add seeded quantum names (IONQ, RGTI, ...)
python screener.py --solar                   # add the Solar industry
python screener.py --seeds NOK,ARQQ          # force-include any tickers (e.g. ADRs)
```

Notes: memory (MU, WDC, STX, RMBS) lives inside `semis`; AVGO/INTC are `semis`
too. Nokia (NOK) is a foreign ADR — Yahoo's `region=us` filter can drop it, so
seed it. Quantum names are mostly pre-revenue, so the `--growth` filter excludes
them; use `--quantum` to seed them in regardless. Solar is a *different macro
regime* (rates/policy, not AI-capex) — expect very different verdicts there.

The curated list still exists, but only as a `--watchlist` convenience — it is
**not** the discovery universe. Discovery is the default.

- The **`verdict`** column gives a one-line read so you don't have to parse the
  numbers: `PRIMED*` (fundamentals + coiled near high — the setup you want),
  `primed` (ready, just no tight squeeze), `early (fundies)` (fundamentals
  leading, still base-building), `extended` (move already underway — confirm,
  not pre-break), `coiling (no fund)` (technical only, weak fundamentals),
  `watch` (mixed), `broken trend` (below the 200d — avoid).
- To change *what* gets discovered, edit `HARDWARE_INDUSTRIES`, or use
  `--all-tech` / `--growth` / `--cap-min` / `--vol-min`. The `WATCHLIST` dict
  only feeds `--watchlist`.
- `eps_rev` (estimate-revision momentum) and `rev_accel` are the leading
  signals — weighted highest. Watch the `cov` column: a high score on low
  coverage = low confidence (some data was missing).
- Free data via yfinance is delayed and occasionally gappy; for serious use
  swap in a paid feed (Financial Modeling Prep, Tiingo, Polygon, EODHD).

---

## B1) Earnings awareness — the dominant catalyst

In these sectors earnings are the biggest single catalyst: a beat-and-raise from
a coiled, rising-estimate name gaps up and often *drifts* for weeks (post-earnings
drift, PEAD — a well-documented effect). So the screener is earnings-aware:

- Every row shows **`earn_in`** (days to next earnings) and **`next_earn`** (date).
- A dedicated **EARNINGS WATCH** block prints at the *top* of the report: strong
  picks (score ≥ `--earn-watch-score`, default 65) reporting within
  `--earn-days` (default 14). So a `primed` name reporting in 5 days never slips
  past you.
- Tags: `PRE-EARN Nd` (reports soon — *event risk both ways*, the higher `eps_rev`
  the better the pre-print odds) and `post-beat Nd` (just beat + reacted up — a
  drift candidate, the more validated play).

```bash
python screener.py --earn-days 10 --earn-watch-score 70   # tighten the watch
```

**Honest framing:** "buy before earnings so we don't miss the pop" only counts
the upside. Earnings are symmetric — the same setup gaps *down* on a soft guide,
and implied volatility is highest right before the print. The edge isn't guessing
direction; it's (1) rising estimate revisions *into* the print (`eps_rev`) and
(2) the drift *after* a confirmed beat. The earnings layer adds the *signal and
the heads-up*; it is **not** a backtested earnings strategy. Size pre-earnings
positions as the event risk they are.

## B2) Validation — does it actually work? (`backtest.py`)

A screener is only "world-class" if its factors are *measured*, not asserted.
`backtest.py` tests whether the signals predict FORWARD returns, out-of-sample,
after costs. Methodology: point-in-time factors (date t uses only data ≤ t),
forward-return labels, Spearman rank-IC per rebalance, quintile spreads,
walk-forward weights (trailing-24mo IC applied to the *next* month only),
transaction costs.

```bash
python backtest.py --start 2016-01-01 --horizon 63   # quarterly horizon
```

### What it found (78 names, 2016–2026, 110 monthly rebalances, 63d horizon)

| factor | mean IC | IC_IR (ann) | t-stat | read |
|---|---|---|---|---|
| trend | 0.023 | 0.37 | 1.13 | best |
| near_high | 0.018 | 0.35 | 1.05 | good |
| mom_12_1 | 0.017 | 0.28 | 0.86 | modest |
| rel_str | 0.010 | 0.17 | 0.53 | weak |
| squeeze | 0.006 | 0.12 | 0.36 | very weak |
| lowvol | 0.000 | 0.00 | -0.01 | dead |
| **composite** | **0.028** | **0.47** | **1.44** | weak-but-real |
| **composite (walk-forward OOS)** | **0.031** | — | — | holds OOS |

### The honest verdict (do not skip this)
- **At a 1-month horizon the technical factors have ~no edge** (composite t-stat
  0.59, negative quintile spread). At a **quarterly (63d) horizon they show
  weak-but-real signal** (IC_IR 0.47, positive OOS IC) — but the t-stat (1.44)
  is **below the 2.0 significance bar**, and the quintile spread is ~0.
- A long top-quintile portfolio returned **~36% CAGR vs SMH's ~35.5%** — i.e.
  the technical layer roughly **reproduces the semis-ETF beta**, it does not
  clearly beat it. (Both numbers are inflated by survivorship bias; the spread
  is the only meaningful read, and the spread is small.)
- **Conclusion:** treat this as a **discovery + organization + timing** tool, not
  an alpha engine. Its job is to scan the whole universe, surface coiled
  setups, and rank them by sensible factors — not to promise outperformance.

### What this changed in the live screener
- Added **12-1 momentum** (it tested among the best and was missing).
- Re-weighted toward **trend / near_high / momentum**; cut **squeeze / vol-dryup**.
- The **fundamental factors (`eps_rev`, `rev_accel`) are NOT in the backtest** —
  yfinance fundamentals are restated, not point-in-time, so testing them on free
  data would be look-ahead-biased. Estimate-revision momentum is well-supported
  in the literature, so it stays weighted, but it is an **untested overlay** here.
  Validating it (and lifting this from "weak technical edge" to "real edge")
  requires a **point-in-time fundamentals feed** (Sharadar / Norgate / CRSP /
  paid FMP). That is the single highest-value upgrade.

### Backtest limitations (stated, not hidden)
- **Survivorship bias**: current listings only (delisted losers like Infinera
  absent) → optimistic. Needs a bias-free dataset to remove.
- Free EOD data; overlapping horizons; no borrow/short modeling. The equity
  curve is illustrative, not a tradeable track record.

## B3) Data reliability (`datasource.py`)

The screener's biggest risk is its data source: **yfinance is an unofficial Yahoo
scraper with no SLA** — it rate-limits, can change schema, and (worst) can return
nulls so the screener *silently scores on garbage*. All data access now goes
through a `DataSource` interface (`datasource.py`) that hardens this:

- **Swappable** — yfinance lives behind one class; moving to a paid API
  (FMP/Tiingo/Polygon) later is a one-file change, not a rewrite.
- **Cached** — per-ticker data is cached to `.cache/` for 6h, so same-day reruns
  are near-instant and don't re-hammer Yahoo (a rerun dropped 6.8s → 0.8s in
  testing). This is the main rate-limit defense.
- **Retried** — transient errors get exponential-backoff retries.
- **Fails LOUD** — if >25% of fetches fail or >40% of data is stale (latest price
  bar >6 days old), the run **ABORTS with exit code 2 and prints why**, instead
  of printing rankings built on bad data. Silent-wrong is the failure mode that
  hurts; this converts it into a visible stop. Every run prints a `Data health:`
  line (fetched / cached / failed / stale).

**Remaining ceiling (honest):** yfinance is still the underlying source, so the
*reliability* is capped — the hardening removes silent failure and reduces
rate-limiting, but cannot make Yahoo dependable. The real fix is a paid API key
(also unlocks point-in-time data → fixes the backtest look-ahead gap). Note
`backtest.py` still calls yfinance directly (bulk download, offline analysis) and
is not yet behind this layer.

## B4) Sell side — exit monitor (`monitor.py`)

Entries are half the game; most damage happens on exits. Feed it your holdings;
it reads the screener's signals in reverse and returns **HOLD / TRIM / EXIT**:

```bash
python monitor.py NVDA MU COHR          # or: --file holdings.txt
```

Signals: trend break (50<200, or below 50d), 12m momentum negative, estimates
being cut (`eps_rev` flips down — mirror of the entry), lagging SOX (RS
breakdown), below a chandelier trailing stop (22d high − 3·ATR), post-earnings
drop. **EXIT** = a severe signal (trend fully broken / below stop / earnings gap)
or ≥3 signals; **TRIM** = 1–2; **HOLD** = clean. Shows the stop level and how far
price sits above it. Exits here are *risk-management rules*, not backtested
predictions — defensible as discipline, not as forecasting.

## B5) Market regime — risk context (`regime.py`)

```bash
python regime.py
```

Rolls widely-watched risk gauges into RISK-ON / NEUTRAL / RISK-OFF: SPX vs 200d,
breadth (RSP/SPY), credit (HYG/IEF), VIX, yield curve (10y−3mo), semis leadership
(SOXX/SPY). **This does NOT predict crashes — nobody can.** It reads the *ambient
risk environment* for position-sizing ("fragile → tighten up"), and every gauge
prints its own false-positive caveat. High false-positive rates, long/variable
lead times. The most actionable early warning for *this* basket is sector-internal
(memory pricing rollover + capex guide-downs), which the daily catalyst monitor
already tracks — the macro gauges are secondary context.

## C) TradingView (the trigger + alerting)

Finviz/Python find the *candidates*; TradingView is best for the *trigger and
alert*. In the built-in Stock Screener replicate layers 2–3 (perf vs index,
above SMA50/200, near 52w high). For the squeeze, add the **TTM Squeeze** or
**Bollinger Band Width** indicator and set an alert for "squeeze fires +
volume > 1.5× avg." That alert is your day-before-it-breaks ping.

---

## How to actually run it (weekly cadence)

1. **Weekly:** run Python scorer + both Finviz presets → master watchlist.
2. **Tag the leaders:** highest `eps_rev` + `rev_accel`, near (not at) highs.
3. **Set TradingView squeeze+volume alerts** on the top ~15.
4. **Act on the trigger,** not the screen. Screen = candidate; trigger = entry.
5. **Re-rank weekly** — estimate revisions and acceleration change fast in this
   space; a name can go from EARLY to broken-out in a month.

### What this will NOT catch
- Pure-narrative pops with no fundamental inflection (by design — those are
  the ones that round-trip).
- True ground-floor pre-revenue stories (no estimates/financials to screen on).
- Macro/rate shocks that hit the whole complex at once — no bottom-up screen
  saves you from a sector-wide de-rate. That's position-sizing's job, not the
  screener's.
