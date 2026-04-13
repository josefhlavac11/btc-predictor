# BTC Predictor — Session Summary
Datum: 2026-04-13 (odpoledne + večer)

### Projekt
Manuální BTC trading asistent — prediktor (btc_live.py) + monitor (btc_ticker.py). Josef obchoduje DCA na predikované zóny. Cílová architektura: Freqtrade + FreqAI. Branch: josefhlavac11-patch-1.

---

### Co jsme dnes řešili

**1. ZADANI_CC.md v1 → v2 — strukturované zadání pro Claude Code**
- Vytvořeno kompletní zadání s 10 fázemi
- Identifikovány závislosti mezi úkoly
- Definovány konkrétní ověření pro každý úkol
- Identifikována místa kde teorie není dostatečně přesná pro implementaci

**2. Architektura — čtyři vrstvy**
- Vrstva 1: Trend Context (aktuální režim 1H/4H)
- Vrstva 2: Signal Generator (triggery + klasifikace setupů)
- Vrstva 3: Trade Manager (správa pozice, DCA, state machine)
- Vrstva 4: Regime Shift Predictor (predikce změny trendu + ETA + scénáře A/B)

**3. Regime Shift — kompletní definice**
- 4 kategorie po 3 bodech (Momentum, Trend, Objem, Kontext) = 12 bodů
- Pravidlo: každá kategorie musí mít ≥1 bod (zabraňuje false signals)
- 4 stupně: žádný shift → formuje se → pravděpodobný → potvrzený
- Každý stupeň mění DCA alokaci a hedge velikost
- Hedge short se postupně zmenšuje s rostoucím stupněm

**4. DCA logika — sekvenční rozhodování**
- Josef nerozloží 3 příkazy dopředu najednou — rozhoduje sekvenčně
- Nákup 1: anticipatory na zóně, nákup 2: retest pokud zóna drží, nákup 3: safety pod zónou
- Kvalita retestu rozhoduje jestli přidá nebo ne (volume ratio, CVD, resilience change)
- Ticker musí vyhodnotit retest a dát jasné doporučení: KUP / NEKUPUJ / POČKEJ

**5. Hladiny — zásadní přepracování přístupu**
- Tři typy: vypočítané (Fib/Gann), volume-based (HVN), discovered (z dat)
- Psychologické hladiny závisí na seanci — EUR v Evropě, USDT v Americe, KRW v Asii
- KRITICKÁ KOREKCE: nepřepočítávat kurzem! Příkazy na Binance jsou fixní USDC ceny z momentu zadání, kurz se od té doby posunul
- Hledáme OB clustery a zpětně identifikujeme měnu původu
- Multi-currency konvergence = super-zóna (3 měny na 150 USDC = mega odolné)
- Resilience = volume × diverzita měn. Jedna měna = křehké, tři = odolné.
- Ticker sleduje rozpad zón v reálném čase (diverzita klesá → varování)

**6. DCA plán jako celek — ne perfektní vstup**
- Josef akceptuje mírně vyšší vstup — DCA zprůměruje
- Klíčová metrika: "dává celý DCA plán smysl ve všech scénářích?"
- Ticker ukazuje průměrnou cenu pro každý scénář (jen 1. nákup / 1.+2. / všechny 3)
- Nejhorší scénář musí být stále profitabilní při Scalp TP

**7. Predikce optimální zóny**
- Ticker odpovídá na: "jsem v rozumné zóně nebo mám čekat?"
- P(cena dosáhne zóny) z ATR distance, momentum, regime
- P(cena se otočí na hladině) ze síly zóny, kaskády, CVD
- Situace "zóna nedosažena" — cena se otočila NAD zónou, ticker nabídne A) vstup výš, B) čekej na retest, C) pásuj

**8. Timeframe hierarchie pro scalping**
- KRITICKÝ POZNATEK: 15M StochRSI 97 = pod tím proběhne několik kompletních 1M cyklů
- Každý 1M cyklus dolů = scalp nákupní příležitost
- 15M dává povolení (kontext), 1M/3M dává timing (vstup)
- Ticker nesmí říkat "korekce za pár 15M svíček" pro scalp — musí říct "korekce na 1M do 2-5 minut"

