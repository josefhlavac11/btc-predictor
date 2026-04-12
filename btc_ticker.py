"""
BTC/USDC – Rychlý ticker + okamžitý signál
===========================================
Minimální verze: stáhne jen co je potřeba, výsledek za < 3 sekundy.

POUŽITÍ:
    python btc_ticker.py            # jednorázový výpis
    python btc_ticker.py --loop 20  # každých 20 sekund, zvýrazní signál
"""

import requests, pandas as pd, numpy as np, time, argparse, os, json
from datetime import datetime
from zoneinfo import ZoneInfo

TZ_PRAGUE = ZoneInfo("Europe/Prague")  # automaticky CET/CEST podle data

def now_local():
    return datetime.now(TZ_PRAGUE).strftime("%H:%M:%S")

def ts_to_local(ms):
    """Převede Binance timestamp (ms UTC) na čas v pražském pásmu."""
    return datetime.fromtimestamp(ms / 1000, tz=TZ_PRAGUE).strftime("%Y-%m-%d %H:%M")

SYMBOL  = "BTCUSDC"
API     = "https://api.binance.com"
TP, SL  = 500, 150

def get(endpoint, params):
    return requests.get(f"{API}{endpoint}", params=params, timeout=8).json()

def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def indicators(closes):
    s = pd.Series(closes, dtype=float)
    # RSI
    d = s.diff(); g = d.clip(lower=0); l = -d.clip(upper=0)
    rsi = float((100 - 100/(1 + g.ewm(alpha=1/14,adjust=False).mean() /
                               (l.ewm(alpha=1/14,adjust=False).mean()+1e-10))).iloc[-1])
    # MACD hist
    ef = ema(s,12); es = ema(s,26); m = ef-es; mh = float((m - ema(m,9)).iloc[-1])
    mh_prev = float((m - ema(m,9)).iloc[-2])
    # StochRSI
    rsi_s = 100 - 100/(1 + g.ewm(alpha=1/14,adjust=False).mean() /
                           (l.ewm(alpha=1/14,adjust=False).mean()+1e-10))
    mn = rsi_s.rolling(14).min(); mx = rsi_s.rolling(14).max()
    K = ((rsi_s-mn)/(mx-mn+1e-10)*100).rolling(3).mean()
    D = K.rolling(3).mean()
    sk = float(K.iloc[-1]); sd = float(D.iloc[-1]); sk_prev = float(K.iloc[-2]); sd_prev = float(D.iloc[-2])
    # EMA trend
    e9 = float(ema(s,9).iloc[-1]); e21 = float(ema(s,21).iloc[-1])
    return dict(rsi=rsi, mh=mh, mh_up=mh>mh_prev,
                sk=sk, sd=sd, sk_cross=sk_prev<sd_prev and sk>=sd,
                bull=e9>e21, e9=e9, e21=e21)

def klines(tf, n=200):
    """Stáhne n svíček s paginací (Binance limit 1000/req)."""
    BATCH = 1000
    all_rows = []; end_time = None; remaining = n
    while remaining > 0:
        batch = min(remaining, BATCH)
        params = {"symbol": SYMBOL, "interval": tf, "limit": batch}
        if end_time is not None:
            params["endTime"] = end_time
        raw = requests.get(f"https://api.binance.com/api/v3/klines", params=params, timeout=15).json()
        if not raw: break
        all_rows = raw + all_rows
        remaining -= len(raw)
        end_time = raw[0][0] - 1
        if len(raw) < batch: break
        time.sleep(0.12)
    closes = [float(r[4]) for r in all_rows]
    highs  = [float(r[2]) for r in all_rows]
    lows   = [float(r[3]) for r in all_rows]
    times  = [ts_to_local(r[0]) for r in all_rows]
    # VWAP (denní reset)
    import pandas as _pd
    df = _pd.DataFrame({"close": closes, "high": highs, "low": lows,
                        "vol": [float(r[5]) for r in all_rows],
                        "ts":  [r[0] for r in all_rows]})
    df["date"] = _pd.to_datetime(df["ts"], unit="ms").dt.date
    df["tp"]   = (df["high"] + df["low"] + df["close"]) / 3
    vwap_vals = []
    for _, grp in df.groupby("date"):
        cum = (grp["tp"] * grp["vol"]).cumsum() / grp["vol"].cumsum()
        vwap_vals.extend(cum.tolist())
    vwap = vwap_vals
    return closes, highs, lows, times, vwap

def orderbook():
    ob = get("/api/v3/depth", {"symbol": SYMBOL, "limit": 20})
    bids = [(float(p), float(q)) for p,q in ob["bids"]]
    asks = [(float(p), float(q)) for p,q in ob["asks"]]
    bv = sum(q for _,q in bids); av = sum(q for _,q in asks)
    return dict(bid=bids[0][0], ask=asks[0][0],
                spread=round(asks[0][0]-bids[0][0],2),
                imb=round(bv/(bv+av),4), bid_btc=round(bv,3), ask_btc=round(av,3))

def local_bottom(lows, window=15):
    """Vrátí (idx, price) posledního lokálního dna nebo None."""
    n = len(lows)
    for i in range(n-1, window-1, -1):
        if i+window >= n: continue
        if lows[i] == min(lows[i-window:i+window+1]):
            return i, lows[i]
    return None, None

