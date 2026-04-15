# Dynamický Model Bodu Obratu — Verifikace, Korekce a Integrace do BTC Prediktoru

---

## ČÁST 1: VERIFIKACE TEORETICKÉHO RÁMCE

### 1.1 Co je správně

#### Aukční teorie a Volume Profile
Základní premisa je solidní: trh je aukční proces, cena hledá rovnováhu mezi agresivními (market) a pasivními (limit) příkazy. POC, VAH, VAL jako skryté S/R hladiny — toto je standardní Market Profile teorie (Steidlmayer). Koncept absorpce (cena stojí, volume roste) je dobře popsaný a odpovídá reálnému mechanismu.

#### Kritika fixních timeframů
Správný postřeh. Fixní TF skutečně vytvářejí zkreslení — 14-periodový RSI na 15M svíčkách v asijské seanci (nízký volume) má úplně jinou informační hodnotu než stejný RSI při NY open. Přechod k event-based logice (volume milestones) je legitimní směr.

#### Kritika fixních Volume Profilů
Rovněž správné. Daily Session VP ignoruje historický kontext. Anchored VP od začátku posledního trendu a Composite VP přes celou strukturu jsou lepší přístupy.

#### Multiplikativní model odolnosti
P(průraz) = ∏(1 - Pᵢ) — toto je v SESSION_SUMMARY správně definováno a Sonnet konverzace to potvrzuje z jiného úhlu. Nezávislé zdroje se násobí, ne sčítají.

#### Mechanismus vyčerpání
Popis cyklů vyčerpání (momentum, volume, odolnost hladiny) je konceptuálně správný. Každý test S/R spotřebovává limitní příkazy = snižuje odolnost.

---

### 1.2 Co je CHYBNĚ nebo NEPŘESNĚ

#### CHYBA 1: Rovnice P_reversal jako lineární vážený součet

**Problém:** Sonnet napsal:
```
P_reversal = w₁·S_confluence + w₂·D_momentum + w₃·V_anomaly + w₄·E_external
```
s vahami 50/30/20 %. Toto je lineární model, který PROTIŘEČÍ vlastnímu tvrzení o multiplikativním modelu z části o P(průraz).

**Korekce:** Správný přístup je buď:
- **Multiplikativní:** P(reversal) = 1 - ∏(1 - Pᵢ) pro nezávislé zdroje
- **Bayesovský:** P(reversal | evidence) ∝ P(evidence | reversal) × P(reversal)
- **Logistická regrese:** logit(P) = β₀ + β₁x₁ + β₂x₂ + ... (co Hyperopt skutečně optimalizuje)

Lineární vážený součet s fixními vahami 50/30/20 je ad-hoc a nekalibrovaný. Váhy MUSÍ vyjít z dat (Hyperopt/backtest), ne z intuice.

#### CHYBA 2: "Rozpuštění" RSI na volume milestones

**Problém:** Nápad přepočítávat RSI ne po X svíčkách ale po protečení Y objemu je teoreticky elegantní, ale prakticky problematický:
- Binance API nedodává tick-by-tick data zdarma (jen OHLCV svíčky)
- Volume-based resampling vyžaduje vlastní aggregátor svíček
- Výsledný "adaptivní RSI" nemá žádný backtest — nevíme jestli funguje lépe než standardní

**Korekce:** Reálnější přístup pro btc_live.py:
1. Použít standardní RSI(14) na OHLCV datech (funguje, je otestovaný)
2. PŘIDAT volume-weighted RSI jako DOPLŇKOVÝ indikátor (ne náhrada)
3. Případně použít Connors RSI nebo adaptivní periodu přes ATR

#### CHYBA 3: Formule vyčerpání hybnosti

**Problém:** Sonnet napsal:
```
E_momentum = Σ|Δprice| / Σ|volume| over trend duration
```
Toto je nesmysl jako poměr — jednotky nesedí (USDC / BTC = ???). A "pokud podíl roste, trend umírá" — to není obecně pravda. Podíl cena/volume může růst i při zdravém trendu s klesající participací.

**Korekce:** Správné metriky vyčerpání:
- **RSI divergence:** cena nové high, RSI nižší high — dobře definováno
- **Volume divergence:** cena nové high, volume nižší — dobře definováno
- **MACD plocha:** kumulativní histogram klesá cyklus od cyklu — vaše STRATEGIE.md to popisuje správně
- **OBV slope:** On-Balance Volume zpomaluje růst

