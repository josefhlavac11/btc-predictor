"""
BTC/USDC – Live data fetcher + swing entry analyzer
====================================================
Stáhne aktuální data z Binance a okamžitě vyhodnotí
vstupní příležitost do long pozice (swing 500+ USDC).

INSTALACE:
    pip install requests pandas numpy

POUŽITÍ:
    python btc_live.py              # jednorázová analýza
    python btc_live.py --watch 60   # opakuj každých 60 sekund
    python btc_live.py --watch 30   # opakuj každých 30 sekund
"""

import requests, pandas as pd, numpy as np
import json, os, time, argparse
from datetime import datetime
from zoneinfo import ZoneInfo

TZ_PRAGUE = ZoneInfo("Europe/Prague")  # automaticky CET/CEST podle data

def now_local():
    return datetime.now(TZ_PRAGUE).strftime("%H:%M:%S")

def ts_ms_to_local(ms):
    """Binance ms timestamp → pražský čas bez timezone info (pro pandas/CSV)."""
    return datetime.fromtimestamp(ms / 1000, tz=TZ_PRAGUE).replace(tzinfo=None)

# ── KONFIGURACE ───────────────────────────────────────────────────────────────
SYMBOL      = "BTCUSDC"
BASE_URL    = "https://api.binance.com"
DATA_DIR    = "data"
TP          = 500
SL          = 150
BE_TRIGGER  = 200   # přesuň SL na BE po +BE_TRIGGER USDC
SCORE_MIN   = 6     # min skóre pro signál vstupu (z 9)

# ── KOLIK SVÍČEK STAHOVAT PER TIMEFRAME ──────────────────────────────────────
# 1m:  7 dní  = 10 080  → VWAP od půlnoci + týdenní vzorek swing cyklů
# 5m:  14 dní =  4 032  → backtesting vstupních podmínek
# 15m: 30 dní =  2 880  → měsíční support/resistance
# 1h:  90 dní =  2 160  → trendový kontext 3 měsíce
# 4h: 180 dní =  1 080  → makro trend + klíčové S/R
# 3m:   7 dní =  3 360  → pomocný TF
TF_CANDLES = {"1m": 10080, "3m": 3360, "5m": 4032, "15m": 2880, "1h": 2160, "4h": 1080}
os.makedirs(DATA_DIR, exist_ok=True)

# ── INDIKÁTORY ────────────────────────────────────────────────────────────────
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()

def rsi(s, n=14):
    d = s.diff(); g = d.clip(lower=0); l = -d.clip(upper=0)
    return 100 - 100 / (1 + g.ewm(alpha=1/n, adjust=False).mean() /
                           (l.ewm(alpha=1/n, adjust=False).mean() + 1e-10))

def macd(s, f=12, sl=26, sig=9):
    m = ema(s, f) - ema(s, sl)
    return m, ema(m, sig), m - ema(m, sig)

def stoch_rsi(s, rp=14, sp=14, k=3, d=3):
    r = rsi(s, rp)
    mn = r.rolling(sp).min(); mx = r.rolling(sp).max()
    K = ((r - mn) / (mx - mn + 1e-10) * 100).rolling(k).mean()
    return K, K.rolling(d).mean()

def add_indicators(df):
    df = df.copy()
    df["rsi"]  = rsi(df["close"])
    df["macd"], df["macd_sig"], df["mh"] = macd(df["close"])
    df["sk"], df["sd"] = stoch_rsi(df["close"])
    df["e9"]   = ema(df["close"], 9)
    df["e21"]  = ema(df["close"], 21)
    df["e50"]  = ema(df["close"], 50)
    # VWAP — denní reset (Binance počítá od 00:00 UTC = 02:00 SEČ)
    df["tp"]   = (df["high"] + df["low"] + df["close"]) / 3
    df["date"] = df["time"].dt.date
    vwap_parts = []
    for _, grp in df.groupby("date"):
        g = grp.copy()
        cum_tpv = (g["tp"] * g["volume"]).cumsum()
        cum_vol = g["volume"].cumsum()
        g["vwap"] = cum_tpv / cum_vol
        vwap_parts.append(g)
    df = pd.concat(vwap_parts).sort_values("time").reset_index(drop=True)
    return df

