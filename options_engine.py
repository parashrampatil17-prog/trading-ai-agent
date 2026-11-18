from datetime import date

INDEX_EXCHANGES = {"NIFTY": "NFO", "SENSEX": "BFO"}

def get_option_analysis(kite, underlying, spot_price, exchange=None):
    underlying = str(underlying).upper().strip()
    if not underlying or spot_price is None or float(spot_price) <= 0:
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
        x for x in instruments
        if x.get("name") == instrument_name
        and x.get("instrument_type") in ("CE", "PE")
        and x.get("expiry") and x["expiry"] >= today
    ]
    if not options:
        return {"error": f"No option contracts found for {underlying}. It may not have active F&O."}

    expiry = sorted({x["expiry"] for x in options})[0]
    contracts = [x for x in options if x["expiry"] == expiry]
    strikes = sorted({float(x["strike"]) for x in contracts})
    atm = min(strikes, key=lambda x: abs(x - float(spot_price)))
    idx = strikes.index(atm)
    selected_strikes = strikes[max(0, idx - 5):idx + 6]
    selected = [x for x in contracts if float(x["strike"]) in selected_strikes]

    keys = [f'{x["exchange"]}:{x["tradingsymbol"]}' for x in selected]
    quotes = {}
    for start in range(0, len(keys), 200):
        quotes.update(kite.quote(keys[start:start + 200]))

    chain = {}
    for item in selected:
        strike = float(item["strike"])
        typ = item["instrument_type"]
        key = f'{item["exchange"]}:{item["tradingsymbol"]}'
        q = quotes.get(key, {})
        chain.setdefault(strike, {"strike": strike, "CE": {}, "PE": {}})
        chain[strike][typ] = {
            "symbol": item["tradingsymbol"],
            "ltp": q.get("last_price", 0),
            "oi": q.get("oi", 0),
            "volume": q.get("volume", 0),
        }

    rows = sorted(chain.values(), key=lambda x: x["strike"])
    ce_oi = sum(x["CE"].get("oi", 0) for x in rows)
    pe_oi = sum(x["PE"].get("oi", 0) for x in rows)
    pcr = round(pe_oi / ce_oi, 2) if ce_oi else 0

    pains = {}
    for settlement in selected_strikes:
        pain = 0
        for row in rows:
            strike = row["strike"]
            pain += row["CE"].get("oi", 0) * max(0, settlement - strike)
            pain += row["PE"].get("oi", 0) * max(0, strike - settlement)
        pains[settlement] = pain

    pe_rows = [x for x in rows if x["PE"].get("oi", 0) > 0]
    ce_rows = [x for x in rows if x["CE"].get("oi", 0) > 0]
    return {
        "underlying": underlying, "exchange": exchange,
        "spot_price": float(spot_price), "expiry": expiry.isoformat(),
        "atm_strike": atm, "total_ce_oi": ce_oi, "total_pe_oi": pe_oi,
        "pcr": pcr, "max_pain": min(pains, key=pains.get),
        "support": max(pe_rows, key=lambda x: x["PE"]["oi"])["strike"] if pe_rows else None,
        "resistance": max(ce_rows, key=lambda x: x["CE"]["oi"])["strike"] if ce_rows else None,
        "option_chain": rows,
    }
