"""
BTC/USDC – Live Multi-Timeframe Analyzer v3
============================================
Funkce:
  • Scalping (300+ USDC, <=60 min)  +  Micro-swing (500+ USDC)
  • LONG i SHORT signály s TP pravděpodobnostmi
  • PDH / PDL / PDC / Weekly Open jako S/R magnety
  • Seance filtr (Asie / Evropa / Amerika / Noc)
  • Víkendový efekt (snížená velikost pozice)
  • VWAP denní reset od 00:00 UTC
  • Automatická paginace Binance API

INSTALACE:  pip install requests pandas numpy
POUŽITÍ:
  python btc_live.py              # jednorázová analýza
  python btc_live.py --watch 60   # každých 60 sekund
"""

import requests, pandas as pd, numpy as np
import json, os, time, argparse
from datetime import datetime
from zoneinfo import ZoneInfo

TZ_PRAGUE = ZoneInfo("Europe/Prague")

def now_local():
    return datetime.now(TZ_PRAGUE).strftime("%H:%M:%S")

def ts_ms_to_local(ms):
    return datetime.fromtimestamp(ms / 1000, tz=TZ_PRAGUE).replace(tzinfo=None)

SYMBOL    = "BTCUSDC"
BASE_URL  = "https://api.binance.com"
DATA_DIR  = "data"
SCORE_MIN = 6

TF_CANDLES = {
    "1m":  10080,
    "5m":  4032,
    "15m": 2880,
    "1h":  2160,
    "4h":  1080,
}

SCALP  = {"tp": 300, "sl": 100, "be": 120, "rr": 3.0,  "label": "Scalp"}
SWING  = {"tp": 500, "sl": 150, "be": 200, "rr": 3.33, "label": "Swing"}
SWING2 = {"tp": 750, "sl": 200, "be": 300, "rr": 3.75, "label": "Swing+"}

TP_PROBS_LONG  = {100:100,150:82,200:70,300:55,400:40,500:33,600:22,750:15,1000:8}
TP_PROBS_SHORT = {100:100,150:88,200:75,300:65,400:50,500:40,600:30,750:20,1000:10}

def tp_prob(tp, direction):
    probs = TP_PROBS_LONG if direction=="LONG" else TP_PROBS_SHORT
    keys = sorted(probs.keys())
    for k in keys:
        if tp <= k: return probs[k]
    return probs[keys[-1]]

def get_session(hour, weekday):
    if weekday >= 5:
        return {"name":"Víkend","vol_mult":0.4,"notes":"Falešné průlomy — opatrně"}
    if 15 <= hour < 17:
        return {"name":"Překryv EU/US","vol_mult":1.5,"notes":"Nejsilnější pohyby"}
    elif 2 <= hour < 9:
        return {"name":"Asie","vol_mult":0.7,"notes":"Nízká likvidita, menší pozice"}
    elif 9 <= hour < 17:
        return {"name":"Evropa","vol_mult":1.0,"notes":"Standardní"}
    elif 15 <= hour < 23:
        return {"name":"Amerika","vol_mult":1.3,"notes":"Vysoká likvidita, velké pohyby"}
    else:
        return {"name":"Noc","vol_mult":0.5,"notes":"Vyhni se"}

def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def rsi(s, n=14):
    d=s.diff(); g=d.clip(lower=0); l=-d.clip(upper=0)
    return 100-100/(1+g.ewm(alpha=1/n,adjust=False).mean()/(l.ewm(alpha=1/n,adjust=False).mean()+1e-10))

def macd(s, f=12, sl=26, sig=9):
    m=ema(s,f)-ema(s,sl); return m, ema(m,sig), m-ema(m,sig)

def stoch_rsi(s, rp=14, sp=14, k=3, d=3):
    r=rsi(s,rp); mn=r.rolling(sp).min(); mx=r.rolling(sp).max()
    K=((r-mn)/(mx-mn+1e-10)*100).rolling(k).mean(); return K, K.rolling(d).mean()

def add_indicators(df):
    df=df.copy()
    df["rsi"]=rsi(df["close"])
    df["macd"],df["macd_sig"],df["mh"]=macd(df["close"])
    df["sk"],df["sd"]=stoch_rsi(df["close"])
    df["e9"]=ema(df["close"],9); df["e21"]=ema(df["close"],21); df["e50"]=ema(df["close"],50)
    df["tp_p"]=(df["high"]+df["low"]+df["close"])/3
    df["date_utc"]=df["time"].dt.date
    parts=[]
    for _,g in df.groupby("date_utc"):
        g=g.copy()
        g["vwap"]=(g["tp_p"]*g["volume"]).cumsum()/g["volume"].cumsum()
        parts.append(g)
    return pd.concat(parts).sort_values("time").reset_index(drop=True)

