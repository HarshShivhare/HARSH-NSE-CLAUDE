"""Round-trip cost models for NSE equities, expressed as a return fraction.

  intraday  settings `costs`           MIS, one round trip within the session
  delivery  settings `costs_delivery`  cash (CNC) long held overnight: STT both legs + DP charge
  futures   settings `costs_futures`   stock-futures position (overnight shorts); roll not modelled
"""
from __future__ import annotations

from ..config import load_settings

COST_SECTIONS = {"intraday": "costs", "delivery": "costs_delivery", "futures": "costs_futures"}


def _round_trip(c: dict, notional: float | None = None) -> float:
    n = float(notional or c["notional_inr"])
    brokerage = 2 * min(n * c["brokerage_rate"], c["brokerage_cap_per_order"])
    stt = n * (c["stt_sell_rate"] + c.get("stt_buy_rate", 0.0))   # sell leg (+ buy leg for delivery)
    exchange = 2 * n * c["exchange_txn_rate"]
    sebi = 2 * n * c["sebi_rate"]
    stamp = n * c["stamp_buy_rate"]                 # one buy leg
    gst = (brokerage + exchange + sebi) * c["gst_rate"]
    charges = brokerage + stt + exchange + sebi + stamp + gst
    charges += c.get("dp_charge_inr", 0.0) * (1 + c["gst_rate"])   # depository charge on the delivery sell
    slippage = 2 * c["slippage_bps_per_side"] / 1e4
    return charges / n + slippage


def round_trip_cost(settings=None, notional: float | None = None) -> float:
    return _round_trip((settings or load_settings()).costs, notional)


def model_cost(model: str = "intraday", settings=None) -> float:
    if model not in COST_SECTIONS:
        raise ValueError(f"unknown cost model {model!r}; expected one of {sorted(COST_SECTIONS)}")
    s = settings or load_settings()
    if model == "intraday":
        return round_trip_cost(s)
    return _round_trip(s.raw[COST_SECTIONS[model]])
