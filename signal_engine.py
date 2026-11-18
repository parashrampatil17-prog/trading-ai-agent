from datetime import datetime, timedelta
from statistics import mean

# Illustrative paper-test assumptions; not validated trading parameters.
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


def _indicators(candles):
    closes = [float(x["close"]) for x in candles if x.get("close") is not None]
    if len(closes) < 30:
        return None
    e9, e21, rsi = _ema(closes, 9), _ema(closes, 21), _rsi(closes, 14)
    if e9 is None or e21 is None or rsi is None:
        return None
    last = closes[-1]
    bias = (
        "BULLISH" if last > e9 > e21 and rsi >= 55
        else "BEARISH" if last < e9 < e21 and rsi <= 45
        else "MIXED"
    )
    return {"close": last, "ema9": e9, "ema21": e21, "rsi14": rsi, "bias": bias}


def _paper_option_plan(option_ltp):
    keys = ("entry_zone_low", "entry_zone_high", "stop_loss",
            "target1", "target2", "risk_reward_t1", "risk_reward_t2")
    if option_ltp is None or float(option_ltp) <= 0:
        return {key: None for key in keys}
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

    candles_5m = kite.historical_data(
        token, now - timedelta(days=5), now, "5minute"
    )
    candles_15m = kite.historical_data(
        token, now - timedelta(days=12), now, "15minute"
    )
    ind5 = _indicators(candles_5m)
    ind15 = _indicators(candles_15m)

    if not ind5 or not ind15:
        return {
            "underlying": underlying, "signal": "WAIT",
            "reason": "Not enough completed 5-minute or 15-minute candles.",
            "confidence_score": 0,
            "confidence_label": "Insufficient data",
            "timeframe_5m": ind5, "timeframe_15m": ind15
        }

    # 5-minute chart is the entry trigger; 15-minute chart confirms trend.
    technical = (
        "BULLISH" if ind5["bias"] == "BULLISH" and ind15["bias"] == "BULLISH"
        else "BEARISH" if ind5["bias"] == "BEARISH" and ind15["bias"] == "BEARISH"
        else "MIXED"
    )
    pcr = float(chain.get("pcr") or 0)
    oi_bias = (
        "BULLISH" if pcr >= 1.10
        else "BEARISH" if 0 < pcr <= 0.85
        else "MIXED"
    )
    hilega_ok = bool(hilega_result and hilega_result.get("signal") == "BUY WATCH")
    bull = (2 if technical == "BULLISH" else 0) + (1 if oi_bias == "BULLISH" else 0) + (1 if hilega_ok else 0)
    bear = (2 if technical == "BEARISH" else 0) + (1 if oi_bias == "BEARISH" else 0)

    signal = (
        "CE WATCH" if bull >= 3 and technical == "BULLISH"
        else "PE WATCH" if bear >= 3 and technical == "BEARISH"
        else "NO TRADE"
    )
    confidence = (
        round(bull / 4 * 100) if signal == "CE WATCH"
        else round(bear / 4 * 100) if signal == "PE WATCH"
        else 0
    )

    atm = chain.get("atm_strike")
    atm_row = next((x for x in chain.get("option_chain", [])
                    if atm is not None and float(x.get("strike", -1)) == float(atm)), {})
    option_type = "CE" if signal == "CE WATCH" else "PE"
    contract = atm_row.get(option_type, {}) if signal in ("CE WATCH", "PE WATCH") else {}
    option_ltp = contract.get("ltp")
    plan = _paper_option_plan(option_ltp) if contract else _paper_option_plan(None)

    return {
        "underlying": underlying, "signal": signal,
        "spot": round(float(spot), 2), "expiry": chain.get("expiry"),
        "atm_strike": atm, "option_symbol": contract.get("symbol"),
        "option_ltp": option_ltp, "option_type": option_type if contract else None,
        "pcr": pcr, "technical_bias": technical, "oi_bias": oi_bias,
        "rsi14": round(ind5["rsi14"], 2),
        "ema9": round(ind5["ema9"], 2),
        "ema21": round(ind5["ema21"], 2),
        "rsi14_15m": round(ind15["rsi14"], 2),
        "ema9_15m": round(ind15["ema9"], 2),
        "ema21_15m": round(ind15["ema21"], 2),
        "bias_5m": ind5["bias"], "bias_15m": ind15["bias"],
        "timeframe_rule": "5-minute entry + 15-minute trend confirmation",
        "support": chain.get("support"), "resistance": chain.get("resistance"),
        "hilega_confirmation": (
            "YES" if hilega_ok else
            "NOT APPLICABLE" if underlying in ("NIFTY", "SENSEX") else "NO"
        ),
        "bull_score": bull, "bear_score": bear,
        "confidence_score": confidence,
        "confidence_label": "Rule-based confirmation score, not win probability",
        **plan,
        "plan_note": (
            "Illustrative paper plan based on the option LTP snapshot; "
            "entry band +2%, stop -20%, targets +25% and +50%. "
            "These assumptions are not validated."
        ),
        "note": (
            "Paper mode only. 5m is the entry trigger and 15m confirms trend. "
            "Verify candle freshness, live option price, spread and liquidity. "
            "No order is placed."
        )
    }