**9. Trigger registry — otevřená architektura**
- Každý trigger = samostatná funkce se standardním interface
- Registry pattern — přidáš funkci, zaregistruješ, hotovo
- Hyperopt optimalizuje váhy mezi triggery
- Setup klasifikace: trigger → setup typ → trade plan
- Fallback pro neznámé setupy: "příležitost detekována ale neodpovídá známému setupu"

**10. Position State Machine**
- 6 stavů: WAITING → FILLED → HOLDING / DCA_READY → HEDGE_ACTIVE → EXIT
- Každý stav má omezený set akcí = redukce cognitive overload
- Přechody řízeny kvalitou odrazu, kaskádou, regime shiftem

**11. Feedback loop**
- Po každém obchodu zaznamenat: triggery, regime, kaskádu, bounce quality, výsledek
- Palivo pro Hyperopt — najde kombinace které fungují

**12. Organizační poznámky**
- sunpavlik se připojí jako contributor (programátor s algo trading zkušeností)
- Potřebuje ARCHITECTURE.md — čistě technický dokument
- CC má pracovat postupně: jedna funkce → odzkoušet → další
- Nedělat velké přepisy najednou

**13. Live trading diskuze**
- Josef prodal pozice z 70,953 a 71,492 kolem 72,350 = profitabilní trade
- Doporučení: čekat na korekci, nezadávat FOMO nákup na vrcholu
- DCA plán pro korekci: 71,750 / 71,500 / 71,300

**14. Analýza tvorby dna 19:49-20:31 na 5M**
- 19:50: dlouhý dolní knot = první signál odmítnutí
- Začátečník koupí hned na 72,182. Zkušený počká 1-2 svíčky na 1M, vezme low (72,150), odečte 20-30 buffer = limit na ~72,120
- 20:05-20:15: zmenšující se těla, prodlužující se dolní knoty = vyčerpání prodejců
- 20:25: retest pod první dno — většina scalpérů by to považovala za pokračování pádu, ale 15M SK=1.4 říká že na 15M jsme na dně
- Cena se otočila na ~72,085 — pravděpodobně skrytá HVN

**15. Fibonacci multi-TF konvergence**
- Fib závisí zásadně na tom ODKUD ho táhneš
- Situace 1: 15M cyklus končí → Fib od hlavního lokálního dna
- Situace 2: korekce uvnitř vyššího cyklu → Fib od předchozího NIŽŠÍHO lokálního dna
- Rozlišení: co dominuje (15M SK z 97 padá = Situace 1, 15M SK na 50 s Higher Highs = Situace 2)
- Překrývající se Fib z různých TF a různých kotevních bodů se vzájemně NÁSOBÍ v pravděpodobnosti

**16. P(odraz) — pravděpodobnostní model hladin**
- Každý nezávislý zdroj (Fib, HVN, EMA, psych level) snižuje P(průraz)
- P(průraz) = (1-P1) × (1-P2) × (1-P3)...
- P(odraz) = 1 - P(průraz)
- Čtyři slabé zdroje po 30% → P(odraz) = 76%
- Base probability per zdroj = placeholder, Hyperopt nakalibruje

**17. Momentum korekce P(odraz) — setrvačnost**
- Statická P(odraz) nestačí — záleží na rychlosti a síle pohybu
- Pomalý pokles (zmenšující se svíčky, knoty) → korekční faktor > 1.0 → hladina drží lépe
- Rychlý pád (velká těla, rostoucí volume, akcelerace) → faktor < 1.0 → hladina možná nedrží
- Měří se: drop_vs_atr, akcelerace, volume trend, body_ratio (tělo vs knoty)
- Platí i obráceně: silný momentum NAHORU brzdí korekci → hladiny jsou silnější ale cena k nim možná nedojde

**18. Expected Value pro DCA zóny**
- Klíčová metrika: EV = P(cena dosáhne hladiny) × P(cena se odrazí)
- Hladina s P(odraz)=90% ale P(dosáhne)=10% má EV=0.09 → k ničemu
- Hladina s P(odraz)=50% ale P(dosáhne)=80% má EV=0.40 → lepší DCA zóna
- DCA zóny seřadit podle EV, ne podle P(odraz)
- Při silném momentum nahoru: kupuj výš (nižší hladiny nedosažitelné)
- Při slabém momentum / pádu: kupuj na silnějších hladinách níž

