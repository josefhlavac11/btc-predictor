"""
btc_ticker.py  v5.0
====================
BTC/USDC Rychlý ticker — kompletní analýza za < 10 sekund.

TERMINOLOGIE:
  Scalp       — 1M cyklus, 2–15 min,  cíl 100–250 USDC  (TF: 1M, 3M partial)
  Micro-swing — 3M+5M cyklus, 15–90 min, cíl 250–500 USDC (TF: 3M/5M, 15M partial)
  Swing       — 15M cyklus, 1–6 hod,  cíl 500–1500 USDC (TF: 15M, 1H partial)
  Swing+      — 1H+ cyklus, 6–48 hod, cíl 1000–3000 USDC (TF: 1H/4H)

CHANGELOG:
  v5.0  Momentum skóre (kalibrováno: ROC/MACD inverted), klasifikátor
         obchodu (scalp/micro-swing/swing/swing+), kalkulátor velikosti
         pozice (min 300 USDC), kontrolní mechanismus "co chybí + za jak
         dlouho", predikce backtest na historických datech.
  v4.0  Camarilla, Fibonacci, Gann, predikce času, self-validace.
  v3.0  PDH/PDL/WO, seance, short, TP pravděpodobnosti.
  v2.0  VWAP, paginace.
  v1.0  Základní ticker.

POUŽITÍ:
  python btc_ticker.py                       # jednorázový výpis
  python btc_ticker.py --loop 30             # každých 30 sekund
  python btc_ticker.py --loop 30 --equity 5000 --risk 1.5
  python btc_ticker.py --no-validate         # přeskočit self-validaci
  python btc_ticker.py --backtest            # pouze backtest bez live dat
"""

VERSION = "5.0.0"

import requests, pandas as pd, numpy as np, time, argparse, os, json
from datetime import datetime
from collections import defaultdict
from zoneinfo import ZoneInfo

TZ_PRAGUE = ZoneInfo("Europe/Prague")
SYMBOL    = "BTCUSDC"
API       = "https://api.binance.com"
LOG_DIR   = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs("data", exist_ok=True)

# ── TRADE TYPY ────────────────────────────────────────────────────────────────
TRADE_TYPES = {
    "scalp":       {"label":"Scalp",       "tp":150, "sl":60,  "be":80,  "rr":2.5, "max_min":15,  "min_usdc":100},
    "micro_swing": {"label":"Micro-swing", "tp":350, "sl":100, "be":130, "rr":3.5, "max_min":90,  "min_usdc":250},
    "swing":       {"label":"Swing",       "tp":600, "sl":150, "be":200, "rr":4.0, "max_min":360, "min_usdc":500},
    "swing_plus":  {"label":"Swing+",      "tp":1200,"sl":250, "be":350, "rr":4.8, "max_min":2880,"min_usdc":1000},
}

TP_PROBS_L = {100:100,150:82,200:70,300:55,400:40,500:33,600:22,750:15,1000:8}
TP_PROBS_S = {100:100,150:88,200:75,300:65,400:50,500:40,600:30,750:20,1000:10}

CALIB = {
    "avg_scalp_dur_min":  36,
    "timing_err_min":     19,
    "avg_rsi_rate_per_h": 1.03,
}

# ── UTILITY ───────────────────────────────────────────────────────────────────
def now_local(): return datetime.now(TZ_PRAGUE).strftime("%H:%M:%S")
def ts_fmt(ms):  return datetime.fromtimestamp(ms/1000, tz=TZ_PRAGUE).strftime("%Y-%m-%d %H:%M")
def add_min(n):  return (datetime.now(TZ_PRAGUE)+pd.Timedelta(minutes=n)).strftime("%H:%M")
def tp_prob(tp,d): p=(TP_PROBS_L if d=="LONG" else TP_PROBS_S); return p.get(tp,next((v for k,v in sorted(p.items()) if tp<=k),5))

def get_session(h, wd):
    if wd>=5: return {"name":"Víkend","mult":0.4,"note":"Falešné průlomy"}
    if  2<=h< 9: return {"name":"Asie","mult":0.7,"note":"Nízká likvidita"}
    if  9<=h<15: return {"name":"Evropa","mult":1.0,"note":"Standardní"}
    if 15<=h<17: return {"name":"EU/US překryv","mult":1.5,"note":"Max pohyby"}
    if 17<=h<23: return {"name":"Amerika","mult":1.3,"note":"Velké pohyby"}
    return {"name":"Noc","mult":0.5,"note":"Vyhni se"}

# ── BINANCE API ───────────────────────────────────────────────────────────────
def klines(tf, n=300):
    BATCH=1000; all_rows=[]; end_time=None; rem=n
    while rem>0:
        batch=min(rem,BATCH)
        params={"symbol":SYMBOL,"interval":tf,"limit":batch}
        if end_time: params["endTime"]=end_time
        raw=requests.get(f"{API}/api/v3/klines",params=params,timeout=15).json()
        if not raw: break
        all_rows=raw+all_rows; rem-=len(raw); end_time=raw[0][0]-1
        if len(raw)<batch: break
        time.sleep(0.1)
    if not all_rows: return [],[],[],[],[],[],[]
    c=[float(r[4]) for r in all_rows]; h=[float(r[2]) for r in all_rows]
    l=[float(r[3]) for r in all_rows]; o=[float(r[1]) for r in all_rows]
    v=[float(r[5]) for r in all_rows]; t=[ts_fmt(r[0]) for r in all_rows]
    # VWAP denní reset
    dates=[datetime.fromtimestamp(r[0]/1000,tz=TZ_PRAGUE).date() for r in all_rows]
    tp_v=[(hi+lo+cl)/3 for hi,lo,cl in zip(h,l,c)]
    vwap=[]; cum_tv=cum_v=0; cur_d=None
    for i,(dd,tpv,vi) in enumerate(zip(dates,tp_v,v)):
        if dd!=cur_d: cum_tv=cum_v=0; cur_d=dd
        cum_tv+=tpv*vi; cum_v+=vi
        vwap.append(cum_tv/cum_v if cum_v>0 else c[i])
    return c,h,l,t,vwap,o,v