# ── BINANCE API ───────────────────────────────────────────────────────────────
def fetch_klines(symbol, interval, limit=300):
    """
    Stáhne posledních `limit` svíček s automatickou paginací.
    Binance vrací max 1000/req — pro více svíček stránkuje zpětně.
    """
    BATCH    = 1000
    cols     = ["open_time","open","high","low","close","volume",
                "close_time","qvol","trades","tbb","tbq","ignore"]
    all_rows = []
    end_time = None
    remaining = limit

    while remaining > 0:
        batch = min(remaining, BATCH)
        params = {"symbol": symbol, "interval": interval, "limit": batch}
        if end_time is not None:
            params["endTime"] = end_time

        r = requests.get(f"{BASE_URL}/api/v3/klines", params=params, timeout=15)
        r.raise_for_status()

        used = int(r.headers.get("X-MBX-USED-WEIGHT-1M", 0))
        if used > 900:
            print(f"  ⚠  API váha {used}/1200 — čekám 60s")
            time.sleep(60)

        data = r.json()
        if not data:
            break

        all_rows = data + all_rows
        remaining -= len(data)
        end_time = data[0][0] - 1

        if len(data) < batch:
            break

        time.sleep(0.12)

    if not all_rows:
        return pd.DataFrame(columns=["time","open","high","low","close","volume"])

    df = pd.DataFrame(all_rows, columns=cols)
    df["time"] = df["open_time"].apply(ts_ms_to_local)
    for c in ["open","high","low","close","volume"]:
        df[c] = df[c].astype(float)
    df = df.drop_duplicates("open_time").sort_values("time").reset_index(drop=True)
    return df[["time","open","high","low","close","volume"]]

def fetch_orderbook(symbol, depth=20):
    r = requests.get(f"{BASE_URL}/api/v3/depth",
                     params={"symbol": symbol, "limit": depth}, timeout=10)
    r.raise_for_status()
    raw = r.json()
    bids = [(float(p), float(q)) for p, q in raw["bids"]]
    asks = [(float(p), float(q)) for p, q in raw["asks"]]
    bv = sum(q for _, q in bids); av = sum(q for _, q in asks)
    return {
        "time":     now_local(),
        "bid":      bids[0][0] if bids else 0,
        "ask":      asks[0][0] if asks else 0,
        "spread":   round(asks[0][0] - bids[0][0], 2) if (bids and asks) else 0,
        "mid":      round((bids[0][0] + asks[0][0]) / 2, 2) if (bids and asks) else 0,
        "imb":      round(bv / (bv + av), 4) if (bv + av) > 0 else 0.5,
        "bid_btc":  round(bv, 4),
        "ask_btc":  round(av, 4),
        "bids":     bids,
        "asks":     asks,
    }

def fetch_price(symbol):
    """Okamžitá cena (ticker)."""
    r = requests.get(f"{BASE_URL}/api/v3/ticker/price",
                     params={"symbol": symbol}, timeout=5)
    r.raise_for_status()
    return float(r.json()["price"])

# ── LOKÁLNÍ DNA / VRCHOLY ────────────────────────────────────────────────────
def find_extrema(df, window=15):
    lows, highs = [], []
    for i in range(window, len(df) - window):
        if df["low"].iloc[i]  == df["low"].iloc[i-window:i+window+1].min():  lows.append(i)
        if df["high"].iloc[i] == df["high"].iloc[i-window:i+window+1].max(): highs.append(i)
    return lows, highs

