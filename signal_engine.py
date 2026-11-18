from datetime import datetime, timedelta
from statistics import mean


def _ema(values, period):
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    value = mean(values[:period])
    for price in values[period:]:
        value = price * k + value * (1-k)
    return value


def _rsi(values, period=14):
    if len(values) <= period:
        return None
    changes = [values[i] - values[i-1] for i in range(1, len(values))]
    gains = [max(x, 0) for x in changes]
    losses = [max(-x, 0) for x in changes]
    avg_gain = mean(gains[:period]); avg_loss = mean(losses[:period])
    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain*(period-1)+g)/period
        avg_loss = (avg_loss*(period-1)+l)/period
    if avg_loss == 0:
        return 100.0
    return 100 - 100/(1 + avg_gain/avg_loss)


def _instrument_token(kite, underlying):
    if underlying == 'NIFTY':
        rows = kite.instruments('NSE')
        item = next((x for x in rows if x.get('tradingsymbol') == 'NIFTY 50' and x.get('segment') == 'INDICES'), None)
    elif underlying == 'SENSEX':
        rows = kite.instruments('BSE')
        item = next((x for x in rows if x.get('tradingsymbol') == 'SENSEX' and x.get('segment') == 'INDICES'), None)
    else:
        rows = kite.instruments('NSE')
        item = next((x for x in rows if x.get('tradingsymbol') == underlying and x.get('segment') == 'NSE'), None)
    if not item:
        raise ValueError('Underlying instrument token not found')
    return item['instrument_token']


def build_signal(kite, underlying, spot, chain, hilega_result=None):
    token = _instrument_token(kite, underlying)
    now = datetime.now()
    candles = kite.historical_data(token, now-timedelta(days=12), now, '15minute')
    closes = [float(x['close']) for x in candles if x.get('close') is not None]
    if len(closes) < 30:
        return {'signal':'WAIT', 'reason':'Not enough 15-minute candles for confirmation.', 'underlying':underlying}
    e9, e21, rsi = _ema(closes, 9), _ema(closes, 21), _rsi(closes, 14)
    if e9 is None or e21 is None or rsi is None:
        return {'signal':'WAIT', 'reason':'Indicator data unavailable.', 'underlying':underlying}
    pcr = float(chain.get('pcr') or 0)
    technical = 'BULLISH' if spot > e9 > e21 and rsi >= 55 else ('BEARISH' if spot < e9 < e21 and rsi <= 45 else 'MIXED')
    oi_bias = 'BULLISH' if pcr >= 1.10 else ('BEARISH' if 0 < pcr <= 0.85 else 'MIXED')
    hilega_ok = bool(hilega_result and hilega_result.get('signal') == 'BUY WATCH')
    bull = (2 if technical == 'BULLISH' else 0) + (1 if oi_bias == 'BULLISH' else 0) + (1 if hilega_ok else 0)
    bear = (2 if technical == 'BEARISH' else 0) + (1 if oi_bias == 'BEARISH' else 0)
    signal = 'CE WATCH' if bull >= 3 and technical == 'BULLISH' else ('PE WATCH' if bear >= 3 and technical == 'BEARISH' else 'NO TRADE')
    atm = chain.get('atm_strike')
    atm_row = next((x for x in chain.get('option_chain', []) if x.get('strike') == atm), {})
    contract = atm_row.get('CE' if signal == 'CE WATCH' else 'PE' if signal == 'PE WATCH' else 'CE', {})
    return {
        'underlying': underlying, 'signal': signal, 'spot': round(float(spot),2),
        'expiry': chain.get('expiry'), 'atm_strike': atm,
        'option_symbol': contract.get('symbol'), 'option_ltp': contract.get('ltp'),
        'pcr': pcr, 'technical_bias': technical, 'oi_bias': oi_bias,
        'rsi14': round(rsi,2), 'ema9': round(e9,2), 'ema21': round(e21,2),
        'support': chain.get('support'), 'resistance': chain.get('resistance'),
        'hilega_confirmation': 'YES' if hilega_ok else ('NOT APPLICABLE' if underlying in ('NIFTY','SENSEX') else 'NO'),
        'bull_score': bull, 'bear_score': bear,
        'note': 'WATCH signal only. Option LTP is a snapshot; verify spread, liquidity and live chart. No order is placed.'
    }
