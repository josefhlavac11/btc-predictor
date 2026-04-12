# BTC Trading Strategie — Kompletní dokumentace
## Verze: 2.0

---

## Styl obchodování
- Manuální obchodování podle signálů prediktoru
- Skalping s přechodem do mikro swingovů
- Timeframy: 1M, 3M, 5M, 15M, 30M, 1H, 2H, 4H, 12H, 1D
- Aktivní hodiny: 6:00 - 23:00 SEČ
- Páry: BTC + korelované (ETH, SOL, BNB, AVAX)
- Analýza: TradingView, Exekuce: Binance app

---

## Dvouvrstvá architektura systému

### Vrstva 1 — Prediktor (btc_live.py)
Časový horizont: hodiny dopředu
- Identifikuje setup před tím než nastane
- Vypočítá očekávaný čas do ideálního vstupu
- Zpřesňuje odhad každých 30 sekund jak setup dozrává
- Navrhne připravené limit příkazy s pravděpodobnostmi

Výstup prediktoru:
- Vstupní cena s pravděpodobností dosažení v %
- TP úrovně s pravděpodobností a očekávaným časem dosažení
- SL umístění pod/nad HVN (ne pevné USDC)
- DCA rozdělení kapitálu podle pravděpodobnosti obratu
- Zajišťovací short/long příkazy předem připravené

### Vrstva 2 — Monitor (btc_ticker.py)
Časový horizont: sekundy po vyplnění příkazu, minuty během pozice
- Okamžitě po vyplnění příkazu vyhodnotí kvalitu odrazu
- Aktualizuje pravděpodobnosti každých 20-30 sekund
- Rozlišuje manipulation sweep od skutečného obratu
- Sleduje CVD a absorption pattern v reálném čase
- Upozorní na přechod do mikro swing režimu
- Doporučí: drž / vystup / DCA / posuň SL na BE

---

## Třístupňové notifikace
1. Informační (hodiny dopředu): "Za ~2h se formuje vstup na XX XXX"
2. Přípravná (15-30 minut): "Za ~20 minut připrav příkazy"
3. Akční (2-5 minut): "PRIPRAV SE — zadej příkazy teď"

---

## Vstupní logika

### Anticipatory entry
- Vstup před potvrzením na pravděpodobné hladině obratu
- Identifikace pomocí HVN + konvergence indikátorů + časová analýza
- Limit příkazy zadány dopředu — žádný stres při exekuci

### Retest entry
- Vstup při návratu k otestované hladině
- FVG (Fair Value Gap) jako přesný cíl retestů

### Counter-trend scalp
- Long při downtrendu na korekci
- Short při uptrendu na retracementu
- Podmínka: vysoké ATR + objem klesá při pohybu = slabý tlak

---

## DCA zónový vstup
- Kapitál se rozdělí do tří příkazů v zóně obratu
- Alokace přímo úměrná pravděpodobnosti obratu na dané ceně
- Hladina s nejvyšším Volume Profile = největší alokace
- Platí pro LONG i SHORT
- DCA short po rychlém pohybu: přidávej jak cena potvrzuje hlubší retracement

---

## Fibonacci — kompletní logika

### Výpočet vždy od skutečného dna/vrcholu
- Fibonacci se táhne od skutečného dna nebo vrcholu
- Nikdy od vstupní ceny
- Dno = skutečné minimum pohybu včetně krátkých výkyvů

### TP cíle — Fibonacci extenze od dna
- TP1 = dno + pohyb x 1.000
- TP2 = dno + pohyb x 1.272
- TP3 = dno + pohyb x 1.382  (často konverguje s VWAP)
- TP4 = dno + pohyb x 1.618
- TP5 = dno + pohyb x 2.000
- TP6 = dno + pohyb x 2.618

### Retracement cíle — dynamické s pravděpodobností
- 0.382 = primární TP shortu, pravděpodobnost ~85%
- 0.500 = sekundární TP, pravděpodobnost ~60%
- Golden Pocket 0.618-0.650, pravděpodobnost ~35%
- 0.618 přesný = max cíl, pravděpodobnost ~15%
- Pravděpodobnosti jsou dynamické podle objemu, ATR a režimu trhu
- Platí pro SHORT po rychlém růstu i LONG po rychlém poklesu