def orderbook():
    try:
        ob=requests.get(f"{API}/api/v3/depth",params={"symbol":SYMBOL,"limit":20},timeout=10).json()
        bids=[(float(p),float(q)) for p,q in ob["bids"]]
        asks=[(float(p),float(q)) for p,q in ob["asks"]]
        bv=sum(q for _,q in bids); av=sum(q for _,q in asks)
        bw=max(bids,key=lambda x:x[1]) if bids else (0,0)
        aw=max(asks,key=lambda x:x[1]) if asks else (0,0)
        return dict(bid=bids[0][0],ask=asks[0][0],spread=round(asks[0][0]-bids[0][0],2),
                    imb=round(bv/(bv+av),4),bid_btc=round(bv,3),ask_btc=round(av,3),
                    bid_wall=bw,ask_wall=aw)
    except: return dict(bid=0,ask=0,spread=0,imb=0.5,bid_btc=0,ask_btc=0,bid_wall=(0,0),ask_wall=(0,0))

# ── INDIKÁTORY ────────────────────────────────────────────────────────────────
def calc_indicators(closes, highs=None, lows=None, volumes=None, n=300):
    """Vrátí dict s aktuálními a předchozími hodnotami indikátorů."""
    s=pd.Series(closes[-n:],dtype=float)
    h=pd.Series(highs[-n:],dtype=float) if highs else s
    l=pd.Series(lows[-n:],dtype=float) if lows else s
    vol=pd.Series(volumes[-n:],dtype=float) if volumes else pd.Series([1.0]*len(s))

    d=s.diff(); g=d.clip(lower=0); lg=-d.clip(upper=0)
    ag=g.ewm(alpha=1/14,adjust=False).mean(); al=lg.ewm(alpha=1/14,adjust=False).mean()
    rsi_s=100-100/(1+ag/(al+1e-10))

    ef=s.ewm(span=12,adjust=False).mean(); es=s.ewm(span=26,adjust=False).mean()
    m=ef-es; sg=m.ewm(span=9,adjust=False).mean(); mh_s=m-sg

    mn=rsi_s.rolling(14).min(); mx=rsi_s.rolling(14).max()
    K=((rsi_s-mn)/(mx-mn+1e-10)*100).rolling(3).mean(); D=K.rolling(3).mean()

    e9=s.ewm(span=9,adjust=False).mean(); e21=s.ewm(span=21,adjust=False).mean()

    # ATR
    tr=pd.concat([h-l,(h-s.shift()).abs(),(l-s.shift()).abs()],axis=1).max(axis=1)
    atr_s=tr.ewm(span=14,adjust=False).mean()

    # ROC 5
    roc5=(s-s.shift(5))/s.shift(5)*100

    # Volume ratio
    vol_ma=vol.rolling(20).mean()
    vol_r=vol/(vol_ma+1e-10)

    def v(series, i=-1): return float(series.iloc[i]) if len(series)>abs(i) else 0.0

    return {
        "rsi":  v(rsi_s),   "rsi_p": v(rsi_s,-2),
        "sk":   v(K),       "sk_p":  v(K,-2),
        "sd":   v(D),       "sd_p":  v(D,-2),
        "mh":   v(mh_s),    "mh_p":  v(mh_s,-2),
        "e9":   v(e9),      "e21":   v(e21),
        "atr":  v(atr_s),
        "roc5": v(roc5),
        "vol_ratio": v(vol_r),
        "bull": v(e9)>v(e21),
        "sk_cross_up": v(K,-2)<v(D,-2) and v(K)>=v(D),
        "sk_cross_dn": v(K,-2)>v(D,-2) and v(K)<=v(D),
    }

# ── MOMENTUM SKÓRE (kalibrováno) ─────────────────────────────────────────────
def momentum_score(i1, i3, i5):
    """
    0–100, kalibrováno na historických datech:
    ROC korelace s výsledkem: -0.872 → INVERTUJEME
    MACD korelace:            -0.763 → INVERTUJEME
    Volume korelace:          +0.436 → přímá
    Stoch korelace:           -0.566 → INVERTUJEME
    """
    # Volume: vyšší = lepší
    m_vol = min(100, i1["vol_ratio"]*50)

    # MACD hist inverted: záporný a otáčí se = ideální u dna
    mh=i1["mh"]; mh_p=i1["mh_p"]
    if mh<0 and mh>mh_p:    m_macd=90   # záporný ale zlepšuje → ideální
    elif mh<0 and mh<=mh_p: m_macd=40   # záporný a zhoršuje
    elif mh>=0 and mh>mh_p: m_macd=70   # kladný a roste
    else:                    m_macd=30   # kladný ale klesá

    # ROC inverted: záporné = cena klesala = připravuje odraz
    roc=i1["roc5"]
    m_roc=min(100, max(0, 50-roc*5))

    # Stoch inverted: nízká SK = prostor pro odraz + bonus za cross
    sk=i1["sk"]
    m_stoch=min(100, max(0, (50-sk)*1.5 + (25 if i1["sk_cross_up"] else 0)))

    # ATR: volatilita
    atr_v=i1["atr"]
    m_atr=min(100, (atr_v/40)*60)

    # 3M/5M kontext bonus
    bonus=0
    if i3["sk"]<25: bonus+=5
    if i5["rsi"]<45: bonus+=5

    total=0.35*m_vol + 0.25*m_macd + 0.20*m_roc + 0.15*m_stoch + 0.05*m_atr + bonus
    total=min(100,total)

    return round(total,1), {
        "volume":     round(m_vol,1),
        "macd_inv":   round(m_macd,1),
        "roc_inv":    round(m_roc,1),
        "stoch_inv":  round(m_stoch,1),
        "atr":        round(m_atr,1),
    }