def fetch_klines(symbol, interval, limit=300):
    cols=["open_time","open","high","low","close","volume","close_time","qvol","trades","tbb","tbq","ignore"]
    all_rows=[]; end_time=None; remaining=limit
    while remaining>0:
        batch=min(remaining,1000)
        params={"symbol":symbol,"interval":interval,"limit":batch}
        if end_time is not None: params["endTime"]=end_time
        r=requests.get(f"{BASE_URL}/api/v3/klines",params=params,timeout=15)
        r.raise_for_status()
        used=int(r.headers.get("X-MBX-USED-WEIGHT-1M",0))
        if used>900: print(f"  Váha {used}/1200 — čekám 60s"); time.sleep(60)
        data=r.json()
        if not data: break
        all_rows=data+all_rows; remaining-=len(data); end_time=data[0][0]-1
        if len(data)<batch: break
        time.sleep(0.12)
    if not all_rows: return pd.DataFrame(columns=["time","open","high","low","close","volume"])
    df=pd.DataFrame(all_rows,columns=cols)
    df["time"]=df["open_time"].apply(ts_ms_to_local)
    for c in ["open","high","low","close","volume"]: df[c]=df[c].astype(float)
    return df.drop_duplicates("open_time").sort_values("time").reset_index(drop=True)[["time","open","high","low","close","volume"]]

def fetch_orderbook(symbol, depth=20):
    r=requests.get(f"{BASE_URL}/api/v3/depth",params={"symbol":symbol,"limit":depth},timeout=10)
    r.raise_for_status(); raw=r.json()
    bids=[(float(p),float(q)) for p,q in raw["bids"]]
    asks=[(float(p),float(q)) for p,q in raw["asks"]]
    bv=sum(q for _,q in bids); av=sum(q for _,q in asks)
    bw=max(bids,key=lambda x:x[1]) if bids else (0,0)
    aw=max(asks,key=lambda x:x[1]) if asks else (0,0)
    return {"time":now_local(),"bid":bids[0][0] if bids else 0,"ask":asks[0][0] if asks else 0,
            "spread":round(asks[0][0]-bids[0][0],2) if (bids and asks) else 0,
            "imb":round(bv/(bv+av),4) if (bv+av)>0 else 0.5,
            "bid_btc":round(bv,4),"ask_btc":round(av,4),
            "bid_wall":{"price":bw[0],"qty":round(bw[1],4)},
            "ask_wall":{"price":aw[0],"qty":round(aw[1],4)}}

def get_key_levels(df4h):
    df=df4h.copy(); df["date"]=df["time"].dt.date
    daily=df.groupby("date").agg(hi=("high","max"),lo=("low","min"),cl=("close","last"),op=("open","first")).reset_index()
    pdh=pdl=pdc=pdo=wo=0
    if len(daily)>=2:
        p=daily.iloc[-2]; pdh=float(p["hi"]); pdl=float(p["lo"]); pdc=float(p["cl"]); pdo=float(p["op"])
    df["weekday"]=df["time"].dt.weekday
    mon=df[df["weekday"]==0]
    wo=float(mon["open"].iloc[-1]) if len(mon)>0 else 0
    today=df["date"].max(); td=df[df["date"]==today]
    today_hi=float(td["high"].max()) if len(td)>0 else 0
    today_lo=float(td["low"].min()) if len(td)>0 else 0
    return {"pdh":round(pdh,2),"pdl":round(pdl,2),"pdc":round(pdc,2),"pdo":round(pdo,2),
            "weekly_open":round(wo,2),"today_hi":round(today_hi,2),"today_lo":round(today_lo,2)}

def find_extrema(df, window=15):
    lows=[]; highs=[]
    for i in range(window,len(df)-window):
        if df["low"].iloc[i]==df["low"].iloc[i-window:i+window+1].min(): lows.append(i)
        if df["high"].iloc[i]==df["high"].iloc[i-window:i+window+1].max(): highs.append(i)
    return lows, highs

def long_score(snaps):
    s1=snaps["1m"]; p1=snaps["1m_prev"]; s5=snaps["5m"]; p5=snaps["5m_prev"]
    s15=snaps["15m"]; s1h=snaps["1h"]
    conds={"1M RSI 20-58":20<=s1["rsi"]<=58,"1M SK ≤45":s1["sk"]<=45,
           "1M MH roste":s1["mh"]>p1["mh"],"5M RSI <68":s5["rsi"]<68,
           "5M SK <55":s5["sk"]<55,"5M MH ok":s5["mh"]>p5["mh"] or s5["mh"]>-10,
           "15M trend":s15["e9"]>=s15["e21"]*0.998,"15M RSI 25-68":25<s15["rsi"]<68,
           "1H bull":s1h["e9"]>=s1h["e21"]*0.995}
    return sum(conds.values()), [k for k,v in conds.items() if v], [k for k,v in conds.items() if not v]

