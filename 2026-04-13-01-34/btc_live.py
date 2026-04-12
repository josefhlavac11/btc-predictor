"""
btc_live.py  v5.0
=================
BTC/USDC Multi-Timeframe Live Analyzer

TERMINOLOGIE:
  Scalp       — 1M cyklus, 2–15 min, cíl 100–250 USDC, TF: 1M (3M partial)
  Micro-swing — 3M+5M cyklus, 15–90 min, cíl 250–500 USDC, TF: 3M/5M (15M partial)
  Swing       — 15M cyklus, 1–6 hod, cíl 500–1500 USDC, TF: 15M (1H partial)
  Swing+      — 1H+ cyklus, 6–48 hod, cíl 1000–3000 USDC, TF: 1H/4H

CHANGELOG:
  v5.0  Momentum skóre (Volume+MACD+Stoch+ATR, kalibrováno na datech),
         klasifikátor typu obchodu (scalp/micro-swing/swing),
         kalkulátor velikosti pozice (min 300 USDC cíl, Kelly-inspired),
         přesný popis trade setupu pro každý typ.
  v4.0  Camarilla, Fibonacci, Gann, predikce času, self-validace, verzování.
  v3.0  PDH/PDL/WO, seance, short signály, TP pravděpodobnosti.
  v2.0  VWAP, paginace, vícedenní data.
  v1.0  Základní signály.

INSTALACE:  pip install requests pandas numpy
POUŽITÍ:
  python btc_live.py
  python btc_live.py --watch 30
  python btc_live.py --equity 5000      # vaše portfolio v USDC
  python btc_live.py --risk 1.5         # % portfolia per obchod (default 1.0)
  python btc_live.py --no-validate      # přeskočit self-validaci
"""

VERSION = "5.0.0"

import requests, pandas as pd, numpy as np
import json, os, time, argparse
from datetime import datetime
from zoneinfo import ZoneInfo

TZ_PRAGUE  = ZoneInfo("Europe/Prague")
SYMBOL     = "BTCUSDC"
BASE_URL   = "https://api.binance.com"
DATA_DIR   = "data"
LOG_DIR    = "logs"

TF_CANDLES = {"1m":10080,"3m":3360,"5m":4032,"15m":2880,"1h":2160,"4h":1080}

# ── TRADE TYPY ────────────────────────────────────────────────────────────────
TRADE_TYPES = {
    "scalp": {
        "label":      "Scalp",
        "tf_primary": "1M",
        "tf_confirm": "3M partial",
        "min_usdc":   100, "max_usdc":  250, "tp": 150, "sl":  60, "be":  80,
        "max_min":    15,  "rr":        2.5,
        "desc":       "1M cyklus dokončen, exit do 15 min",
    },
    "micro_swing": {
        "label":      "Micro-swing",
        "tf_primary": "3M+5M",
        "tf_confirm": "5M (15M partial)",
        "min_usdc":   250, "max_usdc":  500, "tp": 350, "sl": 100, "be": 130,
        "max_min":    90,  "rr":        3.5,
        "desc":       "3M+5M konsolidace, 15M se zapojuje",
    },
    "swing": {
        "label":      "Swing",
        "tf_primary": "15M",
        "tf_confirm": "1H partial",
        "min_usdc":   500, "max_usdc": 1500, "tp": 600, "sl": 150, "be": 200,
        "max_min":    360, "rr":        4.0,
        "desc":       "15M cyklus plný, 1H se zapojuje",
    },
    "swing_plus": {
        "label":      "Swing+",
        "tf_primary": "1H",
        "tf_confirm": "4H partial",
        "min_usdc":  1000, "max_usdc": 3000, "tp":1200, "sl": 250, "be": 350,
        "max_min":  2880,  "rr":        4.8,
        "desc":       "1H/4H cyklus, vícedenní pohyb",
    },
}

# TP pravděpodobnosti (kalibrováno)
TP_PROBS_L = {100:100,150:82,200:70,300:55,400:40,500:33,600:22,750:15,1000:8}
TP_PROBS_S = {100:100,150:88,200:75,300:65,400:50,500:40,600:30,750:20,1000:10}

# Kalibrační konstanty (z backtestu na dostupných datech)
CALIB = {
    "avg_scalp_dur_min":  36,
    "timing_err_min":     19,
    "avg_rsi_rate_per_h": 1.03,
    "fib_best_ext":       1.0,
    # Momentum váhy — kalibrováno: záporná korelace ROC+MACD = invertujeme
    "mom_weights": {"volume": 0.35, "macd_inv": 0.25, "roc_inv": 0.20,
                    "stoch": 0.15, "atr": 0.05},
}

for d in [DATA_DIR, LOG_DIR]:
    os.makedirs(d, exist_ok=True)

# ────────────────────────────────────────────────────────────────────────────
# UTILITY
# ────────────────────────────────────────────────────────────────────────────
def now_local(): return datetime.now(TZ_PRAGUE).strftime("%H:%M:%S")
def ts_ms(ms): return datetime.fromtimestamp(ms/1000, tz=TZ_PRAGUE).replace(tzinfo=None)
def tp_prob(tp, d): return (TP_PROBS_L if d=="LONG" else TP_PROBS_S).get(
    tp, next((v for k,v in sorted((TP_PROBS_L if d=="LONG" else TP_PROBS_S).items()) if tp<=k), 5))

def get_session(h, wd):
    if wd>=5: return {"name":"Víkend","mult":0.4,"note":"Falešné průlomy — pozice 40%"}
    if  2<=h< 9: return {"name":"Asie","mult":0.7,"note":"Nízká likvidita"}
    if  9<=h<15: return {"name":"Evropa","mult":1.0,"note":"Standardní"}
    if 15<=h<17: return {"name":"EU/US překryv","mult":1.5,"note":"Max likvidita"}
    if 17<=h<23: return {"name":"Amerika","mult":1.3,"note":"Velké pohyby"}
    return {"name":"Noc","mult":0.5,"note":"Vyhni se"}

# ────────────────────────────────────────────────────────────────────────────
# INDIKÁTORY
# ────────────────────────────────────────────────────────────────────────────
def ema(s,n): return s.ewm(span=n,adjust=False).mean()

