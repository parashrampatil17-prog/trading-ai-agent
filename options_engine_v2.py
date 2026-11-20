"""
Trading AI Options Engine V2
Research / paper mode only. Does not place orders.
Compatible with the existing dashboard keys while adding richer OI analysis.
"""
from datetime import date

INDEX_EXCHANGES = {"NIFTY": "NFO", "SENSEX": "BFO"}


def _safe_float(value, default=0.0):
    try:
        number = float(value)
        return number if number == number and abs(number) != float("inf") else default
    except (TypeError, ValueError):
        return default


def _oi_wall(rows, option_type):
    eligible = [
        row for row in rows
        if _safe_float((row.get(option_type) or {}).get("oi")) > 0
    ]
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda row: _safe_float((row.get(option_type) or {}).get("oi"))
    )["strike"]


def get_option_analysis(kite, underlying, spot_price, exchange=None):
    underlying = str(underlying or "").upper().strip()
    spot = _safe_float(spot_price, -1)
    if not underlying or spot <= 0:
        raise ValueError("Invalid underlying or spot price")

    if underlying in INDEX_EXCHANGES:
        exchange = INDEX_EXCHANGES[underlying]
        instrument_name = underlying
    else:
        exchange = exchange or "NFO"
        instrument_name = underlying

    instruments = kite.instruments(exchange)
    today = date.today()
    options = [
        item for item in instruments
        if item.get("name") == instrument_name
        and item.get("instrument_type") in ("CE", "PE")
        and item.get("expiry") and item["expiry"] >= today
        and _safe_float(item.get("strike")) > 0
    ]
    if not options:
        return {"error": f"No option contracts found for {underlying}. It may not have active F&O contracts."}

    expiry = min(item["expiry"] for item in options)
    contracts = [item for item in options if item["expiry"] == expiry]
    strikes = sorted({_safe_float(item.get("strike")) for item in contracts})
    if not strikes:
        return {"error": f"No valid strikes found for {underlying}."}

    atm = min(strikes, key=lambda strike: abs(strike - spot))
    atm_index = strikes.index(atm)
    selected_strikes = strikes[max(0, atm_index - 5):atm_index + 6]
    selected_set = set(selected_strikes)
    selected = [item for item in contracts if _safe_float(item.get("strike")) in selected_set]

    keys = [
        f'{item["exchange"]}:{item["tradingsymbol"]}'
        for item in selected if item.get("exchange") and item.get("tradingsymbol")
    ]
    quotes = {}
    for start in range(0, len(keys), 200):
        try:
            quotes.update(kite.quote(keys[start:start + 200]) or {})
        except Exception:
            continue

    chain = {}
    for item in selected:
        strike = _safe_float(item.get("strike"))
        option_type = item.get("instrument_type")
        key = f'{item["exchange"]}:{item["tradingsymbol"]}'
        quote = quotes.get(key) or {}
        ltp = _safe_float(quote.get("last_price"))
        chain.setdefault(strike, {"strike": strike, "CE": {}, "PE": {}})
        chain[strike][option_type] = {
            "symbol": item.get("tradingsymbol"),
            "ltp": ltp if ltp > 0 else None,
            "oi": int(_safe_float(quote.get("oi"))),
            "volume": int(_safe_float(quote.get("volume"))),
            "quote_available": bool(quote),
        }

    rows = sorted(chain.values(), key=lambda row: row["strike"])
    total_ce_oi = sum(int(_safe_float((row.get("CE") or {}).get("oi"))) for row in rows)
    total_pe_oi = sum(int(_safe_float((row.get("PE") or {}).get("oi"))) for row in rows)
    pcr = round(total_pe_oi / total_ce_oi, 2) if total_ce_oi else None

    # Max pain is approximate: calculated over this selected near-ATM window only.
    pain_values = {}
    for settlement in selected_strikes:
        pain = 0.0
        for row in rows:
            strike = row["strike"]
            ce_oi = _safe_float((row.get("CE") or {}).get("oi"))
            pe_oi = _safe_float((row.get("PE") or {}).get("oi"))
            pain += ce_oi * max(0.0, settlement - strike)
            pain += pe_oi * max(0.0, strike - settlement)
        pain_values[settlement] = pain

    support = _oi_wall(rows, "PE")
    resistance = _oi_wall(rows, "CE")
    if pcr is None:
        pcr_bias = "UNAVAILABLE"
    elif pcr >= 1.10:
        pcr_bias = "BULLISH"
    elif pcr <= 0.85:
        pcr_bias = "BEARISH"
    else:
        pcr_bias = "NEUTRAL"

    atm_row = next((row for row in rows if row["strike"] == atm), {})
    atm_ce, atm_pe = atm_row.get("CE") or {}, atm_row.get("PE") or {}
    quotes_missing = sum(
        1 for row in rows for option_type in ("CE", "PE")
        if not (row.get(option_type) or {}).get("quote_available")
    )

    return {
        "underlying": underlying,
        "exchange": exchange,
        "spot_price": round(spot, 2),
        "expiry": expiry.isoformat(),
        "atm_strike": atm,
        "total_ce_oi": total_ce_oi,
        "total_pe_oi": total_pe_oi,
        "pcr": pcr,
        "pcr_bias": pcr_bias,
        "max_pain": min(pain_values, key=pain_values.get) if pain_values else None,
        "support": support,
        "resistance": resistance,
        "atm_ce_symbol": atm_ce.get("symbol"),
        "atm_ce_ltp": atm_ce.get("ltp"),
        "atm_pe_symbol": atm_pe.get("symbol"),
        "atm_pe_ltp": atm_pe.get("ltp"),
        "quote_missing_count": quotes_missing,
        "option_chain": rows,
        "analysis_note": (
            "OI support/resistance and PCR are snapshot-based. Max pain uses "
            "only selected near-ATM strikes; none is a standalone signal."
        ),
    }
