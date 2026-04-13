# BTC Predictor — Zadání pro Claude Code v2
**Datum:** 2026-04-13
**Branch:** josefhlavac11-patch-1
**Kontributor:** sunpavlik (programátor, algo trading)

---

## 0. Princip práce

**CC pracuje postupně** — jedna funkce od nejjednodušší ke složitější.
Vyexcludovat ostatní části, odzkoušet jednu, pak přidat další.
NEDĚLAT velké přepisy najednou.

**Pořadí:**
```
FÁZE 1: Oprava bugů (v existujícím kódu)
FÁZE 2: Extrakce do modulární struktury (indicators/)
FÁZE 3: CVD implementace
FÁZE 4: Hladiny (multi-source, multi-currency)
FÁZE 5: Regime shift + kontext
FÁZE 6: DCA logika + position state machine
FÁZE 7: Pattern hints + výstup redesign
FÁZE 8: Trigger registry + setup klasifikace
FÁZE 9: Feedback loop + data logging
FÁZE 10: Freqtrade přechod
```

---

## Cílová architektura

```
indicators/                    ← SDÍLENÉ (ticker + Freqtrade)
├── triggers/                  ← KROK 1: detekce příležitostí
│   ├── registry.py             ← registr triggerů, eval s váhami
│   ├── stochrsi.py
│   ├── macd_div.py
│   ├── volume.py
│   ├── cvd.py
│   ├── funding.py
│   └── hyperopt_custom.py      ← auto-discovered triggery
│
├── levels.py                  ← hladiny (multi-source)
│   ├── fib_levels()
│   ├── gann_levels()
│   ├── camarilla_levels()
│   ├── ema_levels()
│   ├── hvn_levels()            ← krátkodobé (1M) + dlouhodobé (1H/4H)
│   ├── psychological_levels()  ← session-dependent (EUR/USDT/KRW)
│   ├── discovered_levels()     ← z historických dat
│   ├── cluster_levels()        ← seskupení blízkých hladin
│   └── zone_resilience()       ← volume × diverzita měn
│
├── regime.py                  ← regime shift
│   ├── calc_regime_score()     ← 4 kategorie × 3 body = 12
│   ├── calc_shift_stage()      ← stupeň 0-3
│   ├── calc_shift_eta()        ← kdy může nastat přechod
│   └── scenario_ab()           ← co dělat když potvrdí/selže
│
├── cascade.py                 ← kaskáda potvrzení dna
│   ├── calc_cascade_level()    ← 1M→3M→5M→15M
│   └── p_bottom()              ← pravděpodobnost dna
│
├── setup_classifier.py        ← klasifikace setupů
│   ├── SETUP_DEFINITIONS       ← slovník setupů (1A, 1B, ...)
│   └── classify()              ← trigger results → setup typ
│
├── dca.py                     ← DCA plánování
│   ├── calculate_zones()       ← zóny z hladin + regime
│   ├── evaluate_retest()       ← kvalita retestu
│   ├── evaluate_dca_plan()     ← celý plán profitabilní?
│   └── p_price_reaches_zone()  ← pravděpodobnost dosažení
│
├── position_state.py          ← state machine pozice
│   ├── WAITING/FILLED/HOLDING/DCA_READY/HEDGE_ACTIVE/EXIT
│   └── evaluate_position()     ← přechody mezi stavy
│
├── exit_logic.py              ← výstupní triggery s prioritami
│
├── hints.py                   ← pattern napovídání svíček
│   ├── PATTERN_HINTS           ← slovník hint + anti_hint
│   └── get_active_hints()
│
└── trade_logger.py            ← feedback pro Hyperopt

freqtrade/strategies/
└── BTCMultiTF.py              ← IStrategy (importuje z indicators/)

btc_ticker.py                  ← UI vrstva (importuje z indicators/)

config/
├── config.json                ← Freqtrade config
└── manual_levels.json         ← Josefovy manuální hladiny (pokud potřeba)
```

---

## FÁZE 1: Oprava bugů

