"""
BTC/USDC – Rychlý ticker v3
============================
Scalp + Swing LONG/SHORT signály, PDH/PDL/VWAP, seance filtr.
Výsledek za < 5 sekund.

POUŽITÍ:
  python btc_ticker.py            # jednorázový výpis
  python btc_ticker.py --loop 30  # každých 30 sekund
"""

import requests, pandas as pd, numpy as np, time, argparse, os, json
from datetime import datetime
from zoneinfo import ZoneInfo

TZ_PRAGUE = ZoneInfo("Europe/Prague")
SYMBOL    = "BTCUSDC"
API       = "https://api.binance.com"

def now_local(): return datetime.now(TZ_PRAGUE).strftime("%H:%M:%S")

def ts_to_local(ms):
    return datetime.fromtimestamp(ms/1000, tz=TZ_PRAGUE).strftime("%Y-%m-%d %H:%M")

def get(ep, params):
    return requests.get(f"{API}{ep}", params=params, timeout=10).json()

def klines(tf, n=200):
    """Stáhne n svíček s paginací. Vrací closes,highs,lows,times,opens,vols."""
    BATCH=1000; all_rows=[]; end_time=None; remaining=n
    while remaining>0:
        batch=min(remaining,BATCH)
        params={"symbol":SYMBOL,"interval":tf,"limit":batch}
        if end_time is not None: params["endTime"]=end_time
        raw=requests.get(f"{API}/api/v3/klines",params=params,timeout=15).json()
        if not raw: break
        all_rows=raw+all_rows; remaining-=len(raw); end_time=raw[0][0]-1
        if len(raw)<batch: break
        time.sleep(0.1)
    closes=[float(r[4]) for r in all_rows]; highs=[float(r[2]) for r in all_rows]
    lows=[float(r[3]) for r in all_rows]; opens=[float(r[1]) for r in all_rows]
    vols=[float(r[5]) for r in all_rows]; times=[ts_to_local(r[0]) for r in all_rows]
    # VWAP denní reset
    tss=[r[0] for r in all_rows]
    dates=[datetime.fromtimestamp(t/1000, tz=TZ_PRAGUE).date() for t in tss]
    tp=[(h+l+c)/3 for h,l,c in zip(highs,lows,closes)]
    vwap=[]; cum_tv=0; cum_v=0; cur_date=None
    for i,(d,t,v) in enumerate(zip(dates,tp,vols)):
        if d!=cur_date: cum_tv=0; cum_v=0; cur_date=d
        cum_tv+=t*v; cum_v+=v
        vwap.append(cum_tv/cum_v if cum_v>0 else closes[i])
    return closes,highs,lows,times,vwap,opens

def orderbook():
    ob=get("/api/v3/depth",{"symbol":SYMBOL,"limit":20})
    bids=[(float(p),float(q)) for p,q in ob["bids"]]
    asks=[(float(p),float(q)) for p,q in ob["asks"]]
    bv=sum(q for _,q in bids); av=sum(q for _,q in asks)
    bw=max(bids,key=lambda x:x[1]) if bids else (0,0)
    aw=max(asks,key=lambda x:x[1]) if asks else (0,0)
    return dict(bid=bids[0][0],ask=asks[0][0],spread=round(asks[0][0]-bids[0][0],2),
                imb=round(bv/(bv+av),4),bid_btc=round(bv,3),ask_btc=round(av,3),
                bid_wall=bw,ask_wall=aw)