#### CHYBA 4: Koeficient shody σ_confluence

**Problém:** Sonnet definoval koeficient s "σ = směrodatná odchylka shody" bez vysvětlení co přesně se měří. Směrodatná odchylka čeho? Cenových úrovní signálů? To není dobře definované.

**Korekce:** Správný přístup ke confluence:
- Definovat cenovou zónu (ne bod): cluster = oblast kde se POC/VAH/VAL/Fib/EMA potkávají v rozmezí ±0.2% ceny
- Počítat počet nezávislých zdrojů v clusteru
- Šířka clusteru (jak těsně se hladiny shlukují) jako metrika kvality

#### CHYBA 5: Odolnost hladiny R = V_zone / n_touches

**Problém:** Tvrzení "s každým dalším dotykem odolnost klesá" je ZJEDNODUŠENÍ. V praxi:
- První 1-2 testy mohou odolnost ZVÝŠIT (potvrzení hladiny, přilákání dalších limitních příkazů)
- Teprve 3.+ test začíná spotřebovávat likviditu
- Záleží na volume při každém testu a na čase mezi testy

**Korekce:** Vaše STRATEGIE.md to popisuje přesněji v sekci "Pattern absorption na HVN":
> "Každý test: klesající sell volume + rostoucí buy delta (CVD). Každý odraz menší než předchozí = prodejci slábnou."
Toto je správnější model — nezáleží jen na počtu testů, ale na kvalitě každého testu.

#### CHYBA 6: Fixní váhy v tabulce (50% / 30% / 20%)

**Problém:** Jakékoliv fixní váhy jsou arbitrární dokud neprojdou Hyperopt. Sonnet je prezentuje jako hotový model, ale jsou to jen placeholdery.

**Korekce:** V STRATEGIE.md i SESSION_SUMMARY je jasně řečeno: váhy kalibruje Hyperopt na historických datech. Fixní čísla jsou počáteční odhad, ne výsledek.

---

### 1.3 Co CHYBÍ v Sonnet modelu

1. **Momentum korekce P(odraz)** — SESSION_SUMMARY definuje korekční faktor podle rychlosti pohybu (drop_vs_atr, akcelerace, volume trend, body_ratio). Sonnet model to nemá.

2. **Expected Value** — P(dosáhne) × P(odrazí) jako rozhodovací metrika pro DCA. Sonnet model mluví jen o P(reversal), ne o P(cena tam vůbec dojde).

3. **Regime detection** — manipulace vs organický pohyb. Sonnet model nemá žádný filtr pro fake signály.

4. **Session dependency** — asijská vs londýnská vs NY seance zásadně mění validitu signálů. Chybí.

5. **Multi-currency resilience** — hladiny z EUR/KRW/USDT příkazů. Chybí.

6. **DCA sekvenční logika** — Sonnet model uvažuje single entry, ne DCA plán.

7. **Kaskáda hladin** — co se stane když hladina padne → kam cena letí → jaká je další hladina. Chybí.

---

## ČÁST 2: OPRAVENÝ A DOPLNĚNÝ MODEL

### 2.1 Terminologie (rozšířená)