### 1.1 Weekly Open
- `get_key_levels()` / `get_pdh_pdl()` bere první pondělí v datasetu místo posledního
- Oprava: WO = open první 4H svíčky AKTUÁLNÍHO týdne
- Ověření: `WO {cena} (po {datum})` ve výstupu, porovnat s TradingView

### 1.2 Momentum korelace = 0
- Pravděpodobná příčina: `self_validate()` používá placeholder `{"sk":50,"rsi":50,"mh":0}` pro 3M/5M
- Diagnostika: vypsat 20 cyklů, ověřit příčinu
- Ověření: |korelace| > 0.15 po opravě, jinak momentum skóre přepracovat

### 1.3 Contradiction ve výstupu
- VWAP filtr blokuje signál ale tabulka se zobrazí
- Pravidlo: `|cena - VWAP| > 2×ATR(1H)` = hard block → žádná tabulka
- Ověření: nikdy současně "blokováno" a signální tabulka

---

## FÁZE 2: Extrakce do indicators/

Vytáhnout výpočetní logiku z btc_ticker.py a btc_live.py do indicators/ jako čisté funkce.

**Postup:**
1. Začni s `indicators/stochrsi.py` — extrahuj StochRSI výpočet
2. Odzkoušej izolovaně — stejné výsledky jako v btc_ticker.py
3. Přidej `indicators/regime.py` — trend context (1H/4H bull/bear)
4. Odzkoušej
5. btc_ticker.py importuje z indicators/ místo vlastních výpočtů
6. Opakuj pro další indikátory

**Ověření po každém kroku:**
```bash
# Výstup tickeru musí být IDENTICKÝ před a po extrakci
python btc_ticker.py > before.txt
# ... extrakce ...
python btc_ticker.py > after.txt
diff before.txt after.txt  # musí být prázdný (kromě timestampů)
```

---

## FÁZE 3: CVD implementace

- Binance klines sloupec 9 (taker buy base) se stahuje ale zahazuje
- `delta = taker_buy - (total_volume - taker_buy)`
- `CVD = cumsum(delta)` s denním resetem
- Přidat trend: rostoucí/klesající za posledních 15 svíček
- Divergence: cena klesá + CVD roste = bullish
- Zapsat do live_report.json

**Ověření:**
```bash
python btc_live.py 2>&1 | grep "CVD"
# Musí zobrazit hodnotu a trend
```

---

## FÁZE 4: Hladiny (indicators/levels.py)

### 4.1 Multi-source hladiny

Čtyři zdroje, všechny se clusterují dohromady:
1. **Vypočítané** — Fib, Gann, Camarilla, EMA
2. **Volume krátkodobé** — HVN z 1M (7 dní)
3. **Volume dlouhodobé** — HVN z 1H (90 dní) + 4H (180 dní)
4. **Discovered** — místa kde se cena opakovaně otočila bez zjevného důvodu

Clustering: hladiny bližší než `ATR × 0.5` = jedna zóna.
Síla zóny = počet nezávislých zdrojů.

### 4.2 Session-dependent psychologické hladiny

**KRITICKÉ:** Nepřepočítávat aktuálním kurzem! Příkazy na Binance jsou fixní USDC z momentu zadání.

Postup:
1. Najdi OB clustery (reálné příkazy)
2. Najdi VP clustery (historický volume)
3. Pro každý cluster: zkus zpětně identifikovat měnu původu
   - Kulatá EUR hodnota při historickém EUR/USD kurzu za posledních 24h?
   - Kulatá KRW hodnota při historickém KRW/USD kurzu?
4. Session weights: plynulý přechod, ne ostré hranice
   - Evropa (9-15): EUR dominantní
   - Overlap EU+US (15-17): obě platí, hledej cross-currency konvergence
   - Amerika (17-23): USDT dominantní
   - Asie (23-9): KRW dominantní

### 4.3 Multi-currency resilience

Zóna kde konvergují příkazy z více měn = odolnější.
- `resilience = total_volume × n_currencies × (1 - concentration × 0.5)`
- Ticker sleduje rozpad v reálném čase: diverzita klesá → varování