def indicators(closes, highs=None, lows=None):
    s=pd.Series(closes,dtype=float)
    d=s.diff(); g=d.clip(lower=0); l=-d.clip(upper=0)
    rsi=float((100-100/(1+g.ewm(alpha=1/14,adjust=False).mean()/(l.ewm(alpha=1/14,adjust=False).mean()+1e-10))).iloc[-1])
    ef=s.ewm(span=12,adjust=False).mean(); es=s.ewm(span=26,adjust=False).mean()
    m=ef-es; sg=m.ewm(span=9,adjust=False).mean()
    mh=float((m-sg).iloc[-1]); mh_prev=float((m-sg).iloc[-2])
    rsi_s=100-100/(1+g.ewm(alpha=1/14,adjust=False).mean()/(l.ewm(alpha=1/14,adjust=False).mean()+1e-10))
    mn=rsi_s.rolling(14).min(); mx=rsi_s.rolling(14).max()
    K=((rsi_s-mn)/(mx-mn+1e-10)*100).rolling(3).mean()
    D=K.rolling(3).mean()
    sk=float(K.iloc[-1]); sd=float(D.iloc[-1]); sk_p=float(K.iloc[-2]); sd_p=float(D.iloc[-2])
    e9=float(s.ewm(span=9,adjust=False).mean().iloc[-1])
    e21=float(s.ewm(span=21,adjust=False).mean().iloc[-1])
    return dict(rsi=rsi,mh=mh,mh_up=mh>mh_prev,sk=sk,sd=sd,
                sk_cross_up=sk_p<sd_p and sk>=sd,sk_cross_dn=sk_p>sd_p and sk<=sd,
                bull=e9>e21,e9=e9,e21=e21)

def get_session(hour, weekday):
    if weekday>=5: return {"name":"Víkend","mult":0.4,"note":"Falešné průlomy"}
    if 15<=hour<17: return {"name":"Překryv EU/US","mult":1.5,"note":"Nejsilnější pohyby"}
    elif 2<=hour<9:  return {"name":"Asie","mult":0.7,"note":"Menší pozice"}
    elif 9<=hour<17: return {"name":"Evropa","mult":1.0,"note":"Standardní"}
    elif 15<=hour<23:return {"name":"Amerika","mult":1.3,"note":"Velké pohyby"}
    else: return {"name":"Noc","mult":0.5,"note":"Vyhni se"}

TP_PROBS_L={100:100,150:82,200:70,300:55,400:40,500:33,600:22,750:15}
TP_PROBS_S={100:100,150:88,200:75,300:65,400:50,500:40,600:30,750:20}

def local_bottom(lows, window=12):
    n=len(lows)
    for i in range(n-1,window-1,-1):
        if i+window>=n: continue
        if lows[i]==min(lows[i-window:i+window+1]): return i,lows[i]
    return None,None

def local_top(highs, window=12):
    n=len(highs)
    for i in range(n-1,window-1,-1):
        if i+window>=n: continue
        if highs[i]==max(highs[i-window:i+window+1]): return i,highs[i]
    return None,None