| Pojem | Definice | Závislost |
|-------|----------|-----------|
| **Odolnost hladiny (R)** | Schopnost S/R zóny absorbovat agresivní příkazy. Měří se hustotou limitních příkazů × historickým volume × diverzitou měn. | Klesá s počtem testů (po 2. testu). Roste s časem bez testu (nové příkazy se akumulují). |
| **POC** | Cenová hladina s největším kumulativním volume. "Těžiště" trhu. | Platný dokud není objemově překonán novou POC. |
| **Value Area (VA)** | 70% celkového volume. VAH/VAL = hranice. | Závisí na zvoleném časovém okně — Anchored VP je přesnější než Session VP. |
| **Absorpce** | Limitní příkazy pohlcují market příkazy. Cena stojí, volume roste. | Předchází obratu. Končí buď průrazem (absorpce vyčerpána) nebo odrazem (agresivní strana vyčerpána). |
| **Vyčerpání (Exhaustion)** | Agresivní strana nemá kapitál/ochotu pokračovat. Volume klesá, svíčky se zmenšují. | Závisí na délce a strmost předchozího pohybu. Měří se přes RSI/MACD divergence a volume trend. |
| **CHoCH** | Průraz posledního lokálního high/low na nižším TF. První strukturální známka změny trendu. | Musí být potvrzena volume (ne jen wick). |
| **BOS** | Break of Structure — trend pokračuje (opak CHoCH). | |
| **Confluence Cluster** | Oblast kde se ≥3 nezávislé hladiny potkávají v rozmezí ±0.15% ceny. | Čím víc zdrojů, tím silnější zóna (multiplikativní model). |
| **Momentum korekce** | Faktor 0.5–1.5 který upravuje statickou P(odraz) podle rychlosti příchozího pohybu. | Závisí na: drop_vs_atr, akcelerace, volume trend, body_ratio. |
| **Expected Value (EV)** | P(cena dosáhne hladiny) × P(cena se odrazí). Rozhoduje o umístění DCA příkazů. | Hladina s EV=0.40 je lepší DCA zóna než hladina s P(odraz)=0.90 ale EV=0.09. |
| **Regime** | Manipulace vs organický pohyb. V manipulačním režimu indikátory lžou. | Detekce: delta volume, OI, funding rate, OB spoofing. |
| **Volume Velocity (Vᵥ)** | Rychlost přibývání volume nezávislá na uzavření svíčky. dV/dt v reálném čase. | Spike Vᵥ na S/R = potvrzení bodu obratu PŘED uzavřením svíčky. |
| **Fraktální Confluence** | Stav kdy POC na nižším TF = VAH/VAL na vyšším TF. Čas je irelevantní. | Nejsilnější typ hladiny. |
| **Resilience** | Volume × diverzita měn na hladině. Jedna měna = křehké, tři = odolné. | Ticker sleduje rozpad v reálném čase. |
| **Kaskáda** | Sekvence hladin pod/nad aktuální cenou seřazená podle EV. | Pokud hladina 1 padne → cena letí přes LVN k hladině 2. |

### 2.2 Opravená architektura výpočtu

#### Krok 1: Identifikace hladin (statická složka)

```
Pro každý zdroj z = {Fib, HVN, EMA, VWAP, Gann, Camarilla, Psych_level}:
    hladina[z] = vypočítaná cenová úroveň
    P_base[z] = base probability odrazu (placeholder, Hyperopt kalibruje)
    
    Příklady base P:
    - HVN s vysokým volume:     P_base ~ 0.35
    - Fib 0.618:                P_base ~ 0.30
    - EMA 200:                  P_base ~ 0.25
    - Psychologická (round):    P_base ~ 0.15
    - VWAP:                     P_base ~ 0.20
```

#### Krok 2: Clustering — najdi confluence zóny

```
Pro každou cenovou oblast šířky ±0.15% aktuální ceny:
    sources[] = seznam zdrojů jejichž hladiny padají do oblasti
    
    Pokud len(sources) >= 2:
        cluster = {
            center: vážený průměr hladin (váha = P_base),
            width: max(hladiny) - min(hladiny),
            sources: sources,
            n_sources: len(sources)
        }
```

#### Krok 3: P(odraz) — multiplikativní model

```
Pro každý cluster:
    P(průraz) = ∏(1 - P_base[z]) pro z in sources
    P(odraz)_static = 1 - P(průraz)
    
    Příklad: HVN(0.35) + Fib(0.30) + EMA(0.25)
    P(průraz) = 0.65 × 0.70 × 0.75 = 0.341
    P(odraz)_static = 0.659 = 65.9%
```

#### Krok 4: Momentum korekce