### Korekce v mikro swingy
- Po dosažení Fib extenze 1.000 očekávej korekci
- Pokud korekce nepřekročí 0.618 = struktura drží, long bezpečný
- Korekci zobchoduj zajišťovacím shortem od vrcholu
- TP shortu = 0.382 nebo 0.500 Fib retracementu
- Po odrazu od korekční hladiny = long znovu aktivní

---

## ATR korekční pattern
- Korekce odpovídají násobkům ATR oběma směry
- Typické hodnoty: 400 / 500 / 600 USDC podle denního ATR
- Výpočet: změř ATR na 1H nebo 4H = očekávaná korekce = násobek ATR
- HVN v oblasti korekce potvrzuje přesnou vstupní cenu
- Pattern platí symetricky nahoru i dolů

---

## Volume Profile — klíčová logika

### High Volume Node (HVN)
- Historicky nejvyšší objem na cenové hladině
- Cena se zpomalí, konsoliduje nebo otočí
- Základ pro vstupní ceny DCA i TP cíle
- SL vždy pod/nad HVN — nikdy ne pevné USDC od vstupu

### Low Volume Node (LVN)
- Minimum historických obchodů
- Cena projede rychle bez odporu
- Rychlé pohyby o stovky USDC vznikají v LVN zónách
- Po průlomu HVN = cena letí přes LVN k další HVN

### Použití v prediktoru
- Nejbližší HVN nad a pod cenou = přirozené TP cíle
- LVN mezi vstupem a TP = odhad rychlosti pohybu
- Přesnost cílení na desítky USDC

---

## Přechod skalp na mikro swing

### Tři situace přechodu
1. Plánovaný přechod — StochRSI reset + cena drží nad 0.618
2. Záchranný přechod — averaging down, nový TP od průměrné ceny
3. Korekce v mikro swingy — zobchodovat korekci shortem, long drží

### Pattern absorption na HVN
- Cena testuje HVN zespodu opakovaně
- Každý test: klesající sell volume + rostoucí buy delta (CVD)
- Každý odraz menší než předchozí = prodejci slábnou
- Šplhací pattern na 1M — zelené svíčky se stále méně odráží od HVN
- N-tý pokus = průraz do LVN nad HVN
- Anticipatory entry long těsně před průrazem
- Inverzně platí pro short shora

### Rozpoznání supportu na Fib / HVN
Po dopadu ceny na Fib úroveň nebo HVN sleduj první 2-3 svíčky 1M:

SILNÝ ODRAZ = čekej na absorption = long
- Velká zelená svíčka, objem roste, buy delta převažuje
- Krátký dolní knot = hladina drží pevně
- Pravděpodobnost supportu: ~71%

SLABÝ ODRAZ = připrav DCA níže
- Doji nebo malé tělo, nízký objem, delta neutrální
- Dlouhý horní knot = prodejci tlačí zpět okamžitě
- Pravděpodobnost dalšího pádu: ~66%
- Nový cíl = další Fib úroveň nebo HVN níže

### Poznámka k pořadí
Slabý nebo silný odraz se vyhodnocuje AŽ PO vyplnění příkazu.
Ticker okamžitě po vyplnění zobrazí vyhodnocení a doporučení.

### Signály skutečného dna vs zastávka před pádem

SIGNÁLY DNA:
- Objem při poklesu klesá = prodejci slábnou
- Klinger oscilátor se otáčí nahoru před cenou
- Buy delta roste i když cena ještě klesá = akumulace
- Dlouhé dolní knoty na HVN = odráží se
- 15M nebo 30M StochRSI v extrémní přeprodanosti
- MACD divergence = cena dělá nižší minimum ale MACD ne
- OI klesá při poklesu = čištění trhu

SIGNÁLY ZASTÁVKY:
- Objem při poklesu roste nebo drží = prodejci silní
- Při odrazu objem klesá = slabý odraz bez přesvědčení
- Velká těla červených svíček = rozhodný prodej
- OI roste při poklesu = nové short pozice
- Funding rate negativní = trh sází na pokles
- Equal lows pod zónou = magnetická hladina pro market makery

