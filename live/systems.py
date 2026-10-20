"""The live paper systems -- the ONLY place they are defined (spec
2026-10-19-live-paper-scanner-design.md section 2). A label is permanent
once it has signals: changed rules get a new label.

PRIME = positive in every calendar year 2022-2026 in the 2026-10-19
$1000 account projection; flagged so they get more attention."""
from dataclasses import dataclass


@dataclass(frozen=True)
class LiveSystem:
    label: str
    profile: str
    variant: str | None  # None = rulebook baseline
    prime: bool = False
    calib_exp: float = 0.0  # calib expectancy R net of spread (2026-10-20 v0.5.0 grids, look-ahead fixed), for decay comparison


LIVE_SYSTEMS: list[LiveSystem] = [
    LiveSystem("H4-AB-2R", "H4_H1", "grid_close_AB_fixed2r", calib_exp=-0.12),
    LiveSystem("H4-AB-3R", "H4_H1", "grid_close_AB_fixed3r", calib_exp=-0.097),
    LiveSystem("H4-A-3R", "H4_H1", "grid_wick_A_fixed3r", calib_exp=-0.054),
    LiveSystem("H4-A-2R", "H4_H1", "grid_wick_A_fixed2r", calib_exp=-0.087),
    LiveSystem("PRIME-H1-CT", "H1_M15", "sw_counter_trend", prime=True, calib_exp=-0.103),
    LiveSystem("PRIME-H1-AB-2R", "H1_M15", "grid_close_AB_fixed2r", prime=True, calib_exp=-0.188),
    LiveSystem("H1-AB-NL", "H1_M15", "grid_close_AB_nextlevel", calib_exp=-0.11),
    LiveSystem("H1-AB-3R", "H1_M15", "grid_close_AB_fixed3r", calib_exp=-0.169),
    LiveSystem("PRIME-H1-A-2R", "H1_M15", "grid_wick_A_fixed2r", prime=True, calib_exp=-0.167),
    LiveSystem("H1-BASE", "H1_M15", None, calib_exp=-0.105),
]
