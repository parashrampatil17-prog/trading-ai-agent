from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from statistics import mean

# Illustrative paper assumptions only; not validated trading parameters.
PAPER_STOP_LOSS_PCT = 0.20
PAPER_TARGET1_PCT = 0.25
PAPER_TARGET2_PCT = 0.50
PAPER_ENTRY_BUFFER_PCT = 0.02
IST = ZoneInfo("Asia/Kolkata")


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


def _completed_candles(candles, interval_minutes, now):
    """Keep candles whose full interval has ended; interpret naive dates as IST."""
    completed = []
    for candle in candles or []:
        stamp = candle.get("date")
        if stamp is None:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=IST)
        else:
            stamp = stamp.astimezone(IST)
        if stamp + timedelta(minutes=interval_minutes) <= now:
            completed.append(candle)
    return completed


def _indicators(candles):
    closes = [float(x["close"]) for x in candles if x.get("close") is not None]
    if len(closes) < 30:
        return None
    e9, e21, rsi = _ema(closes, 9), _ema(closes, 21), _rsi(closes, 14)
    if e9 is None or e21 is None or rsi is None:
        return None
    last = closes[-1]
    bias = ("BULLISH" if last > e9 > e21 and rsi >= 55
            else "BEARISH" if last < e9 < e21 and rsi <= 45
            else "MIXED")
    stamp = candles[-1].get("date")
    return {
        "close": round(last, 2), "ema9": round(e9, 2),
        "ema21": round(e21, 2), "rsi14": round(rsi, 2),
        "bias": bias,
        "last_candle": stamp.isoformat() if hasattr(stamp, "isoformat") else str(stamp),
        "candles_count": len(closes)
    }


def _paper_option_plan(option_ltp):
    keys = ("entry_zone_low", "entry_zone_high", "stop_loss",
            "target1", "target2", "risk_reward_t1", "risk_reward_t2")
    if option_ltp is None:
        return {key: None for key in keys}
    try:
        premium = float(option_ltp)
    except (TypeError, ValueError):
        return {key: None for key in keys}
    if premium <= 0:
        return {key: None for key in keys}
    low = premium
    high = premium * (1 + PAPER_ENTRY_BUFFER_PCT)
    entry = (low + high) / 2
    stop = entry * (1 - PAPER_STOP_LOSS_PCT)
    t1 = entry * (1 + PAPER_TARGET1_PCT)
    t2 = entry * (1 + PAPER_TARGET2_PCT)
    risk = entry - stop
    return {
        "entry_zone_low": round(low, 2), "entry_zone_high": round(high, 2),
        "stop_loss": round(stop, 2), "target1": round(t1, 2),
        "target2": round(t2, 2),
        "risk_reward_t1": round((t1 - entry) / risk, 2),
        "risk_reward_t2": round((t2 - entry) / risk, 2)
    }


def _atm_quotes(chain):
    atm = chain.get("atm_strike")
    row = next((x for x in chain.get("option_chain", [])
                if atm is not None and x.get("strike") is not None
                and float(x.get("strike")) == float(atm)), {})
    ce, pe = row.get("CE", {}) or {}, row.get("PE", {}) or {}
    return {
        "atm_ce_symbol": ce.get("symbol"), "atm_ce_ltp": ce.get("ltp"),
        "atm_pe_symbol": pe.get("symbol"), "atm_pe_ltp": pe.get("ltp")
    }, row