def find_swing_cycles(df, min_swing=400, window=15):
    lows, highs = find_extrema(df, window)
    cycles = []
    for li in lows:
        fh = [hi for hi in highs if hi > li]
        if not fh: continue
        hi = fh[0]
        sw = float(df["high"].iloc[hi] - df["low"].iloc[li])
        if sw >= min_swing:
            dur = float((df["time"].iloc[hi] - df["time"].iloc[li]).total_seconds() / 60)
            cycles.append({
                "lo_t": str(df["time"].iloc[li])[:16], "hi_t": str(df["time"].iloc[hi])[:16],
                "lo_p": round(float(df["low"].iloc[li]), 2),
                "hi_p": round(float(df["high"].iloc[hi]), 2),
                "sw":   round(sw, 0), "dur": round(dur, 0),
            })
    return cycles

# ── VSTUPNÍ SCORE ─────────────────────────────────────────────────────────────
def entry_score(snaps):
    """snaps = dict {tf: row} pro 1m,5m,15m,1h"""
    s1  = snaps["1m"];  p1  = snaps["1m_prev"]
    s5  = snaps["5m"];  p5  = snaps["5m_prev"]
    s15 = snaps["15m"]
    s1h = snaps["1h"]

    conds = {
        "1M RSI 20-58":   20 <= s1["rsi"]  <= 58,
        "1M SK ≤ 45":     s1["sk"]          <= 45,
        "1M MH roste":    s1["mh"]           > p1["mh"],
        "5M RSI < 68":    s5["rsi"]          < 68,
        "5M SK < 55":     s5["sk"]           < 55,
        "5M MH ok":       s5["mh"]           > p5["mh"] or s5["mh"] > -10,
        "15M trend ok":   s15["e9"]          >= s15["e21"] * 0.998,
        "15M RSI 25-68":  25 < s15["rsi"]   < 68,
        "1H trend bull":  s1h["e9"]          >= s1h["e21"] * 0.995,
    }
    return conds

def is_near_local_low(df1m, threshold_pct=0.3):
    """Jsme v posledních 10 svíčkách blízko lokálního dna (v rámci threshold_pct % od low)?"""
    lows, _ = find_extrema(df1m, window=10)
    if not lows: return False, 0, 0
    last_lo = lows[-1]
    if len(df1m) - last_lo > 25: return False, 0, 0   # dno starší než 25 minut
    lo_price  = float(df1m["low"].iloc[last_lo])
    cur_price = float(df1m["close"].iloc[-1])
    dist_pct  = (cur_price - lo_price) / lo_price * 100
    return dist_pct <= threshold_pct, lo_price, dist_pct

