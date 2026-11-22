# Pravna i porezna pitanja: popis za provjeru

Ovaj dokument je popis **otvorenih pitanja** za vlasnika i računovođu.
Ništa ovdje nije utvrđena činjenica. Svaka stavka ima status **ZA PROVJERU** dok je
odgovorna osoba ne potvrdi i ne upiše izvor (zakon, pravilnik, mišljenje računovođe).
Sustav Dućan AI ne smije pretpostaviti nijednu od ovih vrijednosti.

| # | Tema | Status | Tko provjerava | Potvrđeno (datum, izvor) |
|---|------|--------|----------------|--------------------------|
| 1 | Fiskalizacija i samoposluživanje | ZA PROVJERU | računovođa | |
| 2 | Alkohol i duhan | ZA PROVJERU | vlasnik / pravnik | |
| 3 | Stope PDV-a po kategorijama | ZA PROVJERU | računovođa | |
| 4 | Sigurnost hrane (HACCP) | ZA PROVJERU | vlasnik / nadležna inspekcija | |
| 5 | GDPR (chat, recenzije) | ZA PROVJERU | vlasnik / pravnik | |
| 6 | Radno pravo i raspored smjena | ZA PROVJERU | računovođa / pravnik | |

## 1. Fiskalizacija i samoposluživanje

**Zašto je važno:** svaki gotovinski i kartični račun mora biti fiskaliziran. Ako se
kasnije uvede samoposluživanje izvan radnog vremena (Faza 5), treba znati smije li i
kako automat ili samoposlužna blagajna izdavati fiskalne račune.

Pitanja:
- Koje obveze vrijede za blagajnu koju ćemo koristiti (certifikat, operater, oznaka)?
- Je li samoposlužna prodaja dopuštena i pod kojim uvjetima?
- Tko je „operater“ na računu kad nema zaposlenika na blagajni?
- Treba li izvoz prodaje (CSV) zadovoljiti neki format ili rok čuvanja?

## 2. Alkohol i duhan

**Zašto je važno:** asortiman može sadržavati pivo, vino ili rakiju. Za njih mogu
vrijediti posebna pravila koja sustav mora poštovati u rasporedu i u samoposluživanju.

Pitanja:
- Treba li posebna dozvola za prodaju alkohola i/ili duhana na ovoj lokaciji?
- Postoje li vremenska ograničenja prodaje alkohola (npr. noću), državna ili gradska?
- Kako se provjerava dob kupca i tko odgovara za provjeru?
- Smije li se alkohol ili duhan uopće prodavati bez prisutnog zaposlenika? (Za Fazu 5
  pretpostavka je **ne**, dok se ne potvrdi suprotno.)

## 3. Stope PDV-a po kategorijama

**Zašto je važno:** sustav računa marže i preporučuje cijene. Pogrešna stopa PDV-a
znači pogrešnu maržu i pogrešnu cijenu na polici.

Sustav trenutačno prihvaća stope 5 %, 13 % i 25 % (`config/settings.yaml`), ali
**stopu za svaki proizvod upisuje vlasnik**; sustav je ne pogađa.

Pitanja:
- Koja stopa vrijedi za svaku kategoriju u asortimanu (pića, sladoled, kruh, mlijeko,
  lokalni proizvodi poput maslinovog ulja i meda, suveniri, kozmetika za sunčanje)?
- Postoje li proizvodi kod kojih stopa ovisi o pakiranju ili sastavu?
- Je li dućan u sustavu PDV-a od prvog dana?

## 4. Sigurnost hrane (HACCP)

**Zašto je važno:** dućan prodaje hranu, a neki proizvodi traže hladni lanac (mlijeko,
jogurt, sladoled, led). Sustav prati rok trajanja, ali ne zamjenjuje HACCP plan.

Pitanja:
- Treba li dućan HACCP plan ili je dovoljan pojednostavljeni sustav za maloprodaju
  pakirane hrane?
- Koje evidencije treba voditi (temperatura hladnjaka, sljedivost, povlačenje proizvoda)?
- Posebni uvjeti za lokalne proizvode (med, ulje, suhe smokve) od malih proizvođača:
  deklaracije, registracija proizvođača?
- Treba li zaposlenik imati potvrdu o zdravstvenoj sposobnosti / higijenskom minimumu?

## 5. GDPR (chat s kupcima i recenzije)

**Zašto je važno:** u Fazi 4 sustav će odgovarati kupcima u chatu i pripremati odgovore
na Google recenzije. To su osobni podaci (ime autora recenzije, sadržaj poruka).

Pitanja:
- Koja je pravna osnova za obradu poruka iz chata i koliko ih smijemo čuvati?
- Treba li obavijest o privatnosti na web stranici i u chat widgetu, i što mora sadržavati?
- Smiju li se poruke kupaca slati pružatelju AI modela (Anthropic) i treba li ugovor o
  obradi podataka (DPA)?
- Kako kupac može tražiti brisanje svojih podataka?

Napomena: sustav ne sprema brojeve kartica ni osobne podatke iz blagajne, i ne
analizira snimke kamera ni ponašanje zaposlenika.

## 6. Radno pravo i raspored smjena

**Zašto je važno:** u Fazi 3 sustav predlaže raspored. Ograničenja se upisuju u
`config/labour.yaml` i sustav ih mora poštovati, ali **vrijednosti upisuje vlasnik
nakon provjere**, sustav ih ne pogađa.

Pitanja:
- Najviše sati rada dnevno i tjedno (uključujući prekovremeni rad i sezonski rad)?
- Minimalni dnevni i tjedni odmor između smjena?
- Pravo na stanku: od koliko sati rada i koliko traje?
- Rad nedjeljom i blagdanima: ograničenja i dodaci?
- Rokovi za objavu rasporeda zaposlenicima?
- Pravila za studente i sezonske radnike?