---

## Dva režimy trhu — pre-filter

### Režim 1: Manipulace/likvidita
- Velký hráč vysazuje stop lossy retail traderů
- Equal highs/lows, swing highs/lows = cíle manipulace
- Inducement = záměrný falešný signál před skutečným pohybem
- Indikátory lžou — ignoruj signály

### Režim 2: Organický pohyb
- Indikátory fungují spolehlivě
- Cena sleduje HVN, Fibonacci, EMA přesně
- Důvěřuj signálům

### Detekce režimu
- Delta objem — převaha kupujících nebo prodávajících
- Open interest — rostoucí OI = reálný trend, klesající = slábnutí
- Funding rate — extrémní hodnoty = přehřátý trh
- Order book — spoofing, náhlé zmizení likvidity

---

## MACD — kompletní logika

### Divergence
BULLISH DIVERGENCE = nákupní signál:
- Cena dělá nižší minimum
- MACD histogram dělá vyšší minimum (červené sloupce blednou)
- = prodejní síla slábne, obrat blízko

BEARISH DIVERGENCE = prodejní signál:
- Cena dělá vyšší maximum
- MACD histogram dělá nižší maximum (zelené sloupce blednou)
- = kupní síla slábne, korekce nebo obrat blízko

### Teorie zachování plochy MACD cyklů
- Velká plocha zeleného cyklu musí být splacena v červeném cyklu
- Buď jednou velkou červenou plochou
- Nebo několika menšími červenými obloučky
- Čáry MACD vysoko nad nulovou linií = gravitace je táhne zpět
- Silný cyklus na 15M nebo 1H přebíjí cykly na 3M a 5M
- Velká zelená plocha na 15M = korekce se rozmelí do několika
  menších červených vln na 3M a 5M = každá vlna = příležitost pro skalp

### Decoupling indikátorů při silném trendu

NORMÁLNÍ REŽIM:
- MACD a StochRSI jdou spolu
- Oba potvrzují vstup = standardní confluence

FINAL RUN REŽIM:
- StochRSI drží nad 80 = přetrvávající překoupenost
- MACD dělá vlny nahoru a dolů pod tím
- Normální pravidlo překoupenost = prodej NEPLATÍ
- Každá MACD korekce dolů = příležitost přidat do longu

FINAL RUN — detekce:
- StochRSI nad 80 více než 3-5 svíček na 15M
- MACD dělá vlny ale čáry zůstávají nad nulou
- Objem roste na každé vlně nahoru
- Cena dělá vyšší maxima bez hlubších korekcí

### Predikce konce final runu
- Vrchol se NEPREDIKUJE čekáním na pokles StochRSI pod 80
  = příliš pozdě, cena už 300 USDC níže
- Strategie: posunovat limit příkaz postupně za rychle
  stoupající cenou na Fib extenze
- Lepší vzít část pohybu než čekat na přesné maximum

Skryté souvztažnosti k hledání v databázi:
- Funding rate překročí práh X
- OI přestane růst při pokračující ceně
- Delta objem klesá při rostoucí ceně
- Cena dosáhla Fib extenze 1.382 nebo 1.618
- Vysáty equal highs nad cenou
- Timeframová bearish divergence na 15M nebo 1H
Souběh těchto faktorů = pravděpodobnost konce final runu > 75%
Přesné hodnoty určí analýza historické databáze.

---

## StochRSI — confluence logika

### Základní confluence
- 3M a 5M StochRSI se potkají pod úrovní 30 = silný signál
- Pokud přidá i 15M StochRSI pod 30 = velmi silný signál
- Není to pevné pravidlo ale výrazně zvyšuje pravděpodobnost

### Agresivní pohyb nahoru
- Agresivnější pohyb nahoru když StochRSI 15M je nad 50
  v době confluence 3M a 5M
- = vyšší timeframe potvrzuje směr = silnější impulz

### Final run a StochRSI
- StochRSI drží nad 80 = final run aktivní
- Inverzně: drží pod 20 = silný downtrend
  = MACD vlny nahoru jsou jen korekce = příležitost pro short

---

## Svíčky a patterny

