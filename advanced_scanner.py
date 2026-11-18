"""
Advanced Market Scanner V1 for the Trading AI Agent.

Research / paper mode only:
- No order placement.
- Uses Zerodha Kite historical candles.
- Scans daily trend, momentum, breakout, volume expansion,
  and intraday VWAP alignment.
- Returns WATCH labels, not buy/sell recommendations.

Integration:
    from advanced_scanner import scan_market
    results = scan_market(kite, symbols)

Pass a list of NSE cash-market trading symbols, e.g.:
    ["RELIANCE", "HDFCBANK", "INFY"]
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from statistics import mean

IST = ZoneInfo("Asia/Kolkata")


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
    gains = [max(x, 0) for x in changes]
    losses = [max(-x, 0) for x in changes]
    avg_gain = mean(gains[:period])
    avg_loss = mean(losses[:period])
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
    if len(closes) < period * 2 + 1:
        return None

    plus_dm, minus_dm, tr = [], [], []
    for i in range(1, len(closes)):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        tr.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        ))

    def smooth(values):
        first = sum(values[:period])
        out = [first]
        current = first
        for value in values[period:]:
            current = current - current / period + value
            out.append(current)
        return out

    atr = smooth(tr)
    psm = smooth(plus_dm)
    msm = smooth(minus_dm)
    dx = []
    for a, p, m in zip(atr, psm, msm):
        if a <= 0:
            dx.append(0.0)
            continue
        pdi = 100 * p / a
        mdi = 100 * m / a
        denom = pdi + mdi
        dx.append(100 * abs(pdi - mdi) / denom if denom else 0.0)

    if len(dx) < period:
        return None
    adx_values = [mean(dx[:period])]
    current = adx_values[0]
    for value in dx[period:]:
        current = (current * (period - 1) + value) / period
        adx_values.append(current)
    return adx_values[-1]


def _vwap(candles):
    total_pv = 0.0
    total_volume = 0.0
    today = datetime.now(IST).date()
    for candle in candles:
        stamp = candle.get("date")
        if stamp is None:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=IST)
        else:
            stamp = stamp.astimezone(IST)
        if stamp.date() != today:
            continue
        volume = float(candle.get("volume") or 0)
        typical = (
            float(candle["high"]) + float(candle["low"]) +
            float(candle["close"])
        ) / 3
        total_pv += typical * volume
        total_volume += volume
    return total_pv / total_volume if total_volume > 0 else None


def _safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _scan_one(kite, token, symbol, now):
    daily_raw = kite.historical_data(
        token, now - timedelta(days=180), now, "day"
    )
    # Exclude the current daily candle while the market is open.
    daily = []
    for candle in daily_raw or []:
        stamp = candle.get("date")
        if stamp is None:
            continue
        local_stamp = (
            stamp.replace(tzinfo=IST) if stamp.tzinfo is None
            else stamp.astimezone(IST)
        )
        if local_stamp.date() < now.date() or now.hour >= 15 and now.minute >= 30:
            daily.append(candle)

    if len(daily) < 55:
        return {
            "symbol": symbol, "status": "INSUFFICIENT_DATA",
            "reason": "Need at least 55 completed daily candles."
        }

    closes = [float(x["close"]) for x in daily]
    highs = [float(x["high"]) for x in daily]
    lows = [float(x["low"]) for x in daily]
    volumes = [float(x.get("volume") or 0) for x in daily]

    e20 = _ema(closes, 20)
    e50 = _ema(closes, 50)
    strength = _rsi(closes, 14)
    adx = _adx(highs, lows, closes, 14)
    price = closes[-1]
    prior_high = max(highs[-21:-1])
    prior_low = min(lows[-21:-1])
    avg_volume = mean(volumes[-21:-1]) if len(volumes) >= 21 else 0
    volume_ratio = volumes[-1] / avg_volume if avg_volume > 0 else None

    intraday_raw = kite.historical_data(
        token, now.replace(hour=9, minute=15, second=0, microsecond=0),
        now, "5minute"
    )
    intraday = []
    for candle in intraday_raw or []:
        stamp = candle.get("date")
        if stamp is None:
            continue
        local_stamp = (
            stamp.replace(tzinfo=IST) if stamp.tzinfo is None
            else stamp.astimezone(IST)
        )
        if local_stamp + timedelta(minutes=5) <= now:
            intraday.append(candle)
    vwap = _vwap(intraday)

    trend = (
        "BULLISH" if price > e20 > e50 else
        "BEARISH" if price < e20 < e50 else "MIXED"
    )
    breakout = price > prior_high
    breakdown = price < prior_low
    volume_confirmed = volume_ratio is not None and volume_ratio >= 1.5
    trend_strength = adx is not None and adx >= 20
    vwap_bull = vwap is not None and price > vwap
    vwap_bear = vwap is not None and price < vwap

    bull_score = sum([
        trend == "BULLISH",
        strength >= 55,
        breakout,
        volume_confirmed,
        trend_strength,
        vwap_bull
    ])
    bear_score = sum([
        trend == "BEARISH",
        strength <= 45,
        breakdown,
        volume_confirmed,
        trend_strength,
        vwap_bear
    ])

    if bull_score >= 5:
        signal = "HIGH_CONFLUENCE_BULLISH_WATCH"
    elif bear_score >= 5:
        signal = "HIGH_CONFLUENCE_BEARISH_WATCH"
    elif bull_score >= 3:
        signal = "BULLISH_WATCH"
    elif bear_score >= 3:
        signal = "BEARISH_WATCH"
    else:
        signal = "NEUTRAL"

    return {
        "symbol": symbol,
        "status": "OK",
        "price": round(price, 2),
        "ema20": round(e20, 2),
        "ema50": round(e50, 2),
        "rsi14": round(strength, 2),
        "adx14": round(adx, 2) if adx is not None else None,
        "volume_ratio": round(volume_ratio, 2) if volume_ratio is not None else None,
        "vwap_5m": round(vwap, 2) if vwap is not None else None,
        "trend": trend,
        "breakout": bool(breakout),
        "breakdown": bool(breakdown),
        "volume_confirmed": bool(volume_confirmed),
        "vwap_bullish": bool(vwap_bull),
        "vwap_bearish": bool(vwap_bear),
        "bull_score": int(bull_score),
        "bear_score": int(bear_score),
        "signal": signal,
        "completed_daily_candle": daily[-1]["date"].isoformat(),
        "updated_at": now.isoformat(timespec="seconds"),
        "note": "Research watch only; not a trade recommendation."
    }


def scan_market(kite, symbols, max_symbols=None):
    """
    Scan NSE cash symbols with one NSE instrument dump.
    Returns {"updated_at": ..., "results": [...], "errors": [...]}
    """
    now = datetime.now(IST)
    requested = list(dict.fromkeys(
        str(s).upper().strip() for s in (symbols or []) if str(s).strip()
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
            errors.append({"symbol": symbol, "error": "NSE cash instrument not found"})
            continue
        try:
            results.append(_scan_one(kite, token, symbol, now))
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc)})

    results.sort(
        key=lambda x: (
            x.get("bull_score", 0) + x.get("bear_score", 0),
            x.get("volume_ratio") or 0
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
