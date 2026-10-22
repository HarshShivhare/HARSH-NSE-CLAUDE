"""Round-trip cost model for NSE equity intraday, expressed as a return fraction."""
from __future__ import annotations

from ..config import load_settings


def round_trip_cost(settings=None, notional: float | None = None) -> float:
    c = (settings or load_settings()).costs
    n = float(notional or c["notional_inr"])
    brokerage = 2 * min(n * c["brokerage_rate"], c["brokerage_cap_per_order"])
    stt = n * c["stt_sell_rate"]                    # one sell leg (long exit or short entry)
    exchange = 2 * n * c["exchange_txn_rate"]
    sebi = 2 * n * c["sebi_rate"]
    stamp = n * c["stamp_buy_rate"]                 # one buy leg
    gst = (brokerage + exchange + sebi) * c["gst_rate"]
    charges = brokerage + stt + exchange + sebi + stamp + gst
    slippage = 2 * c["slippage_bps_per_side"] / 1e4
    return charges / n + slippage