```
momentum_factor = f(drop_vs_atr, acceleration, volume_trend, body_ratio)

Kde:
    drop_vs_atr = |cenový_pohyb| / ATR_1H
        < 0.5 ATR: factor = 1.2  (pomalý pohyb, hladina drží lépe)
        0.5-1.0:   factor = 1.0  (normální)
        1.0-2.0:   factor = 0.7  (rychlý, hladina oslabena)
        > 2.0:     factor = 0.4  (extrémní, hladina pravděpodobně nedrží)
    
    acceleration = (speed_last_5_candles - speed_prev_5_candles) / speed_prev_5
        > 0: pohyb zrychluje → factor *= 0.85
        < 0: pohyb zpomaluje → factor *= 1.15
    
    volume_trend = volume_last_5 / volume_prev_5
        > 1.5: rostoucí volume → factor *= 0.80 (silný tlak)
        < 0.7: klesající volume → factor *= 1.20 (vyčerpání)
    
    body_ratio = avg(|close-open|) / avg(high-low) za posledních 5 svíček
        > 0.7: velká těla → factor *= 0.85 (rozhodný pohyb)
        < 0.3: malá těla, dlouhé knoty → factor *= 1.15 (nerozhodnost)

P(odraz)_adjusted = min(0.95, max(0.05, P(odraz)_static × momentum_factor))
```

#### Krok 5: P(dosáhne) — dosažitelnost hladiny

```
distance = |aktuální_cena - hladina| / ATR_1H

P(dosáhne) = sigmoid(-k × (distance - d₀))

Kde:
    k = strmost sigmoidní křivky (Hyperopt)
    d₀ = střed (typicky 1.0 ATR = 50% šance)
    
    Korekce podle momentu:
    - Pokud cena padá a hladina je pod ní: P(dosáhne) *= 1 + momentum_boost
    - Pokud cena roste a hladina je pod ní: P(dosáhne) *= 1 - momentum_penalty
```

#### Krok 6: Expected Value

```
EV = P(dosáhne) × P(odraz)_adjusted

DCA zóny seřadit podle EV sestupně.
Alokace kapitálu proporcionální k EV.
```

#### Krok 7: Regime filtr

```
regime = detect_regime(delta_volume, OI_change, funding_rate, OB_spoofing)

Pokud regime == MANIPULATION:
    - Všechny P(odraz) *= 0.5
    - Přidat varování: "Manipulační režim — indikátory nespolehlivé"
    
Pokud regime == ORGANIC:
    - P(odraz) beze změny
    - Důvěřuj signálům
```

### 2.3 Mechanismus vyčerpání — opravené závislosti

#### A. Vyčerpání hybnosti (RSI, MACD, StochRSI)

**Mechanismus:** Trend spotřebovává "palivo" — ochotu nových účastníků vstoupit za aktuální ceny.

**Měření:**
- RSI divergence: cena nové high → RSI nižší high
- MACD plocha: zelený cyklus menší než předchozí
- StochRSI: drží nad 80 déle než 5 svíček na 15M (final run) NEBO padá z 80 (vyčerpání)

**Cyklus závisí na:**
- Strmosti předchozího trendu (strmější = rychlejší vyčerpání)
- Volume při pohybu (vysoký volume = rychlejší vyčerpání likvidity)
- Šířce trhu (kolik různých účastníků se zapojilo)

**Rovnice:**
```
exhaustion_momentum = Σ(divergence_signals) / expected_signals_for_trend_length

Kde expected_signals roste lineárně s délkou trendu.
Pokud exhaustion > 0.7 → trend je zralý na obrat.
```

#### B. Vyčerpání volume (absorpce → průraz)

**Mechanismus:** Limitní příkazy na hladině mají konečnou velikost. Každý test je částečně spotřebuje.

**Měření:**
- Volume při testu klesá = méně příkazů k dispozici
- Odraz po testu menší = méně síly v odpovědi
- CVD delta klesá i při odrazu = kupující slábnou

**Cyklus závisí na:**
- Velikosti původní likvidní stěny (velká HVN vydrží více testů)
- Čase mezi testy (čas = příležitost pro doplnění příkazů)
- Diverzitě příkazů (jedna velryba vs tisíc retailů — velryba může odstoupit najednou)

**Rovnice:**
```
R_after_test[n] = R_initial × decay_factor^(n-1) × time_recovery_factor

Kde:
    decay_factor ~ 0.7–0.9 (Hyperopt)
    time_recovery = min(1.0, hours_since_last_test / 4)
    n = počet testů
```

#### C. Vyčerpání odolnosti (regime shift)

**Mechanismus:** Z SESSION_SUMMARY — 4 kategorie po 3 bodech (Momentum, Trend, Objem, Kontext) = 12 bodů. Každá kategorie musí mít ≥1 bod.

**Měření:** Regime shift score 0–12