def short_score(snaps):
    s1=snaps["1m"]; p1=snaps["1m_prev"]; s5=snaps["5m"]; p5=snaps["5m_prev"]
    s15=snaps["15m"]; s1h=snaps["1h"]
    conds={"1M RSI 62-82":62<=s1["rsi"]<=82,"1M SK ≥65":s1["sk"]>=65,
           "1M MH klesá":s1["mh"]<p1["mh"],"5M RSI >55":s5["rsi"]>55,
           "5M SK >55":s5["sk"]>55,"5M MH klesá":s5["mh"]<p5["mh"] or s5["mh"]<10,
           "15M trend":s15["e9"]<=s15["e21"]*1.002,"15M RSI 45-75":45<s15["rsi"]<75,
           "1H RSI <70":s1h["rsi"]<70}
    return sum(conds.values()), [k for k,v in conds.items() if v], [k for k,v in conds.items() if not v]

def scalp_long(snaps):
    s1=snaps["1m"]; p1=snaps["1m_prev"]; s5=snaps["5m"]
    ok=s1["sk"]<=10 and s1["rsi"]<=35 and s1["mh"]>p1["mh"] and s5["rsi"]<55
    return ok, "SK1M≤10 + RSI1M≤35 + MH obrací" if ok else ""

def scalp_short(snaps):
    s1=snaps["1m"]; p1=snaps["1m_prev"]; s5=snaps["5m"]
    ok=s1["sk"]>=90 and s1["rsi"]>=65 and s1["mh"]<p1["mh"] and s5["rsi"]>55
    return ok, "SK1M≥90 + RSI1M≥65 + MH obrací" if ok else ""

