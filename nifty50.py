# ============================================================
# INTRADAY CONFLUENCE SCREENER - DHAN ONLY v5.0
# Sirf Dhan API - yfinance completely removed
# ============================================================
# SECRETS SETUP (Streamlit secrets.toml mein yeh dalo):
# -------------------------------------------------------
# MY_APP_PASSWORD   = "your_app_password"
# DHAN_ACCESS_TOKEN = "your_dhan_access_token"
# DHAN_CLIENT_ID    = "your_dhan_client_id"
# TELEGRAM_BOT_TOKEN = "your_telegram_bot_token"   # optional
# TELEGRAM_CHAT_ID   = "your_telegram_chat_id"     # optional
# -------------------------------------------------------
# Dhan API docs: https://dhanhq.co/docs/v2/

from streamlit_autorefresh import st_autorefresh
import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime
import time
import requests
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================
# PAGE CONFIG
# ============================================================
st.set_page_config(page_title="Intraday Screener v5 | Dhan", layout="wide", page_icon="⚡")

st.markdown("""
<style>
html, body, [data-testid="stAppViewContainer"] {
    background-color: #080808 !important; color: #e0e0e0;
}
[data-testid="stSidebar"] { background-color: #0d0d0d !important; }
.block-container { padding-top: 0.5rem; }
.live-badge {
    display: inline-block; background: #00e676; color: #000;
    font-size: 11px; font-weight: bold; padding: 2px 10px;
    border-radius: 20px; animation: pulse 1.2s infinite; margin-left: 8px;
}
.dhan-badge {
    display: inline-block; background: #ff6d00; color: #fff;
    font-size: 11px; font-weight: bold; padding: 2px 8px;
    border-radius: 20px; margin-left: 6px;
}
@keyframes pulse { 0%{opacity:1} 50%{opacity:0.25} 100%{opacity:1} }
.header-title {
    font-size: 22px; font-weight: 800; letter-spacing: 2px;
    color: #fff; font-family: 'Courier New', monospace;
}
textarea {
    background: #111 !important; color: #e0e0e0 !important;
    font-family: 'Courier New', monospace !important;
}
div[data-testid="stDataFrame"] { font-size: 12px; }
</style>
""", unsafe_allow_html=True)

