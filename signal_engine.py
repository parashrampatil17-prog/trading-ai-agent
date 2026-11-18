from datetime import datetime, timedelta
from statistics import mean

# Paper-mode assumptions only; not validated trading parameters.
PAPER_STOP_LOSS_PCT = 0.20
PAPER_TARGET1_PCT = 0.25
PAPER_TARGET2_PCT = 0.50
PAPER_ENTRY_BUFFER_PCT = 0.02


def _ema(values, period):
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    value = mean(values[:period])
    for price in values[period:]:
        value = price * k + value * (1 - k)
    return value


def _rsi(values, period=14):
    if len(values) <= period:
        return None
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(x, 0) for x in changes]
    losses = [max(-x, 0) for x in changes]
    avg_gain, avg_loss = mean(gains[:period]), mean(losses[:period])
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def _instrument_token(kite, underlying):
    if underlying == "NIFTY":
        rows = kite.instruments("NSE")
        item = next((x for x in rows if x.get("tradingsymbol") == "NIFTY 50"
                     and x.get("segment") == "INDICES"), None)
    elif underlying == "SENSEX":
        rows = kite.instruments("BSE")
        item = next((x for x in rows if x.get("tradingsymbol") == "SENSEX"
                     and x.get("segment") == "INDICES"), None)
    else:
        rows = kite.instruments("NSE")
        item = next((x for x in rows if x.get("tradingsymbol") == underlying
                     and x.get("segment") == "NSE"), None)
    if not item:
        raise ValueError("Underlying instrument token not found")
    return item["instrument_token"]


def _paper_option_plan(option_ltp):
    if option_ltp is None or float(option_ltp) <= 0:
        return {key: None for key in (
            "entry_zone_low", "entry_zone_high", "stop_loss",
            "target1", "target2", "risk_reward_t1", "risk_reward_t2"
        )}
    premium = float(option_ltp)
    entry_low = premium
    entry_high = premium * (1 + PAPER_ENTRY_BUFFER_PCT)
    reference_entry = (entry_low + entry_high) / 2
    stop = reference_entry * (1 - PAPER_STOP_LOSS_PCT)
    target1 = reference_entry * (1 + PAPER_TARGET1_PCT)
    target2 = reference_entry * (1 + PAPER_TARGET2_PCT)
    risk = reference_entry - stop
    return {
        "entry_zone_low": round(entry_low, 2),
        "entry_zone_high": round(entry_high, 2),
        "stop_loss": round(stop, 2),
        "target1": round(target1, 2),
        "target2": round(target2, 2),
        "risk_reward_t1": round((target1 - reference_entry) / risk, 2),
        "risk_reward_t2": round((target2 - reference_entry) / risk, 2)
    }


def build_signal(kite, underlying, spot, chain, hilega_result=None):
    underlying = str(underlying).upper().strip()
    token = _instrument_token(kite, underlying)
    now = datetime.now()
    candles = kite.historical_data(token, now - timedelta(days=12), now, "15minute")
    closes = [float(x["close"]) for x in candles if x.get("close") is not None]
    if len(closes) < 30:
        return {"signal": "WAIT", "reason": "Not enough 15-minute candles for confirmation.",
                "underlying": underlying, "confidence_score": 0,
                "confidence_label": "Insufficient data"}

    ema9, ema21, rsi14 = _ema(closes, 9), _ema(closes, 21), _rsi(closes, 14)
    if ema9 is None or ema21 is None or rsi14 is None:
        return {"signal": "WAIT", "reason": "Indicator data unavailable.",
                "underlying": underlying, "confidence_score": 0,
                "confidence_label": "Insufficient data"}

    pcr = float(chain.get("pcr") or 0)
    strong_bull_technical = spot > ema9 > ema21 and rsi14 >= 55
    strong_bear_technical = spot < ema9 < ema21 and rsi14 <= 45
    technical = ("BULLISH" if strong_bull_technical else
                 "BEARISH" if strong_bear_technical else "MIXED")
    oi_bias = ("BULLISH" if pcr >= 1.10 else
               "BEARISH" if 0 < pcr <= 0.85 else "MIXED")
    hilega_ok = bool(hilega_result and hilega_result.get("signal") == "BUY WATCH")

    bull = (2 if strong_bull_technical else 0) + (1 if oi_bias == "BULLISH" else 0) + (1 if hilega_ok else 0)
    bear = (2 if strong_bear_technical else 0) + (1 if oi_bias == "BEARISH" else 0)

    if bull >= 3 and strong_bull_technical:
        signal, option_type, confidence = "STRONG CE WATCH", "CE", round(bull / 4 * 100)
    elif bear >= 3 and strong_bear_technical:
        signal, option_type, confidence = "STRONG PE WATCH", "PE", round(bear / 4 * 100)
    else:
        early_bull = spot > ema9 > ema21 and rsi14 >= 50 and pcr >= 0.95
        early_bear = spot < ema9 < ema21 and rsi14 <= 50 and 0 < pcr <= 1.05
        if early_bull:
            signal, option_type, confidence = "EARLY CE WATCH", "CE", 50
        elif early_bear:
            signal, option_type, confidence = "EARLY PE WATCH", "PE", 50
        else:
            signal, option_type, confidence = "NO TRADE", None, 0

    atm = chain.get("atm_strike")
    atm_row = next((x for x in chain.get("option_chain", [])
                    if atm is not None and float(x.get("strike", -1)) == float(atm)), {})
    # Always return both ATM option quotes, even when there is no trade signal.
    atm_ce = atm_row.get("CE", {}) or {}
    atm_pe = atm_row.get("PE", {}) or {}
    contract = atm_ce if option_type == "CE" else atm_pe if option_type == "PE" else {}
    option_ltp = contract.get("ltp")
    plan = _paper_option_plan(option_ltp) if contract else _paper_option_plan(None)

    return {
        "underlying": underlying, "signal": signal,
        "spot": round(float(spot), 2), "expiry": chain.get("expiry"),
        "atm_strike": atm,
        "atm_ce_symbol": atm_ce.get("symbol"),
        "atm_ce_ltp": atm_ce.get("ltp"),
        "atm_pe_symbol": atm_pe.get("symbol"),
        "atm_pe_ltp": atm_pe.get("ltp"),
        "option_symbol": contract.get("symbol"),
        "option_ltp": option_ltp, "option_type": option_type,
        "pcr": pcr, "technical_bias": technical, "oi_bias": oi_bias,
        "rsi14": round(rsi14, 2), "ema9": round(ema9, 2),
        "ema21": round(ema21, 2), "support": chain.get("support"),
        "resistance": chain.get("resistance"),
        "hilega_confirmation": ("YES" if hilega_ok else
            "NOT APPLICABLE" if underlying in ("NIFTY", "SENSEX") else "NO"),
        "bull_score": bull, "bear_score": bear,
        "confidence_score": confidence,
        "confidence_label": "Rule-based confirmation score, not win probability",
        **plan,
        "plan_note": ("Illustrative paper plan from current option LTP snapshot: "
            "entry band up to 2% above snapshot, stop 20% below reference entry, "
            "targets 25% and 50% above reference entry. Not validated."),
        "note": ("Research/paper mode only. EARLY WATCH is preliminary, not a confirmed entry. "
            "Verify live price, spread, liquidity and chart. No order is placed.")
    }