**Cyklus závisí na:**
- Akumulaci signálů přes timeframy (fraktální process — menší TF infikuje větší)
- Fundamentálním kontextu (makro ekonomika, regulace, on-chain metriky)

---

## ČÁST 3: INTEGRACE DO BTC PREDIKTORU

### 3.1 Co přidat do btc_live.py (Prediktor)

#### Priorita 1: Confluence Cluster detektor (nahrazuje pevný support/resistance)

**Aktuální stav (LOGIC.md):**
```python
support = min(close[-60:])  # 60 minut zpět na 1M
resistance = max(high[-60:])
entry = round(support + 50, -1)
```
Toto je primitivní — nerespektuje HVN, Fib, EMA, ani multi-source confluence.

**Cílový stav:**
```python
def find_confluence_clusters(price, indicators, lookback_hours=4):
    """
    Najde cenové zóny kde se potkávají ≥2 nezávislé hladiny.
    
    Vstupy:
        price: aktuální cena
        indicators: dict s EMA, VWAP, Fib úrovněmi, POC/HVN
        lookback_hours: jak daleko hledat
    
    Výstup:
        clusters: list[{center, width, sources, P_bounce, EV}]
        seřazený podle EV sestupně
    """
    levels = []
    
    # Zdroj 1: Fibonacci (od posledního swing low/high)
    for fib_level in [0.382, 0.500, 0.618, 0.786]:
        levels.append({
            'price': swing_low + (swing_high - swing_low) * fib_level,
            'source': f'Fib_{fib_level}',
            'P_base': FIB_BASE_P[fib_level]  # Hyperopt kalibruje
        })
    
    # Zdroj 2: EMA hladiny
    for ema_name, ema_value in [('EMA9', e9), ('EMA21', e21), ('EMA50', e50), ('EMA200', e200)]:
        levels.append({
            'price': ema_value,
            'source': ema_name,
            'P_base': EMA_BASE_P[ema_name]
        })
    
    # Zdroj 3: VWAP
    levels.append({'price': vwap, 'source': 'VWAP', 'P_base': 0.20})
    
    # Zdroj 4: HVN z Volume Profile (Anchored od posledního swing)
    for hvn in volume_profile_hvns:
        levels.append({'price': hvn['price'], 'source': 'HVN', 'P_base': hvn['strength']})
    
    # Zdroj 5: Psychologické úrovně (round numbers)
    for round_level in generate_round_levels(price, range=2000):
        levels.append({'price': round_level, 'source': 'Psych', 'P_base': 0.15})
    
    # Clustering: seskup hladiny které jsou blízko sebe
    clusters = cluster_levels(levels, tolerance_pct=0.15)
    
    # Pro každý cluster vypočítej P(odraz) a EV
    for cluster in clusters:
        cluster['P_bounce'] = calc_P_bounce(cluster['sources'])
        cluster['P_bounce_adj'] = adjust_for_momentum(cluster['P_bounce'], momentum_data)
        cluster['P_reach'] = calc_P_reach(price, cluster['center'], atr_1h, momentum_data)
        cluster['EV'] = cluster['P_reach'] * cluster['P_bounce_adj']
    
    return sorted(clusters, key=lambda c: c['EV'], reverse=True)
```

#### Priorita 2: Momentum korekce P(odraz)

**Nový modul: `indicators/momentum_correction.py`**
```python
def calc_momentum_factor(candles_1m, atr_1h):
    """
    Vrací korekční faktor 0.4 – 1.5 pro P(odraz).
    
    Pomalý pokles (knoty, zmenšující se svíčky) → > 1.0
    Rychlý pád (velká těla, rostoucí volume) → < 1.0
    """
    last_5 = candles_1m[-5:]
    prev_5 = candles_1m[-10:-5]
    
    # Drop vs ATR
    price_move = abs(last_5[-1]['close'] - last_5[0]['open'])
    drop_ratio = price_move / atr_1h
    
    # Akcelerace
    speed_last = avg_candle_range(last_5)
    speed_prev = avg_candle_range(prev_5)
    acceleration = (speed_last - speed_prev) / max(speed_prev, 1)
    
    # Volume trend
    vol_last = sum(c['volume'] for c in last_5)
    vol_prev = sum(c['volume'] for c in prev_5)
    vol_ratio = vol_last / max(vol_prev, 1)
    
    # Body ratio (tělo vs celý range)
    body_ratio = avg(abs(c['close']-c['open']) / max(c['high']-c['low'], 0.01) for c in last_5)
    
    factor = 1.0
    
    # Drop vs ATR tabulka
    if drop_ratio < 0.5:   factor *= 1.2
    elif drop_ratio < 1.0: factor *= 1.0
    elif drop_ratio < 2.0: factor *= 0.7
    else:                  factor *= 0.4
    
    # Akcelerace
    if acceleration > 0:   factor *= 0.85
    else:                  factor *= 1.15
    
    # Volume
    if vol_ratio > 1.5:    factor *= 0.80
    elif vol_ratio < 0.7:  factor *= 1.20
    
    # Body ratio
    if body_ratio > 0.7:   factor *= 0.85
    elif body_ratio < 0.3: factor *= 1.15
    
    return max(0.4, min(1.5, factor))
```