# ── KLASIFIKÁTOR OBCHODU ──────────────────────────────────────────────────────
def classify_trade(i1,i3,i5,i15,i1h):
    """
    Hierarchický klasifikátor — podmínky místo bodování.
    Pořadí priority: Scalp → Micro-swing → Swing → Swing+.
    Swing+ long vetován pokud je 1H v downtrendu (e9 < e21 x 0.998).
    """
    mh_up   = i1["mh"] > i1["mh_p"]
    h1_bull = i1h["e9"] >= i1h["e21"] * 0.995
    h1_down = i1h["e9"] <  i1h["e21"] * 0.998

    conds={
        "scalp":       i1["sk"]  <= 20 and mh_up,
        "micro_swing": i3["sk"]  <= 30 and i5["sk"] <= 35,
        "swing":       i15["sk"] <= 30 and h1_bull,
        "swing_plus":  i1h["sk"] <= 25 and not h1_down,
    }

    order  = ["scalp","micro_swing","swing","swing_plus"]
    best   = next((t for t in order if conds[t]), None)
    signal = best is not None

    if not signal:
        best = "scalp"

    met  = sum(conds.values())
    conf = "vysoká" if signal and met>=2 else "střední" if signal else "žádný"
    return {"type":best,"signal":signal,"conditions":conds,
            "scores":{k:(1 if v else 0) for k,v in conds.items()},
            "confidence":conf,"best_score":met,**TRADE_TYPES[best]}

# ── POSITION SIZING ───────────────────────────────────────────────────────────
def position_size(equity, entry, trade_type, momentum, sess_mult, weekend, risk_pct=1.0):
    tt=TRADE_TYPES[trade_type]
    tp_usdc=tt["tp"]; sl_usdc=tt["sl"]
    risk=equity*risk_pct/100
    mom_mult=0.7+(momentum/100)*0.8
    type_mult={"scalp":0.5,"micro_swing":1.0,"swing":1.5,"swing_plus":2.0}.get(trade_type,1.0)
    risk=min(risk*mom_mult*type_mult*sess_mult*(0.5 if weekend else 1.0), equity*0.02)
    btc=risk/sl_usdc; notional=btc*entry; expected=btc*tp_usdc
    min_enforced=False
    if expected<300:
        min_enforced=True; btc=300/tp_usdc; notional=btc*entry
        risk=btc*sl_usdc; expected=300.0
    return {"btc":round(btc,5),"notional":round(notional,0),"risk":round(risk,0),
            "risk_pct":round(risk/equity*100,2),"target":round(expected,0),
            "leverage":round(notional/equity,2),"min_enforced":min_enforced,
            "tp_price":round(entry+tp_usdc,0),"sl_price":round(entry-sl_usdc,0),"be_price":round(entry+tt["be"],0)}

# ── KONTROLNÍ MECHANISMUS: CO CHYBÍ + ZA JAK DLOUHO ─────────────────────────
def gap_to_signal(i1,i3,i5,i15,i1h, direction="LONG"):
    """
    Pro každou nesplněnou podmínku vypočítá o kolik se musí indikátor změnit
    a odhadne čas (minuty) na základě aktuální rychlosti pohybu.
    """
    gaps=[]

    if direction=="LONG":
        conditions=[
            ("1M RSI 20-58",  i1["rsi"],   lambda v: 20<=v<=58,  58,  "↓", 1.0,  "1M"),
            ("1M SK ≤45",     i1["sk"],    lambda v: v<=45,       45,  "↓", 2.5,  "1M"),
            ("1M MH↑",        i1["mh"]-i1["mh_p"], lambda v: v>0, 0,  "↑", 0.5,  "1M"),
            ("5M RSI <68",    i5["rsi"],   lambda v: v<68,        68,  "↓", 0.3,  "5M"),
            ("5M SK <55",     i5["sk"],    lambda v: v<55,        55,  "↓", 1.5,  "5M"),
            ("5M MH ok",      i5["mh"],    lambda v: v>-10,      -10,  "↑", 0.2,  "5M"),
            ("15M trend",     i15["e9"]/i15["e21"] if i15["e21"]>0 else 1,
                              lambda v: v>=0.998, 0.998,"↑", 0.01, "15M"),
            ("15M RSI 25-68", i15["rsi"],  lambda v: 25<v<68,     50,  "↓", 0.25, "15M"),
            ("1H bull",       i1h["e9"]/i1h["e21"] if i1h["e21"]>0 else 1,
                              lambda v: v>=0.995, 0.995,"↑", 0.01, "1H"),
        ]
    else:  # SHORT
        conditions=[
            ("1M RSI 62-82",  i1["rsi"],   lambda v: 62<=v<=82,  62,  "↑", 1.0,  "1M"),
            ("1M SK ≥65",     i1["sk"],    lambda v: v>=65,       65,  "↑", 2.5,  "1M"),
            ("1M MH↓",        i1["mh_p"]-i1["mh"], lambda v: v>0,0,  "↑", 0.5,  "1M"),
            ("5M RSI >55",    i5["rsi"],   lambda v: v>55,        55,  "↑", 0.3,  "5M"),
            ("5M SK >55",     i5["sk"],    lambda v: v>55,        55,  "↑", 1.5,  "5M"),
            ("5M MH↓",        i5["mh"],    lambda v: v<10,        10,  "↓", 0.2,  "5M"),
            ("15M bear",      i15["e9"]/i15["e21"] if i15["e21"]>0 else 1,
                              lambda v: v<=1.002,1.002, "↓", 0.01, "15M"),
            ("15M RSI 45-75", i15["rsi"],  lambda v: 45<v<75,     60,  "↑", 0.25, "15M"),
            ("1H RSI <70",    i1h["rsi"],  lambda v: v<70,        70,  "↓", 0.08, "1H"),
        ]

    met=[]; missing=[]
    for name,cur_val,check,target,direction_sym,rate_per_min,tf in conditions:
        ok=check(cur_val)
        if ok:
            met.append(name)
        else:
            diff=abs(target-cur_val)
            if rate_per_min>0:
                eta_min=round(diff/rate_per_min,0)
                eta_time=add_min(eta_min) if eta_min<300 else "?"
            else:
                eta_min=0; eta_time="?"
            missing.append({
                "name":   name,
                "current":round(cur_val,2) if isinstance(cur_val,float) else cur_val,
                "target": target,
                "diff":   round(diff,2),
                "eta_min":int(eta_min),
                "eta_time":eta_time,
                "tf":     tf,
                "sym":    direction_sym,
            })

    return met, missing

