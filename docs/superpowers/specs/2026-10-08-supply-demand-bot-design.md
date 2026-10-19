# Supply & Demand Bot — Design Spec

Date: 2026-10-08
Status: draft for review

## 1. Purpose and scope

Mechanically implement the Colibri Trader "Ultimate Supply And Demand Course"
(30 video transcripts + DEMSU P01/P02/P03 PDFs in `docs/`) as a rule-based
system, and determine by backtest whether it has a real, cost-adjusted edge.

Two sections, built in order:

1. **Research + alerting** (this spec): a backtester, then — only after the
   edge-found bar in §7 is cleared — a live scanner that sends Telegram alerts.
2. **Executor** (out of scope here): automated trading. Gets its own spec only
   after live alerts have run and track the backtest.

Constraints:

- Modelled on `/home/www/python/harmonic_bot` (architecture, conventions,
  backtest method). Useful modules are **copied** into this repo, never
  imported from harmonic_bot. No harmonic_bot signals, results, or DB are read.
- Instruments (v1): harmonic_bot's 20 FX pairs + XAU/USD.
- The course's 4th PDF (the "trading manual") is unavailable; rules below are
  derived from the transcripts and the three article PDFs.

Non-goals (v1): indices, bonds, stocks; scaling-in (belongs to the author's
other course); news filter in backtests; any executor work.

## 2. Strategy summary (source of truth for the rules)

Zone (base between large candles) → price retraces to the zone → price trades
beyond the zone's far edge and fails (bull/bear trap) → price-action
confirmation on the zone timeframe → second price-action confirmation on the
lower timeframe → enter against the fake breakout. 1% risk, minimum 1:2 RR,
target at next opposing level. Sources: L2–L4, L12, L13, L19–L21, L29;
P01 pp6–12; P02 pp5–8; P03 pp12, 22–24.

## 3. Timeframe profiles

Rule logic is timeframe-agnostic. A **profile** = (zone TF, confirmation TF):

| Profile | Zone / setup / first confirmation | Entry confirmation |
|---|---|---|
| `D1_H4` (course as written; run first) | D1 | H4 |
| `H4_H1` | H4 | H1 |
| `H1_M15` | H1 | M15 |

All thresholds are expressed in **bars and ATR(14) multiples** of the relevant
timeframe — never pips or days.

Optional **HTF zone filter** (P03 "seashell"): a setup only counts if its zone
overlaps an active zone one or more profile-levels higher. A variant switch.

## 4. Project layout

```
supllydemand_bot/
  config.py  .env.example  version.py  requirements.txt  .gitignore
  rulebook/        rules.yaml, models.py (pydantic), loader.py
  core/
    candles.py         Candle dataclass                          (copied)
    candle_cache.py    CSV cache, range-aware                    (adapted)
    feeds/             yfinance (adds 1d), twelvedata, market_data (copied+extended)
    resample.py        M15→H1, H1/M15→H4, H4→D1 (22:00 UTC day)   (adapted)
    atr.py                                                       (copied)
    patterns.py        pin bar, engulfing (1/2/3-bar), inside bar
    zones.py           zone detection, bounds, close line, lifecycle
    trend.py           swing-structure trend (fractal pivots)
    setup_machine.py   per-zone state machine
    levels.py          entry, SL, target selection, RR gate
    trade_sim.py       bar-by-bar exit models
    backtest_engine.py
    backtest_account.py                                          (copied)
    backtest_report.py                                           (adapted)
  backtest_cli.py
  scripts/fetch_history.py
  data_cache/          seeded from harmonic_bot's CSVs, then extended
  docs/backtest_reports/
  tests/
  -- after edge found --
  scanner.py  main.py  alerts/  charts/  db/  api/  systemd units  scripts/watchdog.py
```

## 5. Data