# ── HLAVNÍ ANALÝZA ────────────────────────────────────────────────────────────
def analyze():
    now = now_local()
    print(f"\n{'='*60}")
    print(f"  BTC/USDC Live Swing Analyzer  [{now} SEČ]")
    print(f"{'='*60}")

    # 1) Stáhni data
    print("→ Stahuji data...")
    dfs = {}
    for tf, n in TF_CANDLES.items():
        try:
            d = fetch_klines(SYMBOL, tf, n)
            d = add_indicators(d)
            dfs[tf] = d
            d.to_csv(f"{DATA_DIR}/btc_{tf}.csv", index=False)
            time.sleep(0.15)
        except Exception as e:
            print(f"  [!] Chyba {tf}: {e}")

    # 2) Order book
    try:
        ob = fetch_orderbook(SYMBOL, 20)
        with open(f"{DATA_DIR}/orderbook.json", "w") as f:
            json.dump(ob, f, indent=2)
    except Exception as e:
        ob = {"imb": 0.5, "bid_btc": 0, "ask_btc": 0, "bid": 0, "ask": 0, "spread": 0, "mid": 0}
        print(f"  [!] Chyba orderbook: {e}")

    # 3) Aktuální snapshot
    def last2(tf):
        d = dfs[tf]
        return d.iloc[-1].to_dict(), d.iloc[-2].to_dict()

    try:
        r1m, p1m = last2("1m");  r5m, p5m = last2("5m")
        r15, _   = last2("15m"); r1h, _   = last2("1h"); r4h, _ = last2("4h")
    except KeyError as e:
        print(f"  [!] Chybí timeframe: {e}"); return

    cur_price = float(r1m["close"])

    # 4) Výpis indikátorů
    vwap_cur  = float(r1m.get("vwap", 0))
    vwap_diff = cur_price - vwap_cur
    vwap_lbl  = "nad VWAP" if vwap_diff >= 0 else "pod VWAP"
    print(f"\n  Cena:  {cur_price:,.2f} USDC")
    print(f"  VWAP:  {vwap_cur:,.2f} USDC  ({'+' if vwap_diff>=0 else ''}{vwap_diff:.0f}  {vwap_lbl})")
    print(f"  Čas:   {str(r1m['time'])[:16]} SEČ")
    print(f"\n  {'TF':<5} {'RSI':>6} {'SK':>7} {'SD':>7} {'MH':>8}  {'Trend'}")
    print(f"  {'-'*50}")
    for tf, r, p in [("1m",r1m,p1m),("5m",r5m,p5m),("15m",r15,r15),("1h",r1h,r1h),("4h",r4h,r4h)]:
        trend = "BULL" if r["e9"] > r["e21"] else "BEAR"
        mh_arrow = "↑" if r["mh"] > p["mh"] else "↓"
        print(f"  {tf:<5} {r['rsi']:>6.1f} {r['sk']:>7.1f} {r['sd']:>7.1f} {r['mh']:>7.1f}{mh_arrow}  {trend}")

    # 5) Order book
    imb_pct = ob["imb"] * 100
    imb_lbl = "SILNĚ BULLISH" if ob["imb"] > 0.75 else "BULLISH" if ob["imb"] > 0.55 else \
              "NEUTRÁLNÍ" if ob["imb"] > 0.45 else "BEARISH"
    print(f"\n  Order book:  bid {ob['bid_btc']:.3f} BTC  |  ask {ob['ask_btc']:.3f} BTC")
    print(f"  Imbalance:   {imb_pct:.1f}%  →  {imb_lbl}")
    print(f"  Spread:      {ob['spread']:.2f} USDC")

    # 6) Lokální dno
    near_lo, lo_price, dist_pct = is_near_local_low(dfs["1m"])
    if near_lo:
        print(f"\n  [!] BLÍZKO LOKÁLNÍHO DNA: {lo_price:,.0f} USDC  (jsme +{dist_pct:.2f}%)")
    else:
        # Najdi poslední dno
        lows, _ = find_extrema(dfs["1m"], window=15)
        if lows:
            li = lows[-1]
            lo_p = dfs["1m"]["low"].iloc[li]
            lo_t = dfs["1m"]["time"].iloc[li]
            mins = (dfs["1m"]["time"].iloc[-1] - lo_t).total_seconds() / 60
            bounce = cur_price - lo_p
            print(f"\n  Poslední dno: {lo_p:,.0f} USDC v {str(lo_t)[11:16]} SEČ ({mins:.0f} min zpět)")
            print(f"  Bounce od dna: {bounce:+.0f} USDC")

    # 7) Entry score
    snaps = {"1m": r1m, "1m_prev": p1m, "5m": r5m, "5m_prev": p5m, "15m": r15, "1h": r1h}
    conds = entry_score(snaps)
    score = sum(conds.values())
    print(f"\n  Confluence skóre: {score}/9")
    for name, ok in conds.items():
        print(f"    {'✅' if ok else '❌'} {name}")

    # 8) Trade setup
    support = float(dfs["1m"]["low"].tail(60).min())
    resist  = float(dfs["1m"]["high"].tail(60).max())
    entry_p = round(support + 50, -1)
    tp_p    = round(entry_p + TP, -1)
    sl_p    = round(entry_p - SL, -1)
    be_p    = round(entry_p + BE_TRIGGER, -1)

    print(f"\n  {'─'*50}")
    if score >= SCORE_MIN or (score >= 4 and near_lo and ob["imb"] > 0.7):
        sig = "VSTUP" if score >= SCORE_MIN else "SLEDOVAT"
        print(f"  SIGNÁL: {sig}  (skóre {score}/9, OB {imb_pct:.0f}%)")
        print(f"\n  Vstupní zóna:  {support:,.0f} – {support+150:,.0f} USDC")
        print(f"  Ideální vstup: {entry_p:,.0f} USDC")
        print(f"  Stop Loss:     {sl_p:,.0f} USDC  (−{SL} USDC)")
        print(f"  Break Even:    přesuň SL po dosažení {be_p:,.0f} (+{BE_TRIGGER})")
        print(f"  Take Profit:   {tp_p:,.0f} USDC  (+{TP} USDC)  R:R 1:3.33")
    else:
        print(f"  Žádný signál. Čekej na skóre ≥ {SCORE_MIN} nebo blízkost dna.")
        print(f"  Sleduj: SK 5M cross D zdola + MACD hist otočení")

    # 9) Swing cykly
    cycles = find_swing_cycles(dfs["1m"], min_swing=400)
    if cycles:
        print(f"\n  Swing cykly 400+ USDC v datech ({len(cycles)} celkem):")
        avg_sw  = np.mean([c["sw"]  for c in cycles])
        avg_dur = np.mean([c["dur"] for c in cycles])
        print(f"    Průměrný swing: {avg_sw:.0f} USDC  |  Průměrná délka: {avg_dur:.0f} min")
        big = [c for c in cycles if c["sw"] >= 500]
        print(f"    Cykly ≥ 500 USDC: {len(big)}")
        for c in cycles[-3:]:
            print(f"    {c['lo_t']} → {c['hi_t']}  +{c['sw']:.0f} USDC  {c['dur']:.0f} min")

    # 10) Uložit report
    report = {
        "time_sec": now,
        "price":    cur_price,
        "vwap":     round(vwap_cur, 2),
        "vwap_diff": round(vwap_diff, 2),
        "score":    score,
        "signal":   score >= SCORE_MIN,
        "near_lo":  near_lo,
        "ob_imb":   ob["imb"],
        "entry":    {"zone_lo": support, "zone_hi": support+150,
                     "tp": tp_p, "sl": sl_p, "be": be_p},
        "indicators": {
            "1m":  {"rsi": round(r1m["rsi"],1), "sk": round(r1m["sk"],1), "mh": round(r1m["mh"],2)},
            "5m":  {"rsi": round(r5m["rsi"],1), "sk": round(r5m["sk"],1), "mh": round(r5m["mh"],2)},
            "15m": {"rsi": round(r15["rsi"],1), "sk": round(r15["sk"],1), "mh": round(r15["mh"],2)},
            "1h":  {"rsi": round(r1h["rsi"],1), "sk": round(r1h["sk"],1)},
        }
    }
    with open(f"{DATA_DIR}/live_report.json", "w") as f:
        json.dump(report, f, indent=2)
    print(f"\n  Data uložena → {DATA_DIR}/  |  report → {DATA_DIR}/live_report.json")
    print(f"{'='*60}")
    return report

# ── ENTRY POINT ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", type=int, default=0,
                        help="Opakuj analýzu každých N sekund (0 = jednorázově)")
    parser.add_argument("--symbol", default=SYMBOL)
    args = parser.parse_args()

    SYMBOL = args.symbol

    if args.watch > 0:
        print(f"Watch mode: analýza každých {args.watch}s  (Ctrl+C pro ukončení)")
        try:
            while True:
                report = analyze()
                if report and report.get("signal"):
                    print(f"\n  *** VSTUPNÍ SIGNÁL — skóre {report['score']}/9 ***")
                print(f"\n  Další refresh za {args.watch}s...")
                time.sleep(args.watch)
        except KeyboardInterrupt:
            print("\nUkončeno.")
    else:
        analyze()
