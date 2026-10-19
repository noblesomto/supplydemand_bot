# Live paper scanner — design

Date: 2026-10-19 · Status: draft for review · Parent spec:
`2026-10-08-supply-demand-bot-design.md`

## 1. Purpose

Forward-test the S/D rules on live data. The holdout window has been viewed
too many times to count as an independent test (parent spec §7, 2026-10-19
amendment), so live bars are the only clean evidence left. The scanner runs
several labeled **systems** side by side. It records each system's signals
and outcomes and posts them to Telegram.

It is **paper only**: no orders are ever placed. Success is a months-long
per-system record of signals and realized R that can be judged against the
parent spec's §7 edge-found bar.

Modeled on harmonic_bot's live systems (`scanner.LIVE_SYSTEMS`, a `system`
column, `[LABEL]`-tagged messages), but it shares no code, data or database
with harmonic_bot.

## 2. Live systems

Selection rule: in the 2026-10-19 calib grids (`departure_atr=1.0`), the
system's expectancy 95% CI lower bound is > 0. Exact duplicates (identical
trade lists) are merged. Wick/close pairs with grades A+B take almost the
same trades, so only the close variant is kept, to avoid double-posting.

| Label | Profile | Rulebook variant | calib expR | CI low | calib DD |
|---|---|---|---|---|---|
| `H4-AB-2R` | H4_H1 | `grid_close_AB_fixed2r` | +0.335 | +0.18 | 13.3% |
| `H4-AB-3R` | H4_H1 | `grid_close_AB_fixed3r` | +0.344 | +0.14 | 12.3% |
| `H4-A-3R` | H4_H1 | `grid_wick_A_fixed3r` | +0.380 | +0.10 | 14.5% |
| `H4-A-2R` | H4_H1 | `grid_wick_A_fixed2r` | +0.289 | +0.07 | 11.9% |
| `PRIME-H1-CT` | H1_M15 | `sw_counter_trend` | +0.375 | +0.15 | 31.6% |
| `PRIME-H1-AB-2R` | H1_M15 | `grid_close_AB_fixed2r` | +0.249 | +0.17 | 22.1% |
| `H1-AB-NL` | H1_M15 | `grid_close_AB_nextlevel` | +0.267 | +0.11 | 43.8% |
| `H1-AB-3R` | H1_M15 | `grid_close_AB_fixed3r` | +0.197 | +0.09 | 32.1% |
| `PRIME-H1-A-2R` | H1_M15 | `grid_wick_A_fixed2r` | +0.154 | +0.04 | 19.9% |
| `H1-BASE` | H1_M15 | baseline | +0.212 | +0.001 | 38.3% |

The systems are defined in exactly one place:
`live/systems.py: LIVE_SYSTEMS: list[LiveSystem]`, where
`LiveSystem(label, profile, variant)` is a frozen dataclass. Adding,
removing or changing a system means editing only that list. A label is
permanent once it has signals: a changed rule set gets a new label, so one
label's record never mixes two rule sets.

All systems trade all 16 pairs in `settings.PAIRS`.