### 4.4 Konvergence a mezery v overlap

- Cross-currency konvergence (EUR + USDT blízko v overlap) = SUPER-ZÓNA
- Mezera mezi currency hladinami = LVN, rychlý průchod
- Handoff hladiny (Asie držela → Evropa přebírá)
- Součty blízkých hladin v různých měnách zvyšují odolnost zóny

**Datové potřeby:**
- EUR/USD: Binance `EURUSDT`
- USDT/USDC spread: Binance `USDCUSDT`
- KRW: `BTCKRW` / `BTCUSDT` poměr

---

## FÁZE 5: Regime Shift (indicators/regime.py)

### 5.1 Aktuální režim

Čtyři stavy: 1H+4H bull / 1H bull+4H bear / 1H bear+4H bull / 1H+4H bear.
Každý stav omezuje povolené typy obchodů a modifikuje TP.

### 5.2 Regime Shift skóre

4 kategorie × 3 body = 12:

**Momentum (3):** SK cross up, MACD histogram otáčí, RSI > 50
**Trend (3):** EMA9 > EMA21, Close > EMA50, Higher High+Low
**Objem (3):** Buy vol > sell vol, CVD rostoucí, Taker buy > 0.55
**Kontext (3):** OB imbalance > 0.55, Funding neutrální, OI roste při růstu

Pravidlo: každá kategorie ≥1 bod!

Stupně: 0 (žádný) → 1 (formuje se) → 2 (pravděpodobný) → 3 (potvrzený)

### 5.3 Scénáře pro otevřenou pozici

- Scénář A (shift potvrdí): rozšiř TP, zruš hedge
- Scénář B (shift selže): drž hedge, TP omezené
- Hedge: 80-100% (stupeň 0) → 0% (stupeň 3)

### 5.4 Timeframe hierarchie

15M kontext dává povolení, 1M/3M dává timing.
15M StochRSI 97 = pod tím proběhne několik 1M cyklů = scalp příležitosti.
Ticker musí říct timing v TF obchodu, ne v TF indikátoru.

---

## FÁZE 6: DCA logika (indicators/dca.py)

### 6.1 Sekvenční rozhodování
1. Anticipatory na zóně → 2. Retest pokud drží → 3. Safety pod zónou

### 6.2 Kvalita retestu
volume_ratio, cvd_at_retest, zone_resilience_change, ob_change
→ HEALTHY (kup) / WEAK (nekupuj) / NEUTRAL (čekej)

### 6.3 DCA plán jako celek
Všechny scénáře profitabilní? Nejhorší scénář vydržitelný?
Josef akceptuje mírně vyšší vstup — DCA zprůměruje.

### 6.4 Position state machine
WAITING → FILLED → HOLDING / DCA_READY → HEDGE_ACTIVE → EXIT

---

## FÁZE 7-9: Pattern hints, trigger registry, feedback

Viz architektura výše. Implementovat až po Fázi 6.

---

## FÁZE 10: Freqtrade

Až Fáze 1-6 hotové a odzkoušené. Mapování viz architektura.

---

## Placeholdery pro kalibraci

| Parametr | Placeholder | Zdroj finální hodnoty |
|----------|------------|----------------------|
| TP modifikátor per regime | 0.3×–1.0× | Hyperopt |
| Kaskáda pravděpodobnosti | 30/55/75/85/92% | Analýza dat |
| Retest volume_ratio práh | 0.8 / 1.2 | Kalibrace |
| Zone resilience min score | TBD | Hyperopt |
| Session weights | Ruční odhad | Hyperopt |
| Trigger váhy | Equal (1.0) | Hyperopt |
| Setup A vs B síla | Neznámá | Analýza dat |
| CVD práh 1A vs 1B | Neznámý | Analýza dat |

---

## Pro sunpavlika: vytvořit ARCHITECTURE.md
- Datový tok, interface, Freqtrade mapování
- Jak přidat trigger/indikátor
- Žádná obchodní teorie