# ============================================================
# SESSION STATE
# ============================================================
for k, v in {
    "auth":         False,
    "sent_alerts":  {},
    "ws_prices":    {},
    "ws_volumes":   {},
    "ws_connected": False,
    "last_results": [],
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ============================================================
# PASSWORD WALL
# ============================================================
if not st.session_state["auth"]:
    st.markdown("""
    <div style='text-align:center;padding:70px 0 16px 0;'>
        <div style='font-size:48px;'>🔒</div>
        <div style='font-size:20px;font-weight:bold;font-family:Courier New;
                    color:#fff;margin:14px 0 6px 0;'>AUTHORIZED ACCESS ONLY</div>
        <div style='font-size:11px;color:#333;font-family:Courier New;
                    letter-spacing:3px;'>INTRADAY SCREENER v5.0 - DHAN ONLY</div>
    </div>
    """, unsafe_allow_html=True)
    _, col, _ = st.columns([1, 2, 1])
    with col:
        pwd = st.text_input("", placeholder="Password enter karo...",
                            type="password", label_visibility="collapsed")
        if st.button("LOGIN >>", use_container_width=True):
            try:
                correct = st.secrets["MY_APP_PASSWORD"]
            except Exception:
                correct = "admin123"
            if pwd == correct:
                st.session_state["auth"] = True
                st.rerun()
            else:
                st.error("Wrong password!")
    st.stop()

# ============================================================
# DHAN SECURITY IDs (NSE EQ)
# Apne aur stocks ke IDs Dhan portal se leke yahan add karo
# ============================================================
DHAN_SECURITY_IDS = {
    "RELIANCE":   "2885",
    "HDFCBANK":   "1333",
    "TCS":        "11536",
    "INFY":       "1594",
    "SBIN":       "3045",
    "ICICIBANK":  "4963",
    "BHARTIARTL": "10604",
    "AXISBANK":   "596",
    "TATASTEEL":  "3499",
    "ITC":        "1660",
    "LT":         "11483",
    "TATAMOTORS": "3456",
    "SUNPHARMA":  "3351",
    "KOTAKBANK":  "1922",
    "BAJFINANCE": "317",
    "WIPRO":      "3787",
    "HCLTECH":    "7229",
    "MARUTI":     "10999",
    "ADANIENT":   "25",
    "NTPC":       "11630",
    # BankNifty Components
    "INDUSINDBK": "5258",
    "BANDHANBNK": "2263",
    "FEDERALBNK": "1023",
    "IDFCFIRSTB": "17873",
    "AUBANK":     "3660",
    "PNB":        "14366",
    # Index (for Nifty50 trend)
    "NIFTY50":    "13",     # Dhan index security ID - verify karo
    "BANKNIFTY":  "25",     # Dhan index security ID - verify karo
}

# ============================================================
# DHAN API HELPERS
# ============================================================
def _dhan_headers():
    return {
        "access-token": st.secrets["DHAN_ACCESS_TOKEN"],
        "client-id":    st.secrets["DHAN_CLIENT_ID"],
        "Content-Type": "application/json",
    }

def get_dhan_ltp(symbol):
    """Single stock LTP from Dhan"""
    try:
        sec_id = DHAN_SECURITY_IDS.get(symbol)
        if not sec_id:
            return None
        r = requests.post(
            "https://api.dhan.co/v2/marketfeed/ltp",
            json={"NSE_EQ": [int(sec_id)]},
            headers=_dhan_headers(),
            timeout=3,
        )
        if r.status_code == 200:
            ltp = (r.json()
                   .get("data", {})
                   .get("NSE_EQ", {})
                   .get(sec_id, {})
                   .get("last_price"))
            return float(ltp) if ltp else None
    except Exception:
        pass
    return None

def get_dhan_ltp_bulk(symbols):
    """
    Fetch LTP for multiple symbols in ONE API call.
    Returns dict: {symbol: price}
    """
    id_to_sym = {}
    sec_ids   = []
    for sym in symbols:
        sid = DHAN_SECURITY_IDS.get(sym)
        if sid:
            id_to_sym[sid] = sym
            sec_ids.append(int(sid))
    if not sec_ids:
        return {}
    try:
        r = requests.post(
            "https://api.dhan.co/v2/marketfeed/ltp",
            json={"NSE_EQ": sec_ids},
            headers=_dhan_headers(),
            timeout=5,
        )
        if r.status_code == 200:
            nse_data = r.json().get("data", {}).get("NSE_EQ", {})
            result   = {}
            for sid_str, info in nse_data.items():
                sym = id_to_sym.get(str(sid_str))
                lp  = info.get("last_price") if isinstance(info, dict) else None
                if sym and lp:
                    result[sym] = float(lp)
            return result
    except Exception:
        pass
    return {}

def get_dhan_ohlcv(symbol, interval="15"):
    """
    Fetch intraday OHLCV candles from Dhan.
    interval: "1" | "5" | "15" | "25" | "60"
    Returns DataFrame with Open/High/Low/Close/Volume or None
    """
    try:
        sec_id = DHAN_SECURITY_IDS.get(symbol)
        if not sec_id:
            return None
        r = requests.post(
            "https://api.dhan.co/v2/charts/intraday",
            json={
                "securityId":      sec_id,
                "exchangeSegment": "NSE_EQ",
                "instrument":      "EQUITY",
                "interval":        interval,
                "oi":              False,
            },
            headers=_dhan_headers(),
            timeout=6,
        )
        if r.status_code == 200:
            d  = r.json()
            df = pd.DataFrame({
                "Open":   d.get("open",   []),
                "High":   d.get("high",   []),
                "Low":    d.get("low",    []),
                "Close":  d.get("close",  []),
                "Volume": d.get("volume", []),
            })
            df.dropna(inplace=True)
            if len(df) >= 15:
                return df
    except Exception:
        pass
    return None

def get_dhan_index_ohlcv(index_name="NIFTY50", interval="15"):
    """
    Fetch index candles for Nifty50 / BankNifty trend.
    Uses INDEX instrument type.
    """
    try:
        sec_id = DHAN_SECURITY_IDS.get(index_name)
        if not sec_id:
            return None
        r = requests.post(
            "https://api.dhan.co/v2/charts/intraday",
            json={
                "securityId":      sec_id,
                "exchangeSegment": "IDX_I",   # Index segment
                "instrument":      "INDEX",
                "interval":        interval,
                "oi":              False,
            },
            headers=_dhan_headers(),
            timeout=6,
        )
        if r.status_code == 200:
            d  = r.json()
            df = pd.DataFrame({
                "Open":   d.get("open",   []),
                "High":   d.get("high",   []),
                "Low":    d.get("low",    []),
                "Close":  d.get("close",  []),
                "Volume": d.get("volume", [0]*len(d.get("close", []))),
            })
            df.dropna(inplace=True)
            if len(df) >= 10:
                return df
    except Exception:
        pass
    return None

# ============================================================
# BACKGROUND PRICE POLLING (WebSocket-style)
# ============================================================
_ws_stop_event = threading.Event()

def _ws_price_worker(symbols, interval_sec=3):
    """Background thread - polls Dhan LTP bulk every N seconds"""
    while not _ws_stop_event.is_set():
        try:
            prices = get_dhan_ltp_bulk(symbols)
            if prices:
                st.session_state["ws_prices"].update(prices)
                st.session_state["ws_connected"] = True
        except Exception:
            pass
        time.sleep(interval_sec)

def start_ws_engine(symbols):
    alive = False
    t = st.session_state.get("ws_thread")
    if t and isinstance(t, threading.Thread):
        alive = t.is_alive()
    if not alive:
        _ws_stop_event.clear()
        t = threading.Thread(target=_ws_price_worker, args=(symbols,), daemon=True)
        t.start()
        st.session_state["ws_thread"] = t

# ============================================================
# TELEGRAM ALERTS
# ============================================================
def send_telegram(msg):
    try:
        token   = st.secrets["TELEGRAM_BOT_TOKEN"]
        chat_id = st.secrets["TELEGRAM_CHAT_ID"]
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": msg, "parse_mode": "HTML"},
            timeout=5,
        )
    except Exception:
        pass