# ── CAMARILLA / FIBONACCI / GANN ─────────────────────────────────────────────
def camarilla(ph,pl,pc):
    r=ph-pl
    return {"R4":pc+r*1.1/2,"R3":pc+r*1.1/4,"R2":pc+r*1.1/6,"R1":pc+r*1.1/12,
            "PP":(ph+pl+pc)/3,
            "S1":pc-r*1.1/12,"S2":pc-r*1.1/6,"S3":pc-r*1.1/4,"S4":pc-r*1.1/2}

def fib_ret(lo,hi):
    r=hi-lo
    return {k:hi-r*v for k,v in {"0%":0,"23.6%":0.236,"38.2%":0.382,
                                  "50%":0.5,"61.8%":0.618,"78.6%":0.786,"100%":1.0}.items()}

def fib_ext(lo2,hi2,new_lo):
    r=hi2-lo2
    return {k:new_lo+r*v for k,v in {"100%":1.0,"127.2%":1.272,"161.8%":1.618}.items()}

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

# ── PREDIKCE ČASU ────────────────────────────────────────────────────────────
def predict_timing(closes, lows, highs, rsi_cur, trade_type):
    lows_i=[]; highs_i=[]
    n=min(len(closes),len(lows),len(highs))
    for i in range(15,n-15):
        if lows[i]==min(lows[i-15:i+16]): lows_i.append(i)
        if highs[i]==max(highs[i-15:i+16]): highs_i.append(i)
    max_dur=TRADE_TYPES[trade_type]["max_min"]
    cycles=[]
    for li in lows_i:
        fh=[h for h in highs_i if h>li]
        if not fh: continue
        hi=fh[0]
        if hi-li>max_dur*1.5: continue
        cycles.append({"dur":hi-li,"swing":float(highs[hi]-lows[li])})
    if not cycles:
        base=TRADE_TYPES[trade_type]["max_min"]//2
        return {"eta":base,"lo_t":add_min(max(3,base//2)),"hi_t":add_min(base*2),
                "time":add_min(base),"n":0,"avg_swing":300}
    recent=cycles[-4:]
    avg=np.mean([c["dur"] for c in recent])
    std=np.std([c["dur"] for c in recent]) if len(recent)>1 else 8
    # RSI predikce
    rsi_target=62; rsi_gap=max(0,rsi_target-rsi_cur)
    eta_rsi=rsi_gap/max(CALIB["avg_rsi_rate_per_h"]/60,0.05)
    eta=round(0.5*avg+0.5*eta_rsi,0)
    lo=max(3,eta-std-CALIB["timing_err_min"]//2)
    hi_t=eta+std+CALIB["timing_err_min"]//2
    return {"eta":eta,"lo_t":add_min(lo),"hi_t":add_min(hi_t),"time":add_min(eta),
            "n":len(cycles),"avg_swing":round(np.mean([c["swing"] for c in recent]),0)}

# ── SELF-VALIDACE + BACKTEST ──────────────────────────────────────────────────
def backtest_validate(closes, highs, lows, volumes, timestamps):
    """
    Projde historická data, identifikuje cykly, zpětně ověří:
    1. Zda signální podmínky byly splněny <= 5 min před dnem
    2. Přesnost Fib 100% predikce vrcholu
    3. Přesnost timing predikce
    4. Zda momentum skóre korelovalo s výsledkem
    5. Kde signál CHYBĚL (missed trades) a proč
    """
    n=min(len(closes),len(highs),len(lows))
    closes=closes[-n:]; highs=highs[-n:]; lows=lows[-n:]; volumes=volumes[:n]

    # Výpočet indikátorů pro celou historii
    s=pd.Series(closes,dtype=float)
    d=s.diff(); g=d.clip(lower=0); lg=-d.clip(upper=0)
    ag=g.ewm(alpha=1/14,adjust=False).mean(); al=lg.ewm(alpha=1/14,adjust=False).mean()
    rsi_all=100-100/(1+ag/(al+1e-10))
    ef=s.ewm(span=12,adjust=False).mean(); es=s.ewm(span=26,adjust=False).mean()
    mh_all=ef-es-((ef-es).ewm(span=9,adjust=False).mean())
    mn=rsi_all.rolling(14).min(); mx=rsi_all.rolling(14).max()
    K_all=((rsi_all-mn)/(mx-mn+1e-10)*100).rolling(3).mean()
    D_all=K_all.rolling(3).mean()
    vol_s=pd.Series(volumes,dtype=float)
    vol_r_all=vol_s/(vol_s.rolling(20).mean()+1e-10)
    roc5_all=(s-s.shift(5))/s.shift(5)*100

    # Lokální dna a vrcholy
    lows_i=[]; highs_i=[]
    for i in range(15,n-15):
        if lows[i]==min(lows[i-15:i+16]): lows_i.append(i)
        if highs[i]==max(highs[i-15:i+16]): highs_i.append(i)

    results=[]; missed_trades=[]
    for li in lows_i:
        fh=[h for h in highs_i if h>li]
        if not fh: continue
        hi=fh[0]
        if hi-li>200: continue
        lo_p=lows[li]; hi_p=highs[hi]; sw=hi_p-lo_p; dur=hi-li
        lo_t=timestamps[li] if li<len(timestamps) else "?"
        hi_t=timestamps[hi] if hi<len(timestamps) else "?"

        # Indikátory v době dna
        rsi_lo=float(rsi_all.iloc[li]); sk_lo=float(K_all.iloc[li])
        mh_lo=float(mh_all.iloc[li]); mh_lo_p=float(mh_all.iloc[li-1]) if li>0 else mh_lo
        vol_lo=float(vol_r_all.iloc[li]); roc_lo=float(roc5_all.iloc[li]) if li>=5 else 0

        # Momentum skóre v dně
        i1_sim={"sk":sk_lo,"mh":mh_lo,"mh_p":mh_lo_p,"rsi":rsi_lo,
                "roc5":roc_lo,"vol_ratio":vol_lo,"atr":30,
                "sk_cross_up": float(K_all.iloc[li-1] if li>0 else sk_lo) <= float(D_all.iloc[li-1] if li>0 else sk_lo) and sk_lo >= float(D_all.iloc[li])}
        i_dummy={"sk":50,"rsi":50,"mh":0,"mh_p":0,"e9":lo_p,"e21":lo_p,"vol_ratio":1,"atr":30}
        mom_score_lo,_=momentum_score(i1_sim,i_dummy,i_dummy)

        # Signál podmínky v době dna
        signal_conditions={
            "RSI ≤40": rsi_lo<=40,
            "SK ≤25":  sk_lo<=25,
            "MH otáčí": mh_lo>mh_lo_p,
            "Moment≥40": mom_score_lo>=40,
        }
        signal_met=sum(signal_conditions.values())
        signal_ok=signal_met>=3

        # Fib predikce vrcholu (z předchozího cyklu)
        prev_lows=[l for l in lows_i if l<li]
        prev_highs=[h for h in highs_i if prev_lows and h<li and h>prev_lows[-1]]
        fib_pred=0; fib_err=None
        if prev_lows and prev_highs:
            prev_sw=highs[prev_highs[-1]]-lows[prev_lows[-1]]
            fib_pred=round(lo_p+prev_sw*1.0,0)
            fib_err=round(hi_p-fib_pred,0)

        # Timing predikce (klouzavý průměr posledních 3 cyklů)
        prev_cycles=[r for r in results[-3:] if r]
        pred_dur=round(np.mean([r["dur"] for r in prev_cycles]),0) if prev_cycles else CALIB["avg_scalp_dur_min"]
        timing_err=round(abs(dur-pred_dur),0)

        entry={
            "lo_t": lo_t[11:16] if len(lo_t)>11 else lo_t,
            "hi_t": hi_t[11:16] if len(hi_t)>11 else hi_t,
            "lo_p": round(lo_p,0), "hi_p": round(hi_p,0),
            "swing": round(sw,0), "dur": dur,
            "rsi_lo": round(rsi_lo,1), "sk_lo": round(sk_lo,1),
            "mom": round(mom_score_lo,1),
            "signal_ok": signal_ok, "signal_met": signal_met,
            "fib_pred": fib_pred, "fib_err": fib_err,
            "pred_dur": pred_dur, "timing_err": timing_err,
        }
        results.append(entry)

        # Missed trade: cyklus s gain>=300 ale signál nebyl ok
        if sw>=300 and not signal_ok:
            missed_trades.append({
                "time": lo_t[11:16] if len(lo_t)>11 else lo_t,
                "swing": round(sw,0), "dur": dur,
                "rsi": round(rsi_lo,1), "sk": round(sk_lo,1),
                "mom": round(mom_score_lo,1),
                "missing": [k for k,v in signal_conditions.items() if not v],
                "signal_met": signal_met,
            })

    if not results: return {"ok":False,"n":0,"missed":[]}

    n_ok=sum(1 for r in results if r["signal_ok"])
    n_300=sum(1 for r in results if r["swing"]>=300)
    n_500=sum(1 for r in results if r["swing"]>=500)
    timing_errs=[r["timing_err"] for r in results if r["timing_err"] is not None]
    fib_errs=[abs(r["fib_err"]) for r in results if r["fib_err"] is not None]
    mom_vals=[r["mom"] for r in results]
    swing_vals=[r["swing"] for r in results]
    mom_corr=round(float(pd.Series(mom_vals).corr(pd.Series(swing_vals))),3) if len(results)>=3 else 0

    return {
        "ok": True,
        "n_cycles": len(results),
        "n_signal_ok": n_ok,
        "signal_catch_rate": round(n_ok/len(results)*100,0),
        "n_swing_300": n_300,
        "n_swing_500": n_500,
        "timing_err_avg": round(np.mean(timing_errs),1) if timing_errs else 0,
        "fib_err_avg": round(np.mean(fib_errs),0) if fib_errs else 0,
        "momentum_corr": mom_corr,
        "tp_hit_pct": {
            tt_v["label"]: round(sum(1 for r in results if r["swing"]>=tt_v["tp"])/len(results)*100,0)
            for tt_v in TRADE_TYPES.values()},
        "missed_trades": missed_trades,
        "last_cycles": results[-5:],
    }

# ── PDH/PDL ───────────────────────────────────────────────────────────────────
def get_pdh_pdl(c4h, h4h, l4h, t4h, o4h):
    daily=defaultdict(lambda:{"hi":0,"lo":999999,"cl":0,"op":0})
    for ts_s,hi,lo,cl,op in zip(t4h,h4h,l4h,c4h,o4h):
        d=ts_s[:10]
        if daily[d]["op"]==0: daily[d]["op"]=op
        daily[d]["hi"]=max(daily[d]["hi"],hi); daily[d]["lo"]=min(daily[d]["lo"],lo)
        daily[d]["cl"]=cl
    days=sorted(daily.keys())
    pdh=daily[days[-2]]["hi"] if len(days)>=2 else 0
    pdl=daily[days[-2]]["lo"] if len(days)>=2 else 0
    pdc=daily[days[-2]]["cl"] if len(days)>=2 else 0
    wo=next((o4h[i] for i,ts_s in enumerate(t4h)
             if datetime.strptime(ts_s,"%Y-%m-%d %H:%M").weekday()==0),0)
    return pdh,pdl,pdc,wo

def local_extrema(lows,highs,window=12):
    n=len(lows); lo_i=lo_p=hi_i=hi_p=None
    for i in range(n-1,window-1,-1):
        if i+window>=n: continue
        if lows[i]==min(lows[i-window:i+window+1]): lo_i=i; lo_p=lows[i]; break
    n2=len(highs)
    for i in range(n2-1,window-1,-1):
        if i+window>=n2: continue
        if highs[i]==max(highs[i-window:i+window+1]): hi_i=i; hi_p=highs[i]; break
    return lo_i,lo_p,hi_i,hi_p

# ── HLAVNÍ FUNKCE ─────────────────────────────────────────────────────────────
def run(equity=10000, risk_pct=1.0, do_validate=True, backtest_only=False):
    SEP="─"*66; now=now_local()
    print(f"\n{'═'*66}")
    print(f"  btc_ticker.py v{VERSION}  [{now} SEČ]  {SYMBOL}")
    print('═'*66)

    # Data
    print("→ Stahuji data...", end=" ", flush=True)
    c1,h1,l1,t1,vw1,o1,vol1 = klines("1m", 10080)
    c3,h3,l3,t3,_,o3,vol3   = klines("3m", 3360)
    c5,h5,l5,t5,_,o5,vol5   = klines("5m", 4032)
    c15,h15,l15,t15,_,_,_   = klines("15m",2880)
    c1h,h1h,l1h,t1h,_,_,_   = klines("1h", 2160)
    c4h,h4h,l4h,t4h,_,o4h,_ = klines("4h", 1080)
    ob=orderbook()
    print("OK")

    if not c1: print("  [!] Žádná data — zkontroluj připojení"); return

    # Indikátory
    i1=calc_indicators(c1,h1,l1,vol1)
    i3=calc_indicators(c3,h3,l3,vol3)
    i5=calc_indicators(c5,h5,l5,vol5)
    i15=calc_indicators(c15,h15,l15)
    i1h=calc_indicators(c1h,h1h,l1h)

    price=c1[-1]; vwap=vw1[-1]; vdiff=price-vwap
    now_dt=datetime.now(TZ_PRAGUE); hour=now_dt.hour; wday=now_dt.weekday()
    sess=get_session(hour,wday)

    # PDH/PDL
    pdh,pdl,pdc,wo=get_pdh_pdl(c4h,h4h,l4h,t4h,o4h)

    # Camarilla
    cam=camarilla(pdh,pdl,pdc) if pdh>0 else {}

    # Fibonacci
    lo_i,lo_p,hi_i,hi_p=local_extrema(l1,h1,15)
    bars_lo=len(l1)-1-lo_i if lo_i else 999
    bars_hi=len(h1)-1-hi_i if hi_i else 999
    fr=fib_ret(lo_p,hi_p) if (lo_p and hi_p) else {}
    # Ext z předchozího cyklu
    lo_i2,lo_p2,hi_i2,hi_p2=local_extrema(l1[:max(1,(lo_i or len(l1)))-1],
                                            h1[:max(1,(lo_i or len(h1)))-1],15)
    fe=fib_ext(lo_p2,hi_p2,lo_p) if (lo_p2 and hi_p2 and lo_p) else {}
    fch=fib_channel(c1,60)
    gann=gann_sq9(price,4)

    # Momentum
    mom,mom_c=momentum_score(i1,i3,i5)

    # Klasifikace
    trade=classify_trade(i1,i3,i5,i15,i1h)

    # Position sizing
    ps=position_size(equity,price,trade["type"],mom,sess["mult"],wday>=5,risk_pct)

    # Predikce
    pred=predict_timing(c1,l1,h1,i1["rsi"],trade["type"])

    # Self-validace / backtest
    val={}
    if do_validate:
        print("→ Backtest validace...", end=" ", flush=True)
        val=backtest_validate(c1,h1,l1,vol1,t1)
        print(f"OK  ({val.get('n_cycles',0)} cyklů)")

    # Signály
    l_conds=[20<=i1["rsi"]<=58, i1["sk"]<=45, i1["mh"]>i1["mh_p"],
             i5["rsi"]<68, i5["sk"]<55, i5["mh"]>i5["mh_p"] or i5["mh"]>-10,
             i15["bull"], 25<i15["rsi"]<68, i1h["e9"]>=i1h["e21"]*0.995]
    s_conds=[62<=i1["rsi"]<=82, i1["sk"]>=65, i1["mh"]<i1["mh_p"],
             i5["rsi"]>55, i5["sk"]>55, i5["mh"]<i5["mh_p"] or i5["mh"]<10,
             not i15["bull"], 45<i15["rsi"]<75, i1h["rsi"]<70]
    l_sc=sum(l_conds); s_sc=sum(s_conds)
    sl_ok=i1["sk"]<=10 and i1["rsi"]<=35 and i1["mh"]>i1["mh_p"] and i5["rsi"]<55
    ss_ok=i1["sk"]>=90 and i1["rsi"]>=65 and i1["mh"]<i1["mh_p"] and i5["rsi"]>55
    l_sig=l_sc>=6 or sl_ok; s_sig=s_sc>=6 or ss_ok

    support=min(l1[-60:]); resist=max(h1[-60:])

    # ═══ VÝSTUP ═════════════════════════════════════════════════════════════
    if sl_ok or (l_sig and l_sc>=7) or ss_ok or (s_sig and s_sc>=7):
        print(f"\n  {'!'*30}")
        if sl_ok or l_sc>=7: print(f"  *** LONG  L:{l_sc}/9{'  ⚡SCALP' if sl_ok else ''} ***")
        if ss_ok or s_sc>=7: print(f"  *** SHORT S:{s_sc}/9{'  ⚡SCALP' if ss_ok else ''} ***")
        print(f"  {'!'*30}")
    else:
        print(f"\n{SEP}")

    print(f"  {price:,.2f}  VWAP {vwap:,.2f} ({'+' if vdiff>=0 else ''}{vdiff:.0f})")
    print(f"  {sess['name']} {sess['mult']}×  |  L:{l_sc}/9  S:{s_sc}/9  |  OB {ob['imb']*100:.0f}%")
    print(SEP)

    # Indikátory
    for tf_n,ii in [("1M",i1),("3M",i3),("5M",i5),("15M",i15),("1H",i1h)]:
        rf="!" if ii["rsi"]<25 or ii["rsi"]>75 else " "
        sf="!" if ii["sk"]<10 or ii["sk"]>90 else " "
        tr="B↑" if ii["bull"] else "B↓"
        mha="↑" if ii["mh"]>ii["mh_p"] else "↓"
        print(f"  {tf_n:<4} RSI{rf}{ii['rsi']:>5.1f}  SK{sf}{ii['sk']:>5.1f}/{ii['sd']:>4.1f}  MH{mha}{ii['mh']:>+6.1f}  {tr}")
    print(f"  OB  bid {ob['bid_btc']:.3f}/ask {ob['ask_btc']:.3f}  spread {ob['spread']:.2f}")
    if ob["bid_wall"][0]>0:
        print(f"  Walls: bid {ob['bid_wall'][0]:,.0f}({ob['bid_wall'][1]:.3f})  ask {ob['ask_wall'][0]:,.0f}({ob['ask_wall'][1]:.3f})")
    print(SEP)

    # Momentum
    bar=("▓"*int(mom/5)).ljust(20)
    lvl="SILNÝ" if mom>70 else "STŘEDNÍ" if mom>45 else "SLABÝ" if mom>25 else "NÍZKÝ"
    print(f"  MOMENTUM {mom:.1f}/100  [{bar}] {lvl}")
    for k,v in mom_c.items():
        b=("█"*int(v/10)).ljust(10)
        print(f"    {k:<12} {v:>5.1f}  {b}")

    # Obchod + pozice
    print(SEP)
    print(f"  TYP: {trade['label'].upper()}  ({trade['confidence']}, skóre {trade['best_score']})")
    scores_str = ', '.join(f"{TRADE_TYPES[k]['label']}:{v}" for k,v in trade['scores'].items())
    print(f"  Typy skóre: {scores_str}")
    print(f"  POZICE  portfolio {equity:,} USDC  risk {risk_pct}%")
    print(f"  BTC {ps['btc']}  notional {ps['notional']:,}  leverage {ps['leverage']}×")
    print(f"  Risk {ps['risk']} USDC ({ps['risk_pct']}%)  Cíl {ps['target']} USDC{'  ⚠min' if ps['min_enforced'] else ''}")
    print(f"  TP {ps['tp_price']:,.0f}  SL {ps['sl_price']:,.0f}  BE {ps['be_price']:,.0f}")
    if wday>=5: print(f"  ⚠ VÍKEND — pozice {sess['mult']*100:.0f}%")
    print(SEP)

    # Klíčové úrovně
    if pdh: print(f"  PDH {pdh:,.0f}  PDL {pdl:,.0f}  PDC {pdc:,.0f}  WO {wo:,.0f}")
    if lo_p: print(f"  Dno {lo_p:,.0f}({bars_lo}min)  Vrchol {hi_p:,.0f}({bars_hi}min)")
    if cam:
        near=[f"{k}={v:,.0f}" for k,v in cam.items() if abs(price-v)<100]
        print(f"  CAM: {', '.join(near) if near else '—'}   S1={cam.get('S1',0):,.0f} S2={cam.get('S2',0):,.0f} R1={cam.get('R1',0):,.0f} R2={cam.get('R2',0):,.0f}")
    if fr:
        near_f=[f"{k}={v:,.0f}" for k,v in fr.items() if abs(price-v)<60]
        print(f"  FIB ret: {', '.join(near_f) if near_f else '—'}")
    if fe: print(f"  FIB ext: 100%={fe.get('100%',0):,.0f}  127%={fe.get('127.2%',0):,.0f}  161%={fe.get('161.8%',0):,.0f}")
    print(f"  FIB ch: {fch['dir']} {fch['slope']:+.1f}  up={fch['up2']:,.0f}  mid={fch['mid']:,.0f}  lo={fch['lo2']:,.0f}")
    print(f"  GANN R: {'/'.join(f'{v:,.0f}' for v in gann['r'][:3])}  S: {'/'.join(f'{v:,.0f}' for v in gann['s'][:3])}")
    print(SEP)

    # Predikce
    print(f"  ETA ({trade['label']}): ~{pred['eta']:.0f}min → {pred['time']} SEČ  ({pred['lo_t']}–{pred['hi_t']})")
    if pred.get("avg_swing"): print(f"  Avg swing: {pred['avg_swing']} USDC  ({pred['n']} cyklů)")
    print(SEP)

    # ── KONTROLNÍ MECHANISMUS: CO CHYBÍ ──────────────────────────────────────
    l_met,l_missing=gap_to_signal(i1,i3,i5,i15,i1h,"LONG")
    s_met,s_missing=gap_to_signal(i1,i3,i5,i15,i1h,"SHORT")

    if l_sig or sl_ok:
        el=round(support+50,-1)
        print(f"  🟢 LONG{' ⚡' if sl_ok else ''}  {l_sc}/9  vstup ~{el:,.0f}")
        l_names=["1M RSI","1M SK","1M MH↑","5M RSI","5M SK","5M MH","15M trend","15M RSI","1H bull"]
        ok_names=[l_names[i] for i,ok in enumerate(l_conds) if ok]
        print(f"  OK: {', '.join(ok_names)}")
        for tt_k,tt_v in TRADE_TYPES.items():
            p=tp_prob(tt_v["tp"],"LONG")
            print(f"    {tt_v['label']:12s} TP {el+tt_v['tp']:,.0f}(+{tt_v['tp']}) SL {el-tt_v['sl']:,.0f}(-{tt_v['sl']}) BE+{tt_v['be']}  1:{tt_v['rr']}  P={p}%  max {tt_v['max_min']}min")
    elif l_sc>=4:
        print(f"\n  🟡 LONG BLÍZKO  {l_sc}/9  — chybí {len(l_missing)} podmínek:")
        for m in l_missing:
            print(f"    {m['name']:<18} nyní {m['current']:>8.2f}  →  cíl {m['target']:>8.2f}  "
                  f"(diff {m['diff']:>6.2f}  ETA ~{m['eta_min']}min ≈{m['eta_time']})")
    else:
        print(f"\n  ⏸ LONG {l_sc}/9  — chybí:")
        for m in sorted(l_missing, key=lambda x: x["eta_min"])[:5]:
            print(f"    {m['name']:<18} nyní {m['current']:>8.2f}  →  cíl {m['target']:>8.2f}  "
                  f"(diff {m['diff']:>6.2f}  ETA ~{m['eta_min']}min ≈{m['eta_time']})")

    if s_sig or ss_ok:
        es=round(resist-50,-1)
        print(f"\n  🔴 SHORT{' ⚡' if ss_ok else ''}  {s_sc}/9  vstup ~{es:,.0f}")
        s_names_a=["1M RSI","1M SK","1M MH↓","5M RSI","5M SK","5M MH↓","15M bear","15M RSI","1H RSI"]
        ok_names=[s_names_a[i] for i,ok in enumerate(s_conds) if ok]
        print(f"  OK: {', '.join(ok_names)}")
        for tt_k,tt_v in TRADE_TYPES.items():
            p=tp_prob(tt_v["tp"],"SHORT")
            print(f"    {tt_v['label']:12s} TP {es-tt_v['tp']:,.0f}(-{tt_v['tp']}) SL {es+tt_v['sl']:,.0f}(+{tt_v['sl']}) BE-{tt_v['be']}  1:{tt_v['rr']}  P={p}%  max {tt_v['max_min']}min")
    elif s_sc>=4:
        print(f"\n  🟡 SHORT BLÍZKO {s_sc}/9  — chybí:")
        for m in s_missing:
            print(f"    {m['name']:<18} nyní {m['current']:>8.2f}  →  cíl {m['target']:>8.2f}  "
                  f"(diff {m['diff']:>6.2f}  ETA ~{m['eta_min']}min ≈{m['eta_time']})")
    print(SEP)

    # ── BACKTEST VÝSLEDKY ──────────────────────────────────────────────────────
    if val.get("ok"):
        print(f"  BACKTEST  {val['n_cycles']} cyklů v datech")
        print(f"  Signál zachytil:  {val['signal_catch_rate']:.0f}% cyklů  ({val['n_signal_ok']}/{val['n_cycles']})")
        print(f"  Cykly 300+ USDC:  {val['n_swing_300']}  |  500+: {val['n_swing_500']}")
        print(f"  Timing ±: {val['timing_err_avg']} min  |  Fib 100% ±: {val['fib_err_avg']} USDC")
        print(f"  Momentum korelace se swingem: {val['momentum_corr']}")
        print(f"  TP hit rates: {' | '.join(f'{k}: {v:.0f}%' for k,v in val['tp_hit_pct'].items())}")

        if val["missed_trades"]:
            print(f"\n  MISSED TRADES ({len(val['missed_trades'])} cyklů s 300+ USDC kde signál chyběl):")
            for mt in val["missed_trades"]:
                print(f"    {mt['time']}  +{mt['swing']} USDC  {mt['dur']}min  "
                      f"RSI={mt['rsi']} SK={mt['sk']} mom={mt['mom']}")
                print(f"      Chybělo: {', '.join(mt['missing'])}")

        if val["last_cycles"]:
            print(f"\n  Posledních {len(val['last_cycles'])} cyklů:")
            for c in val["last_cycles"]:
                sig="✅" if c["signal_ok"] else f"❌({c['signal_met']}/4)"
                fib=f"Fib±{c['fib_err']:+.0f}" if c["fib_err"] is not None else ""
                print(f"    {c['lo_t']}→{c['hi_t']}  +{c['swing']} USDC  {c['dur']}min  "
                      f"RSI={c['rsi_lo']} SK={c['sk_lo']} mom={c['mom']} {sig} {fib}")
    print(SEP)

    # Kontext
    if pdh:
        for cond,msg in [(abs(price-pdh)<150,f"Blízko PDH {pdh:.0f}→odpor"),
                         (abs(price-pdl)<150,f"Blízko PDL {pdl:.0f}→podpora"),
                         (abs(price-pdc)<100,f"Blízko PDC {pdc:.0f}→mean rev"),
                         (wo and abs(price-wo)<200,f"Blízko WO {wo:.0f}→gravitace")]:
            if cond: print(f"  ℹ  {msg}")

    # Uložit
    ts=datetime.now(TZ_PRAGUE).strftime("%Y%m%d_%H%M%S")
    report={"version":VERSION,"time":now,"price":price,"vwap":round(vwap,2),
            "session":sess["name"],"long_score":l_sc,"short_score":s_sc,
            "long_signal":l_sig,"short_signal":s_sig,"momentum":mom,
            "trade_type":trade["type"],"position":ps,
            "ob_imb":ob["imb"],
            "levels":{"pdh":pdh,"pdl":pdl,"pdc":pdc,"weekly_open":wo},
            "camarilla":{k:round(v,2) for k,v in cam.items()} if cam else {},
            "fib_ext":{k:round(v,2) for k,v in fe.items()},
            "gann":gann,"prediction":pred,"validation":val,
            "gap_to_long":[{"name":m["name"],"eta_min":m["eta_min"],"eta_time":m["eta_time"]} for m in l_missing],
            "gap_to_short":[{"name":m["name"],"eta_min":m["eta_min"],"eta_time":m["eta_time"]} for m in s_missing],
            "ind":{"1m":{"rsi":round(i1["rsi"],1),"sk":round(i1["sk"],1),"mh":round(i1["mh"],1)},
                   "3m":{"rsi":round(i3["rsi"],1),"sk":round(i3["sk"],1)},
                   "5m":{"rsi":round(i5["rsi"],1),"sk":round(i5["sk"],1)},
                   "15m":{"rsi":round(i15["rsi"],1),"sk":round(i15["sk"],1)},
                   "1h":{"rsi":round(i1h["rsi"],1),"sk":round(i1h["sk"],1)}}}
    with open("data/live_report.json","w") as f: json.dump(report,f,indent=2)
    with open(f"{LOG_DIR}/ticker_{ts}.json","w") as f: json.dump(report,f,indent=2)
    print(f"  → data/live_report.json  |  logs/{ts}")
    print('═'*66)
    return report

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=f"btc_ticker v{VERSION}")
    parser.add_argument("--loop",type=int,default=0,help="Refresh v sekundách")
    parser.add_argument("--symbol",default=SYMBOL)
    parser.add_argument("--equity",type=float,default=10000)
    parser.add_argument("--risk",type=float,default=1.0)
    parser.add_argument("--no-validate",action="store_true")
    parser.add_argument("--backtest",action="store_true",help="Pouze backtest")
    args=parser.parse_args()
    SYMBOL=args.symbol; do_val=not args.no_validate

    if args.loop>0:
        print(f"Watch {SYMBOL} {args.loop}s  equity={args.equity} risk={args.risk}% (Ctrl+C)")
        try:
            while True:
                r=run(args.equity,args.risk,do_val)
                if r and (r.get("long_signal") or r.get("short_signal")):
                    print(f"  *** SIGNÁL L:{r.get('long_score')}/9 S:{r.get('short_score')}/9 mom:{r.get('momentum')} ***")
                time.sleep(args.loop)
        except KeyboardInterrupt: print("\nUkončeno.")
    else:
        run(args.equity, args.risk, do_val)