#### Priorita 3: Adaptivní indikátory (pragmatický přístup)

Sonnet navrhuje "rozpustit" RSI na volume milestones. To je v čistém tvaru nepraktické pro Binance OHLCV data. Pragmatický kompromis:

**Nový modul: `indicators/adaptive.py`**
```python
def adaptive_rsi(close, volume, base_period=14):
    """
    RSI s adaptivní periodou podle volume aktivity.
    
    V období vysokého volume (NY open): perioda se zkracuje (citlivější)
    V období nízkého volume (asijská seance): perioda se prodlužuje (méně šumu)
    
    Toto je PRAKTICKÁ aproximace Sonnetova "rozpuštěného RSI"
    bez nutnosti tick-by-tick dat.
    """
    vol_ma = volume.rolling(100).mean()
    vol_ratio = volume / vol_ma
    
    # Adaptivní perioda: 10-20 (base 14)
    adaptive_period = (base_period / vol_ratio).clip(10, 20).astype(int)
    
    # Výpočet RSI s variabilní periodou
    # (implementačně: použít několik RSI s různými periodami a interpolovat)
    rsi_fast = calc_rsi(close, 10)
    rsi_base = calc_rsi(close, 14)
    rsi_slow = calc_rsi(close, 20)
    
    # Blend podle volume aktivity
    weight = (vol_ratio - 0.7) / (1.3 - 0.7)  # 0 = nízký volume, 1 = vysoký
    weight = weight.clip(0, 1)
    
    return rsi_fast * weight + rsi_slow * (1 - weight)


def volume_weighted_macd(close, volume, fast=12, slow=26, signal=9):
    """
    MACD kde EMA periody jsou váženy objemem.
    Při vysokém volume reaguje rychleji.
    """
    # Volume-weighted close: svíčky s vyšším volume mají větší vliv
    vw_close = (close * volume).cumsum() / volume.cumsum()
    
    macd_line = ema(vw_close, fast) - ema(vw_close, slow)
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    
    return macd_line, signal_line, histogram
```

#### Priorita 4: Vyčerpání detektor

**Nový modul: `indicators/exhaustion.py`**
```python
def detect_exhaustion(candles, rsi, macd_hist, stochrsi_k, volume):
    """
    Detekuje vyčerpání trendu. Vrací exhaustion_score 0.0 - 1.0.
    
    0.0 = trend zdravý
    0.5 = první známky vyčerpání
    0.8+ = trend zralý na obrat
    """
    signals = []
    
    # 1. RSI divergence
    price_highs = find_swing_highs(candles, window=5)
    rsi_at_highs = rsi[price_highs.index]
    if len(price_highs) >= 2:
        if price_highs[-1] > price_highs[-2] and rsi_at_highs[-1] < rsi_at_highs[-2]:
            signals.append(('rsi_divergence', 0.3))
    
    # 2. MACD plocha klesá
    current_cycle_area = abs(macd_hist[last_zero_cross:]).sum()
    prev_cycle_area = abs(macd_hist[prev_zero_cross:last_zero_cross]).sum()
    if current_cycle_area < prev_cycle_area * 0.7:
        signals.append(('macd_area_decline', 0.25))
    
    # 3. Volume klesá při pohybu ve směru trendu
    trend_volume = volume_during_trend_moves(candles, volume)
    if trend_volume[-5:].mean() < trend_volume[-20:].mean() * 0.7:
        signals.append(('volume_exhaustion', 0.25))
    
    # 4. Svíčky se zmenšují (těla menší, knoty delší)
    body_trend = body_sizes[-5:].mean() / body_sizes[-20:].mean()
    wick_trend = wick_sizes[-5:].mean() / wick_sizes[-20:].mean()
    if body_trend < 0.6 and wick_trend > 1.3:
        signals.append(('candle_exhaustion', 0.2))
    
    # Celkové skóre
    score = sum(weight for _, weight in signals)
    return min(1.0, score), signals
```