### Princip spolupráce člověk + stroj
Mozek zpracuje vizuální pattern svíčky za milisekundy.
Svíčky jsou kompresní formát dat:
- Tvar = momentum
- Barva = směr dominance
- Knot = odmítnutí ceny = boj kupující vs prodávající
- Tělo = kdo kontroloval svíčku od open do close
- Velikost = síla pohybu
Svíčkový pattern = CVD + momentum + objem + směr v jednom obrazci.

### Sekvence svíček jako obraz objemu a momentu
- Tři klesající korekční červené s vyššími dny = CVD roste
  i když cena klesá = akumulace
- Každá zelená close výše než předchozí = Higher Highs
- Každá červená low výše než předchozí = Higher Lows
- Velké zelené tělo = kupující dominují celou svíčku
- Dlouhý dolní knot = prodejci odmítnuti = buy pressure
- Close blízko high = kupující kontrolují až do konce

### Timeframe hierarchie svíček
- 1M = tvorba patternu, hodně šumu, čti jako proud momentu
- 3M = agreguje 1M sekvence, spolehlivější signál, napřed před 5M
- 5M = potvrzuje 3M ale se zpožděním, méně spolehlivý samostatně
- 15M = kontext a trend, kde jsme v cyklu
3M je klíčový timeframe pro skalp a mikro swing — zachytí
sekvenci dřív než 5M a není tak zašuměný jako 1M.

### Ideální workflow
PREDIKTOR = upozornění na telefon: "dívej se sem, za X minut vstup"
TY otevřeš TradingView = přečteš svíčky vizuálně za milisekundy
TICKER = potvrdí nebo varuje: "CVD roste, momentum potvrzuje"
TY rozhodneš a zadáš limit příkaz na Binance

### Zásadní princip
Prediktor a ticker NIKDY nenahrazují vizuální čtení svíček.
Stroj počítá pravděpodobnosti na pozadí.
Ty čteš kontext a syntetizuješ vizuálně.
Výsledek = člověk + stroj kde každý dělá co umí nejlépe.

### Ergonomie — ticker zobrazuje závěry ne data
ŠPATNĚ: proud čísel = cognitive overload
SPRÁVNĚ:
- MACD 3M: čekej na potvrzení vyššího dna
- StochRSI: confluence 3M+5M pod 30
- RSI: neutrální, prostor nahoru
- Celkové hodnocení: 2/3 podmínek splněno, čekej

---

## Princip časové platnosti predikce

### Každá predikce má tři složky
1. CO očekáváme (cena, hladina, směr)
2. KDY to očekáváme (čas, časové okno)
3. JAK POZNÁME že predikce selhává (co sledovat, kdy jednat)
Bez složky 3 = sedíš v pozici a doufáš.

### Čtyři stavy průběhu predikce
Ticker každých 30 sekund přepočítá pravděpodobnost a stav:

STAV 1 — Rychleji než plán
- CVD roste rychleji, objem silný
- Akce: neuzavírej předčasně, posuň TP výše
- Upozornění: možný final run, sleduj Fib extenze

STAV 2 — Podle plánu
- Cena i čas v toleranci +-10%
- Akce: drž, nic nedělej, čekej na TP

STAV 3 — Pomaleji ale směr drží
- Čas vypršel ale CVD neutrální, směr zachován
- Akce: prodluž okno, sleduj CVD
- Pokud CVD roste = predikce obnoví tempo
- Pokud CVD klesá = přechod do stavu 4

STAV 4 — Selhává
- Časové okno vypršelo, cena jde špatným směrem
- Vyhodnoť proč:
  A) Změnil se vyšší timeframe? = vystup
  B) Manipulation sweep? = drž, čekej na návrat
  C) Fundamentální změna? = vystup celou pozici

### Prahová hodnota
- Pravděpodobnost dosažení TP pod 50% = jednej
- Bez akce při selhání = největší zdroj zbytečných ztrát

---

## Princip předčasného výstupu

### Předčasný výstup = stejně velká ztráta jako špatný vstup

### Tři nepřátelé předčasného výstupu
1. Velcí hráči — záměrný spike proti tobě těsně před TP
   aby vysadili retailové obchodníky
2. Burza — poplatky za předčasný výstup + znovu vstup
   = dvojité poplatky, žere zisk ze skalpů
