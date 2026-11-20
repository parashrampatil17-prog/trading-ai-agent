"""Hilega Milega research scanner (15m, completed candles only).

Documented approximation based on public descriptions: RSI(9), EMA(3) of RSI,
WMA(21) of RSI. Not exact Chartink parity or a copy of protected source code.
Uses Yahoo Finance via yfinance; data may be delayed/incomplete.
"""
from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo
import logging, math
import numpy as np
import pandas as pd
import yfinance as yf

IST = ZoneInfo('Asia/Kolkata')
LOG = logging.getLogger(__name__)
INTERVAL, PERIOD = '15m', '60d'
RSI_LEN, EMA_LEN, WMA_LEN, PRICE_EMA_LEN, ATR_LEN = 9, 3, 21, 20, 14
MIN_BARS = 80
SYMBOLS = [
    'ADANIENT','ADANIPORTS','APOLLOHOSP','ASIANPAINT','AXISBANK','BAJAJ-AUTO',
    'BAJFINANCE','BAJAJFINSV','BEL','BHARTIARTL','CIPLA','COALINDIA','DRREDDY',
    'EICHERMOT','ETERNAL','GRASIM','HCLTECH','HDFCBANK','HDFCLIFE','HINDALCO',
    'HINDUNILVR','ICICIBANK','ITC','INFY','INDIGO','JSWSTEEL','JIOFIN','KOTAKBANK',
    'LT','MARUTI','MAXHEALTH','NTPC','NESTLEIND','ONGC','POWERGRID','RELIANCE',
    'SBILIFE','SHRIRAMFIN','SBIN','SUNPHARMA','TCS','TATACONSUM','TATASTEEL',
    'TECHM','TITAN','TRENT','ULTRACEMCO'
]

def _ticker_symbol(symbol: str) -> str:
    s = str(symbol).upper().strip()
    if not s: raise ValueError('Empty symbol')
    return s.replace('&', '%26') + '.NS'

def _normalise(raw: pd.DataFrame) -> pd.DataFrame:
    if raw is None or raw.empty: raise ValueError('No market data returned')
    if isinstance(raw.columns, pd.MultiIndex): raw.columns = raw.columns.get_level_values(0)
    raw.columns = [str(c).strip().lower().replace(' ', '_') for c in raw.columns]
    cols = ['open','high','low','close','volume']
    if not set(cols).issubset(raw.columns): raise ValueError('Missing OHLCV columns')
    df = raw[cols].copy()
    for c in cols: df[c] = pd.to_numeric(df[c], errors='coerce')
    return df.dropna(subset=['open','high','low','close']).loc[lambda x: ~x.index.duplicated(keep='last')].sort_index()