def maybe_alert(ticker, sig, price, atr, confidence):
    if sig not in ("🟢 STRONG BUY", "🔴 STRONG SELL"):
        return
    key  = f"{ticker}_{sig}"
    now  = time.time()
    last = st.session_state["sent_alerts"].get(key, 0)
    if now - last < 900:
        return
    sl_b  = round(price - 1.5 * atr, 2)
    sl_s  = round(price + 1.5 * atr, 2)
    tgt_b = round(price + 3.0 * atr, 2)
    tgt_s = round(price - 3.0 * atr, 2)
    if "BUY" in sig:
        msg = (f"🟢 <b>STRONG BUY</b>\n\n<b>{ticker}</b>\n\n"
               f"Price:      Rs {price:.2f}\nSL:         Rs {sl_b:.2f}\n"
               f"Target:     Rs {tgt_b:.2f}\nConfidence: {confidence}%")
    else:
        msg = (f"🔴 <b>STRONG SELL</b>\n\n<b>{ticker}</b>\n\n"
               f"Price:      Rs {price:.2f}\nSL:         Rs {sl_s:.2f}\n"
               f"Target:     Rs {tgt_s:.2f}\nConfidence: {confidence}%")
    send_telegram(msg)
    st.session_state["sent_alerts"][key] = now

# ============================================================
# INDICATORS
# ============================================================
def ema(s, p):
    return s.ewm(span=p, adjust=False).mean()

def calc_rsi(s, n=14):
    d = s.diff()
    g = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - (100 / (1 + g / (l + 1e-10)))

def macd_hist(s):
    ml = ema(s, 12) - ema(s, 26)
    return ml - ema(ml, 9)

def vwap_calc(df):
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    return (tp * df["Volume"]).cumsum() / (df["Volume"].cumsum() + 1e-10)

