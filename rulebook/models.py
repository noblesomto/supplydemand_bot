from typing import Literal

from pydantic import BaseModel, Field


class ZoneRules(BaseModel):
    max_base: int = Field(gt=0, default=6)
    base_body_atr: float = Field(gt=0, default=0.5)
    base_range_atr: float = Field(gt=0, default=1.5)
    leg_atr: float = Field(gt=0, default=1.0)
    leg_out_bars: int = Field(gt=0, default=3)
    departure_atr: float = Field(gt=0, default=2.0)
    max_touches: int = Field(gt=0, default=3)
    break_atr: float = Field(gt=0, default=1.0)
    break_bars: int = Field(gt=0, default=3)
    zone_max_age: int = Field(gt=0, default=250)
    atr_period: int = Field(gt=0, default=14)


class TrendRules(BaseModel):
    pivot_n: int = Field(gt=0, default=3)
    counter_trend_extreme: bool = False


class SetupRules(BaseModel):
    trap_mode: Literal["wick", "close"] = "wick"
    accepted_grades: list[Literal["A", "B"]] = Field(default_factory=lambda: ["A"])
    confirm_window: int = Field(gt=0, default=3)
    h4_window: int = Field(gt=0, default=2)
    ib_entry: Literal["close", "break"] = "close"
    engulfing_lookbacks: tuple[int, ...] = (1, 2, 3)


class RiskRules(BaseModel):
    risk_per_trade_pct: float = Field(gt=0, default=1.0)
    min_rr: float = Field(gt=0, default=2.0)
    sl_buffer_atr: float = Field(ge=0, default=0.25)
    max_concurrent_positions: int = Field(gt=0, default=3)
    max_daily_loss_pct: float = Field(gt=0, default=3.0)
    max_consecutive_losses: int = Field(gt=0, default=5)
    round_targets: bool = False
    round_step: dict[str, float] = Field(default_factory=dict)
    # Per-pair round-trip spread in price units, used to convert cost to
    # an R-fraction per trade (each trade's own SL distance), rather than
    # a flat R cost that would misprice tight-stop lower-timeframe trades.
    spreads: dict[str, float] = Field(default_factory=dict)
    default_spread: float = Field(ge=0, default=0.00015)


class ExitRules(BaseModel):
    model: Literal["fixed_2r", "fixed_3r", "next_level", "ladder", "ladder_pa_exit"] = "next_level"


class HTFFilterRules(BaseModel):
    enabled: bool = False


class Rulebook(BaseModel):
    profile: Literal["D1_H4", "H4_H1", "H1_M15"] = "D1_H4"
    zone: ZoneRules = Field(default_factory=ZoneRules)
    trend: TrendRules = Field(default_factory=TrendRules)
    setup: SetupRules = Field(default_factory=SetupRules)
    risk: RiskRules = Field(default_factory=RiskRules)
    exit: ExitRules = Field(default_factory=ExitRules)
    htf_filter: HTFFilterRules = Field(default_factory=HTFFilterRules)