**Amendment (2026-10-09, superseding the table below)**: Twelve Data's free
tier (800 credits/day) could not realistically backfill 16 pairs x
multiple timeframes over several years -- two days of fetching barely
completed H4 and still left H1 gaps for 8 pairs. `/home/www/python/maker_ot`
already has a complete, gap-free M1 bid cache for the same 16 pairs via the
cTrader Open API (no daily credit ceiling). This project now seeds from a
one-time copy of that cache (`data_cache/ctrader_m1/`) and resamples
M1→M15→H1→H4→D1 locally (`scripts/build_from_ctrader_m1.py`,
`core/resample.py`). History starts **2022-01**, not 2016 -- the
calib/holdout split in section 7 (originally ~10 years, then revised to
~5.5 on Twelve Data) is now **~4.8 years**; recompute the calib/holdout
boundary from whatever each pair's cache actually covers before running
section 7. Twelve Data + yfinance (`scripts/fetch_history.py`) remain as a
documented fallback for pulling fresh bars beyond the cached range, via
this project's own cTrader tokens (`core/feeds/ctrader.py` +
`ctrader_auth.py`) going forward in preference to Twelve Data. See
README.md "Fetching history" for the exact commands.

| TF | Source | History |
|---|---|---|
| D1 | aggregated from H4 on a 22:00-UTC day (Sunday stub merged into Monday); yfinance daily only for history before H4 coverage | 2015→ (2015 = warm-up) |
| H4 | Twelve Data (`TWELVE_DATA_API_KEY_2`) | 2016→ |
| H1 | aggregated from cached M15 where present; Twelve Data otherwise | 2019→ |
| M15 | harmonic_bot cache (2024–26) + Twelve Data | 2022→ |

- Seed: copy harmonic_bot's `data_cache/*.csv` once; normalize the per-year
  files (`_2024`, `_2026`, unsuffixed = 2025) into one file per (pair, TF).
- `candle_cache` becomes range-aware: stores coverage, fetches only missing
  ranges, de-duplicates by timestamp. File name `PAIR_TF.csv`.
- `scripts/fetch_history.py` fetches in priority order H4 → H1 → M15, resumable,
  paced for the free-tier quota.
- Timestamps: naive UTC strings `YYYY-MM-DD HH:MM:SS` (harmonic convention).
- Gold on yfinance uses `GC=F` (harmonic convention).
- Resampling keeps only full buckets, except the D1 Sunday merge.

## 6. Rules

All numeric values are rulebook parameters; bracketed values are course-derived
starting defaults, not tuned.

### 6.1 Candlestick patterns (`patterns.py`, pure functions)

- **Pin bar** (bullish; bearish mirrored): lower wick ≥ 2×body and ≥ 60% of
  range; close in upper third of range.
- **Engulfing** (bullish; bearish mirrored): current bar bullish, its body
  covers the previous bar's body (open ≤ prev body low, close ≥ prev body
  high), previous bar bearish. **Multi-bar engulfing** (P02, L29): the same
  test against the combined body span of the previous 2 or 3 bars.
- **Inside bar**: high < prev high and low > prev low. Directional meaning
  comes from context (it must follow the trap bar), not its own colour.

### 6.2 Zone detection (`zones.py`, zone TF, closed bars only)

- **Base**: 1..`max_base` [6] consecutive bars, each body ≤ `base_body_atr`
  [0.5]×ATR; total base range ≤ `base_range_atr` [1.5]×ATR.
- **Leg-in**: bar immediately before the base, body ≥ `leg_atr` [1.0]×ATR.
- **Leg-out**: bar immediately after the base, body ≥ `leg_atr`×ATR, and within
  `leg_out_bars` [3] bars price closes ≥ `departure_atr` [2.0]×ATR beyond the
  base in the leg-out direction.
- **Type**: (leg-in, leg-out) = (down, up) DBU or (up, up) UBU → **demand**;
  (up, down) UBD or (down, down) DBD → **supply**.
- **Bounds**: wick-to-wick of base bars. Demand: proximal = base high,
  distal = base low. Supply: proximal = base low, distal = base high.
  **Close line**: base's most extreme close on the distal side.