### 3.2 Co přidat do btc_ticker.py (Monitor)

#### Priorita 1: Real-time vyčerpání tracking

```python
def ticker_exhaustion_display(exhaustion_score, signals):
    """
    Ticker zobrazí stav vyčerpání v každém cyklu (30s).
    """
    if exhaustion_score < 0.3:
        return "Trend: ZDRAVÝ — momentum potvrzuje"
    elif exhaustion_score < 0.6:
        active = [name for name, _ in signals]
        return f"Trend: PRVNÍ ZNÁMKY VYČERPÁNÍ — {', '.join(active)}"
    elif exhaustion_score < 0.8:
        return "Trend: VYČERPÁVÁNÍ — připrav DCA / hedge"
    else:
        return "Trend: ZRALÝ NA OBRAT — hledej vstup"
```

#### Priorita 2: Hladina pod útokem — live tracking

```python
def track_level_resilience(cluster, test_history):
    """
    Sleduje jak hladina reaguje na opakované testy.
    
    Výstup pro ticker:
    "HVN 71,500: test #3, odolnost 62% (klesá), odraz -15% vs předchozí"
    """
    tests = [t for t in test_history if abs(t['price'] - cluster['center']) < cluster['width']]
    
    if len(tests) < 2:
        return f"Hladina {cluster['center']:.0f}: zatím {len(tests)} test(y), odolnost stabilní"
    
    # Porovnej poslední test s předchozím
    last_bounce = tests[-1]['bounce_size']
    prev_bounce = tests[-2]['bounce_size']
    bounce_change = (last_bounce - prev_bounce) / prev_bounce * 100
    
    # Odolnost po testu
    decay = 0.8 ** (len(tests) - 1)  # placeholder, Hyperopt
    resilience = cluster['P_bounce_adj'] * decay * 100
    
    trend = "klesá" if bounce_change < -10 else "stabilní" if bounce_change < 10 else "roste"
    
    return (f"Hladina {cluster['center']:.0f}: test #{len(tests)}, "
            f"odolnost {resilience:.0f}% ({trend}), "
            f"odraz {bounce_change:+.0f}% vs předchozí")
```

#### Priorita 3: Absorpce pattern detektor

```python
def detect_absorption(candles_1m, volume, cvd, cluster_price, window=10):
    """
    Detekuje absorpci na hladině — velký volume, malý pohyb ceny.
    
    Vrací:
        'ABSORPTION_BUY': limitní kupci absorbují prodejce → bullish
        'ABSORPTION_SELL': limitní prodejci absorbují kupce → bearish  
        'NONE': žádná absorpce
    """
    near_level = [c for c in candles_1m[-window:]
                  if abs(c['close'] - cluster_price) / cluster_price < 0.001]
    
    if len(near_level) < 3:
        return 'NONE', 0.0
    
    avg_volume = sum(c['volume'] for c in near_level) / len(near_level)
    avg_range = sum(c['high'] - c['low'] for c in near_level) / len(near_level)
    normal_volume = sum(c['volume'] for c in candles_1m[-60:]) / 60
    
    # Vysoký volume + malý range = absorpce
    volume_ratio = avg_volume / normal_volume
    range_ratio = avg_range / atr_1m
    
    if volume_ratio > 2.0 and range_ratio < 0.5:
        # Kdo absorbuje? CVD rozhodne
        cvd_delta = cvd[-1] - cvd[-window]
        if cvd_delta > 0:
            return 'ABSORPTION_BUY', min(1.0, volume_ratio / 4)
        else:
            return 'ABSORPTION_SELL', min(1.0, volume_ratio / 4)
    
    return 'NONE', 0.0
```

### 3.3 Mapování Sonnet konceptů → Prediktor moduly

