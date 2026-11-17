from datetime import date
from kiteconnect import KiteConnect


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

    atm_strike = min(
        strikes,
        key=lambda strike: abs(strike - spot_price)
    )

    atm_index = strikes.index(atm_strike)

    selected_strikes = strikes[
        max(0, atm_index - 5):
        atm_index + 6
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