- **Lifecycle**:
  - `ACTIVE` from leg-out completion.
  - **Touch**: a bar entering the zone after price has been outside it.
    Retire after `max_touches` [3].
  - `BROKEN`: close beyond distal by > `break_atr` [1.0]×ATR, or
    `break_bars` [3] consecutive closes beyond distal with no confirmation.
  - `EXPIRED` after `zone_max_age` [250] bars.
  - New zone overlapping an active same-type zone → merged (union bounds).
  - A zone that produced a trade is retired.

### 6.3 Trend (`trend.py`)

Fractal pivots, `pivot_n` [3] bars each side, on the zone TF. **Up** = last two
pivot highs and lows both higher; **down** = both lower; else **range**.

- Trade with trend; in range both directions allowed.
- Variant `counter_trend_extreme` (L20): allow counter-trend only when the zone
  lies beyond the last range high/low (range extremity).

### 6.4 Setup state machine (`setup_machine.py`)

One instance per active zone, advanced on each closed zone-TF bar:

```
WATCHING        → bar enters zone                         → IN_ZONE
IN_ZONE         → track deepest penetration
                  confirmation candle (6.1) in trade direction, within
                  confirm_window [3] bars after the deepest bar:
                    grade A: deepest point beyond distal edge
                             (trap_mode=wick: wick beyond distal;
                              trap_mode=close: a close beyond the close line)
                    grade B: deepest point reached zone midpoint
                    grade C: proximal-half only → discard, counts as touch
                  if grade ∈ accepted_grades              → ZONE_CONFIRMED
ZONE_CONFIRMED  → scan confirmation-TF bars that close after the zone-TF
                  confirmation bar, up to h4_window [2] zone bars' worth,
                  for a pattern (6.1) in trade direction  → ENTRY
any             → zone BROKEN / window expired / trend filter fails
                                                          → EXPIRED(reason)
```

- Inside-bar entry variant: `ib_entry=close` (enter at IB close, L19) or
  `ib_entry=break` (enter when a later bar breaks the IB range in trade
  direction, within the same window).
- Every transition and expiry is logged for funnel diagnostics (§8).

### 6.5 Levels and sizing (`levels.py`)

- **Entry** = close of the confirmation-TF bar that triggered ENTRY.
- **SL** (short; long mirrored) = highest high from the trap/deepest bar
  through the entry bar + `sl_buffer_atr` [0.25]×confirmation-TF ATR +
  pair spread.
- **Target** = nearest level beyond entry in trade direction among:
  proximal edge of the nearest opposite active zone; last confirmed zone-TF
  pivot; (variant `round_targets`) next round number at `round_step` per pair
  [FX 0.0100, JPY 1.00, XAU 10.0].
- **Reject** if risk ≤ 0, target behind entry, or reward/risk < `min_rr` [2.0].
- **Sizing**: `risk_per_trade_pct` [1.0] of current equity, compounding.
- **Account limits** (copied `backtest_account`): `max_concurrent_positions`
  [3], `max_daily_loss_pct` [3.0], `max_consecutive_losses` [5].
- **Costs**: per-pair spread table in price units (rulebook); each trade's
  cost in R = round-trip spread / that trade's SL distance.

### 6.6 Exit models (`trade_sim.py`, simulated on confirmation-TF bars)

| Variant | Rule |
|---|---|
| `fixed_2r`, `fixed_3r` | TP at 2R / 3R (P01) |
| `next_level` | TP at the target from 6.5 |
| `ladder` | No early breakeven. When a zone-TF bar closes beyond the next opposing level and the following zone-TF bar does not close back through it, move SL to that level ± `sl_buffer_atr`. Repeat for each further level. Exit only by SL. (L5, L24) |
| `ladder_pa_exit` | `ladder` + at any opposing level, an opposite-direction confirmation candle on the zone TF closes the trade at that bar's close. (L7, L24) |