def analyze():
    now=now_local(); SEP="═"*62; sep="─"*62
    print(f"\n{SEP}\n  BTC/USDC Analyzer v3  [{now} SEČ]\n{SEP}")

    print("→ Stahuji data...", end=" ", flush=True)
    dfs={}
    for tf,n in TF_CANDLES.items():
        try:
            d=fetch_klines(SYMBOL,tf,n); d=add_indicators(d)
            dfs[tf]=d; d.to_csv(f"{DATA_DIR}/btc_{tf}.csv",index=False); time.sleep(0.1)
        except Exception as e: print(f"\n  [!] {tf}: {e}")
    try:
        ob=fetch_orderbook(SYMBOL,20)
        with open(f"{DATA_DIR}/orderbook.json","w") as f: json.dump(ob,f,indent=2)
    except Exception as e:
        ob={"imb":0.5,"bid_btc":0,"ask_btc":0,"bid":0,"ask":0,"spread":0,"bid_wall":{},"ask_wall":{}}
    print("OK")

    def s2(tf): d=dfs[tf]; return d.iloc[-1].to_dict(), d.iloc[-2].to_dict()
    r1m,p1m=s2("1m"); r5m,p5m=s2("5m"); r15,p15=s2("15m"); r1h,p1h=s2("1h"); r4h,_=s2("4h")
    snaps={"1m":r1m,"1m_prev":p1m,"5m":r5m,"5m_prev":p5m,"15m":r15,"1h":r1h}

    cur=float(r1m["close"]); vwap=float(r1m.get("vwap",0)); vdiff=cur-vwap
    now_dt=r1m["time"]; hour=pd.Timestamp(now_dt).hour; wday=pd.Timestamp(now_dt).weekday()
    sess=get_session(hour,wday)
    levels=get_key_levels(dfs["4h"])
    pdh=levels["pdh"]; pdl=levels["pdl"]; pdc=levels["pdc"]; wo=levels["weekly_open"]

    lows_i,highs_i=find_extrema(dfs["1m"],15)
    ll_i=lows_i[-1] if lows_i else len(dfs["1m"])-5
    lh_i=highs_i[-1] if highs_i else len(dfs["1m"])-5
    ll_p=float(dfs["1m"]["low"].iloc[ll_i]); lh_p=float(dfs["1m"]["high"].iloc[lh_i])
    ll_t=dfs["1m"]["time"].iloc[ll_i]; lh_t=dfs["1m"]["time"].iloc[lh_i]
    min_lo=(pd.Timestamp(now_dt)-pd.Timestamp(ll_t)).total_seconds()/60
    min_hi=(pd.Timestamp(now_dt)-pd.Timestamp(lh_t)).total_seconds()/60

    print(f"\n  Cena:    {cur:>10,.2f} USDC")
    print(f"  VWAP:    {vwap:>10,.2f} USDC  ({'+' if vdiff>=0 else ''}{vdiff:.0f}  {'↑ nad' if vdiff>=0 else '↓ pod'} VWAP)")
    print(f"  Čas:     {str(now_dt)[:16]} SEČ")
    print(f"  Seance:  {sess['name']}  (mult {sess['vol_mult']}×)  — {sess['notes']}")

    print(f"\n  {sep}")
    print(f"  KLÍČOVÉ ÚROVNĚ")
    print(f"  {sep}")
    for lbl,val,note in [
        ("Weekly Open",wo,f"{cur-wo:+.0f}"),
        ("PDH",pdh,f"{cur-pdh:+.0f}"),("PDC",pdc,f"{cur-pdc:+.0f}"),("PDL",pdl,f"{cur-pdl:+.0f}"),
        ("Dnes high",levels["today_hi"],f"{cur-levels['today_hi']:+.0f}"),
        ("Dnes low",levels["today_lo"],f"{cur-levels['today_lo']:+.0f}"),
        ("VWAP",vwap,f"{'+' if vdiff>=0 else ''}{vdiff:.0f}"),
        ("Posl. dno 1M",ll_p,f"{cur-ll_p:+.0f}  ({min_lo:.0f} min)"),
        ("Posl. vrchol 1M",lh_p,f"{cur-lh_p:+.0f}  ({min_hi:.0f} min)"),
    ]:
        m=" ←" if val>0 and abs(cur-val)<80 else ""
        print(f"  {lbl:<20} {val:>10,.0f}   {note}{m}")

    print(f"\n  {sep}")
    print(f"  {'TF':<5} {'RSI':>6} {'SK':>7} {'SD':>7} {'MH':>8}  {'Trend':<6}  VWAP diff")
    print(f"  {'-'*56}")
    for tf,r,p in [("1m",r1m,p1m),("5m",r5m,p5m),("15m",r15,p15),("1h",r1h,p1h),("4h",r4h,r4h)]:
        trend="BULL" if r["e9"]>r["e21"] else "BEAR"
        mha="↑" if r["mh"]>p["mh"] else "↓"
        vd=float(r.get("vwap",0)); vdt=float(r["close"])-vd if vd>0 else 0
        print(f"  {tf:<5} {r['rsi']:>6.1f} {r['sk']:>7.1f} {r['sd']:>7.1f} {r['mh']:>7.1f}{mha}  {trend:<6}  {vdt:>+9.0f}")

    imb_lbl=("SILNĚ BULL" if ob["imb"]>0.75 else "BULL" if ob["imb"]>0.55 else
             "NEUTRÁLNÍ" if ob["imb"]>0.45 else "BEAR" if ob["imb"]>0.25 else "SILNĚ BEAR")
    print(f"\n  OB: {ob['imb']*100:.1f}%  {imb_lbl}  | bid {ob['bid_btc']:.3f} / ask {ob['ask_btc']:.3f} BTC")
    if ob.get("bid_wall") and ob["bid_wall"].get("price",0)>0:
        print(f"  Bid wall: {ob['bid_wall']['price']:,.0f} ({ob['bid_wall']['qty']:.3f} BTC)  "
              f"Ask wall: {ob['ask_wall']['price']:,.0f} ({ob['ask_wall']['qty']:.3f} BTC)")

    l_sc,l_ok,l_miss=long_score(snaps)
    s_sc,s_ok,s_miss=short_score(snaps)
    sl_ok,sl_msg=scalp_long(snaps)
    ss_ok,ss_msg=scalp_short(snaps)

    print(f"\n  {sep}")
    print(f"  LONG skóre: {l_sc}/9   SHORT skóre: {s_sc}/9")

    support=float(dfs["1m"]["low"].tail(60).min())
    resist=float(dfs["1m"]["high"].tail(60).max())

    if l_sc>=SCORE_MIN or sl_ok:
        emoji="🟢" if l_sc>=7 else "🟡"
        print(f"\n  {emoji} LONG{'  (URGENTNÍ)' if sl_ok else ''}  skóre {l_sc}/9")
        if sl_ok: print(f"  ⚡ SCALP: {sl_msg}")
        entry_l=round(support+50,-1)
        for cfg in [SCALP,SWING,SWING2]:
            p=tp_prob(cfg["tp"],"LONG")
            print(f"    {cfg['label']:8s}  vstup {entry_l:,.0f}  "
                  f"TP {entry_l+cfg['tp']:,.0f}(+{cfg['tp']})  "
                  f"SL {entry_l-cfg['sl']:,.0f}(-{cfg['sl']})  "
                  f"BE +{cfg['be']}  R:R 1:{cfg['rr']}  P={p}%")
        if wday>=5: print(f"  ⚠  VÍKEND — pozice na {sess['vol_mult']*100:.0f}% std velikosti")

    if s_sc>=SCORE_MIN or ss_ok:
        emoji="🔴" if s_sc>=7 else "🟡"
        print(f"\n  {emoji} SHORT{'  (URGENTNÍ)' if ss_ok else ''}  skóre {s_sc}/9")
        if ss_ok: print(f"  ⚡ SCALP: {ss_msg}")
        entry_s=round(resist-50,-1)
        for cfg in [SCALP,SWING,SWING2]:
            p=tp_prob(cfg["tp"],"SHORT")
            print(f"    {cfg['label']:8s}  vstup {entry_s:,.0f}  "
                  f"TP {entry_s-cfg['tp']:,.0f}(-{cfg['tp']})  "
                  f"SL {entry_s+cfg['sl']:,.0f}(+{cfg['sl']})  "
                  f"BE -{cfg['be']}  R:R 1:{cfg['rr']}  P={p}%")

    if l_sc<SCORE_MIN and s_sc<SCORE_MIN and not sl_ok and not ss_ok:
        print(f"\n  ⏸  Bez signálu.  L chybí: {', '.join(l_miss[:3])}")

    print(f"\n  {sep}  KONTEXT")
    gap=cur-pdc
    tips=[
        (abs(cur-pdh)<150, f"Blízko PDH {pdh:.0f} → odpor, short bias"),
        (abs(cur-pdl)<150, f"Blízko PDL {pdl:.0f} → podpora, long bias"),
        (abs(cur-pdc)<100, f"Blízko PDC {pdc:.0f} → mean reversion zóna"),
        (abs(cur-wo)<200,  f"Blízko Weekly Open {wo:.0f} → gravitační úroveň"),
        (abs(gap)>200,     f"Gap od PDC: {gap:+.0f} USDC → gap fill k {pdc:.0f}"),
    ]
    for cond,msg in tips:
        if cond: print(f"  ℹ  {msg}")

    report={"time_sec":now,"price":cur,"vwap":round(vwap,2),"vwap_diff":round(vdiff,2),
            "session":sess["name"],"vol_mult":sess["vol_mult"],"weekend":wday>=5,
            "long_score":l_sc,"short_score":s_sc,
            "long_signal":l_sc>=SCORE_MIN or sl_ok,"short_signal":s_sc>=SCORE_MIN or ss_ok,
            "scalp_long":sl_ok,"scalp_short":ss_ok,"ob_imb":ob["imb"],
            "levels":levels,
            "tp_probs":{"long":{str(t):tp_prob(t,"LONG") for t in [150,300,500,750]},
                        "short":{str(t):tp_prob(t,"SHORT") for t in [150,300,500,750]}},
            "indicators":{"1m":{"rsi":round(r1m["rsi"],1),"sk":round(r1m["sk"],1),"mh":round(r1m["mh"],2)},
                          "5m":{"rsi":round(r5m["rsi"],1),"sk":round(r5m["sk"],1)},
                          "15m":{"rsi":round(r15["rsi"],1),"sk":round(r15["sk"],1)},
                          "1h":{"rsi":round(r1h["rsi"],1),"sk":round(r1h["sk"],1)}}}
    with open(f"{DATA_DIR}/live_report.json","w") as f: json.dump(report,f,indent=2)
    print(f"\n  Uloženo → {DATA_DIR}/live_report.json")
    print(SEP)
    return report

os.makedirs(DATA_DIR, exist_ok=True)

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--watch",type=int,default=0)
    parser.add_argument("--symbol",default=SYMBOL)
    args=parser.parse_args()
    SYMBOL=args.symbol
    if args.watch>0:
        print(f"Watch mode  {SYMBOL}  refresh {args.watch}s  (Ctrl+C ukončí)")
        try:
            while True:
                r=analyze()
                if r and (r.get("long_signal") or r.get("short_signal")):
                    print(f"  *** SIGNÁL L:{r['long_score']}/9  S:{r['short_score']}/9 ***")
                time.sleep(args.watch)
        except KeyboardInterrupt: print("\nUkončeno.")
    else:
        analyze()
