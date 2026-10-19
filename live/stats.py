"""Per-system forward stats from the signals table, shared by the dashboard
and scripts/live_report.py. Closed signals only for performance; raw = every
signal, account = signals the system's own $1000 paper account took."""
import math
from collections import defaultdict

from live.store import Signal
from live.systems import LIVE_SYSTEMS

START_BALANCE = 1000.0
RISK = 0.01


def r_stats(rs: list[float]) -> dict:
    n = len(rs)
    if n == 0:
        return {"n": 0, "win_pct": None, "exp_r": None, "ci95": None, "total_r": 0.0}
    m = sum(rs) / n
    ci = None
    if n > 1:
        half = 1.96 * math.sqrt(sum((x - m) ** 2 for x in rs) / (n - 1)) / math.sqrt(n)
        ci = [round(m - half, 3), round(m + half, 3)]
    return {"n": n, "win_pct": round(100 * sum(r > 0 for r in rs) / n, 1), "exp_r": round(m, 3),
            "ci95": ci, "total_r": round(sum(rs), 2)}


def equity(rows: list) -> tuple[list[dict], float, float]:
    """Compounded paper balance over closed, taken signals by exit time."""
    bal = peak = START_BALANCE
    dd = 0.0
    curve = []
    for r in sorted(rows, key=lambda r: r.exit_timestamp):
        bal += bal * RISK * r.realized_r
        peak = max(peak, bal)
        dd = max(dd, 100 * (peak - bal) / peak)
        curve.append({"t": r.exit_timestamp, "balance": round(bal, 2)})
    return curve, round(bal, 2), round(dd, 2)


def _row(r: Signal) -> dict:
    return {"system": r.system, "pair": r.pair, "direction": r.direction, "zone_type": r.zone_type,
            "grade": r.grade, "entry_timestamp": r.entry_timestamp, "entry": r.entry, "sl": r.sl, "tp": r.tp,
            "rr": r.rr, "taken": r.taken, "skip_reason": r.skip_reason, "status": r.status,
            "exit_kind": r.exit_kind, "exit_timestamp": r.exit_timestamp, "realized_r": r.realized_r}


def system_summary(label: str, rows: list) -> dict:
    sys_ = next((s for s in LIVE_SYSTEMS if s.label == label), None)
    closed = [r for r in rows if r.status == "CLOSED"]
    taken = [r for r in closed if r.taken]
    curve, bal, dd = equity(taken)
    return {"label": label, "prime": bool(sys_ and sys_.prime), "profile": sys_.profile if sys_ else None,
            "variant": (sys_.variant or "baseline") if sys_ else None,
            "calib_exp": sys_.calib_exp if sys_ else None,
            "raw": r_stats([r.realized_r for r in closed]), "account": r_stats([r.realized_r for r in taken]),
            "balance": bal, "max_dd_pct": dd, "equity": curve,
            "open": sum(r.status == "OPEN" for r in rows), "skipped": sum(not r.taken for r in rows)}


def build(session, system: str = "ALL", recent: int = 100) -> dict:
    all_rows = session.query(Signal).all()
    by = defaultdict(list)
    for r in all_rows:
        by[r.system].append(r)
    summaries = [system_summary(s.label, by[s.label]) for s in LIVE_SYSTEMS]
    overview = [{k: v for k, v in x.items() if k != "equity"} for x in summaries]
    rows = all_rows if system == "ALL" else by[system]
    detail = system_summary(system, rows)
    pairs = defaultdict(list)
    for r in rows:
        if r.status == "CLOSED":
            pairs[r.pair].append(r.realized_r)
    return {
        "system": system,
        "overview": overview,
        "equity_by_system": [{"label": x["label"], "equity": x["equity"]} for x in summaries] if system == "ALL" else [],
        "detail": detail,
        "pairs": sorted(({"pair": p, **r_stats(v)} for p, v in pairs.items()), key=lambda d: -d["total_r"]),
        "open_signals": [_row(r) for r in sorted(rows, key=lambda r: r.entry_timestamp, reverse=True)
                         if r.status == "OPEN"],
        "recent": [_row(r) for r in sorted(rows, key=lambda r: r.entry_timestamp, reverse=True)[:recent]],
    }