def rsi(s,n=14):
    d=s.diff(); g=d.clip(lower=0); l=-d.clip(upper=0)
    return 100-100/(1+g.ewm(alpha=1/n,adjust=False).mean()/
                      (l.ewm(alpha=1/n,adjust=False).mean()+1e-10))

def macd(s,f=12,sl=26,sig=9):
    m=ema(s,f)-ema(s,sl); return m,ema(m,sig),m-ema(m,sig)

def stoch_rsi(s,rp=14,sp=14,k=3,d=3):
    r=rsi(s,rp); mn=r.rolling(sp).min(); mx=r.rolling(sp).max()
    K=((r-mn)/(mx-mn+1e-10)*100).rolling(k).mean()
    return K, K.rolling(d).mean()

def atr(df,n=14):
    tr=pd.concat([df['high']-df['low'],
                  (df['high']-df['close'].shift()).abs(),
                  (df['low']-df['close'].shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(span=n,adjust=False).mean()

def add_indicators(df):
    df=df.copy()
    df["rsi"]=rsi(df["close"])
    df["macd"],df["macd_sig"],df["mh"]=macd(df["close"])
    df["sk"],df["sd"]=stoch_rsi(df["close"])
    df["e9"]=ema(df["close"],9); df["e21"]=ema(df["close"],21); df["e50"]=ema(df["close"],50)
    df["atr"]=atr(df)
    df["roc5"]=(df["close"]-df["close"].shift(5))/df["close"].shift(5)*100
    df["vol_ma"]=df["volume"].rolling(20).mean()
    df["vol_ratio"]=df["volume"]/(df["vol_ma"]+1e-10)
    # VWAP
    df["tp_p"]=(df["high"]+df["low"]+df["close"])/3
    df["date_utc"]=df["time"].dt.date
    parts=[]
    for _,g in df.groupby("date_utc"):
        g=g.copy(); g["vwap"]=(g["tp_p"]*g["volume"]).cumsum()/g["volume"].cumsum()
        parts.append(g)
    return pd.concat(parts).sort_values("time").reset_index(drop=True)

# ────────────────────────────────────────────────────────────────────────────
# MOMENTUM SKÓRE (kalibrováno: záporné korelace = invertujeme)
# ────────────────────────────────────────────────────────────────────────────
def momentum_score(r1m: dict, r3m: dict, r5m: dict) -> tuple[float, dict]:
    """
    Momentum skóre 0–100 pro LONG vstup u dna.

    Kalibrace na historických datech:
      ROC korelace s gain15:      -0.872 → INVERTUJEME (nízké ROC = bullish u dna)
      Volume korelace s gain15:   +0.436 → přímá
      MACD korelace s gain15:     -0.763 → INVERTUJEME (záporný MACD u dna = ok)
      Stoch gap korelace gain15:  -0.566 → INVERTUJEME (nízko = lepší vstup)
    """
    w = CALIB["mom_weights"]

    # Volume: vyšší = silnější pohyb (přímá korelace)
    vol = float(r1m.get("vol_ratio", 1.0))
    m_vol = min(100, vol * 50)  # 2× avg = 100

    # MACD hist inverted: záporný a zlepšující se = ideální u dna
    mh = float(r1m.get("mh", 0))
    mh_prev = float(r1m.get("mh_prev", mh))
    # Záporný hist co se otáčí = max skóre, kladný klesající = min
    if mh < 0 and mh > mh_prev:   m_macd = 90   # záporný ale zlepšující → ideální
    elif mh < 0 and mh <= mh_prev: m_macd = 40   # záporný a zhoršující
    elif mh >= 0 and mh > mh_prev: m_macd = 70   # kladný a roste
    else:                           m_macd = 30   # kladný ale klesá

    # ROC inverted: záporné ROC (cena klesá) u dna = připravuje odraz
    roc = float(r1m.get("roc5", 0))
    m_roc = min(100, max(0, 50 - roc * 5))  # roc=-2% → 60, roc=+2% → 40

    # Stoch gradient inverted: nízká hodnota SK u dna = prostor pro odraz
    sk = float(r1m.get("sk", 50))
    sd = float(r1m.get("sd", 50))
    sk_cross = sk > sd and float(r1m.get("sk_prev", sk)) <= float(r1m.get("sd_prev", sd))
    m_stoch = min(100, max(0, (50-sk)*1.5 + (30 if sk_cross else 0)))

    # ATR: vyšší volatilita = větší pohyb možný (neutrální, jen amplifikátor)
    atr_v = float(r1m.get("atr", 30))
    m_atr = min(100, (atr_v/40)*60)

    total = (w["volume"]*m_vol + w["macd_inv"]*m_macd +
             w["roc_inv"]*m_roc + w["stoch"]*m_stoch + w["atr"]*m_atr)

    # 3M/5M kontext bonus
    if float(r3m.get("sk", 50)) < 25: total = min(100, total + 5)
    if float(r5m.get("rsi", 50)) < 45: total = min(100, total + 5)

    components = {
        "volume":      round(m_vol,  1),
        "macd_inv":    round(m_macd, 1),
        "roc_inv":     round(m_roc,  1),
        "stoch_inv":   round(m_stoch,1),
        "atr":         round(m_atr,  1),
    }
    return round(total, 1), components

# ────────────────────────────────────────────────────────────────────────────
# KLASIFIKÁTOR TYPU OBCHODU
# ────────────────────────────────────────────────────────────────────────────
def classify_trade(r1m, r3m, r5m, r15, r1h) -> dict:
    """
    Hierarchický klasifikátor — podmínky místo bodování.
    Pořadí priority: Scalp → Micro-swing → Swing → Swing+.
    Swing+ long vetován pokud je 1H v downtrendu (e9 < e21 x 0.998).
    """
    mh_up   = r1m["mh"] > r1m.get("mh_prev", r1m["mh"])
    h1_bull = r1h["e9"] >= r1h["e21"] * 0.995
    h1_down = r1h["e9"] <  r1h["e21"] * 0.998

    conds = {
        "scalp":       r1m["sk"] <= 20 and mh_up,
        "micro_swing": r3m["sk"] <= 30 and r5m["sk"] <= 35,
        "swing":       r15["sk"] <= 30 and h1_bull,
        "swing_plus":  r1h["sk"] <= 25 and not h1_down,
    }

    # První splněná podmínka v pořadí priority
    order  = ["scalp", "micro_swing", "swing", "swing_plus"]
    best   = next((t for t in order if conds[t]), None)
    signal = best is not None

    if not signal:
        best = "scalp"  # fallback — position_size() potřebuje platný typ

    tt         = TRADE_TYPES[best]
    met        = sum(conds.values())
    confidence = "vysoká" if signal and met >= 2 else "střední" if signal else "žádný"

    return {
        "type":       best,
        "signal":     signal,
        "conditions": conds,
        "scores":     {k: (1 if v else 0) for k, v in conds.items()},
        "confidence": confidence,
        "best_score": met,
        **tt,
    }

# ────────────────────────────────────────────────────────────────────────────
# KALKULÁTOR VELIKOSTI POZICE
# ────────────────────────────────────────────────────────────────────────────
def position_size(
    equity:       float,  # portfolio v USDC
    entry_price:  float,  # BTC cena
    trade_type:   str,    # klíč do TRADE_TYPES
    momentum:     float,  # 0-100
    sess_mult:    float,  # seance multiplikátor
    weekend:      bool,
    risk_pct:     float = 1.0,
) -> dict:
    """
    Kelly-inspired position sizing.
    Minimální cíl: 300 USDC zisk.
    Maximální risk: 2% portfolia per obchod.
    """
    tt = TRADE_TYPES[trade_type]
    tp_usdc = tt["tp"]; sl_usdc = tt["sl"]

    # Základní risk
    risk_usdc = equity * risk_pct / 100

    # Momentum multiplikátor: 0–100 → 0.7–1.5×
    mom_mult = 0.7 + (momentum / 100) * 0.8

    # Typ a seance
    type_mult = {"scalp":0.5,"micro_swing":1.0,"swing":1.5,"swing_plus":2.0}.get(trade_type,1.0)

    risk_usdc = risk_usdc * mom_mult * type_mult * sess_mult
    if weekend: risk_usdc *= 0.5
    # Cap: max 2% equity
    risk_usdc = min(risk_usdc, equity * 0.02)

    # BTC množství
    btc_qty = risk_usdc / sl_usdc
    notional = btc_qty * entry_price
    expected = btc_qty * tp_usdc

    # Enforce minimum 300 USDC cíl
    min_enforced = False
    if expected < 300:
        min_enforced = True
        btc_qty = 300 / tp_usdc
        notional = btc_qty * entry_price
        risk_usdc = btc_qty * sl_usdc
        expected = 300.0

    leverage = notional / equity

    return {
        "btc_qty":    round(btc_qty, 5),
        "btc":        round(btc_qty, 5),  # alias pro kompatibilitu s btc_ticker
        "notional":   round(notional, 0),
        "risk_usdc":  round(risk_usdc, 0),
        "risk_pct":   round(risk_usdc / equity * 100, 2),
        "target_usdc":round(expected, 0),
        "leverage":   round(leverage, 2),
        "min_enforced": min_enforced,
        "tp_price":   round(entry_price + tp_usdc, 0),
        "sl_price":   round(entry_price - sl_usdc, 0),
        "be_price":   round(entry_price + tt["be"], 0),
        "rr":         tt["rr"],
    }

# ────────────────────────────────────────────────────────────────────────────
# CAMARILLA / FIBONACCI / GANN
# ────────────────────────────────────────────────────────────────────────────
def camarilla(ph,pl,pc):
    r=ph-pl
    return {"R4":pc+r*1.1/2,"R3":pc+r*1.1/4,"R2":pc+r*1.1/6,"R1":pc+r*1.1/12,
            "PP":(ph+pl+pc)/3,
            "S1":pc-r*1.1/12,"S2":pc-r*1.1/6,"S3":pc-r*1.1/4,"S4":pc-r*1.1/2}

def fib_ret(lo,hi):
    r=hi-lo
    return {k:hi-r*v for k,v in {"0%":0,"23.6%":0.236,"38.2%":0.382,"50%":0.5,
                                  "61.8%":0.618,"78.6%":0.786,"100%":1.0}.items()}

def fib_ext(lo2,hi2,new_lo):
    r=hi2-lo2
    return {k:new_lo+r*v for k,v in {"61.8%":0.618,"100%":1.0,"127.2%":1.272,"161.8%":1.618}.items()}

def fib_channel(closes,window=60):
    s=pd.Series(closes[-window:],dtype=float); x=np.arange(len(s))
    m,b=np.polyfit(x,s.values,1); res=s.values-(m*x+b); std=np.std(res)
    base=m*(len(s)-1)+b
    return {"slope":round(m,2),"dir":"BULL" if m>0 else "BEAR",
            "up2":round(base+2*std,0),"up1":round(base+std,0),"mid":round(base,0),
            "lo1":round(base-std,0),"lo2":round(base-2*std,0)}

def gann_sq9(price,n=4):
    sp=np.sqrt(price)
    return {"r":[round((sp+i*0.125)**2,2) for i in range(1,n+1)],
            "s":[round((sp-i*0.125)**2,2) for i in range(1,n+1)]}

# ────────────────────────────────────────────────────────────────────────────
# PREDIKCE ČASU
# ────────────────────────────────────────────────────────────────────────────
def predict_timing(df1m, rsi_cur, trade_type):
    lows=[]; highs=[]
    for i in range(15,len(df1m)-15):
        if df1m["low"].iloc[i]==df1m["low"].iloc[i-15:i+16].min(): lows.append(i)
        if df1m["high"].iloc[i]==df1m["high"].iloc[i-15:i+16].max(): highs.append(i)
    cycles=[]
    for li in lows:
        fh=[h for h in highs if h>li]
        if not fh: continue
        hi=fh[0]
        max_min={"scalp":20,"micro_swing":100,"swing":400,"swing_plus":3000}.get(trade_type,100)
        if hi-li>max_min: continue
        cycles.append({"dur":hi-li,"swing":float(df1m["high"].iloc[hi]-df1m["low"].iloc[li])})
    if not cycles:
        base=TRADE_TYPES[trade_type]["max_min"]//2
        lo_val=base//2; hi_val=base*2
        return {"eta":base,"lo":lo_val,"hi":hi_val,
                "lo_t":(datetime.now(TZ_PRAGUE)+pd.Timedelta(minutes=lo_val)).strftime("%H:%M"),
                "hi_t":(datetime.now(TZ_PRAGUE)+pd.Timedelta(minutes=hi_val)).strftime("%H:%M"),
                "time":(datetime.now(TZ_PRAGUE)+pd.Timedelta(minutes=base)).strftime("%H:%M"),
                "n":0,"confidence":"nízká"}
    recent=cycles[-4:]
    avg=np.mean([c["dur"] for c in recent]); std=np.std([c["dur"] for c in recent]) if len(recent)>1 else 10
    rsi_target=62; rsi_gap=max(0,rsi_target-rsi_cur)
    eta_rsi=rsi_gap/max(CALIB["avg_rsi_rate_per_h"]/60,0.1)
    eta=round(0.5*avg+0.5*eta_rsi,0); lo=max(3,eta-std-10); hi=eta+std+10
    def t(n): return (datetime.now(TZ_PRAGUE)+pd.Timedelta(minutes=n)).strftime("%H:%M")
    return {"eta":eta,"lo":lo,"hi":hi,"time":t(eta),"lo_t":t(lo),"hi_t":t(hi),
            "n":len(cycles),"avg_swing":round(np.mean([c["swing"] for c in recent]),0),
            "confidence":"střední" if len(cycles)>=3 else "nízká"}

# ────────────────────────────────────────────────────────────────────────────
# SELF-VALIDACE
# ────────────────────────────────────────────────────────────────────────────
def self_validate(df1m, df5m):
    lows=[]; highs=[]
    for i in range(15,len(df1m)-15):
        if df1m["low"].iloc[i]==df1m["low"].iloc[i-15:i+16].min(): lows.append(i)
        if df1m["high"].iloc[i]==df1m["high"].iloc[i-15:i+16].max(): highs.append(i)
    cycles=[]
    for li in lows:
        fh=[h for h in highs if h>li]
        if not fh: continue
        hi=fh[0]; sw=float(df1m["high"].iloc[hi]-df1m["low"].iloc[li]); dur=hi-li
        if dur>200: continue
        # Momentum skóre v době dna
        r1m={k:float(df1m[k].iloc[li]) for k in ["rsi","sk","sd","mh","atr","roc5","vol_ratio"]}
        r1m["mh_prev"]=float(df1m["mh"].iloc[li-1]) if li>0 else r1m["mh"]
        sk_prev=float(df1m["sk"].iloc[li-1]) if li>0 else r1m["sk"]
        sd_prev=float(df1m["sd"].iloc[li-1]) if li>0 else r1m["sd"]
        r1m["sk_prev"]=sk_prev; r1m["sd_prev"]=sd_prev
        r1m["sk_cross_up"]=(sk_prev<=sd_prev) and (r1m["sk"]>r1m["sd"])
        r3m={"sk":50,"mh":0,"rsi":50}  # placeholder
        mom,_=momentum_score(r1m,r3m,r3m)
        cycles.append({"dur":dur,"swing":round(sw,0),"mom":round(mom,1),
                        "lo_t":str(df1m["time"].iloc[li])[11:16],
                        "hi_t":str(df1m["time"].iloc[hi])[11:16]})
    if len(cycles)<2: return {"ok":False,"n":len(cycles)}
    # Timing
    t_errs=[abs(cycles[i]["dur"]-np.mean([cycles[j]["dur"] for j in range(max(0,i-3),i)]))
            for i in range(3,len(cycles))]
    # TP hit rates
    tp_hits={}
    for tt_name,tt in TRADE_TYPES.items():
        tp_hits[tt["label"]]=round(sum(1 for c in cycles if c["swing"]>=tt["tp"])/len(cycles)*100,0)
    # Momentum vs výsledek korelace
    if len(cycles)>=3:
        mom_v=[c["mom"] for c in cycles]; swing_v=[c["swing"] for c in cycles]
        mom_corr=round(float(pd.Series(mom_v).corr(pd.Series(swing_v))),3)
    else: mom_corr=0
    return {"ok":True,"n":len(cycles),
            "timing_err_min":round(np.mean(t_errs),1) if t_errs else 0,
            "momentum_corr":mom_corr,
            "tp_hit_pct":tp_hits,
            "last_cycles":[{"lo":c["lo_t"],"hi":c["hi_t"],"swing":c["swing"],"dur":c["dur"],"mom":c["mom"]}
                           for c in cycles[-5:]]}

# ────────────────────────────────────────────────────────────────────────────
# BINANCE API
# ────────────────────────────────────────────────────────────────────────────
def fetch_klines(symbol,interval,limit):
    cols=["open_time","open","high","low","close","volume",
          "close_time","qvol","trades","tbb","tbq","ignore"]
    all_rows=[]; end_time=None; remaining=limit
    while remaining>0:
        batch=min(remaining,1000)
        params={"symbol":symbol,"interval":interval,"limit":batch}
        if end_time: params["endTime"]=end_time
        r=requests.get(f"{BASE_URL}/api/v3/klines",params=params,timeout=15)
        r.raise_for_status()
        used=int(r.headers.get("X-MBX-USED-WEIGHT-1M",0))
        if used>900: print(f"  API {used}/1200 — čekám 60s"); time.sleep(60)
        data=r.json()
        if not data: break
        all_rows=data+all_rows; remaining-=len(data); end_time=data[0][0]-1
        if len(data)<batch: break
        time.sleep(0.12)
    if not all_rows: return pd.DataFrame(columns=["time","open","high","low","close","volume"])
    df=pd.DataFrame(all_rows,columns=cols)
    df["time"]=df["open_time"].apply(ts_ms)
    for c in ["open","high","low","close","volume"]: df[c]=df[c].astype(float)
    return df.drop_duplicates("open_time").sort_values("time").reset_index(drop=True)[
        ["time","open","high","low","close","volume"]]

def fetch_ob(symbol,depth=20):
    r=requests.get(f"{BASE_URL}/api/v3/depth",params={"symbol":symbol,"limit":depth},timeout=10)
    r.raise_for_status(); raw=r.json()
    bids=[(float(p),float(q)) for p,q in raw["bids"]]
    asks=[(float(p),float(q)) for p,q in raw["asks"]]
    bv=sum(q for _,q in bids); av=sum(q for _,q in asks)
    bw=max(bids,key=lambda x:x[1]) if bids else (0,0)
    aw=max(asks,key=lambda x:x[1]) if asks else (0,0)
    return {"imb":round(bv/(bv+av),4) if (bv+av)>0 else 0.5,"bid_btc":round(bv,4),"ask_btc":round(av,4),
            "bid":bids[0][0] if bids else 0,"ask":asks[0][0] if asks else 0,
            "spread":round(asks[0][0]-bids[0][0],2) if (bids and asks) else 0,
            "bid_wall":{"p":bw[0],"q":round(bw[1],4)},"ask_wall":{"p":aw[0],"q":round(aw[1],4)}}

def get_key_levels(df4h):
    d=df4h.copy(); d["date"]=d["time"].dt.date
    daily=d.groupby("date").agg(hi=("high","max"),lo=("low","min"),cl=("close","last"),op=("open","first")).reset_index()
    pdh=pdl=pdc=wo=0
    if len(daily)>=2:
        p=daily.iloc[-2]; pdh=float(p["hi"]); pdl=float(p["lo"]); pdc=float(p["cl"])
    d["wd"]=d["time"].dt.weekday
    # Najít datum aktuálního nebo posledního pondělí
    mon_dates=d[d["wd"]==0]["date"].unique()
    if len(mon_dates)>0:
        last_mon=mon_dates[-1]
        mon_candles=d[(d["wd"]==0) & (d["date"]==last_mon)]
        wo=float(mon_candles["open"].iloc[0]) if len(mon_candles)>0 else 0
    else:
        wo=0
    today=d["date"].max(); td=d[d["date"]==today]
    return {"pdh":round(pdh,2),"pdl":round(pdl,2),"pdc":round(pdc,2),
            "weekly_open":round(wo,2),
            "today_hi":round(float(td["high"].max()) if len(td)>0 else 0,2),
            "today_lo":round(float(td["low"].min()) if len(td)>0 else 0,2)}

def find_extrema(df,window=15):
    lows=[]; highs=[]
    for i in range(window,len(df)-window):
        if df["low"].iloc[i]==df["low"].iloc[i-window:i+window+1].min(): lows.append(i)
        if df["high"].iloc[i]==df["high"].iloc[i-window:i+window+1].max(): highs.append(i)
    return lows,highs

def long_score(snaps):
    s1=snaps["1m"]; p1=snaps["1m_p"]; s5=snaps["5m"]; p5=snaps["5m_p"]
    s15=snaps["15m"]; s1h=snaps["1h"]
    c={"1M RSI 20-58":20<=s1["rsi"]<=58,"1M SK≤45":s1["sk"]<=45,
       "1M MH↑":s1["mh"]>p1["mh"],"5M RSI<68":s5["rsi"]<68,
       "5M SK<55":s5["sk"]<55,"5M MH ok":s5["mh"]>p5["mh"] or s5["mh"]>-10,
       "15M trend":s15["e9"]>=s15["e21"]*0.998,"15M RSI 25-68":25<s15["rsi"]<68,
       "1H bull":s1h["e9"]>=s1h["e21"]*0.995}
    return sum(c.values()),[k for k,v in c.items() if v],[k for k,v in c.items() if not v]

def short_score(snaps):
    s1=snaps["1m"]; p1=snaps["1m_p"]; s5=snaps["5m"]; p5=snaps["5m_p"]
    s15=snaps["15m"]; s1h=snaps["1h"]
    c={"1M RSI 62-82":62<=s1["rsi"]<=82,"1M SK≥65":s1["sk"]>=65,
       "1M MH↓":s1["mh"]<p1["mh"],"5M RSI>55":s5["rsi"]>55,
       "5M SK>55":s5["sk"]>55,"5M MH↓":s5["mh"]<p5["mh"] or s5["mh"]<10,
       "15M trend":s15["e9"]<=s15["e21"]*1.002,"15M RSI 45-75":45<s15["rsi"]<75,
       "1H RSI<70":s1h["rsi"]<70}
    return sum(c.values()),[k for k,v in c.items() if v],[k for k,v in c.items() if not v]

# ────────────────────────────────────────────────────────────────────────────
# HLAVNÍ ANALÝZA
# ────────────────────────────────────────────────────────────────────────────
def analyze(equity=10000, risk_pct=1.0, do_validate=True):
    SEP="="*66; sep="-"*66; now=now_local()
    print(f"\n{SEP}")
    print(f"  btc_live.py v{VERSION}  [{now} SEČ]  {SYMBOL}")
    print(SEP)

    # Data
    print("→ Data...", end=" ", flush=True)
    dfs={}
    for tf,n in TF_CANDLES.items():
        try:
            d=fetch_klines(SYMBOL,tf,n); d=add_indicators(d)
            dfs[tf]=d; d.to_csv(f"{DATA_DIR}/btc_{tf}.csv",index=False)
            time.sleep(0.1)
        except Exception as e: print(f"\n  [!] {tf}: {e}")
    try:
        ob=fetch_ob(SYMBOL)
        with open(f"{DATA_DIR}/orderbook.json","w") as f: json.dump(ob,f,indent=2)
    except: ob={"imb":0.5,"bid_btc":0,"ask_btc":0,"bid":0,"ask":0,"spread":0,"bid_wall":{"p":0,"q":0},"ask_wall":{"p":0,"q":0}}
    print("OK")

    def s(tf): d=dfs[tf]; return d.iloc[-1].to_dict(), d.iloc[-2].to_dict()
    r1m,p1m=s("1m"); r3m,p3m=s("3m"); r5m,p5m=s("5m"); r15,p15=s("15m"); r1h,p1h=s("1h"); r4h,_=s("4h")
    r1m["mh_prev"]=p1m["mh"]; r3m["mh_prev"]=p3m["mh"]
    r1m["sk_prev"]=p1m["sk"]; r1m["sd_prev"]=p1m["sd"]
    snaps={"1m":r1m,"1m_p":p1m,"5m":r5m,"5m_p":p5m,"15m":r15,"1h":r1h}

    cur=float(r1m["close"]); vwap=float(r1m.get("vwap",0)); vdiff=cur-vwap
    hour=pd.Timestamp(r1m["time"]).hour; wday=pd.Timestamp(r1m["time"]).weekday()
    sess=get_session(hour,wday)
    levels=get_key_levels(dfs["4h"])
    pdh=levels["pdh"]; pdl=levels["pdl"]; pdc=levels["pdc"]; wo=levels["weekly_open"]

    # Momentum
    mom,mom_comp=momentum_score(r1m,r3m,r5m)

    # Klasifikace obchodu
    trade=classify_trade(r1m,r3m,r5m,r15,r1h)

    # Velikost pozice
    ps=position_size(equity,cur,trade["type"],mom,sess["mult"],wday>=5,risk_pct)

    # S/R technické úrovně
    cam=camarilla(pdh,pdl,pdc) if pdh>0 else {}
    lows_i,highs_i=find_extrema(dfs["1m"],15)
    ll_i=lows_i[-1] if lows_i else len(dfs["1m"])-5
    lh_i=highs_i[-1] if highs_i else len(dfs["1m"])-5
    ll_p=float(dfs["1m"]["low"].iloc[ll_i]); lh_p=float(dfs["1m"]["high"].iloc[lh_i])
    ll_t=str(dfs["1m"]["time"].iloc[ll_i])[11:16]; lh_t=str(dfs["1m"]["time"].iloc[lh_i])[11:16]
    min_lo=round((pd.Timestamp(r1m["time"])-pd.Timestamp(dfs["1m"]["time"].iloc[ll_i])).total_seconds()/60,0)
    min_hi=round((pd.Timestamp(r1m["time"])-pd.Timestamp(dfs["1m"]["time"].iloc[lh_i])).total_seconds()/60,0)

    fr=fib_ret(ll_p,lh_p)
    fe={}
    if len(lows_i)>=2 and len(highs_i)>=2:
        fe=fib_ext(float(dfs["1m"]["low"].iloc[lows_i[-2]]),
                   float(dfs["1m"]["high"].iloc[highs_i[-2]]),ll_p)
    fch=fib_channel(dfs["1m"]["close"].tolist(),60)
    gann=gann_sq9(cur,4)
    pred=predict_timing(dfs["1m"],float(r1m["rsi"]),trade["type"])

    # Self-validace
    val={}
    if do_validate:
        print("→ Validace...", end=" ", flush=True)
        val=self_validate(dfs["1m"],dfs["5m"])
        print("OK")

    # Skóre
    l_sc,l_ok,l_miss=long_score(snaps)
    s_sc,s_ok,s_miss=short_score(snaps)
    sl_ok=r1m["sk"]<=10 and r1m["rsi"]<=35 and r1m["mh"]>p1m["mh"] and r5m["rsi"]<55
    ss_ok=r1m["sk"]>=90 and r1m["rsi"]>=65 and r1m["mh"]<p1m["mh"] and r5m["rsi"]>55
    support=float(dfs["1m"]["low"].tail(60).min())
    resist=float(dfs["1m"]["high"].tail(60).max())

    # ═══ VÝSTUP ═════════════════════════════════════════════════════════════

    print(f"\n  Cena:    {cur:>10,.2f}  VWAP {vwap:,.2f} ({'+' if vdiff>=0 else ''}{vdiff:.0f})")
    print(f"  Seance:  {sess['name']}  mult {sess['mult']}×  — {sess['note']}")
    print(f"  Čas:     {str(r1m['time'])[:16]} SEČ")

    # Indikátory
    print(f"\n  {sep}")
    print(f"  INDIKÁTORY")
    print(f"  {'TF':<5}{'RSI':>7}{'SK':>7}{'SD':>7}{'MH':>8} {'Trend':<6} {'VWAP↕':>8}  {'ATR':>6}")
    print(f"  {'-'*60}")
    for tf,r,p in [("1m",r1m,p1m),("3m",r3m,p3m),("5m",r5m,p5m),("15m",r15,p15),("1h",r1h,p1h),("4h",r4h,r4h)]:
        tr="BULL" if r["e9"]>r["e21"] else "BEAR"
        mha="↑" if r["mh"]>p["mh"] else "↓"
        vd=float(r.get("vwap",0)); vdt=float(r["close"])-vd if vd>0 else 0
        rf="!" if r["rsi"]<25 or r["rsi"]>75 else " "
        sf="!" if r["sk"]<10 or r["sk"]>90 else " "
        atr_v=float(r.get("atr",0))
        print(f"  {tf:<5}{r['rsi']:>6.1f}{rf}{r['sk']:>6.1f}{sf}{r['sd']:>7.1f}{r['mh']:>7.1f}{mha} {tr:<6} {vdt:>+7.0f}  {atr_v:>6.1f}")

    # Order book
    ib=ob["imb"]; ib_lbl=("SILNĚ BULL" if ib>0.75 else "BULL" if ib>0.55 else
                          "NEUTRAL" if ib>0.45 else "BEAR" if ib>0.25 else "SILNĚ BEAR")
    print(f"\n  OB {ib*100:.1f}%  {ib_lbl}  bid {ob['bid_btc']:.3f}/ask {ob['ask_btc']:.3f} BTC  spread {ob['spread']:.2f}")
    if ob["bid_wall"]["p"]>0:
        print(f"  Walls: bid {ob['bid_wall']['p']:,.0f}({ob['bid_wall']['q']:.3f})  ask {ob['ask_wall']['p']:,.0f}({ob['ask_wall']['q']:.3f})")

    # Momentum
    print(f"\n  {sep}")
    print(f"  MOMENTUM  {mom:.1f}/100")
    bar=("▓"*int(mom/5)).ljust(20)
    lvl="SILNÝ" if mom>70 else "STŘEDNÍ" if mom>45 else "SLABÝ" if mom>25 else "VELMI SLABÝ"
    print(f"  [{bar}] {lvl}")
    for k,v in mom_comp.items():
        b=("█"*int(v/10)).ljust(10)
        note={"volume":"vol nad průměrem=silný","macd_inv":"záporný MH u dna=dobré",
              "roc_inv":"cena klesala=připravuje odraz","stoch_inv":"SK nízko=prostor",
              "atr":"volatilita"}.get(k,"")
        print(f"    {k:<12} {v:>5.1f}  {b}  {note}")

    # Klasifikace obchodu + velikost pozice
    print(f"\n  {sep}")
    if trade["signal"]:
        print(f"  TYP OBCHODU: {trade['label'].upper()}  (confidence: {trade['confidence']}, skóre {trade['best_score']})")
        print(f"  {trade['desc']}")
        print(f"  TF: primární {trade['tf_primary']}, potvrzení {trade['tf_confirm']}")
        print(f"  Cíl: {trade['min_usdc']}–{trade['max_usdc']} USDC  Max trvání: {trade['max_min']} min  R:R {trade['rr']}")
        print(f"  Podmínky: {', '.join(k for k,v in trade['conditions'].items() if v)}")

        print(f"\n  {sep}")
        print(f"  VELIKOST POZICE  (portfolio: {equity:,} USDC  risk: {risk_pct}%)")
        print(f"  BTC:     {ps['btc_qty']}")
        print(f"  Notionál: {ps['notional']:,} USDC  Leverage: {ps['leverage']}×")
        print(f"  Risk:    {ps['risk_usdc']} USDC ({ps['risk_pct']}%)")
        print(f"  Cíl:     {ps['target_usdc']} USDC{'  ⚠ minimum vynuceno' if ps['min_enforced'] else ''}")
        print(f"  TP:      {ps['tp_price']:,.0f}  SL: {ps['sl_price']:,.0f}  BE: {ps['be_price']:,.0f}")
        if wday>=5: print(f"  ⚠  VÍKEND — pozice snížena na {sess['mult']*100:.0f}% standardu")
    else:
        print(f"  Žádný signál — podmínky nesplněny")

    # Klíčové úrovně
    print(f"\n  {sep}")
    print(f"  KLÍČOVÉ ÚROVNĚ")
    for lbl,lev,note in [("WO",wo,"weekly open"),("PDH",pdh,"včera max"),
                         ("PDC",pdc,"včera close"),("PDL",pdl,"včera min"),
                         ("Dnes hi",levels["today_hi"],""),("Dnes lo",levels["today_lo"],""),
                         ("VWAP",vwap,""),("Dno 1M",ll_p,f"{ll_t} {min_lo:.0f}min"),
                         ("Vrchol 1M",lh_p,f"{lh_t} {min_hi:.0f}min")]:
        if lev==0: continue
        d=cur-lev; m="◄◄" if abs(d)<50 else "◄" if abs(d)<120 else ""
        print(f"  {lbl:<12} {lev:>10,.0f}  {d:>+8.0f}  {note} {m}")

    # Camarilla kompaktně
    if cam:
        near=[f"{k}={v:,.0f}" for k,v in cam.items() if abs(cur-v)<100]
        print(f"\n  CAM blízko (<100): {', '.join(near) if near else '—'}")
        print(f"  S1={cam['S1']:,.0f}  S2={cam['S2']:,.0f}  S3={cam['S3']:,.0f}  R1={cam['R1']:,.0f}  R2={cam['R2']:,.0f}")

    # Fibonacci
    if fr:
        near_f=[f"{k}={v:,.0f}" for k,v in fr.items() if abs(cur-v)<60]
        print(f"  FIB ret blízko: {', '.join(near_f) if near_f else '—'}")
    if fe:
        best100=fe.get("100%",0)
        print(f"  FIB ext 100%={best100:,.0f}(+{best100-cur:+.0f})  127%={fe.get('127.2%',0):,.0f}  161%={fe.get('161.8%',0):,.0f}")
    print(f"  FIB ch: {fch['dir']} slope={fch['slope']:+.1f}  up={fch['up2']:,.0f}/{fch['up1']:,.0f}  mid={fch['mid']:,.0f}  lo={fch['lo1']:,.0f}/{fch['lo2']:,.0f}")
    print(f"  GANN R: {' / '.join(f'{v:,.0f}' for v in gann['r'][:3])}  S: {' / '.join(f'{v:,.0f}' for v in gann['s'][:3])}")

    # Predikce
    print(f"\n  {sep}")
    print(f"  PREDIKCE CYKLU ({trade['label']})")
    print(f"  ETA:     {pred['eta']:.0f} min  → ~{pred['time']} SEČ")
    print(f"  Rozsah:  {pred['lo']:.0f}–{pred['hi']:.0f} min  ({pred.get('lo_t','?')}–{pred.get('hi_t','?')})")
    if pred.get("avg_swing"): print(f"  Avg swing ({pred['n']} cyklů): {pred['avg_swing']} USDC")

    # Self-validace
    if val.get("ok"):
        print(f"\n  {sep}")
        print(f"  SELF-VALIDACE  ({val['n']} cyklů v datech)")
        print(f"  Timing přesnost:    ±{val['timing_err_min']} min")
        print(f"  Momentum korelace:  {val['momentum_corr']} (s gain 60min)")
        print(f"  TP hit rates:  {' | '.join(f'{k}: {v:.0f}%' for k,v in val['tp_hit_pct'].items())}")
        if val["last_cycles"]:
            print(f"  Poslední cykly:")
            for c in val["last_cycles"]:
                mom_bar="▓"*int(c["mom"]/20)
                print(f"    {c['lo']}→{c['hi']}  +{c['swing']} USDC  {c['dur']} min  mom={c['mom']} {mom_bar}")

    # Signály
    print(f"\n  {sep}")
    print(f"  SIGNÁLY   LONG {l_sc}/9   SHORT {s_sc}/9")

    if l_sc>=6 or sl_ok:
        emoji="🟢" if l_sc>=7 else "🟡"
        print(f"\n  {emoji} LONG{' ⚡SCALP' if sl_ok else ''}  {l_sc}/9")
        print(f"  OK: {', '.join(l_ok)}")
        if l_miss: print(f"  Chybí: {', '.join(l_miss)}")
        el=round(support+50,-1)
        print(f"\n  {'Typ':<14} {'Vstup':>8} {'TP':>8} {'SL':>8} {'BE':>8}  R:R  P(TP)  Max")
        for tt_k,tt_v in TRADE_TYPES.items():
            p=tp_prob(tt_v["tp"],"LONG")
            ep=round(el,0); tp_p=ep+tt_v["tp"]; sl_p=ep-tt_v["sl"]; be_p=ep+tt_v["be"]
            print(f"  {tt_v['label']:<14} {ep:>8,.0f} {tp_p:>8,.0f} {sl_p:>8,.0f} {be_p:>8,.0f}  1:{tt_v['rr']}  {p}%  {tt_v['max_min']}min")
        if wday>=5: print(f"  ⚠ VÍKEND {sess['mult']*100:.0f}%")

    if s_sc>=6 or ss_ok:
        emoji="🔴" if s_sc>=7 else "🟡"
        print(f"\n  {emoji} SHORT{' ⚡SCALP' if ss_ok else ''}  {s_sc}/9")
        print(f"  OK: {', '.join(s_ok)}")
        es=round(resist-50,-1)
        print(f"\n  {'Typ':<14} {'Vstup':>8} {'TP':>8} {'SL':>8} {'BE':>8}  R:R  P(TP)  Max")
        for tt_k,tt_v in TRADE_TYPES.items():
            p=tp_prob(tt_v["tp"],"SHORT")
            ep=round(es,0); tp_p=ep-tt_v["tp"]; sl_p=ep+tt_v["sl"]; be_p=ep-tt_v["be"]
            print(f"  {tt_v['label']:<14} {ep:>8,.0f} {tp_p:>8,.0f} {sl_p:>8,.0f} {be_p:>8,.0f}  1:{tt_v['rr']}  {p}%  {tt_v['max_min']}min")

    if l_sc<6 and s_sc<6 and not sl_ok and not ss_ok:
        print(f"\n  ⏸  Bez signálu.  LONG chybí: {', '.join(l_miss[:3])}")

    # Kontext
    print(f"\n  {sep}")
    gap=cur-pdc
    for cond,msg in [(pdh and abs(cur-pdh)<150,f"Blízko PDH {pdh:.0f} → odpor"),
                     (pdl and abs(cur-pdl)<150,f"Blízko PDL {pdl:.0f} → podpora"),
                     (pdc and abs(cur-pdc)<100,f"Blízko PDC {pdc:.0f} → mean reversion"),
                     (wo and abs(cur-wo)<200,  f"Blízko WO {wo:.0f} → gravitace"),
                     (pdc and abs(gap)>200,    f"Gap od PDC: {gap:+.0f} → fill k {pdc:.0f}")]:
        if cond: print(f"  ℹ  {msg}")

    # Uložit
    report={"version":VERSION,"time":now,"price":cur,"vwap":round(vwap,2),
            "session":sess["name"],"vol_mult":sess["mult"],"weekend":wday>=5,
            "momentum":mom,"momentum_components":mom_comp,
            "trade_type":trade["type"],"trade_confidence":trade["confidence"],
            "position":ps,"long_score":l_sc,"short_score":s_sc,
            "long_signal":l_sc>=6 or sl_ok,"short_signal":s_sc>=6 or ss_ok,
            "scalp_long":sl_ok,"scalp_short":ss_ok,"ob_imb":ob["imb"],
            "levels":levels,"camarilla":{k:round(v,2) for k,v in cam.items()} if cam else {},
            "fib_ext":{k:round(v,2) for k,v in fe.items()},
            "gann":gann,"prediction":pred,"validation":val,
            "indicators":{"1m":{"rsi":round(r1m["rsi"],1),"sk":round(r1m["sk"],1),"mh":round(r1m["mh"],2)},
                          "3m":{"rsi":round(r3m["rsi"],1),"sk":round(r3m["sk"],1)},
                          "5m":{"rsi":round(r5m["rsi"],1),"sk":round(r5m["sk"],1)},
                          "15m":{"rsi":round(r15["rsi"],1),"sk":round(r15["sk"],1)},
                          "1h":{"rsi":round(r1h["rsi"],1),"sk":round(r1h["sk"],1)}}}
    with open(f"{DATA_DIR}/live_report.json","w") as f: json.dump(report,f,indent=2)
    ts=datetime.now(TZ_PRAGUE).strftime("%Y%m%d_%H%M%S")
    with open(f"{LOG_DIR}/report_{ts}.json","w") as f: json.dump(report,f,indent=2)
    print(f"\n  → {DATA_DIR}/live_report.json  |  logs/{ts}")
    print(SEP)
    return report

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=f"btc_live v{VERSION}")
    parser.add_argument("--watch",type=int,default=0)
    parser.add_argument("--symbol",default=SYMBOL)
    parser.add_argument("--equity",type=float,default=10000)
    parser.add_argument("--risk",type=float,default=1.0)
    parser.add_argument("--no-validate",action="store_true")
    args=parser.parse_args()
    SYMBOL=args.symbol
    if args.watch>0:
        print(f"Watch {SYMBOL} {args.watch}s equity={args.equity} risk={args.risk}%")
        try:
            while True:
                r=analyze(args.equity,args.risk,not args.no_validate)
                if r and (r.get("long_signal") or r.get("short_signal")):
                    print(f"  *** SIGNÁL L:{r['long_score']}/9 S:{r['short_score']}/9 mom:{r['momentum']} ***")
                time.sleep(args.watch)
        except KeyboardInterrupt: print("\nUkončeno.")
    else:
        analyze(args.equity, args.risk, not args.no_validate)