**19. Vstup nad HVN — pravidlo posunu**
- Pokud DCA zóna je blízko HVN → posuň nákup k HVN
- Posun maximálně o malé "drobné" (placeholder, kalibrovat)
- Nekupuj NA HVN (riskuješ že tam nedojde)
- Nekupuj daleko NAD HVN (zabíjíš R:R pro scalp)
- Důvod posunu: HVN odrazí cenu BEZ tebe pokud čekáš pod ní

**20. Agresivní DCA při silném setupu**
- Silný setup (overlap seancí, 4H shift, vysoký volume) → užší spacing, víc nákupů rychle
- Slabý setup (jedna seance, bear trend) → širší spacing, čekej na potvrzení
- Při silném setupu chceš být v pozici s objemem — 2 nákupy s spacing 50 USDC, ne 1 nákup na perfektní ceně

---

### Aktuální stav kódu

| Soubor | Stav |
|--------|------|
| 2026-04-13-01-34/btc_live.py | Aktivní, v5.1, 3 bugy |
| 2026-04-13-01-34/btc_ticker.py | Aktivní, v5.1, 3 bugy |
| STRATEGIE.md | v2.0, kompletní |
| KONTEXTCC.md | Aktuální |
| ZADANI_CC_v2.md | Nové — s kompletní architekturou |

---

### Na řadě

1. Josef vloží SESSION_SUMMARY.md a ZADANI_CC_v2.md do Projects
2. Vytvořit ARCHITECTURE.md pro sunpavlika
3. CC začne Fází 1 (bugy) → pak refaktor do modulární struktury
4. Josef definuje Setup 2, 3 a další (potřebuje screenshoty a popis)
5. CC neprogramuje setupy sám — čeká na Josefovu definici
6. Dopracovat: EV logika, momentum korekce, Fib multi-TF → do ZADANI_CC

---

### Klíčová rozhodnutí a důvody
- Nepřepočítávat psychologické hladiny kurzem — příkazy jsou fixní USDC z momentu zadání
- Multi-currency resilience je důležitější než single-currency volume
- DCA plán se hodnotí jako celek (všechny scénáře profitabilní?) ne jako jednotlivé vstupy
- 15M kontext dává povolení, 1M/3M dává timing — ticker musí myslet v timeframu obchodu
- Postupný build: jedna funkce → test → další. Ne velké přepisy.
- Architektura musí být otevřená pro Hyperopt — trigger registry, standardní interface, weights
- P(odraz) = multiplikativní model nezávislých zdrojů, ne sčítání
- Expected Value (P(dosáhne)×P(odrazí)) rozhoduje kde DCA, ne P(odraz) samotná
- Momentum korekce P(odraz) — rychlý pád oslabuje hladiny, silný uptrend brzdí korekce
- Vstup těsně nad HVN, ne daleko — malý posun nekazí R:R
- Silný setup = agresivní DCA (víc nákupů, užší spacing)

---

### Kontext který potřebuji mít v hlavě
- Josef obchoduje DCA na predikované zóny, ne single entry
- Mírně vyšší vstup nevadí — DCA to opraví, důležité je nebýt v totálně špatné zóně
- Retest kvalita rozhoduje o druhém nákupu (volume ratio, CVD, resilience)
- Skryté hladiny = OB clustery na historických psychologických cenách v jiných měnách
- Session-dependent hladiny (EUR vs USDT vs KRW) s plynulými přechody
- sunpavlik přijde — potřebuje ARCHITECTURE.md
- Setupy 2+ nejsou definovány — nezačínat kódem bez Josefova vstupu
- Prediktor popisuje očekávané svíčkové patterny (hint + anti_hint)
- Final run na 15M = 1M/3M cykly pod tím jsou nákupní příležitosti
- Zkušený trader čte první knot, čeká 1-2 svíčky, odečte buffer → limit buy
- Fib se táhne od různých den podle kontextu (15M končí vs 1H pokračuje)
- DCA zóny z EV, ne z rovnoměrného rozložení ani jen z P(odraz)
- Momentum = fyzika: setrvačnost buď proráží hladiny nebo brzdí korekce

---

### Selfcheck
- Mám dost detailů abych navázal? ANO
- Zachycena všechna klíčová rozhodnutí? ANO
- Chybí kontext? Setupy 2+ nedefinovány. ARCHITECTURE.md neexistuje. sunpavlik kontext neznámý. EV logika a momentum korekce rozpracovány ale ne finalizovány v ZADANI_CC — dopracovat příště.
