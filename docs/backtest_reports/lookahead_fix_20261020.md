# Look-ahead fix (v0.5.0), 2026-10-20

Triggered by live GBP/JPY + AUD/JPY signals with 07:00/07:15 UTC entry bars
that the scanner could only emit at 09:00 UTC.

## Leaks found in core/backtest_engine.py (v0.4.0)
1. Entry search began at the OPEN of the zone-TF bar that confirms the setup,
   so entries sat up to one zone bar before the confirmation was knowable
   (~75% of calib trades).
2. Zones were watchable from the leg-out bar, though the fast-departure check
   used up to `leg_out_bars` later bars (~17% of sampled trades confirmed
   before the departure was known; mean +0.31R).
3. A trade whose entry bar was the last closed bar was dropped (live lag).

Fix: entry window starts at the confirming bar's close; zones watched from the
bar after `Zone.departed_index`; last-bar entries kept as open. Guarded by
`tests/test_live_equals_backtest_timing.py` (fails on v0.4.0).

## Calib (2022-01-04..2025-05-04), live systems, before -> after
| System | v0.4.0 expR | v0.5.0 expR | CI95 | trades | max DD |
|---|---|---|---|---|---|
| H4-AB-2R | +0.335 | -0.120 | -0.30..+0.06 | 509 | 56.4% |
| H4-AB-3R | +0.344 | -0.097 | -0.30..+0.10 | 498 | 52.5% |
| H4-A-3R | +0.380 | -0.054 | -0.34..+0.23 | 317 | 37.1% |
| H4-A-2R | +0.289 | -0.087 | -0.34..+0.17 | 320 | 38.3% |
| PRIME-H1-CT | +0.375 | -0.103 | -0.27..+0.07 | 1287 | 94.8% |
| PRIME-H1-AB-2R | +0.249 | -0.188 | -0.26..-0.12 | 1701 | 97.0% |
| H1-AB-NL | +0.267 | -0.110 | -0.28..+0.05 | 1556 | 92.0% |
| H1-AB-3R | +0.197 | -0.169 | -0.26..-0.08 | 1617 | 95.4% |
| PRIME-H1-A-2R | +0.154 | -0.167 | -0.26..-0.08 | 1062 | 86.1% |
| H1-BASE | +0.212 | -0.105 | -0.28..+0.07 | 1026 | 88.2% |

No variant in any profile (D1_H4, H4_H1, H1_M15) has CI95 lower bound > 0.
Best point estimates: H4_H1 sw_ib_break +0.15R (CI -0.19..), D1_H4 baseline
+0.16R (66 trades, CI -0.38..). Grids: calib_grid_*_20261020_13*.