- Same-bar SL/TP ambiguity: SL first. Optional M15 replay to resolve, reported
  as a diagnostic (count of ambiguous bars and how they resolved).
- Trades still open at data end: marked-to-market at the last close and flagged
  `open_at_end` (never dropped).

## 7. Calibration protocol and edge-found bar

**Amendment (2026-10-12)**: with the course-derived defaults (§6.2/§6.3),
the full D1_H4→H4_H1→H1_M15 cascade below ran to completion and every
variant on every profile came back with at most 3 entered trades across
all 16 pairs — zone detection itself was the bottleneck (37 zones total
on D1_H4's calib window), not the RR/target gate. A threshold sweep
(`docs/backtest_reports/`) found `leg_atr` (the leg-in/leg-out "big
candle" body-size threshold), not `departure_atr`, is the dominant lever
on zone count. `rulebook/rules.yaml`'s `baseline` now uses `leg_atr=0.5`
(half the course's default), `departure_atr=0.3` (15% of the course's
default), and `pivot_n=21` (sparser swing pivots for target selection) —
a real, documented deviation from the literal course rules, adopted
because the untuned defaults could not produce a statistically testable
sample on this data (16 pairs, ~4.8 years total). Applied uniformly
across all three profiles, consistent with "no per-timeframe retuning"
below. The calib/holdout split itself is **2022-01-04 → 2025-05-04**
(calib) and **2025-05-04 → 2026-10-08** (holdout) for all profiles, per
the §5 amendment — not the 2016–2021/2022–2026 split originally assumed
below.

**Amendment (2026-10-19)**: the 2026-10-12 retune was decided on an
engine with two bugs. Targets scanned every historical pivot instead of
the last one (§6.5). That made results depend on where the data started
and rejected most setups on RR. Spread was also never charged. Both are
fixed. A calib-only re-sweep on the corrected engine
(`scripts/threshold_sweep.py`,
`docs/backtest_reports/threshold_sweep_20261019.md`) found:

- The course defaults are still too strict: `leg_atr=1.0`/`departure_atr=2.0`
  gives 0–25 calib trades on D1_H4 and H4_H1. So `leg_atr=0.5` stays.
- `departure_atr=0.3` was the weak setting. `departure_atr=1.0` (half the
  course default) roughly doubled calib expectancy on all three profiles
  and still leaves a testable sample.

`baseline` now uses `departure_atr=1.0`, with `leg_atr=0.5` and
`pivot_n=21` unchanged, uniformly across profiles. This was picked from
a 108-cell calib sweep, and the holdout has already been viewed several
times (`holdout_log.md`). Holdout results under this setting therefore
cannot count as an independent edge test. Only forward / paper-traded
data can.

- Each profile splits its history: first ~70% **calib**, last ~30%
  **holdout** (D1_H4: calib 2016–2021, holdout 2022–2026-09).
- **Variants** are named override blocks in `rules.yaml`. The **core set** is
  fixed before any run:
  - Grid: `trap_mode` {wick, close} × `accepted_grades` {A, A+B} ×
    exit {fixed_2r, fixed_3r, next_level, ladder}, with `ib_entry=close`.
  - One-at-a-time on the baseline: `ib_entry=break`, `ladder_pa_exit`,
    `htf_zone_filter`, `counter_trend_extreme`, `round_targets`.
  - Baseline = trap_mode=wick, grades A, exit next_level, ib_entry=close.
- Select on calib only. The chosen variant(s) run on holdout once.
  `--period holdout` requires `--i-mean-it`; every holdout run is appended to
  `docs/backtest_reports/holdout_log.md`.
- **Edge-found bar** (all on holdout, after costs):
  1. expectancy > 0 with the 95% CI lower bound > 0
  2. profitable in every calendar year of the holdout
  3. profitable on ≥ 60% of pairs individually; pairs losing standalone in
     both calib and holdout are excluded from live
  4. max drawdown < 15% at 1% risk
  5. ≥ 30 holdout trades (fewer = inconclusive, not a pass)
- If no variant passes on `D1_H4`, repeat the same protocol on `H4_H1`, then
  `H1_M15` — same rules and variant set, no per-timeframe retuning.

## 8. Backtest CLI and report

```
python backtest_cli.py --profile D1_H4 --variant baseline --period calib [--pairs all] [--start-balance 10000] [--charts]
python backtest_cli.py --profile D1_H4 --variant-set core --period calib
python backtest_cli.py --profile D1_H4 --variant <name> --period holdout --i-mean-it
```

Output: JSON + markdown summary in `docs/backtest_reports/`, trades CSV.

- **Headline**: trades, win rate, expectancy R (95% CI), profit factor, net
  profit, max DD %, Sharpe, open-at-end count.
- **Breakdowns**: year, pair, direction, zone type, grade, exit reason.
- **Funnel**: zones detected → retraced → graded A/B/C → zone-TF confirmed →
  conf-TF confirmed → passed RR → entered; expiries by reason.
- **Signal quality**: for every ZONE_CONFIRMED setup (traded or not), MFE/MAE
  in R over N zone-TF bars [10, 20], to separate signal edge from exit edge.
- `--charts`: one PNG per trade (zone, trap, entry, SL, target), plotly,
  adapted from harmonic_bot's plotter.

Engine guarantees: processes bars chronologically; at a zone-TF bar close only
that bar and earlier are visible; conf-TF bars inside a zone-TF bar become
visible one by one as they close.

## 9. Live scanner (built only after §7 bar is cleared)

- harmonic_bot shape: FastAPI + APScheduler, `/health` with `last_scan_at`,
  watchdog + failure-alert systemd units, Telegram with chart, SQLite
  `signals` table (`profile`, `variant`, `zone_id`, levels, status, outcome R).
- Reuses `zones.py`, `setup_machine.py`, `levels.py` unchanged — no duplicate
  rule code.
- Runs after each confirmation-TF bar close (+2 min).
- Alerts: "entry signal" (entry/SL/target/RR/grade/zone type/suggested risk %),
  "resolved" (outcome R); optional "zone armed" (default off). Dedup on
  (pair, zone_id).
- Live data: yfinance-first / Twelve Data fallback (`TWELVE_DATA_API_KEY`),
  harmonic_bot's live candle cache pattern.

## 10. Executor (gate only)

Not designed here. Prerequisites: §7 bar cleared and live alerts' results
consistent with backtest for the same variant. Will follow harmonic_bot's
executor conventions (read-only signal intake, cTrader demo first, pure risk
module, own order labels).

## 11. Testing

- TDD for all `core/` modules.
- Unit tests on hand-built bars: each pattern (positive and negative cases),
  zone detection for each of DBU/UBU/UBD/DBD, bounds/close line, touches,
  break/expiry, merge; trend classification; SL/target/RR; each exit model.
- State machine: one test per transition and per expiry reason.
- **No-look-ahead test**: setups detected at bar t are identical when the
  engine is fed data truncated at t vs the full series.
- Resampling: H4→D1 22:00-UTC boundary and Sunday merge.
- Regression: fixed cached slice end-to-end with a pinned result.
- Golden charts (visual, not a gate): GBPUSD D1 Feb–Apr 2018 (L13/L19 bull
  trap) and EURUSD D1 Mar 2019 (L27/L28 demand) rendered with detected zones
  for comparison with the course.

## 12. Known ambiguities in the source (decided here)

- P01's mid-zone limit entry without confirmation conflicts with the course's
  trap + confirmation rule → course rule is primary; P01 survives only as the
  `fixed_2r/3r` exits.
- Trap measured by wick vs close is inconsistent (L13/L19 vs L21) → `trap_mode`
  variant.
- L2 mislabels figure 3's supply zone as demand; P03's demand-level definition
  is self-contradictory → ignored; definitions in §6.2 apply.
- "Rule of 72" (P02, image only; L5) → informational; risk fixed at 1%.
