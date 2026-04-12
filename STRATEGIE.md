# BTC Trading Strategie — Kompletní dokumentace

## Styl obchodování
- Manuální obchodování podle signálů prediktoru
- Skalping s přechodem do mikro swingovů
- Timeframy: 1M, 3M, 5M, 15M, 30M, 1H, 2H, 4H, 12H, 1D
- Aktivní hodiny: 6:00 - 23:00 SEČ
- Páry: BTC + korelované (ETH, SOL, BNB, AVAX)
- Analýza: TradingView, Exekuce: Binance app

## Dvouvrstvá architektura systému

### Vrstva 1 — Prediktor (btc_live.py)
Časový horizont: hodiny dopředu
- Identifikuje setup před tím než nastane
- Vypočítá očekávaný čas do ideálního vstupu
- Zpřesňuje odhad každých 30 sekund jak setup dozrává
- Zobrazí připravené příkazy s pravděpodobnostmi

Výstup prediktoru:
- Vstupní cena s pravděpodobností dosažení v %
- TP úrovně s pravděpodobností a očekávaným časem dosažení
- SL umístění pod/nad HVN (ne pevné USDC)
- DCA rozdělení kapitálu podle pravděpodobnosti obratu
- Zajišťovací short/long příkazy předem připravené

### Vrstva 2 — Monitor (btc_ticker.py)
Časový horizont: minuty a sekundy během exekuce
- Aktualizuje pravděpodobnosti každých 20-30 sekund
- Rozlišuje manipulation sweep od skutečného obratu
- Upozorní na retest vstupu
- Doporučí: drž / parciální výstup / posuň SL na BE

## Třístupňové notifikace
1. Informační (hodiny dopředu): "Za ~2h se formuje vstup na XX XXX"
2. Přípravná (15-30 minut): "Za ~20 minut připrav příkazy"
3. Akční (2-5 minut): "⚠️ PŘIPRAV SE — zadej příkazy teď"

## Vstupní logika

### Anticipatory entry
- Vstup před potvrzením na pravděpodobné hladině obratu
- Identifikace pomocí HVN + konvergence indikátorů + časová analýza

### Retest entry
- Vstup při návratu k otestované hladině
- FVG (Fair Value Gap) jako přesný cíl retestů

### Counter-trend scalp
- Long při downtrendu na korekci
- Short při uptrendu na retracementu
- Podmínka: vysoké ATR + objem klesá při pohybu = slabý tlak

## DCA zónový vstup
- Kapitál se rozdělí do tří příkazů v zóně obratu
- Alokace přímo úměrná pravděpodobnosti obratu na dané ceně
- Hladina s nejvyšším Volume Profile = největší alokace
- Platí pro LONG i SHORT
- DCA short po rychlém pohybu: přidávej jak cena potvrzuje hlubší retracement

## Fibonacci retracement scalp — dynamické cíle
- Změř rychlý pohyb v USDC
- Zobraz všechny úrovně s pravděpodobností dosažení:
  - 0.382 = primární TP, pravděpodobnost ~85%
  - 0.500 = sekundární TP, pravděpodobnost ~60%
  - Golden Pocket 0.618-0.650 = třetí cíl, pravděpodobnost ~35%
  - 0.618 přesný = maximální cíl, pravděpodobnost ~15%
- Pravděpodobnosti jsou dynamické podle objemu, ATR a režimu trhu
- Platí pro SHORT po rychlém růstu i LONG po rychlém poklesu

## ATR korekční pattern
- Korekce v downtrendu i uptrendu odpovídají násobkům ATR
- Typické hodnoty: 400 / 500 / 600 USDC podle denního ATR
- Výpočet: změř ATR na 1H nebo 4H → očekávaná korekce = násobek ATR
- HVN v oblasti korekce potvrzuje přesnou vstupní cenu

## Volume Profile — klíčová logika
### High Volume Node (HVN)
- Historicky nejvyšší objem na cenové hladině
- Cena se zpomalí, konsoliduje nebo otočí
- Základ pro vstupní ceny DCA i TP cíle
- SL vždy pod/nad HVN nikdy ne pevné USDC od vstupu

### Low Volume Node (LVN)
- Minimum historických obchodů
- Cena projede rychle bez odporu
- Rychlé pohyby o stovky USDC vznikají v LVN zónách
- Po průlomu HVN → cena letí přes LVN k další HVN

### Použití
- Zobrazit nejbližší HVN nad a pod cenou = přirozené TP cíle
- LVN mezi vstupem a TP = odhad rychlosti pohybu
- Přesnost cílení na desítky USDC

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

## SL logika
- SL nikdy ne pevné USDC od vstupu
- SL vždy pod/nad nejbližší HVN pod/nad vstupem
- Ticker rozlišuje: manipulation sweep (nízký objem) vs skutečný obrat (vysoký objem)
- Manipulation sweep = drž nebo znovu vstup
- Skutečný obrat = vystup

## Pravděpodobnost návratu ceny
- Ticker zobrazuje pravděpodobnost návratu pokud jsi mimo pozici
- Horizont: 1h / 4h / 24h / 48h
- Základ pro rozhodnutí: znovu vstoupit nebo čekat

## Market Structure
- Break of Structure (BOS) = trend pokračuje
- Change of Character (CHoCH) = trend se mění, opatrnost
- Inducement = falešný průlom před skutečným pohybem
- Counter-trend scalp povolen při vysokém ATR a slabém objemu při pohybu

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
- Maximální alokace do vysoce korelovaných párů najednou = definovat
- BTC + ETH + SOL = jeden obchod z hlediska rizika ne tři nezávislé
- Zajišťovací short/long připravit dopředu jako limit příkaz

## Indikátory
- RSI 14
- MACD
- StochRSI
- Klinger oscilátor
- EMA 9, 20, 99, 200
- VWAP s denním resetem
- Fibonacci retracement a trend retracement
- Camarilla pivoty
- Gann úrovně
- ATR
- Volume Profile (HVN/LVN)
- Delta objem
- Open Interest
- Funding rate
- Fair Value Gap (FVG)

## Elliottovy vlny
- Vlna 3 v uptrendu = nejsilnější pohyb, nejvyšší pravděpodobnost longu
- Vlna 5 = slábnutí, blíží se obrat
- Svíčkové patterny na HVN = potvrzení anticipatory entry
