from datetime import date


EXCHANGES = {
    "NIFTY": "NFO",
    "SENSEX": "BFO"
}


def get_option_analysis(kite, underlying, spot_price):

    if underlying not in EXCHANGES:
        raise ValueError("Unsupported underlying")

    if spot_price <= 0:
        raise ValueError("Invalid spot price")

    exchange = EXCHANGES[underlying]
    instruments = kite.instruments(exchange)
    today = date.today()

    options = [
        item for item in instruments
        if item["name"] == underlying
        and item["instrument_type"] in ("CE", "PE")
        and item["expiry"] >= today
    ]

    if not options:
        return {"error": "No option contracts available"}

    expiries = sorted(set(
        item["expiry"] for item in options
    ))

    expiry = expiries[0]

    contracts = [
        item for item in options
        if item["expiry"] == expiry
    ]

    strikes = sorted(set(
        float(item["strike"]) for item in contracts
    ))

    atm = min(
        strikes,
        key=lambda x: abs(x - spot_price)
    )

    atm_index = strikes.index(atm)

    selected_strikes = strikes[
        max(0, atm_index - 5):atm_index + 6
    ]

    selected = [
        item for item in contracts
        if float(item["strike"]) in selected_strikes
    ]

    quote_keys = [
        f'{item["exchange"]}:{item["tradingsymbol"]}'
        for item in selected
    ]

    quotes = {}

    for start in range(0, len(quote_keys), 200):
        batch = quote_keys[start:start + 200]
        quotes.update(kite.quote(batch))

    chain = {}

    for item in selected:

        strike = float(item["strike"])
        option_type = item["instrument_type"]

        key = f'{item["exchange"]}:{item["tradingsymbol"]}'
        q = quotes.get(key, {})

        if strike not in chain:
            chain[strike] = {
                "strike": strike,
                "CE": {},
                "PE": {}
            }

        chain[strike][option_type] = {
            "symbol": item["tradingsymbol"],
            "ltp": q.get("last_price", 0),
            "oi": q.get("oi", 0),
            "volume": q.get("volume", 0)
        }

    rows = sorted(
        chain.values(),
        key=lambda x: x["strike"]
    )

    total_ce_oi = sum(
        x["CE"].get("oi", 0) for x in rows
    )

    total_pe_oi = sum(
        x["PE"].get("oi", 0) for x in rows
    )

    pcr = (
        round(total_pe_oi / total_ce_oi, 2)
        if total_ce_oi else 0
    )

    max_pain_values = {}

    for settlement in selected_strikes:

        pain = 0

        for row in rows:
            strike = row["strike"]

            pain += (
                row["CE"].get("oi", 0)
                * max(0, settlement - strike)
            )

            pain += (
                row["PE"].get("oi", 0)
                * max(0, strike - settlement)
            )

        max_pain_values[settlement] = pain

    max_pain = min(
        max_pain_values,
        key=max_pain_values.get
    )

    pe_rows = [
        x for x in rows if x["PE"].get("oi", 0) > 0
    ]

    ce_rows = [
        x for x in rows if x["CE"].get("oi", 0) > 0
    ]

    support = (
        max(pe_rows, key=lambda x: x["PE"]["oi"])["strike"]
        if pe_rows else None
    )

    resistance = (
        max(ce_rows, key=lambda x: x["CE"]["oi"])["strike"]
        if ce_rows else None
    )

    return {
        "underlying": underlying,
        "spot_price": spot_price,
        "expiry": expiry.isoformat(),
        "atm_strike": atm,
        "total_ce_oi": total_ce_oi,
        "total_pe_oi": total_pe_oi,
        "pcr": pcr,
            "max_pain": max_pain,
        "support": support,
        "resistance": resistance,
        "option_chain": rows
    }