`PRIME-` labels mark the systems that were positive in every calendar
year from 2022 to 2026 in the $1000 account projection. Their Telegram
messages start with ⭐ so they get more attention. (Amendment
2026-10-19, at the user's request.)

## 3. Signal generation: replay

The scanner does **not** re-implement the strategy. Each cycle, for each
system and pair, it calls the existing `core.backtest_engine.run_backtest`
on candles from a **fixed anchor** to now:

- `REPLAY_ANCHOR = 2026-01-01T00:00Z` (configurable). Because the anchor is
  fixed, every cycle replays from the same starting state, so results are
  deterministic and a trade seen once is seen again. The anchor leaves
  ample warm-up for zones (`zone_max_age` 250 bars) and pivots
  (`pivot_n` 21).
- Each replayed `BacktestTrade` is identified by
  `(system, pair, entry_timestamp, direction)`.
- **New signal**: an identity not yet in the DB, with
  `entry_timestamp >= GO_LIVE_AT`. `GO_LIVE_AT` is set once, the first
  time the scanner runs, and stored in the DB. Trades from before go-live
  are never stored or alerted, so the record is forward-only.
- **Outcome**: once a stored signal's replayed trade has
  `outcome.resolved` and is not `open_at_end`, the scanner records
  `exit_kind`, `exit_timestamp` and `realized_r`, which is already net of
  spread. The signal is then closed. Outcomes are never edited after that.
- **Missing trade**: a stored, still-open signal that stops appearing in
  the replay means a data revision. Log a warning and post a one-line
  `[LABEL] ... vanished` alert. Keep the row with status `VANISHED`;
  never delete it.

Cost: the corrected engine replays about 10 months of one pair in well
under a second, so 10 systems × 16 pairs fits in each cycle. Data is
loaded once per pair and profile and shared by every system on that
profile.

### Per-system account rules

Each system has its own paper account: $1000 start balance, 1% risk,
max 3 concurrent positions, 3% daily loss stop, and a halt for the day
after 5 consecutive losses. These are the same rules as the backtest.
Each cycle, the scanner runs `core.backtest_account.apply_account_simulation`
on the system's own post-go-live trades. Every signal is stored with
`taken` = true/false, plus a `skip_reason` when it is false.

Skipped signals are still stored and alerted, marked
`SKIPPED (3 open)` or `SKIPPED (daily stop)`. This means both raw and
account-level results can be measured. One system's account never sees
another system's trades.

## 4. Data

- **Source**: cTrader Open API M1 bid bars (`core/feeds/ctrader.py`
  `HistoryClient.trendbars_m1`). This is the same source and broker feed
  as the backtest cache, so live candles match tested candles.
- **Prerequisite (one-time, user)**: `python -m core.feeds.ctrader_auth`,
  with `CT_CLIENT_ID`/`CT_CLIENT_SECRET` in `.env`. The same app
  registration as harmonic_bot's executor is fine; this project gets its
  own tokens.
- **Incremental fetch**: each cycle, for each pair, fetch M1 bars from the
  last cached bar to now. Append them to
  `data_cache/ctrader_m1/<PAIR>/<YYYY-MM>.parquet`, the layout the
  backtest cache already uses. Then resample M15/H1/H4 with
  `core/resample.py`, the same code the backtest uses.
- **Closed bars only**: the newest, still-forming M15 bucket is dropped
  before replay. Higher timeframes only include buckets whose last M15 bar
  is complete.
- **Fetch failure** for a pair: log it and skip that pair this cycle. The
  other pairs and systems carry on. After 3 consecutive failed cycles for
  any pair, send one Telegram warning.

## 5. Schedule and process

- One long-running process: FastAPI + APScheduler, run by systemd, on its
  own port (`PORT`, default 8003). This is the same shape as
  harmonic_bot, so the watchdog pattern carries over.
- **Scan job**: every 15 minutes, at hh:00:30, :15:30, :30:30 and :45:30
  UTC, just after each M15 bar closes. H4_H1 systems only produce new
  entries when an H1 bar closes, so they can skip other cycles. Their
  open signals are still checked for outcomes, because exits are
  evaluated on confirm-TF bars.
- **Isolation**: each (system, pair) replay runs inside its own
  try/except. An exception is logged with the label and pair and never
  stops the rest of the cycle. Each system's DB writes are in their own
  transaction.
- Weekends and holidays: no new M1 bars means no replay; the cycle is a
  no-op.
- `GET /health` returns `{status, version, last_scan_at, go_live_at}` for
  a watchdog. Basic auth is not needed because nothing else is exposed.

## 6. Storage

SQLite via SQLAlchemy at `settings.DB_URL` (`supplydemand_bot.db`, already
in `.gitignore` as `*.db`).

`signals` table:

| column | notes |
|---|---|
| id | pk |
| system | label, e.g. `H4-AB-2R` (indexed) |
| pair, direction, zone_type, grade | from `BacktestTrade` |
| entry_timestamp | UTC; unique together with (system, pair, direction) |
| entry, sl, tp, rr | `tp` is null for exits without a fixed target |
| taken, skip_reason | per-system account rules (§3) |
| status | `OPEN`, `CLOSED`, `VANISHED` |
| exit_kind, exit_timestamp, realized_r | set once, on close |
| created_at, alerted_at, closed_alerted_at | bookkeeping |

`meta` table: `go_live_at` and `last_scan_at`.

## 7. Telegram

Uses its own `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` from this repo's
`.env`. They can point at the same chat as harmonic_bot. Every message
starts with the system label in brackets.

```
[H4-AB-2R] EUR/USD LONG (demand, grade A)
Entry 1.08412  SL 1.08190  TP 1.08856  (2.0R)
Bar 2026-10-20 13:00 UTC · paper

[H4-AB-2R] EUR/USD LONG closed: TP +1.94R (net of spread)

[H1-CT] GBP/JPY SHORT (supply, grade B) SKIPPED (3 open)
```

If a send fails, it is retried on the next cycle, which is driven by
`alerted_at` being null. A Telegram outage therefore never loses a signal
and never blocks a scan.

## 8. Reporting

`scripts/live_report.py` prints, for each system, using closed signals
only:
- trades, win rate and expectancy R with its CI95;
- per-pair results;
- account-level balance and max DD from the `taken` signals;
- forward results next to the calib row from §2, so decay is visible.

No dashboard in v1.

## 9. Layout

```
live/
  systems.py      LIVE_SYSTEMS
  data.py         incremental M1 fetch + append + resample (closed bars only)
  replay.py       run one system on one pair -> trades; diff against DB
  store.py        SQLAlchemy models + queries (signals, meta)
  notify.py       Telegram formatting + send/retry
  scanner.py      cycle orchestration, APScheduler jobs
main.py           FastAPI app, /health, scheduler lifecycle
scripts/live_report.py
supplydemand_bot.service   systemd unit (installing it is a manual step)
```

The core engine is reused unchanged. Any change to `core/` affects the
backtest and live systems equally, and must bump `version.py`.

## 10. Testing

- **Replay diff** (unit): given stored signals and a list of replayed
  trades, the diff yields the right new / closed / vanished sets. Trades
  from before go-live are ignored, and a re-run is idempotent.
- **Live = backtest** (integration): build the "live" candle path from a
  cached historical slice, cutting off bar by bar. Every signal it emits
  must match the trade list of a single `run_backtest` over the whole
  slice. This is the guarantee that matters most.
- **Isolation**: a system whose replay raises still lets the other
  systems' signals be stored, and the error is logged with its label.
- **Account flags**: `taken`/`skip_reason` match
  `apply_account_simulation` for a hand-built trade list.
- **Data**: the forming bar is dropped; appending new M1 bars across a
  month boundary writes both parquet files correctly.
- **Notify**: formatting includes the label; an unsent alert is retried.

## 11. Out of scope (v1)

Order execution, dashboard, per-system pair filtering, changing systems
automatically based on live results, and anything that reads
harmonic_bot's data.