def build_signal(kite, underlying, spot, chain, hilega_result=None):
    underlying = str(underlying).upper().strip()
    chain = chain or {}
    now = datetime.now(IST)
    token = _instrument_token(kite, underlying)

    raw5 = kite.historical_data(token, now - timedelta(days=5), now, "5minute")
    raw15 = kite.historical_data(token, now - timedelta(days=12), now, "15minute")
    candles5 = _completed_candles(raw5, 5, now)
    candles15 = _completed_candles(raw15, 15, now)
    ind5, ind15 = _indicators(candles5), _indicators(candles15)
    atm_fields, atm_row = _atm_quotes(chain)

    base = {
        "underlying": underlying, "spot": round(float(spot), 2),
        "expiry": chain.get("expiry"), "atm_strike": chain.get("atm_strike"),
        **atm_fields,
        "pcr": float(chain.get("pcr") or 0),
        "support": chain.get("support"), "resistance": chain.get("resistance"),
        "analysis_updated_at": now.isoformat(timespec="seconds"),
        "timeframe_rule": "5m entry trigger + 15m trend confirmation; completed candles only",
        "timeframe_5m": ind5, "timeframe_15m": ind15,
        "bias_5m": ind5.get("bias") if ind5 else None,
        "bias_15m": ind15.get("bias") if ind15 else None,
        "last_candle_5m": ind5.get("last_candle") if ind5 else None,
        "last_candle_15m": ind15.get("last_candle") if ind15 else None,
        "candles_count_5m": ind5.get("candles_count") if ind5 else len(candles5),
        "candles_count_15m": ind15.get("candles_count") if ind15 else len(candles15),
    }

    if not ind5 or not ind15:
        return {
            **base, "signal": "WAIT",
            "reason": "Insufficient completed 5m or 15m candles (minimum 30 each).",
            "signal_stage": "WAIT", "confidence_score": 0,
            "confidence_label": "Insufficient data", "option_type": None,
            "option_symbol": None, "option_ltp": None,
            **_paper_option_plan(None)
        }

    market_open = now.weekday() < 5 and (
        (now.hour > 9 or (now.hour == 9 and now.minute >= 15))
        and (now.hour < 15 or (now.hour == 15 and now.minute <= 30))
    )
    latest5 = candles5[-1].get("date")
    latest15 = candles15[-1].get("date")
    def as_ist(value):
        if value is None:
            return None
        return value.replace(tzinfo=IST) if value.tzinfo is None else value.astimezone(IST)

    if market_open:
        t5, t15 = as_ist(latest5), as_ist(latest15)
        # Avoid producing signals if the latest completed candles are old.
        if (t5 is None or t15 is None or t5.date() != now.date()
                or t15.date() != now.date()
                or now - (t5 + timedelta(minutes=5)) > timedelta(minutes=15)
                or now - (t15 + timedelta(minutes=15)) > timedelta(minutes=30)):
            return {
                **base, "signal": "WAIT", "signal_stage": "WAIT",
                "reason": "Candle data is stale. Waiting for fresh completed 5m and 15m candles.",
                "confidence_score": 0, "confidence_label": "Stale market data",
                "option_type": None, "option_symbol": None, "option_ltp": None,
                **_paper_option_plan(None)
            }

    # Trend confirmation: both timeframes must agree for directional stages.
    technical = ("BULLISH" if ind5["bias"] == ind15["bias"] == "BULLISH"
                 else "BEARISH" if ind5["bias"] == ind15["bias"] == "BEARISH"
                 else "MIXED")
    pcr = base["pcr"]
    oi_bias = ("BULLISH" if pcr >= 1.10
               else "BEARISH" if 0 < pcr <= 0.85 else "MIXED")
    hilega_ok = bool(hilega_result and hilega_result.get("signal") == "BUY WATCH")

    bull = (2 if technical == "BULLISH" else 0) + (1 if oi_bias == "BULLISH" else 0) + (1 if hilega_ok else 0)
    bear = (2 if technical == "BEARISH" else 0) + (1 if oi_bias == "BEARISH" else 0)

    # Staged watch logic: preliminary conditions are watch-only, never order instructions.
    pre_ce = ind5["close"] > ind5["ema9"] and ind5["rsi14"] >= 45 and pcr >= 0.90
    pre_pe = ind5["close"] < ind5["ema9"] and ind5["rsi14"] <= 55 and 0 < pcr <= 1.10
    early_ce = (ind5["close"] > ind5["ema9"] > ind5["ema21"]
                and ind5["rsi14"] >= 50 and pcr >= 0.95
                and ind15["bias"] == "BULLISH")
    early_pe = (ind5["close"] < ind5["ema9"] < ind5["ema21"]
                and ind5["rsi14"] <= 50 and 0 < pcr <= 1.05
                and ind15["bias"] == "BEARISH")

    if bull >= 3 and technical == "BULLISH":
        signal, stage, option_type = "CE WATCH", "STRONG", "CE"
    elif bear >= 3 and technical == "BEARISH":
        signal, stage, option_type = "PE WATCH", "STRONG", "PE"
    elif early_ce:
        signal, stage, option_type = "EARLY CE", "EARLY", "CE"
    elif early_pe:
        signal, stage, option_type = "EARLY PE", "EARLY", "PE"
    elif pre_ce and ind15["bias"] != "BEARISH":
        signal, stage, option_type = "PRE-SIGNAL CE", "PRE-SIGNAL", "CE"
    elif pre_pe and ind15["bias"] != "BULLISH":
        signal, stage, option_type = "PRE-SIGNAL PE", "PRE-SIGNAL", "PE"
    else:
        signal, stage, option_type = "NO TRADE", "NONE", None

    contract = (atm_row.get(option_type, {}) or {}) if option_type else {}
    option_ltp = contract.get("ltp")
    confidence = (round(bull / 4 * 100) if option_type == "CE" and stage == "STRONG"
                  else round(bear / 4 * 100) if option_type == "PE" and stage == "STRONG"
                  else 0)

    return {
        **base, "signal": signal, "signal_stage": stage,
        "reason": (
            "5m entry conditions and 15m trend confirmation agree."
            if stage == "STRONG" else
            "Early watch only; wait for stronger confirmation."
            if stage == "EARLY" else
            "Pre-signal only; conditions are preliminary and may reverse."
            if stage == "PRE-SIGNAL" else
            "5m and 15m conditions are not sufficiently aligned."
        ),
        "option_type": option_type, "option_symbol": contract.get("symbol"),
        "option_ltp": option_ltp, "technical_bias": technical, "oi_bias": oi_bias,
        "rsi14": ind5["rsi14"], "ema9": ind5["ema9"], "ema21": ind5["ema21"],
        "rsi14_15m": ind15["rsi14"], "ema9_15m": ind15["ema9"],
        "ema21_15m": ind15["ema21"],
        "hilega_confirmation": ("YES" if hilega_ok else
            "NOT APPLICABLE" if underlying in ("NIFTY", "SENSEX") else "NO"),
        "bull_score": bull, "bear_score": bear,
        "confidence_score": confidence,
        "confidence_label": "Rule-based confirmation score, not win probability",
        **_paper_option_plan(option_ltp),
        "plan_note": (
            "Illustrative paper plan only: entry band +2%, stop -20%, "
            "targets +25% and +50% from reference entry. These assumptions are not validated."
        ),
        "note": (
            "Paper mode only. No order is placed. STRONG/EARLY/PRE-SIGNAL are rule-based "
            "watch labels, not proven profitable signals. Verify live option LTP, spread, "
            "liquidity and broker data before any independent decision."
        )
    }