def run():
    now = now_local()

    # Paralelní fetch (sekvenční kvůli rate limitu ale rychlý)
    c1, h1, l1, t1, vwap1 = klines("1m", 10080)
    c5, h5, l5, _,  _     = klines("5m",  4032)
    c15,h15,l15,_,  _     = klines("15m", 2880)
    c1h,_,_,_,  _         = klines("1h",  2160)
    ob             = orderbook()

    i1  = indicators(c1)
    i5  = indicators(c5)
    i15 = indicators(c15)
    i1h = indicators(c1h)

    price = c1[-1]

    # Score
    conds = [
        20 <= i1["rsi"] <= 58,
        i1["sk"] <= 45,
        i1["mh_up"],
        i5["rsi"] < 68,
        i5["sk"] < 55,
        i5["mh"] > -10 or i5["mh_up"],
        i15["e9"] >= i15["e21"] * 0.998,
        25 < i15["rsi"] < 68,
        i1h["bull"],
    ]
    score = sum(conds)

    # Blízkost lokálního dna
    lo_idx, lo_price = local_bottom(l1, window=12)
    bars_since_lo = len(l1) - 1 - lo_idx if lo_idx else 999
    near_lo = bars_since_lo <= 20
    bounce = price - lo_price if lo_price else 0

    # Support/resistance
    support = min(l1[-60:])
    resist  = max(h1[-60:])
    entry   = round(support + 50, -1)

    # Signál
    ob_bull = ob["imb"] > 0.65
    signal  = score >= 6 or (score >= 4 and near_lo and ob_bull)
    urgent  = score >= 7 or (score >= 5 and near_lo and ob["imb"] > 0.75)

    # ── Výpis ──────────────────────────────────────────────────────────────
    SEP = "─" * 56
    if urgent:
        print(f"\n{'!'*56}")
        print(f"  *** VSTUP — skóre {score}/9  OB {ob['imb']*100:.0f}%  {'DÍVEJ SE!' if near_lo else ''} ***")
        print(f"{'!'*56}")
    else:
        print(f"\n{SEP}")

    print(f"  {now} SEČ  |  {price:,.2f} USDC  |  score {score}/9  |  OB {ob['imb']*100:.0f}% {'🟢' if ob_bull else '🔴'}")
    print(SEP)

    row = lambda lb, v: print(f"  {lb:<22} {v}")
    row("1M  RSI / SK / MH",   f"{i1['rsi']:.1f} / {i1['sk']:.1f} / {i1['mh']:+.1f}{'↑' if i1['mh_up'] else '↓'}  trend={'BULL' if i1['bull'] else 'BEAR'}")
    row("5M  RSI / SK / MH",   f"{i5['rsi']:.1f} / {i5['sk']:.1f} / {i5['mh']:+.1f}{'↑' if i5['mh_up'] else '↓'}")
    row("15M RSI / SK",        f"{i15['rsi']:.1f} / {i15['sk']:.1f}  trend={'BULL' if i15['bull'] else 'BEAR'}")
    row("1H  RSI / trend",     f"{i1h['rsi']:.1f}  {'BULL' if i1h['bull'] else 'BEAR'}")
    row("OB  bid/ask BTC",     f"{ob['bid_btc']:.3f} / {ob['ask_btc']:.3f}  spread {ob['spread']:.2f}")
    vwap_cur = vwap1[-1] if vwap1 else 0
    vwap_diff = price - vwap_cur
    row("VWAP (denní)",         f"{vwap_cur:,.2f}  ({'+' if vwap_diff>=0 else ''}{vwap_diff:.0f} USDC  {'nad' if vwap_diff>=0 else 'pod'} VWAP)")

    if near_lo and lo_price:
        row("Lokální dno", f"{lo_price:,.0f}  ({bars_since_lo} min zpět)  bounce {bounce:+.0f}")
    
    print(SEP)

    if signal:
        print(f"  SIGNÁL VSTUPU  {'(URGENTNÍ)' if urgent else '(sleduj)'}")
        print(f"  Vstup:  {entry:,.0f}  |  TP: {entry+TP:,.0f}  |  SL: {entry-SL:,.0f}  |  BE: {entry+200:,.0f}")
        print(f"  R:R 1:3.33")
    else:
        # Co chybí
        names = ["1M RSI","1M SK","1M MH↑","5M RSI","5M SK","5M MH","15M trend","15M RSI","1H trend"]
        missing = [names[i] for i,ok in enumerate(conds) if not ok]
        print(f"  Čekej — chybí: {', '.join(missing)}")
    print(SEP)

    # Uložit JSON pro případné další zpracování
    os.makedirs("data", exist_ok=True)
    with open("data/live_report.json","w") as f:
        json.dump({"time":now,"price":price,"score":score,"signal":signal,
                   "near_lo":near_lo,"lo_price":lo_price,"bounce":round(bounce,0),
                   "ob_imb":ob["imb"],"entry":entry,"tp":entry+TP,"sl":entry-SL,
                   "ind":{"1m":{"rsi":round(i1["rsi"],1),"sk":round(i1["sk"],1),"mh":round(i1["mh"],1)},
                          "5m":{"rsi":round(i5["rsi"],1),"sk":round(i5["sk"],1)},
                          "15m":{"rsi":round(i15["rsi"],1),"sk":round(i15["sk"],1)},
                          "1h":{"rsi":round(i1h["rsi"],1)}}}, f, indent=2)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", type=int, default=0, help="Opakuj každých N sekund")
    parser.add_argument("--symbol", default=SYMBOL)
    args = parser.parse_args()
    SYMBOL = args.symbol

    if args.loop > 0:
        print(f"Watch mode  {SYMBOL}  refresh {args.loop}s  (Ctrl+C ukončí)")
        try:
            while True:
                run()
                time.sleep(args.loop)
        except KeyboardInterrupt:
            print("\nUkončeno.")
    else:
        run()
