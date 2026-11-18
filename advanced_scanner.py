"""
Advanced Market Scanner V2 for Trading AI Agent.

Research / paper mode only. No order placement.
- Completed daily candles for trend, breakout and volume context.
- Intraday 5-minute candles for VWAP and current intraday price alignment.
- ADX with +DI/-DI directional context.
- Keeps the existing scan_market(kite, symbols) interface and dashboard keys.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from statistics import mean

IST = ZoneInfo("Asia/Kolkata")


def _as_ist(value):
    if value is None:
        return None
    return value.replace(tzinfo=IST) if value.tzinfo is None else value.astimezone(IST)


def _safe_float(value, default=None):
    try:
        result = float(value)
        if result != result or abs(result) == float("inf"):
            return default
        return result
    except (TypeError, ValueError):
        return default


def _ema(values, period):
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    value = mean(values[:period])
    for item in values[period:]:
        value = item * k + value * (1 - k)
    return value


def _rsi(values, period=14):
    if len(values) <= period:
        return None
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(x, 0.0) for x in changes]
    losses = [max(-x, 0.0) for x in changes]
    avg_gain, avg_loss = mean(gains[:period]), mean(losses[:period])
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    if avg_gain == 0:
        return 0.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def _adx(highs, lows, closes, period=14):
    """Return ADX, +DI and -DI using Wilder smoothing."""
    if len(closes) < period * 2 + 1:
        return None, None, None

    plus_dm, minus_dm, true_range = [], [], []
    for i in range(1, len(closes)):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
        true_range.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        ))

    def wilder(values):
        current = sum(values[:period])
        output = [current]
        for value in values[period:]:
            current = current - current / period + value
            output.append(current)
        return output

    atr = wilder(true_range)
    smoothed_plus = wilder(plus_dm)
    smoothed_minus = wilder(minus_dm)
    dx_values = []
    last_plus_di = last_minus_di = None

    for a, p, m in zip(atr, smoothed_plus, smoothed_minus):
        if a <= 0:
            plus_di = minus_di = 0.0
        else:
            plus_di = 100 * p / a
            minus_di = 100 * m / a
        last_plus_di, last_minus_di = plus_di, minus_di
        denominator = plus_di + minus_di
        dx_values.append(
            100 * abs(plus_di - minus_di) / denominator if denominator else 0.0
        )

    if len(dx_values) < period:
        return None, last_plus_di, last_minus_di

    adx_values = [mean(dx_values[:period])]
    current_adx = adx_values[0]
    for dx in dx_values[period:]:
        current_adx = (current_adx * (period - 1) + dx) / period
        adx_values.append(current_adx)

    return adx_values[-1], last_plus_di, last_minus_di


def _completed_candles(candles, interval_minutes, now):
    output = []
    for candle in candles or []:
        stamp = _as_ist(candle.get("date"))
        if stamp and stamp + timedelta(minutes=interval_minutes) <= now:
            item = dict(candle)
            item["_local_date"] = stamp
            output.append(item)
    return output


def _vwap(candles):
    total_pv = 0.0
    total_volume = 0.0
    for candle in candles:
        volume = _safe_float(candle.get("volume"), 0.0) or 0.0
        high = _safe_float(candle.get("high"))
        low = _safe_float(candle.get("low"))
        close = _safe_float(candle.get("close"))
        if high is None or low is None or close is None or volume <= 0:
            continue
        typical_price = (high + low + close) / 3
        total_pv += typical_price * volume
        total_volume += volume
    return total_pv / total_volume if total_volume else None


def _scan_one(kite, token, symbol, now):
    daily_raw = kite.historical_data(
        token, now - timedelta(days=220), now, "day"
    )
    daily = []
    for candle in daily_raw or []:
        stamp = _as_ist(candle.get("date"))
        if not stamp:
            continue
        # Daily candle is considered complete only after its trading date ends.
        if stamp.date() < now.date() or (
            stamp.date() == now.date()
            and (now.hour, now.minute) >= (15, 30)
        ):
            item = dict(candle)
            item["_local_date"] = stamp
            daily.append(item)

    if len(daily) < 55:
        return {
            "symbol": symbol,
            "status": "INSUFFICIENT_DATA",
            "reason": "Need at least 55 completed daily candles."
        }

    closes = [_safe_float(x.get("close")) for x in daily]
    highs = [_safe_float(x.get("high")) for x in daily]
    lows = [_safe_float(x.get("low")) for x in daily]
    volumes = [_safe_float(x.get("volume"), 0.0) or 0.0 for x in daily]
    if any(x is None for x in closes + highs + lows):
        return {
            "symbol": symbol,
            "status": "INVALID_DATA",
            "reason": "Daily candles contain missing or invalid OHLC values."
        }

    ema20, ema50 = _ema(closes, 20), _ema(closes, 50)
    strength = _rsi(closes, 14)
    adx, plus_di, minus_di = _adx(highs, lows, closes, 14)
    if None in (ema20, ema50, strength):
        return {
            "symbol": symbol,
            "status": "INSUFFICIENT_DATA",
            "reason": "Could not calculate daily indicators."
        }

    daily_close = closes[-1]
    prior_high = max(highs[-21:-1])
    prior_low = min(lows[-21:-1])
    average_volume = mean(volumes[-21:-1]) if len(volumes) >= 21 else 0.0
    volume_ratio = volumes[-1] / average_volume if average_volume > 0 else None

    session_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    intraday_raw = kite.historical_data(
        token, session_start, now, "5minute"
    )
    intraday_all = _completed_candles(intraday_raw, 5, now)
    today_intraday = [
        candle for candle in intraday_all
        if candle["_local_date"].date() == now.date()
    ]
    vwap = _vwap(today_intraday)
    intraday_price = (
        _safe_float(today_intraday[-1].get("close"))
        if today_intraday else None
    )

    trend = (
        "BULLISH" if daily_close > ema20 > ema50 else
        "BEARISH" if daily_close < ema20 < ema50 else
        "MIXED"
    )
    breakout = daily_close > prior_high
    breakdown = daily_close < prior_low
    volume_confirmed = volume_ratio is not None and volume_ratio >= 1.5
    adx_strong = adx is not None and adx >= 20
    adx_bullish = adx_strong and plus_di is not None and minus_di is not None and plus_di > minus_di
    adx_bearish = adx_strong and plus_di is not None and minus_di is not None and minus_di > plus_di

    vwap_bull = vwap is not None and intraday_price is not None and intraday_price > vwap
    vwap_bear = vwap is not None and intraday_price is not None and intraday_price < vwap

    bull_score = sum((
        trend == "BULLISH",
        strength >= 55,
        breakout,
        volume_confirmed,
        adx_bullish,
        vwap_bull,
    ))
    bear_score = sum((
        trend == "BEARISH",
        strength <= 45,
        breakdown,
        volume_confirmed,
        adx_bearish,
        vwap_bear,
    ))

    if bull_score >= 5:
        signal = "HIGH_CONFLUENCE_BULLISH_WATCH"
    elif bear_score >= 5:
        signal = "HIGH_CONFLUENCE_BEARISH_WATCH"
    elif bull_score >= 3 and bull_score > bear_score:
        signal = "BULLISH_WATCH"
    elif bear_score >= 3 and bear_score > bull_score:
        signal = "BEARISH_WATCH"
    else:
        signal = "NEUTRAL"

    return {
        "symbol": symbol,
        "status": "OK",
        "price": round(daily_close, 2),
        "intraday_price_5m": round(intraday_price, 2) if intraday_price is not None else None,
        "ema20": round(ema20, 2),
        "ema50": round(ema50, 2),
        "rsi14": round(strength, 2),
        "adx14": round(adx, 2) if adx is not None else None,
        "plus_di14": round(plus_di, 2) if plus_di is not None else None,
        "minus_di14": round(minus_di, 2) if minus_di is not None else None,
        "volume_ratio": round(volume_ratio, 2) if volume_ratio is not None else None,
        "vwap_5m": round(vwap, 2) if vwap is not None else None,
        "trend": trend,
        "breakout": bool(breakout),
        "breakdown": bool(breakdown),
        "volume_confirmed": bool(volume_confirmed),
        "adx_bullish": bool(adx_bullish),
        "adx_bearish": bool(adx_bearish),
        "vwap_bullish": bool(vwap_bull),
        "vwap_bearish": bool(vwap_bear),
        "bull_score": int(bull_score),
        "bear_score": int(bear_score),
        "signal": signal,
        "completed_daily_candle": daily[-1]["_local_date"].isoformat(),
        "updated_at": now.isoformat(timespec="seconds"),
        "note": "Research watch only; rule scores are not win probabilities or trade recommendations."
    }


def scan_market(kite, symbols, max_symbols=None):
    """Scan NSE cash symbols; returns updated_at, results and errors."""
    now = datetime.now(IST)
    requested = list(dict.fromkeys(
        str(symbol).upper().strip()
        for symbol in (symbols or [])
        if str(symbol).strip()
    ))
    if max_symbols is not None:
        requested = requested[:max_symbols]

    instruments = kite.instruments("NSE")
    token_by_symbol = {
        item.get("tradingsymbol"): item.get("instrument_token")
        for item in instruments
        if item.get("segment") == "NSE"
        and item.get("instrument_type") in ("EQ", "")
        and item.get("tradingsymbol")
    }

    results, errors = [], []
    for symbol in requested:
        token = token_by_symbol.get(symbol)
        if not token:
            errors.append({
                "symbol": symbol,
                "error": "NSE cash instrument not found"
            })
            continue
        try:
            results.append(_scan_one(kite, token, symbol, now))
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)})

    results.sort(
        key=lambda item: (
            item.get("bull_score", 0) + item.get("bear_score", 0),
            item.get("volume_ratio") or 0
        ),
        reverse=True
    )
    return {
        "updated_at": now.isoformat(timespec="seconds"),
        "symbols_requested": len(requested),
        "results": results,
        "errors": errors,
        "note": "Paper/research scanner only. Historical API availability and rate limits apply."
    }
