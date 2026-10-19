"""Per-system forward results from the live paper DB (spec section 8).

    python scripts/live_report.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings
from live import stats
from live.store import get_meta, make_session_factory


def _fmt(s: dict) -> str:
    if not s["n"]:
        return "n=0"
    ci = f" ci95=[{s['ci95'][0]:+.2f},{s['ci95'][1]:+.2f}]" if s["ci95"] else ""
    return f"n={s['n']} win={s['win_pct']:.0f}% expR={s['exp_r']:+.3f}{ci}"


def main():
    sf = make_session_factory(settings.DB_URL)
    with sf() as s:
        print(f"go-live {get_meta(s, 'go_live_at')}  last scan {get_meta(s, 'last_scan_at')}\n")
        data = stats.build(s)
    for o in data["overview"]:
        print(f"{o['label']:15} calib={o['calib_exp']:+.3f}  open={o['open']}  skipped={o['skipped']}")
        print(f"   raw     {_fmt(o['raw'])}")
        print(f"   account {_fmt(o['account'])}  balance=${o['balance']:,.0f} maxDD={o['max_dd_pct']:.1f}%")


if __name__ == "__main__":
    main()