| Sonnet koncept | Kde v prediktoru | Modul | Priorita |
|----------------|------------------|-------|----------|
| POC / VA / Volume Profile | find_confluence_clusters() | indicators/volume_profile.py | P1 |
| P(reversal) multiplikativní | calc_P_bounce() | strategies/probability.py | P1 |
| Momentum korekce | calc_momentum_factor() | indicators/momentum_correction.py | P1 |
| Expected Value | EV = P(dosáhne) × P(odrazí) | strategies/dca_planner.py | P1 |
| Adaptivní RSI | adaptive_rsi() | indicators/adaptive.py | P2 |
| Vyčerpání detektor | detect_exhaustion() | indicators/exhaustion.py | P2 |
| Absorpce detektor | detect_absorption() | indicators/exhaustion.py | P2 |
| Regime filtr | detect_regime() | filters/market_regime.py | P2 |
| Fraktální confluence | cluster across TFs | indicators/volume_profile.py | P3 |
| Volume Velocity | real-time dV/dt | btc_ticker.py | P3 |
| "Rozpuštěné" TF | adaptive periods | indicators/adaptive.py | P3 |
| Anchored VP | anchored_volume_profile() | indicators/volume_profile.py | P3 |
| OI/Funding exhaust | external_pressure() | indicators/external.py | P4 |

### 3.4 Co NEIMPLEMENTOVAT (a proč)

1. **Sonnetova lineární rovnice s fixními vahami** — nahrazeno multiplikativním modelem + Hyperopt
2. **E_momentum = Σ|Δprice| / Σ|volume|** — nesmyslné jednotky, nahrazeno divergence-based exhaustion
3. **σ_confluence** — nejasně definované, nahrazeno clustering s tolerance_pct
4. **Plně volume-based RSI** — vyžaduje tick data, nahrazeno adaptivním RSI s volume weighting
5. **R = V_zone / n_touches** — příliš zjednodušené, nahrazeno decay_factor s time_recovery

---

## ČÁST 4: DOPORUČENÁ STRUKTURA ZADÁNÍ PRO CC

### 4.1 Fáze implementace

```
FÁZE 1 (ZÁKLAD): Confluence Cluster detektor
├── indicators/volume_profile.py  — HVN/LVN z historických dat
├── strategies/probability.py     — P(odraz) multiplikativní model
├── strategies/dca_planner.py     — EV výpočet, DCA zóny
└── TEST: porovnat clustery vs aktuální support/resistance v btc_live.py

FÁZE 2 (DYNAMIKA): Momentum a vyčerpání
├── indicators/momentum_correction.py  — korekční faktor pro P(odraz)
├── indicators/exhaustion.py           — RSI/MACD/volume divergence
├── indicators/adaptive.py             — volume-weighted RSI/MACD
└── TEST: backtest na 30 dnech — zlepšuje momentum korekce timing?

FÁZE 3 (LIVE): Ticker integrace
├── btc_ticker.py rozšíření:
│   ├── real-time resilience tracking
│   ├── absorpce pattern detektor
│   └── exhaustion display
└── TEST: paper trading 1 týden

FÁZE 4 (REGIME): Filtr manipulace
├── filters/market_regime.py  — delta volume, OI, funding
└── TEST: identifikuje známé manipulation sweepy v historii?

FÁZE 5 (KALIBRACE): Hyperopt
├── Všechny P_base hodnoty
├── Decay factors
├── Momentum korekční koeficienty
└── Adaptivní RSI parametry
```

### 4.2 Formát zadání pro CC (šablona)

```markdown
## Úkol: [název]

### Kontext
- Které soubory jsou relevantní
- Co už existuje a funguje
- Co tento úkol mění/přidává

### Specifikace
- Přesný vstup (jaká data, jaký formát)
- Přesný výstup (jaký typ, jaký formát)
- Přesná logika (rovnice, pseudokód, prahy)
- Edge cases (co když data chybí, co když je volume 0)

### Omezení
- Nesmí rozbít existující funkčnost
- Nesmí přidat závislost na X
- Musí běžet v rámci 30s cyklu tickeru

### Ověření
- Jak poznat že to funguje správně
- Konkrétní test case s očekávaným výstupem
- Porovnání s aktuálním stavem

### Nezačínej dokud
- [prerekvizita 1] není hotová
- [prerekvizita 2] není otestována
```