def _completed_15m(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty: return df
    ts = pd.Timestamp(df.index[-1])
    ts = ts.tz_localize(IST) if ts.tzinfo is None else ts.tz_convert(IST)
    now = pd.Timestamp.now(tz=IST)
    # Yahoo timestamps mark candle start. Do not use the forming candle.
    return df.iloc[:-1].copy() if ts + pd.Timedelta(minutes=15) > now else df

def _rsi(close: pd.Series, n: int = RSI_LEN) -> pd.Series:
    d = close.diff(); gain = d.clip(lower=0); loss = -d.clip(upper=0)
    ag = gain.ewm(alpha=1/n, min_periods=n, adjust=False).mean()
    al = loss.ewm(alpha=1/n, min_periods=n, adjust=False).mean()
    rs = ag / al.replace(0, np.nan)
    out = 100 - 100/(1+rs)
    out = out.mask((al == 0) & (ag > 0), 100).mask((ag == 0) & (al > 0), 0)
    return out

def _wma(s: pd.Series, n: int = WMA_LEN) -> pd.Series:
    weights = np.arange(1, n+1, dtype=float)
    return s.rolling(n, min_periods=n).apply(lambda x: float(np.dot(x, weights)/weights.sum()), raw=True)

def _atr(df: pd.DataFrame) -> pd.Series:
    pc = df.close.shift(1)
    tr = pd.concat([df.high-df.low, (df.high-pc).abs(), (df.low-pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/ATR_LEN, min_periods=ATR_LEN, adjust=False).mean()

def _trend_30m(df: pd.DataFrame) -> str:
    x = df.resample('30min', label='right', closed='right').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna(subset=['close'])
    if len(x): x = x.iloc[:-1]  # only completed 30m bars
    if len(x) < 25: return 'INSUFFICIENT'
    ema = x.close.ewm(span=PRICE_EMA_LEN, adjust=False).mean()
    return 'BULLISH' if x.close.iloc[-1] > ema.iloc[-1] else ('BEARISH' if x.close.iloc[-1] < ema.iloc[-1] else 'NEUTRAL')

def analyze_frame(raw: pd.DataFrame, symbol: str) -> dict:
    df = _completed_15m(_normalise(raw))
    if len(df) < MIN_BARS: raise ValueError(f'Need {MIN_BARS} completed 15m candles; got {len(df)}')
    close = df.close
    rsi = _rsi(close)
    ema_rsi = rsi.ewm(span=EMA_LEN, adjust=False, min_periods=EMA_LEN).mean()
    wma_rsi = _wma(rsi)
    ema20 = close.ewm(span=PRICE_EMA_LEN, adjust=False).mean()
    atr = _atr(df)
    # Session VWAP resets for each trading date.
    session = pd.Series(df.index.date, index=df.index)
    typical = (df.high + df.low + df.close)/3
    volume = df.volume.fillna(0).clip(lower=0)
    vwap = (typical*volume).groupby(session).cumsum() / volume.groupby(session).cumsum().replace(0, np.nan)
    ind = pd.DataFrame({'rsi':rsi,'ema_rsi':ema_rsi,'wma_rsi':wma_rsi,'ema20':ema20,'atr':atr,'vwap':vwap}).dropna()
    if len(ind) < 2: raise ValueError('Insufficient valid indicator values')
    prev, cur = ind.iloc[-2], ind.iloc[-1]
    price = float(close.loc[ind.index[-1]])
    cross_up = prev.rsi <= prev.wma_rsi and cur.rsi > cur.wma_rsi
    cross_down = prev.rsi >= prev.wma_rsi and cur.rsi < cur.wma_rsi
    buy_ok = cur.rsi > 50 and cur.ema_rsi > cur.wma_rsi and price > cur.ema20 and price > cur.vwap
    sell_ok = cur.rsi < 50 and cur.ema_rsi < cur.wma_rsi and price < cur.ema20 and price < cur.vwap
    signal, reason = '-', 'No fresh RSI/WMA crossover on the last completed candle.'
    if cross_up and buy_ok: signal, reason = 'BUY WATCH', 'Bullish RSI/WMA cross with RSI>50, EMA3>WMA21, price>EMA20 and VWAP.'
    elif cross_down and sell_ok: signal, reason = 'SELL WATCH', 'Bearish RSI/WMA cross with RSI<50, EMA3<WMA21, price<EMA20 and VWAP.'
    elif cross_up: reason = 'Bullish cross, but one or more confirmation filters failed.'
    elif cross_down: reason = 'Bearish cross, but one or more confirmation filters failed.'
    a = float(cur.atr); sl=t1=t2=None
    if math.isfinite(a) and a > 0 and signal == 'BUY WATCH': sl,t1,t2 = round(price-1.5*a,2),round(price+1.5*a,2),round(price+3*a,2)
    elif math.isfinite(a) and a > 0 and signal == 'SELL WATCH': sl,t1,t2 = round(price+1.5*a,2),round(price-1.5*a,2),round(price-3*a,2)
    return {
        'symbol':str(symbol).upper(),'signal':signal,'close':round(price,2),
        'rsi':round(float(cur.rsi),2),'ema_rsi_3':round(float(cur.ema_rsi),2),
        'wma_rsi_21':round(float(cur.wma_rsi),2),'ema20':round(float(cur.ema20),2),
        'vwap':round(float(cur.vwap),2),'atr14':round(a,2),
        'stop_loss':sl,'target1':t1,'target2':t2,
        'trend_15m':'BULLISH' if price>cur.ema20 else 'BEARISH',
        'trend_30m':_trend_30m(df),'as_of':pd.Timestamp(ind.index[-1]).isoformat(),
        'reason':reason,'data_interval':'15m','candle_closed':True,
        'method':'Approximation: RSI9/WMA21 cross + EMA3 RSI and price/EMA20/VWAP filters'
    }

def scan(symbol: str):
    raw = yf.Ticker(_ticker_symbol(symbol)).history(period=PERIOD, interval=INTERVAL, auto_adjust=False, actions=False, prepost=False, timeout=20)
    return analyze_frame(raw, symbol)

def scan_many(symbols=None) -> dict:
    symbols = list(symbols or SYMBOLS); results=[]; errors=[]
    for symbol in symbols:
        try: results.append(scan(symbol))
        except Exception as exc:
            LOG.warning('Hilega scan failed for %s: %s', symbol, exc)
            errors.append({'symbol':symbol,'error':str(exc)})
    return {'results':results,'scanned':len(symbols),'matched':sum(x['signal']!='-' for x in results),'errors':errors,'updated':datetime.now(IST).isoformat(),'note':'Research/paper only. Yahoo data may be delayed/incomplete; not exact Chartink parity.'}

if __name__ == '__main__':
    import json
    print(json.dumps(scan_many(SYMBOLS[:5]), indent=2, default=str))