def calc_atr(df, p=14):
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - df["Close"].shift()).abs(),
        (df["Low"]  - df["Close"].shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(p).mean()

def supertrend(df, p=10, m=3):
    hl2 = (df["High"] + df["Low"]) / 2
    atr = calc_atr(df, p)
    up  = hl2 + m * atr
    dn  = hl2 - m * atr
    d   = pd.Series(0, index=df.index)
    for i in range(1, len(df)):
        if   df["Close"].iloc[i] > up.iloc[i-1]: d.iloc[i] =  1
        elif df["Close"].iloc[i] < dn.iloc[i-1]: d.iloc[i] = -1
        else: d.iloc[i] = d.iloc[i-1]
    return d

def mkt_structure(close):
    v  = close.tail(30).values
    H, L = [], []
    for i in range(1, len(v)-1):
        if v[i] > v[i-1] and v[i] > v[i+1]: H.append(v[i])
        if v[i] < v[i-1] and v[i] < v[i+1]: L.append(v[i])
    if len(H) >= 2 and len(L) >= 2:
        if H[-1] > H[-2] and L[-1] > L[-2]: return "HH+HL 🟢"
        if H[-1] < H[-2] and L[-1] < L[-2]: return "LH+LL 🔴"
    return "Choppy ⚪"

def fib_nearest(df, price):
    hi = float(df["High"].max())
    lo = float(df["Low"].min())
    d  = max(hi - lo, 1)
    lvl = {
        "0%":    hi,
        "23.6%": hi - 0.236*d,
        "38.2%": hi - 0.382*d,
        "50%":   hi - 0.5*d,
        "61.8%": hi - 0.618*d,
        "100%":  lo,
    }
    k = min(lvl, key=lambda x: abs(lvl[x] - price))
    return f"{k} Rs{lvl[k]:.0f}"

def ema_stack(close):
    n    = len(close)
    e9   = float(ema(close, 9).iloc[-1])
    e20  = float(ema(close, 20).iloc[-1])
    e50  = float(ema(close, min(50,  n-1)).iloc[-1])
    e200 = float(ema(close, min(200, n-1)).iloc[-1])
    px   = float(close.iloc[-1])
    bull = sum([px>e9, px>e20, px>e50, px>e200, e9>e20, e20>e50, e50>e200])
    if bull >= 6: grade = "🟢 Strong"
    elif bull >= 4: grade = "🟡 Moderate"
    elif bull >= 2: grade = "⚪ Weak"
    else: grade = "🔴 Bear Stack"
    return grade, e9, e20, e50, e200

def real_volume_ratio(volume_series):
    vol_now = float(volume_series.iloc[-1])
    vol_avg = float(volume_series.rolling(20).mean().iloc[-1])
    if vol_avg == 0:
        return "Normal", 1.0
    ratio = vol_now / vol_avg
    if ratio >= 4.0:   label = "CLIMAX 🌋"
    elif ratio >= 2.0: label = "SURGE 🚀"
    elif ratio >= 1.3: label = "Above Avg"
    elif ratio >= 0.7: label = "Normal"
    else:              label = "Dry 📉"
    return label, round(ratio, 2)

def sideways_market(df):
    try:
        atr = float(calc_atr(df).iloc[-1])
        px  = float(df["Close"].iloc[-1])
        return (atr / (px + 1e-10)) < 0.003
    except Exception:
        return False

def dynamic_confidence(bull, bear, htf, rs, orb, vol_ratio,
                        ema_grade, is_sideways, vix_level):
    if is_sideways:
        return 0
    spread = bull - bear
    base   = 50 + min(spread * 4, 24)
    if "HTF Bull" in htf or "HTF Bear" in htf: base += 8
    if "Strong" in rs or "Weak" in rs:          base += 4
    if "Breakout" in orb or "Breakdown" in orb: base += 4
    if vol_ratio >= 4.0:   base += 6
    elif vol_ratio >= 2.0: base += 4
    elif vol_ratio < 0.7:  base -= 5
    if "Strong"   in ema_grade:   base += 6
    elif "Moderate" in ema_grade: base += 3
    elif "Weak"   in ema_grade:   base -= 3
    elif "Bear"   in ema_grade:   base -= 6
    if vix_level > 20:   base -= 10
    elif vix_level > 15: base -= 5
    return max(0, min(int(base), 95))

# ============================================================
# NIFTY / BANKNIFTY TREND (using Dhan index data)
# ============================================================
@st.cache_data(ttl=60)
def get_index_trend(index_name, interval):
    """
    Returns (trend_str, change_pct) for NIFTY50 or BANKNIFTY.
    Uses Dhan IDX_I segment. Falls back to neutral if unavailable.
    """
    try:
        dhan_int = {"1m":"1","5m":"5","15m":"15","1h":"60"}.get(interval, "15")
        df = get_dhan_index_ohlcv(index_name, dhan_int)
        if df is None or len(df) < 10:
            return "⚪ Unknown", 0.0
        cl  = df["Close"].squeeze()
        e20 = float(ema(cl, 20).iloc[-1])
        e50 = float(ema(cl, min(50, len(cl)-1)).iloc[-1])
        px  = float(cl.iloc[-1])
        chg = (px / float(cl.iloc[max(-20, -len(cl))]) - 1) * 100
        if px > e20 > e50:   return "🟢 Bull", round(chg, 2)
        elif px < e20 < e50: return "🔴 Bear", round(chg, 2)
        return "⚪ Mixed", round(chg, 2)
    except Exception:
        return "⚪ Unknown", 0.0

@st.cache_data(ttl=300)
def fetch_vix_dhan():
    """
    India VIX from Dhan if available, else default 15.
    VIX is not always available via Dhan intraday - returns default.
    """
    # India VIX Dhan security ID = "12" (verify in Dhan portal)
    try:
        r = requests.post(
            "https://api.dhan.co/v2/marketfeed/ltp",
            json={"NSE_EQ": [12]},
            headers=_dhan_headers(),
            timeout=3,
        )
        if r.status_code == 200:
            ltp = (r.json().get("data", {})
                   .get("NSE_EQ", {}).get("12", {}).get("last_price"))
            if ltp:
                return float(ltp)
    except Exception:
        pass
    return 15.0

# ============================================================
# HIGHER TIMEFRAME TREND (using Dhan 15m data)
# ============================================================
def higher_tf_trend_dhan(symbol):
    try:
        df = get_dhan_ohlcv(symbol, "15")
        if df is None or len(df) < 20:
            return "⚪ Unknown"
        cl  = df["Close"].squeeze()
        e20 = float(ema(cl, 20).iloc[-1])
        e50 = float(ema(cl, min(50, len(cl)-1)).iloc[-1])
        px  = float(cl.iloc[-1])
        if px > e20 > e50:   return "🟢 HTF Bull"
        elif px < e20 < e50: return "🔴 HTF Bear"
        return "⚪ Mixed"
    except Exception:
        return "⚪ Unknown"

def relative_strength_dhan(stock_close, nifty_close):
    try:
        sc = stock_close.squeeze()
        nc = nifty_close.squeeze()
        n  = min(len(sc), len(nc), 10)
        if n < 2:
            return "⚪ Neutral"
        sr = float(sc.iloc[-1]) / (float(sc.iloc[-n]) + 1e-10)
        nr = float(nc.iloc[-1]) / (float(nc.iloc[-n]) + 1e-10)
        rs = sr / (nr + 1e-10)
        if rs > 1.02:   return "🟢 Strong"
        elif rs < 0.98: return "🔴 Weak"
        return "⚪ Neutral"
    except Exception:
        return "⚪ Neutral"

def opening_range_breakout(df):
    try:
        orb_h = float(df.head(3)["High"].max())
        orb_l = float(df.head(3)["Low"].min())
        cur   = float(df["Close"].iloc[-1])
        if cur > orb_h:   return "🟢 ORB Breakout"
        elif cur < orb_l: return "🔴 ORB Breakdown"
        return "⚪ Inside Range"
    except Exception:
        return "⚪ Unknown"

# ============================================================
# BANKING STOCKS LIST (BankNifty filter)
# ============================================================
BANKING_STOCKS = {
    "HDFCBANK","ICICIBANK","SBIN","AXISBANK","KOTAKBANK",
    "INDUSINDBK","BANDHANBNK","FEDERALBNK","IDFCFIRSTB","PNB","AUBANK"
}

# ============================================================
# CORE ANALYZE (single stock)
# ============================================================
def analyze_one(sym, interval, nifty_df, bn_trend, vix_level):
    ticker = sym.strip().upper()
    try:
        # Dhan interval map
        dhan_int = {"1m":"1","5m":"5","15m":"15","1h":"60"}.get(interval, "15")

        # Fetch OHLCV from Dhan
        df = get_dhan_ohlcv(ticker, dhan_int)
        if df is None or len(df) < 20:
            return {
                "Stock": ticker, "Src": "NO DATA", "Price": "-",
                "EMA": "-", "EMAStack": "-", "VWAP": "-", "RSI": "-",
                "MACD": "-", "Volume": "-", "ST": "-", "PDH": "-",
                "Struct": "-", "HTF": "-", "RS": "-", "ORB": "-",
                "Fib": "-", "BNifty": "-", "Bull": 0, "Bear": 0,
                "Conf%": "0%", "Signal": "⚠️ NO DATA"
            }

        cl  = df["Close"].squeeze()
        vo  = df["Volume"].squeeze()

        # Live price override from WebSocket cache
        ws_price = st.session_state["ws_prices"].get(ticker)
        px       = float(ws_price if ws_price else cl.iloc[-1])

        # Core indicators
        rv  = float(calc_rsi(cl).iloc[-1])
        mh  = float(macd_hist(cl).iloc[-1])
        mhp = float(macd_hist(cl).iloc[-2])
        vw  = float(vwap_calc(df).iloc[-1])
        st_d= int(supertrend(df).iloc[-1])
        atr = float(calc_atr(df).iloc[-1])

        vol_label, vol_ratio = real_volume_ratio(vo)
        ema_grade, e9, e20, e50, e200 = ema_stack(cl)

        # PDH/PDL - use first 70% candles as "previous" proxy (intraday approx)
        split   = max(int(len(df) * 0.6), 10)
        prev_df = df.iloc[:split]
        pdh = float(prev_df["High"].max())
        pdl = float(prev_df["Low"].min())

        htf      = higher_tf_trend_dhan(ticker)
        is_side  = sideways_market(df)
        rs       = relative_strength_dhan(cl, nifty_df["Close"]) if nifty_df is not None and len(nifty_df) > 5 else "⚪ Neutral"
        orb      = opening_range_breakout(df)
        struct_s = mkt_structure(cl)
        fib_s    = fib_nearest(df, px)

        # Display strings
        ema_s  = f"🟢 E9:{e9:.0f}" if px > e9 else f"🔴 E9:{e9:.0f}"
        vwap_s = f"🟢 {vw:.1f}"   if px > vw else f"🔴 {vw:.1f}"
        rsi_s  = f"{rv:.1f}{'OB' if rv>70 else ('OS' if rv<30 else '')}"
        macd_s = ("🟢 Rise" if mh > 0 and mh > mhp else
                  ("🔴 Fall" if mh < 0 and mh < mhp else "⚪ Flat"))
        st_s   = "🟢 Bull" if st_d == 1 else ("🔴 Bear" if st_d == -1 else "⚪")
        pdh_s  = "🟢 >PDH" if px > pdh else "🔴 <PDH"
        vol_s  = f"{vol_label} x{vol_ratio:.1f}"
        src_s  = "WS+DHAN" if ws_price else "DHAN"

        # BankNifty filter
        bn_filter = ""
        if ticker in BANKING_STOCKS:
            if "Bull" in bn_trend and "🔴" in macd_s:
                bn_filter = "⚠️ BN-Bull/ST-Conflict"
            elif "Bear" in bn_trend and "🟢" in macd_s:
                bn_filter = "⚠️ BN-Bear/ST-Conflict"
            else:
                bn_filter = f"BN:{bn_trend.split()[0]}"

        # SCORING
        b = be = 0
        if "🟢" in ema_s:    b  += 1
        else:                 be += 1
        if "🟢" in vwap_s:   b  += 1
        else:                 be += 1
        try:
            r = float(rsi_s[:4])
            if 50 <= r <= 65:  b  += 1
            elif 35 <= r < 50: be += 1
        except Exception:
            pass
        if "🟢" in macd_s:    b  += 1
        elif "🔴" in macd_s:  be += 1
        if vol_ratio >= 2.0:   b  += 1; be += 1
        elif vol_ratio < 0.7:  b  -= 1; be -= 1
        if "HH" in struct_s:  b  += 1
        elif "LH" in struct_s: be += 1
        if "🟢" in st_s:      b  += 1
        elif "🔴" in st_s:    be += 1
        if "🟢" in pdh_s:     b  += 1
        else:                  be += 1
        if "HTF Bull" in htf:  b  += 2
        elif "HTF Bear" in htf: be += 2
        if "Strong" in rs:     b  += 1
        elif "Weak" in rs:     be += 1
        if "Breakout" in orb:   b  += 1
        elif "Breakdown" in orb: be += 1
        if "Strong"   in ema_grade:   b  += 2
        elif "Moderate" in ema_grade: b  += 1
        elif "Bear"  in ema_grade:    be += 2
        elif "Weak"  in ema_grade:    be += 1
        if ticker in BANKING_STOCKS and "Bull" in bn_trend: b  += 1
        if ticker in BANKING_STOCKS and "Bear" in bn_trend: be += 1

        b  = max(b,  0)
        be = max(be, 0)

        confidence = dynamic_confidence(
            b, be, htf, rs, orb, vol_ratio,
            ema_grade, is_side, vix_level
        )

        if is_side:
            sig = "⚪ SIDEWAYS"
        else:
            sc = b - be
            if sc >= 7:    sig = "🟢 STRONG BUY"
            elif sc >= 3:  sig = "🟡 BUY"
            elif sc <= -7: sig = "🔴 STRONG SELL"
            elif sc <= -3: sig = "🟠 SELL"
            else:          sig = "⚪ WAIT"

        row = {
            "Stock":    ticker,
            "Src":      src_s,
            "Price":    f"Rs{px:.1f}",
            "EMA":      ema_s,
            "EMAStack": ema_grade,
            "VWAP":     vwap_s,
            "RSI":      rsi_s,
            "MACD":     macd_s,
            "Volume":   vol_s,
            "ST":       st_s,
            "PDH":      pdh_s,
            "Struct":   struct_s,
            "HTF":      htf,
            "RS":       rs,
            "ORB":      orb,
            "Fib":      fib_s,
            "BNifty":   bn_filter if bn_filter else "-",
            "Bull":     b,
            "Bear":     be,
            "Conf%":    f"{confidence}%",
            "Signal":   sig,
        }

        maybe_alert(ticker, sig, px, atr, confidence)
        return row

    except Exception as e:
        return {
            "Stock": ticker, "Src": "ERR", "Price": "Error",
            "EMA": "-", "EMAStack": "-", "VWAP": "-", "RSI": "-",
            "MACD": "-", "Volume": "-", "ST": "-", "PDH": "-",
            "Struct": "-", "HTF": "-", "RS": "-", "ORB": "-",
            "Fib": "-", "BNifty": "-", "Bull": 0, "Bear": 0,
            "Conf%": "0%", "Signal": f"ERR: {str(e)[:30]}"
        }

# ============================================================
# PARALLEL SCAN
# ============================================================
def parallel_scan(stocks, interval, nifty_df, bn_trend, vix_level):
    results  = []
    order    = {s: i for i, s in enumerate(stocks)}
    workers  = min(8, len(stocks))
    with ThreadPoolExecutor(max_workers=workers) as exe:
        futures = {
            exe.submit(analyze_one, sym, interval, nifty_df, bn_trend, vix_level): sym
            for sym in stocks
        }
        for fut in as_completed(futures):
            r = fut.result()
            if r:
                results.append(r)
    results.sort(key=lambda x: order.get(x["Stock"], 99))
    return results

# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:
    st.markdown("### ⚙️ Settings")
    st.markdown("**NSE Stock Symbols (ek line = ek stock):**")
    stock_input = st.text_area(
        "", height=220, label_visibility="collapsed",
        value="\n".join([
            "RELIANCE","HDFCBANK","TCS","INFY","SBIN",
            "ICICIBANK","AXISBANK","TATAMOTORS","ITC","LT"
        ])
    )
    interval = st.selectbox("Timeframe:", ["1m","5m","15m","1h"], index=2)
    refresh  = st.selectbox("Refresh:", [10,15,30,60,120], index=1,
                            format_func=lambda x: f"{x} sec")
    show_f   = st.selectbox("Show:", [
        "All","BUY Only","SELL Only","Strong Only","Conf > 70%"
    ])
    st.markdown("---")
    use_ws   = st.toggle("Background Price Polling", value=True,
                         help="Polls Dhan LTP every 3 sec in background thread")
    tg_on    = st.toggle("Telegram Alerts", value=True)
    st.markdown("---")

    # ⚠️ DHAN SECURITY ID CHECKER
    with st.expander("🔑 Stock ID Checker"):
        check_sym = st.text_input("Symbol check karo:", placeholder="e.g. RELIANCE")
        if check_sym:
            sid = DHAN_SECURITY_IDS.get(check_sym.strip().upper())
            if sid:
                st.success(f"ID: {sid}")
            else:
                st.error("ID nahi mila - DHAN_SECURITY_IDS mein add karo")

    if st.button("Logout"):
        st.session_state["auth"] = False
        st.rerun()

stocks = list(dict.fromkeys(
    [s.strip().upper() for s in stock_input.splitlines() if s.strip()]
))[:20]

# Start background engine
if use_ws and stocks:
    start_ws_engine(stocks)

# ============================================================
# HEADER
# ============================================================
ws_badge = '<span class="dhan-badge">DHAN LIVE</span>'
st.markdown(f"""
<div style='margin-bottom:4px;'>
  <span class='header-title'>INTRADAY SCREENER v5.0</span>
  <span class='live-badge'>LIVE</span>
  {ws_badge}
</div>
<div style='font-size:11px;color:#333;font-family:Courier New;
            letter-spacing:2px;margin-bottom:10px;'>
  NSE | DHAN API ONLY | THREADING | EMA STACK | DYNAMIC CONF | VOL RATIO | BN FILTER
</div>
""", unsafe_allow_html=True)

if not stocks:
    st.warning("Sidebar mein stock names likho")
    st.stop()

summary_ph = st.empty()
table_ph   = st.empty()
status_ph  = st.empty()

with st.expander("Strategy + Scoring Guide"):
    st.markdown("""
| Indicator | BUY | SELL | Pts |
|-----------|-----|------|-----|
| EMA 9 | Above | Below | 1 |
| EMA Stack (20/50/200) | Strong | Bear | 1-2 |
| VWAP | Above | Below | 1 |
| RSI | 50-65 | 35-50 | 1 |
| MACD | Rising | Falling | 1 |
| Real Volume | Surge x2+ | Dry <0.7x | 1 |
| Supertrend | Bull | Bear | 1 |
| PDH | Above | Below | 1 |
| Structure | HH+HL | LH+LL | 1 |
| HTF (15m) | HTF Bull | HTF Bear | 2 |
| Rel. Strength | Strong | Weak | 1 |
| ORB | Breakout | Breakdown | 1 |
| BankNifty | Aligned | Conflict | 1 |

Score +7 = STRONG BUY | Score -7 = STRONG SELL
Sideways (ATR<0.3%) = skip | Confidence 0-95%
    """)

st.caption("Powered by Dhan API only. Analysis tool - apna judgment use karo.")

# ============================================================
# AUTO REFRESH
# ============================================================
st_autorefresh(interval=refresh * 1000, key="main_refresh")

# ============================================================
# MAIN SCAN LOOP
# ============================================================
t0 = time.time()

# Shared index data (Dhan)
dhan_int   = {"1m":"1","5m":"5","15m":"15","1h":"60"}.get(interval, "15")
nifty_df   = get_dhan_index_ohlcv("NIFTY50", dhan_int)
bn_str, _  = get_index_trend("BANKNIFTY", interval)
vix        = fetch_vix_dhan()

results = parallel_scan(stocks, interval, nifty_df, bn_str, vix)

if results:
    df_all  = pd.DataFrame(results)
    df_show = df_all.copy()

    if show_f == "BUY Only":
        df_show = df_show[df_show["Signal"].str.contains("BUY",   na=False)]
    elif show_f == "SELL Only":
        df_show = df_show[df_show["Signal"].str.contains("SELL",  na=False)]
    elif show_f == "Strong Only":
        df_show = df_show[df_show["Signal"].str.contains("STRONG",na=False)]
    elif show_f == "Conf > 70%":
        df_show = df_show[
            df_show["Conf%"].str.replace("%","", regex=False).apply(
                lambda x: int(x) > 70 if str(x).isdigit() else False
            )
        ]

    def sig_style(v):
        s = str(v)
        if "STRONG BUY"  in s: return "background:#0a2e0a;color:#00e676;font-weight:bold"
        if "BUY"         in s: return "background:#071a07;color:#69f0ae"
        if "STRONG SELL" in s: return "background:#2e0808;color:#ff1744;font-weight:bold"
        if "SELL"        in s: return "background:#1a0505;color:#ff6d00"
        if "SIDEWAYS"    in s: return "background:#1a1800;color:#ffd740"
        return "color:#444"

    def cell_style(v):
        s = str(v)
        if "🟢" in s: return "color:#00e676"
        if "🔴" in s: return "color:#ff5252"
        if "SURGE" in s or "CLIMAX" in s: return "color:#40c4ff;font-weight:bold"
        if "Dry"   in s: return "color:#ff6d00"
        if "OB"    in s or "OS" in s: return "color:#ffd740"
        return "color:#aaa"

    def conf_style(v):
        try:
            n = int(str(v).replace("%",""))
            if n >= 80: return "color:#00e676;font-weight:bold"
            if n >= 60: return "color:#ffd740"
            return "color:#666"
        except Exception:
            return ""

    cc = [c for c in ["EMA","EMAStack","VWAP","MACD","ST","PDH",
                       "Volume","Struct","HTF","RS","ORB","BNifty"]
          if c in df_show.columns]

    styler = (
        df_show.style
        .map(sig_style,  subset=["Signal"])
        .map(cell_style, subset=cc)
        .map(conf_style, subset=["Conf%"])
        .set_properties(**{"font-size": "11.5px", "font-family": "Courier New"})
        .hide(axis="index")
    )

    sb = (df_all["Signal"] == "🟢 STRONG BUY").sum()
    b  = (df_all["Signal"] == "🟡 BUY").sum()
    ss = (df_all["Signal"] == "🔴 STRONG SELL").sum()
    sl = (df_all["Signal"] == "🟠 SELL").sum()
    sw = (df_all["Signal"] == "⚪ SIDEWAYS").sum()
    elapsed = round(time.time() - t0, 1)
    now     = datetime.now().strftime("%H:%M:%S")
    ws_s    = "ON" if st.session_state.get("ws_connected") else "OFF"

    with summary_ph.container():
        c1,c2,c3,c4,c5,c6,c7,c8 = st.columns(8)
        c1.metric("🟢 S.Buy",   sb)
        c2.metric("🟡 Buy",     b)
        c3.metric("🔴 S.Sell",  ss)
        c4.metric("🟠 Sell",    sl)
        c5.metric("⚪ Sideways",sw)
        c6.metric("VIX",        f"{vix:.1f}")
        c7.metric("BankNifty",  bn_str.split()[0] if bn_str else "-")
        c8.metric("WS Poll",    ws_s)

    with table_ph.container():
        st.dataframe(styler, use_container_width=True,
                     height=min(80 + len(df_show) * 36, 580))

    with status_ph.container():
        st.caption(
            f"⏰ {now} | Scan: {elapsed}s | Next: {refresh}s | "
            f"Stocks: {len(stocks)} | WS: {ws_s} | "
            f"VIX: {vix:.1f} | TG: {'ON' if tg_on else 'OFF'} | "
            f"Source: DHAN API ONLY"
        )
else:
    table_ph.error(
        "❌ Data nahi mila. Check karo: "
        "1) DHAN_ACCESS_TOKEN aur DHAN_CLIENT_ID secrets mein hain? "
        "2) Stock symbols DHAN_SECURITY_IDS mein hain? "
        "3) Market hours mein ho? (9:15 AM - 3:30 PM IST)"
    )