3. Vlastní psychologie — strach ze ztráty zisku který už máš

### Jak ticker rozliší manipulation od skutečného obratu

MANIPULATION SWEEP = nejednej:
- Rychlý spike dolů na nízký objem
- CVD se nezměnilo
- Vrátí se za 1-3 svíčky
- Ticker zobrazí: "Manipulation sweep 87% — DRZI, nepanikař"

SKUTEČNÝ OBRAT = jednej:
- Objem roste při pohybu proti tobě
- CVD klesá
- MACD začíná divergovat
- Ticker zobrazí: "Skutečný obrat 73% — ZVAZ VYSTUP"

---

## SL logika
- SL nikdy ne pevné USDC od vstupu
- SL vždy pod/nad nejbližší HVN pod/nad vstupem
- Manipulation sweep (nízký objem) = drž nebo znovu vstup
- Skutečný obrat (vysoký objem) = vystup

## Pravděpodobnost návratu ceny
- Ticker zobrazuje pravděpodobnost návratu pokud jsi mimo pozici
- Horizont: 1h / 4h / 24h / 48h
- Základ pro rozhodnutí: znovu vstoupit nebo čekat

## Market Structure
- Break of Structure (BOS) = trend pokračuje
- Change of Character (CHoCH) = trend se mění, opatrnost
- Inducement = falešný průlom před skutečným pohybem
- Counter-trend scalp povolen při vysokém ATR a slabém objemu

## Časová analýza seancí
- 2:00-5:00 SEČ: asijská konsolidace, falešné pohyby, vyhni se
- 9:00-11:00 SEČ: londýnský open, první velký pohyb dne
- 15:00-17:00 SEČ: NY open, největší volatilita, skutečný směr dne
- 21:00-23:00 SEČ: konec NY seance, uzavírání pozic

## BTC Dominance filtr
- BTC.D roste = obchoduj BTC long nebo alt short
- BTC.D klesá = alt longy mají vyšší pravděpodobnost
- BTC.D konsoliduje = zvýšená opatrnost na korelované páry

## Korelační divergence
- BTC klesá ale ETH drží = ETH relativně silný, long ETH vyšší pravděpodobnost
- BTC roste ale ETH zaostává = slabost altu, vyhni se alt longu

## Řízení pozice po vstupu
- Parciální výstup na každé HVN nebo pouze na finálním TP = volitelné
- SL přesunout na BE po dosažení první HVN
- Přidat do pozice při retestaci vstupní zóny pokud objem potvrzuje
- Ticker identifikuje změnu trendu = zavři vše

## Riziko management
- Maximální alokace do vysoce korelovaných párů = definovat
- BTC + ETH + SOL = jeden obchod z hlediska rizika ne tři nezávislé
- Zajišťovací short/long připravit dopředu jako limit příkaz

---

## Indikátory
- RSI 14
- MACD (12, 26, 9)
- StochRSI (14, 14, 3, 3)
- Klinger oscilátor
- EMA 9, 20, 99, 200
- VWAP s denním resetem
- Fibonacci retracement a extenze
- Camarilla pivoty
- Gann úrovně
- ATR (14)
- Volume Profile (HVN/LVN)
- Cumulative Volume Delta (CVD)
- Open Interest
- Funding rate
- Fair Value Gap (FVG)

---

## Elliottovy vlny
- Vlna 3 v uptrendu = nejsilnější pohyb, nejvyšší pravděpodobnost longu
- Vlna 5 = slábnutí, blíží se obrat
- Svíčkové patterny na HVN = potvrzení anticipatory entry

---

## Prioritní úkoly pro implementaci
1. Analýza historické databáze — najít skryté souvztažnosti
2. Kalibrace pravděpodobností na reálných datech
3. Detekce HVN/LVN z historického objemu
4. CVD real-time výpočet
5. Final run detekce z kombinace funding rate + OI + delta
6. StochRSI confluence detektor (3M + 5M + 15M)
7. MACD cyklová analýza — výpočet ploch
8. Třístupňové notifikace na telefon
9. Časová platnost predikce — čtyři stavy
10. Manipulation sweep vs skutečný obrat detektor