def run():
    now=now_local()
    c1,h1,l1,t1,vwap1,o1 = klines("1m",10080)
    c5,h5,l5,_,vwap5,_   = klines("5m",4032)
    c15,h15,l15,_,vw15,_ = klines("15m",2880)
    c1h,_,_,_,_,_         = klines("1h",2160)
    c4h,h4h,l4h,t4h,_,o4h= klines("4h",1080)
    ob=orderbook()

    i1=indicators(c1,h1,l1); i5=indicators(c5); i15=indicators(c15); i1h=indicators(c1h)
    price=c1[-1]; vwap=vwap1[-1]; vdiff=price-vwap

    # Seance
    now_dt=datetime.now(TZ_PRAGUE); hour=now_dt.hour; wday=now_dt.weekday()
    sess=get_session(hour,wday)

    # PDH/PDL z 4H
    dates4h=[datetime.fromtimestamp(0).date()]*len(c4h)
    try:
        from collections import defaultdict
        daily=defaultdict(lambda:{"hi":0,"lo":999999,"cl":0,"op":0})
        for i,(ts,hi,lo,cl,op) in enumerate(zip(t4h,h4h,l4h,c4h,o4h)):
            d=ts[:10]
            if daily[d]["op"]==0: daily[d]["op"]=op
            if hi>daily[d]["hi"]: daily[d]["hi"]=hi
            if lo<daily[d]["lo"]: daily[d]["lo"]=lo
            daily[d]["cl"]=cl
        days=sorted(daily.keys())
        pdh=daily[days[-2]]["hi"] if len(days)>=2 else 0
        pdl=daily[days[-2]]["lo"] if len(days)>=2 else 0
        pdc=daily[days[-2]]["cl"] if len(days)>=2 else 0
        # Weekly open
        wo=0
        for ts,op in zip(t4h,o4h):
            dt=datetime.strptime(ts,"%Y-%m-%d %H:%M")
            if dt.weekday()==0:  wo=op
    except: pdh=pdl=pdc=wo=0

    lo_idx,lo_p=local_bottom(l1)
    hi_idx,hi_p=local_top(h1)
    bars_lo=len(l1)-1-lo_idx if lo_idx else 999
    bars_hi=len(h1)-1-hi_idx if hi_idx else 999
    near_lo=bars_lo<=20; near_hi=bars_hi<=20
    bounce=price-lo_p if lo_p else 0; drop=hi_p-price if hi_p else 0

    # Scores
    ob_bull=ob["imb"]>0.55; ob_bear=ob["imb"]<0.45
    l_conds=[20<=i1["rsi"]<=58, i1["sk"]<=45, i1["mh_up"],
             i5["rsi"]<68, i5["sk"]<55, i5["mh"]>-10 or i1["mh_up"],
             i15["bull"], 25<i15["rsi"]<68, i1h["bull"]]
    s_conds=[62<=i1["rsi"]<=82, i1["sk"]>=65, not i1["mh_up"],
             i5["rsi"]>55, i5["sk"]>55, not i5["mh_up"] or i5["mh"]<10,
             not i15["bull"], 45<i15["rsi"]<75, i1h["rsi"]<70]
    l_sc=sum(l_conds); s_sc=sum(s_conds)

    scalp_l=i1["sk"]<=10 and i1["rsi"]<=35 and i1["mh_up"] and i5["rsi"]<55
    scalp_s=i1["sk"]>=90 and i1["rsi"]>=65 and not i1["mh_up"] and i5["rsi"]>55
    l_sig=l_sc>=6 or (l_sc>=4 and near_lo and ob_bull)
    s_sig=s_sc>=6 or (s_sc>=4 and near_hi and ob_bear)

    SEP="─"*60
    if scalp_l or (l_sig and l_sc>=7):
        print(f"\n{'!'*60}\n  *** LONG SIGNÁL — skóre {l_sc}/9 {'SCALP!' if scalp_l else ''} ***\n{'!'*60}")
    elif scalp_s or (s_sig and s_sc>=7):
        print(f"\n{'!'*60}\n  *** SHORT SIGNÁL — skóre {s_sc}/9 {'SCALP!' if scalp_s else ''} ***\n{'!'*60}")
    else:
        print(f"\n{SEP}")

    print(f"  {now} SEČ  |  {price:,.2f} USDC  |  L:{l_sc}/9  S:{s_sc}/9")
    print(f"  {sess['name']}  mult {sess['mult']}×  |  OB {ob['imb']*100:.0f}% {'BULL' if ob_bull else 'BEAR' if ob_bear else 'NEU'}")
    print(SEP)

    row=lambda lb,v: print(f"  {lb:<22} {v}")
    row("1M  RSI/SK/MH",f"{i1['rsi']:.1f} / {i1['sk']:.1f} / {i1['mh']:+.1f}{'↑' if i1['mh_up'] else '↓'}  {'BULL' if i1['bull'] else 'BEAR'}")
    row("5M  RSI/SK/MH",f"{i5['rsi']:.1f} / {i5['sk']:.1f} / {i5['mh']:+.1f}{'↑' if i5['mh_up'] else '↓'}")
    row("15M RSI/SK",   f"{i15['rsi']:.1f} / {i15['sk']:.1f}  {'BULL' if i15['bull'] else 'BEAR'}")
    row("1H  RSI",      f"{i1h['rsi']:.1f}")
    row("VWAP",         f"{vwap:,.2f}  ({'+' if vdiff>=0 else ''}{vdiff:.0f}  {'nad' if vdiff>=0 else 'pod'})")
    if pdh: row("PDH/PDL/PDC",f"{pdh:.0f} / {pdl:.0f} / {pdc:.0f}")
    if wo:  row("Weekly Open",f"{wo:.0f}  ({price-wo:+.0f})")
    row("OB bid/ask BTC",f"{ob['bid_btc']:.3f} / {ob['ask_btc']:.3f}  spread {ob['spread']:.2f}")
    if near_lo: row("Dno 1M",f"{lo_p:.0f}  ({bars_lo} min)  bounce {bounce:+.0f}")
    if near_hi: row("Vrchol 1M",f"{hi_p:.0f}  ({bars_hi} min)  drop {drop:+.0f}")

    print(SEP)
    support=min(l1[-60:]); resist=max(h1[-60:])

    if l_sig or scalp_l:
        el=round(support+50,-1)
        print(f"  🟢 LONG  vstup ~{el:,.0f}")
        for lbl,tp,sl,be,rr in [("Scalp",300,100,120,3.0),("Swing",500,150,200,3.33),("Swing+",750,200,300,3.75)]:
            p=TP_PROBS_L.get(tp,20)
            print(f"    {lbl:8s}  TP {el+tp:,.0f}(+{tp})  SL {el-sl:,.0f}(-{sl})  BE+{be}  R:R 1:{rr}  P={p}%")
        if wday>=5: print(f"  ⚠  VÍKEND — {sess['mult']*100:.0f}% pozice")

    if s_sig or scalp_s:
        es=round(resist-50,-1)
        print(f"  🔴 SHORT vstup ~{es:,.0f}")
        for lbl,tp,sl,be,rr in [("Scalp",300,100,120,3.0),("Swing",500,150,200,3.33),("Swing+",750,200,300,3.75)]:
            p=TP_PROBS_S.get(tp,20)
            print(f"    {lbl:8s}  TP {es-tp:,.0f}(-{tp})  SL {es+sl:,.0f}(+{sl})  BE-{be}  R:R 1:{rr}  P={p}%")
        if wday>=5: print(f"  ⚠  VÍKEND — {sess['mult']*100:.0f}% pozice")

    if not l_sig and not s_sig and not scalp_l and not scalp_s:
        l_names=["1M RSI","1M SK","1M MH","5M RSI","5M SK","5M MH","15M trend","15M RSI","1H trend"]
        miss=[l_names[i] for i,ok in enumerate(l_conds) if not ok]
        print(f"  ⏸  Bez signálu.  L chybí: {', '.join(miss[:3])}")

    # Kontext
    tips=[]
    if pdh and abs(price-pdh)<150: tips.append(f"Blízko PDH {pdh:.0f} → short bias")
    if pdl and abs(price-pdl)<150: tips.append(f"Blízko PDL {pdl:.0f} → long bias")
    if wo and abs(price-wo)<200:   tips.append(f"Blízko WO {wo:.0f} → gravitace")
    if pdc and abs(price-pdc)>200: tips.append(f"Gap od PDC {pdc:.0f}: {price-pdc:+.0f} USDC")
    for t in tips: print(f"  ℹ  {t}")

    print(SEP)
    os.makedirs("data",exist_ok=True)
    with open("data/live_report.json","w") as f:
        json.dump({"time":now,"price":price,"vwap":round(vwap,2),"vwap_diff":round(vdiff,2),
                   "session":sess["name"],"weekend":wday>=5,
                   "long_score":l_sc,"short_score":s_sc,
                   "long_signal":l_sig or scalp_l,"short_signal":s_sig or scalp_s,
                   "scalp_long":scalp_l,"scalp_short":scalp_s,
                   "ob_imb":ob["imb"],
                   "levels":{"pdh":pdh,"pdl":pdl,"pdc":pdc,"weekly_open":wo},
                   "tp_probs":{"long":{str(t):TP_PROBS_L.get(t,20) for t in [150,300,500,750]},
                               "short":{str(t):TP_PROBS_S.get(t,20) for t in [150,300,500,750]}},
                   "ind":{"1m":{"rsi":round(i1["rsi"],1),"sk":round(i1["sk"],1),"mh":round(i1["mh"],1)},
                          "5m":{"rsi":round(i5["rsi"],1),"sk":round(i5["sk"],1)},
                          "15m":{"rsi":round(i15["rsi"],1),"sk":round(i15["sk"],1)},
                          "1h":{"rsi":round(i1h["rsi"],1)}}},f,indent=2)

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--loop",type=int,default=0)
    parser.add_argument("--symbol",default=SYMBOL)
    args=parser.parse_args()
    SYMBOL=args.symbol
    if args.loop>0:
        print(f"Watch mode  {SYMBOL}  {args.loop}s  (Ctrl+C)")
        try:
            while True: run(); time.sleep(args.loop)
        except KeyboardInterrupt: print("\nUkončeno.")
    else:
        run()
