# Market Screener

[![Daily report](https://github.com/hardy30894/market-screener/actions/workflows/daily.yml/badge.svg)](https://github.com/hardy30894/market-screener/actions/workflows/daily.yml)

A discovery-first, earnings-aware stock screener for the AI / memory / semiconductor / photonics complex, with an exit monitor, a market-regime dashboard, and a real backtest that tells you whether any of it actually works.

It scans the whole US tech-hardware market live, ranks coiled pre-breakout setups, flags strong names reporting soon, and watches your holdings for exit signals. It runs itself daily in the cloud and writes one report.

> **It is a decision-support tool, not a money machine.** The backtest below shows the technical edge is weak (roughly market-beta). It surfaces a researched, ranked queue of candidates. It does not promise returns, and it will not tell you the future. Read [What this is NOT](#what-this-is-not) before trusting a single score.

---

## Table of contents

- [What it does](#what-it-does)
- [What this is NOT](#what-this-is-not)
- [Components](#components)
- [Quick start](#quick-start)
- [Usage](#usage)
- [How the score works](#how-the-score-works)
- [Does it actually work? (backtest)](#does-it-actually-work-backtest)
- [Data reliability](#data-reliability)
- [Automation](#automation)
- [Limitations and roadmap](#limitations-and-roadmap)
- [Disclaimer](#disclaimer)

---

## What it does

1. **Discovers** across the entire US tech-hardware universe (semis, equipment, photonics, components, plus optional solar and quantum), not a hand-picked list. Names you have never heard of surface on their own.
2. **Scores** each survivor 0 to 100 by stacking fundamental-inflection signals (estimate-revision momentum, revenue acceleration, margin expansion) with technical pre-breakout signals (proximity to highs, trend, momentum, volatility contraction).
3. **Flags earnings**: every row shows days-to-earnings, and a dedicated EARNINGS WATCH block surfaces strong picks reporting soon, front and center.
4. **Monitors exits**: feed it your holdings and it returns HOLD / TRIM / EXIT with the reasons firing (trend break, estimate cuts, stop levels).
5. **Reads the regime**: a risk-on / risk-off dashboard from yield curve, credit spreads, breadth, volatility, and semis leadership.
6. **Validates itself**: a point-in-time backtest measures whether the factors predict forward returns, out of sample, after costs.

Every morning it assembles all of this into a single dated report.

## What this is NOT

This section exists because the easiest thing to build is a screener that *looks* authoritative and is quietly wrong. This one tries hard not to be that.

- **Not a validated alpha engine.** The backtest says the technical factors carry weak signal (IC_IR about 0.47 at a quarterly horizon, t-stat 1.44, below significance) and a top-quintile basket roughly reproduced the semis ETF. Treat scores as a research queue, not buy signals.
- **Not a crash predictor.** The regime dashboard reads ambient risk. It has high false-positive rates and long, variable lead times. Nobody predicts crashes, and this does not pretend to.
- **Not a backtested earnings or exit strategy.** Earnings tags and exit rules are sensible and literature-backed (post-earnings drift, trailing stops), but they are signals and risk-management discipline, not proven-on-this-data edges.
- **Not reliable data, yet.** It runs on yfinance (an unofficial Yahoo scraper). The data layer is hardened (cache, retry, fail-loud) but yfinance is still the ceiling. See [Data reliability](#data-reliability).

If a number looks confident, check its `cov` (coverage) column and remember the backtest verdict.

## Components

| File | Role |
|------|------|
| `screener.py` | Discovery + scoring + verdicts + EARNINGS WATCH |
| `monitor.py` | Exit monitor: HOLD / TRIM / EXIT on your holdings |
| `regime.py` | Market-regime risk dashboard (context, not timing) |
| `backtest.py` | Factor validation: rank-IC, quintiles, walk-forward, costs |
| `datasource.py` | Swappable, cached, fail-loud data layer |
| `daily_report.sh` | Assembles all of the above into one dated report |
| `MARKET_OUTLOOK_2026_2027.md` | The cited macro thesis behind the basket |
| `SCREENER.md` | Full methodology, Finviz recipe, factor details |

## Quick start

```bash
pip install yfinance pandas numpy

python screener.py                 # discover + score the tech-hardware market
python screener.py --all-themes    # add solar + quantum
python monitor.py NVDA MU COHR     # exit signals on holdings
python regime.py                   # market-risk dashboard
python backtest.py                 # does the strategy actually work?
bash daily_report.sh               # the full combined report
```

## Usage

### Screener

```bash
python screener.py                       # AI/memory/semis/photonics (default)
python screener.py --growth 15           # only names already growing rev >15%
python screener.py --all-themes          # everything incl. solar + quantum
python screener.py --quantum --seeds NOK # add quantum pure-plays + specific tickers
python screener.py --min-score 60        # only strong setups
python screener.py NVDA MU CRDO          # score exactly these
```

Output splits into CORE (large-cap) and EARLY (small/mid), each ranked, with a plain-English `verdict` (`primed`, `early (fundies)`, `extended`, `watch`, `broken trend`) and an EARNINGS WATCH block at the top.

### Exit monitor

```bash
python monitor.py --file holdings.txt    # one ticker per line
```

### Regime + backtest

```bash
python regime.py
python backtest.py --start 2016-01-01 --horizon 63
```

## How the score works

Three layers, stacked. The edge (such as it is) comes from combining them, not from any single filter.

1. **Fundamental inflection (leading).** Forward-EPS revision momentum (the highest-weighted factor, best-documented predictor), revenue-growth acceleration, gross-margin expansion, last earnings surprise.
2. **Technical pre-breakout (timing).** Proximity to the 52-week high (coiled, not extended), 12-1 momentum, trend (50 over 200), relative strength vs the SOX, volatility contraction.
3. **Liquidity gate.** Price and dollar-volume floors to keep junk out.

The technical weights are calibrated to measured backtest rank-IC; the fundamental block is theory-backed but, on free data, an untested overlay (see below). Full detail in [`SCREENER.md`](SCREENER.md).

## Does it actually work? (backtest)

`backtest.py` measures whether the factors predict forward returns, point-in-time, out of sample, after costs. On about 78 names over 2016 to 2026, quarterly horizon:

| factor | IC_IR (annualized) | read |
|--------|-------------------|------|
| trend | 0.37 | best |
| near_high | 0.35 | good |
| mom_12_1 | 0.28 | modest |
| rel_str | 0.17 | weak |
| squeeze | 0.12 | very weak |
| lowvol | 0.00 | dead |
| **composite** | **0.47** | weak-but-real |
| **composite (walk-forward, out-of-sample)** | **positive** | holds OOS |

**Honest verdict:** weak signal at a quarterly horizon, not statistically significant (t-stat 1.44, below the 2.0 bar), and the long top-quintile basket returned about 36% CAGR vs the SOX ETF's 35.5%. In plain terms: the technical machinery roughly reproduces the sector beta. The fundamental factors (the ones that probably carry the real edge) are excluded from the backtest because free data is not point-in-time. Use this as a discovery and timing tool, not a return generator.

## Data reliability

The screener talks only to a `DataSource` interface, so the provider is a one-file swap. The yfinance implementation is hardened:

- **Cached** to disk (6h), so reruns do not re-hammer Yahoo.
- **Retried** with exponential backoff on transient errors.
- **Fails loud**: if more than 25% of fetches fail or more than 40% of data is stale, the run aborts with exit code 2 and prints why, instead of scoring on garbage. Silent-wrong is the failure mode that hurts; this turns it into a visible stop.

The ceiling: yfinance is still an unofficial scraper with no SLA. The real fix is a paid API key (FMP / Tiingo / Polygon), which also unlocks point-in-time data and a genuine backtest. The abstraction is ready for that swap.

## Automation

Two jobs, each placed where it actually works:

| Job | Home | Schedule |
|-----|------|----------|
| Screener + earnings + exits + regime + thesis | **GitHub Actions** (this repo) | daily 13:00 UTC (9am ET) |
| Catalyst-news digest | Claude routine (cloud) | daily |

GitHub Actions runs in the cloud with open internet, so it is laptop-independent and commits each day's report to [`results/`](results). A local launchd job (macOS) can serve as a backup. The Claude cloud environment blocks outbound network to Yahoo, which is why the data job lives on GitHub Actions and only the web-search news job lives there.

To read the latest report: open the newest file in [`results/`](results).

## Limitations and roadmap

- **Validated edge is weak.** The single highest-value upgrade is a point-in-time paid data feed, which would both fix reliability and let the fundamental factors (and exits, and regime) be properly backtested. That is the path from "weak technical edge" to a real, measured edge.
- **Survivorship bias** in the backtest (current listings only) flatters the absolute returns; read the spread, not the level.
- **yfinance reliability** is capped until the API swap.
- `backtest.py` still calls yfinance directly and is not yet behind the data layer.

## Disclaimer

This is software for research and education. It is not investment advice, not a recommendation to buy or sell any security, and carries no warranty. All forward-looking figures referenced in the macro thesis are forecasts subject to revision. Markets carry risk, including loss of principal. Do your own research and consult a licensed professional before acting on anything here.
