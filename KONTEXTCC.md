# KONTEXTCC.md — Souhrn práce za posledních 10 hodin
Datum: 2026-04-13

---

## 1. Dokumentace projektu — LOGIC.md
**Co:** Vytvořen nový soubor `LOGIC.md` jako technická reference.
**Jak:** Přečetl jsem `btc_live.py` a `btc_ticker.py`, extrahoval přesné parametry.
**Výsledek:** Dokumentuje timeframy (počty svíček, časový dosah), parametry všech indikátorů (RSI, MACD, StochRSI, EMA, VWAP, ATR), 9 confluence podmínek, prahy signálů a formuli trade setupu.

---

## 2. Vytvoření STRATEGIE.md v1.0 → v2.0
**Co:** Vytvořen a dvakrát přepsán soubor `STRATEGIE.md` na finální verzi 2.0.
**Jak:** Uživatel dodal obsah, přepsán celý soubor, pushnut na GitHub (branch `josefhlavac11-patch-1`).
**Výsledek:** Kompletní dokumentace trading strategie — HVN/LVN, Fibonacci, MACD cykly, StochRSI confluence, final run detekce, manipulace vs organický pohyb, dva režimy trhu. Commit `8ac0c9e`.

---

## 3. Analýza chyb ve v5 signální logice
**Co:** Identifikace 3 root causes proč v5 nefungoval správně.
**Jak:** Přečetl jsem `btc_live.py` a `btc_ticker.py`, analyzoval `classify_trade()` a `backtest_validate()`.
**Výsledek:**
- Momentum korelace ~0: skóre zahrnovalo záporně korelované indikátory bez inverze
- Prediktor doporučoval Swing (9% hit rate) místo Scalp (81%): bodovací systém nedával prioritu přesnějším typům
- 28 zmeškaných obchodů: RSI ≤40 + SK ≤25 prahy příliš přísné, podmínky kontrolovány přesně na baru dna (confirmation lag)

---

## 4. Přepis classify_trade() — hierarchická logika
**Co:** Kompletní přepis funkce `classify_trade()` v `btc_live.py` a `btc_ticker.py`.
**Jak:** Záloha do nové složky `2026-04-13-01-34/`, pak editace. Bodovací systém nahrazen hierarchií:
- 1M SK ≤20 + MH otáčí → SCALP
- 3M SK ≤30 + 5M SK ≤35 → MICRO-SWING
- 15M SK ≤30 + 1H bull → SWING
- jinak → žádný signál
**Výsledek:** Funkce vrací `signal: bool`, `conditions: dict`, `confidence`. Commit `2339fb4`.

---

## 5. Oprava výstupu — guard pro no-signal stav
**Co:** Výstup obou skriptů upraven tak, aby při `signal=False` nezobrazoval typ obchodu ani TP/SL tabulku.
**Jak:** Přidána podmínka `if trade["signal"]:` před sekci obchodu, jinak `"Žádný signál — podmínky nesplněny"`.
**Výsledek:** Commit `2d8369a`, oba soubory v `2026-04-13-01-34/`.

---

## 6. Krok 2 — Oprava confirmation lag + EMA200 + kontext filtr
**Co:** Čtyři změny najednou v `btc_live.py` a `btc_ticker.py`.
**Jak:**

**a) EMA200:**
- `btc_live.py add_indicators()`: přidán `df["e200"] = ema(df["close"], 200)`
- `btc_ticker.py calc_indicators()`: přidán `e200 = s.ewm(span=200).mean()` do return dict

**b) Backtest prahy uvolněny** (`btc_ticker.py backtest_validate()`):
- `RSI ≤40` → `RSI ≤50`
- `SK ≤25` → `SK ≤35`
- Podmínky kontrolovány v okně `li-5 až li+2` (ne přesně na baru `li`) — zachytí signál i když indikátory dozrály dříve nebo o pár svíček poté

**c) Kontext filtr:**
- Po `classify_trade()`: pokud `cena < VWAP − 2×ATR` → `trade["signal"] = False`, typ Swing/Swing+ zakázán
- Veto důvod uložen do `trade["veto"]` a zobrazen ve výstupu

**d) Výstup:**
- Nový řádek s EMA200 a vzdáleností od VWAP
- Varování `!! KONTEXT FILTR AKTIVAN` pokud je filtr spuštěn
- `btc_ticker.py` sekce obchodu nyní také hlídá `trade["signal"]`

**Výsledek:** Oba soubory prošly `python -m py_compile` bez chyb. Commit `6a2b251`.

---

## 7. STRATEGIE.md — přidání nových sekcí (3 commity)

**Commit `e9e071e`** — dvě sekce najednou:
- Tři typy shortů (po výstřelu, zajišťovací, ve směru trendu) + pořadí obtížnosti
- Detekce dna vs trigger vstupu (2 momenty, 3 vstupní situace)
- Selektivní obchodování 2-5 obchodů denně (TYP A/B, kontext downtrendu)
- Riziko nuceného investora + SL pravidla
- Makro analýza BTC 2026 (strukturální shoda 2022 vs 2026, Camarilla zóny, on-chain indikátory)
- Architektura pravděpodobností — Freqtrade Hyperopt jako jádro

**Commit `c8f69e0`:**
- Modulární architektura projektu — směřování k Freqtrade
- 8 modulů Freqtrade (config, indikátory, vstupní/výstupní signály, Hyperopt, FreqAI, FreqUI, Telegram)
- Mapování `btc_live.py` funkcí na Freqtrade ekvivalenty
- Cílová adresářová struktura projektu
- 6 kroků prioritního refaktoringu

**Commit `f91fa39`:**
- LuxAlgo jako nezávislý oponentní posudek
- Princip: referenční bod bez závislosti v kódu
- Zobrazení shody (+8%/+12%) nebo rozdílu (−10%/−20%) v prediktoru
- Pravidla využití: opisovat logiku, nikdy nečekat na jejich aktualizace

---

## Stav na konci období

| Soubor | Stav |
|--------|------|
| `2026-04-13-01-34/btc_live.py` | Aktivní, opravený, EMA200 + kontext filtr |
| `2026-04-13-01-34/btc_ticker.py` | Aktivní, opravený, backtest okno li-5..li+2 |
| `STRATEGIE.md` | 700+ řádků, kompletní dokumentace |
| `LOGIC.md` | Technická reference indikátorů a prahů |

**Branch:** `josefhlavac11-patch-1`
**Celkem commitů za session:** 7
**Poslední commit:** `f91fa39`
