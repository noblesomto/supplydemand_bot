# supplydemand_bot

Rule-based implementation of the Colibri Trader "Ultimate Supply And
Demand Course": zone (base between large candles) -> retrace -> trap
beyond the zone's far edge -> price-action confirmation on the zone
timeframe -> a second confirmation on the lower timeframe -> enter
against the fake breakout.

Design spec: `docs/superpowers/specs/2026-10-08-supply-demand-bot-design.md`
(architecture, rules, calibration protocol, edge-found bar).

Modeled on `/home/www/python/harmonic_bot` (backtest method, account
simulation, project conventions) -- but the trading rules here are
independent: nothing reads harmonic_bot's signals, results, or database.

## Setup

    python3.11 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env   # fill in TWELVE_DATA_API_KEY / TWELVE_DATA_API_KEY_2

Python 3.11 specifically -- some dependencies don't yet have prebuilt
wheels for 3.14 and fall back to a slow/fragile source build.

## Tests

    pytest

## Fetching history

**Primary source: cTrader M1.** Twelve Data's free tier (800 credits/day)
turned out far too small to backfill 16 pairs x multiple timeframes over
several years -- it took two days to barely cover H4 and still left H1
gaps. `/home/www/python/maker_ot` already has a complete, gap-free M1 bid
cache for the same 16 pairs via the cTrader Open API (no daily credit
ceiling, just a modest per-second pace limit), so this project seeds from
a one-time copy of that cache and resamples locally:

    cp -r /home/www/python/maker_ot/data_cache/ctrader/* data_cache/ctrader_m1/
    python scripts/build_from_ctrader_m1.py                # all pairs: M1 -> M15 -> H1 -> H4 -> D1
    python scripts/build_from_ctrader_m1.py --pairs EUR/USD,XAU/USD

This only reads `data_cache/ctrader_m1/<PAIR>/<YYYY-MM>.parquet` and
resamples via `core/resample.py` (now handles M1->M15 too) -- no network
calls, replaces `data_cache/<PAIR>_{m15,h1,h4,d1}.csv` with one
internally-consistent set derived from a single source. **Known data
constraint**: cTrader's cached history starts **2022-01**, so the actual
calib/holdout window is ~4.8 years, not the ~10 years the design spec
originally assumed (Twelve Data reached back to ~2020-03 for majors only,
with gaps for crosses -- see git history for that abandoned path).

Getting *new* M1 bars beyond what's cached (for future backtest refreshes,
or eventually the live scanner) needs this project's own cTrader tokens --
not maker_ot's:

    cp .env.example .env   # fill in CT_CLIENT_ID / CT_CLIENT_SECRET (can copy harmonic_bot/executor/.env's), CT_REDIRECT_URI
    python -m core.feeds.ctrader_auth   # one-time OAuth consent in a browser; saves .ctrader_tokens.json

`core/feeds/ctrader.py`'s `HistoryClient.trendbars_m1` then pulls M1 the
same way `core/candle_cache.fetch_candles_cached` already supports via an
injectable `fetch_fn` (see `scripts/fetch_history.py` for the pattern).

**Legacy / fallback path** (`scripts/fetch_history.py`, Twelve Data +
yfinance): kept for reference and for `--pairs`/`--skip` one-offs, but no
longer the primary way to build the cache -- see the constraint above.
Resumable: `core.candle_cache` only fetches whatever date range isn't
already cached.

## Backtesting

    python backtest_cli.py --profile D1_H4 --variant grid_wick_A_nextlevel --start 2020-03-01 --end 2024-06-01
    python backtest_cli.py --profile D1_H4 --variant <name> --start 2024-06-01 --end 2026-10-01 --period holdout --i-mean-it

`--profile` is one of `D1_H4`, `H4_H1`, `H1_M15` (spec section 3 --
rules are timeframe-agnostic; the course's own D1/H4 profile is tested
first). `--variant` names a block in `rulebook/rules.yaml`. `--period
holdout` refuses without `--i-mean-it` and logs every holdout run to
`docs/backtest_reports/holdout_log.md`.

## Project layout

See the design spec, section 4. In short: `core/` holds the
timeframe-agnostic rule engine (patterns, zones, trend, the setup state
machine, levels, exit simulation, the backtest engine/account/report);
`rulebook/` holds all tunable thresholds and the variant grid;
`backtest_cli.py` and `scripts/fetch_history.py` are the entry points.

The live scanner (`scanner.py`, `main.py`, `alerts/`, etc., mirroring
harmonic_bot's shape) and the executor are **not built yet** -- per the
design spec, the scanner only gets built once a variant clears the
"edge found" bar in section 7, and the executor is gated on the live
scanner's results matching backtest.
