# Dućan AI na tvom računalu

Sustav radi samo na tvom računalu, na adresi **http://localhost:8000**. Druga računala i
mobiteli ga ne vide. Za ulaz treba tvoja lozinka.

## Što treba (jednom)

1. **Docker Desktop**: preuzmi s https://www.docker.com/products/docker-desktop/ (Windows ili
   Mac), instaliraj i pokreni. Kad u izborniku piše da radi („Engine running“), spreman je.
2. **Kod sustava**: preuzmi ZIP
   https://github.com/tmatkovicml-maker/ducan-ai/archive/refs/heads/claude/gallant-pasteur-aaugam.zip
   i raspakiraj ga, npr. u `Dokumenti/ducan-ai`.

## Prvo pokretanje (tri naredbe)

Otvori terminal u raspakiranoj mapi:
- **Windows:** u mapi klikni desnom tipkom → „Otvori u terminalu“.
- **Mac:** desni klik na mapu → „Nova Terminal kartica u mapi“ (ili Terminal pa `cd ` i povuci mapu u prozor).

Zatim redom:

```bash
docker compose -f docker-compose.local.yml run --rm setup
```
Upiši lozinku (najmanje 14 znakova) dvaput. Dok tipkaš, ništa se ne vidi; tako treba.

```bash
docker compose -f docker-compose.local.yml up -d --build
```
Prvi put traje nekoliko minuta.

```bash
docker compose -f docker-compose.local.yml exec web python -m ducan.jobs.cli seed
```

Otvori **http://localhost:8000** u pregledniku i prijavi se.

## Svakodnevno

- Sustav se sam pokreće kad je Docker Desktop upaljen.
- **Zaustavi:** `docker compose -f docker-compose.local.yml down`
- **Ponovno pokreni:** `docker compose -f docker-compose.local.yml up -d`
- **Sigurnosna kopija:** kopiraj mapu `data` na drugo mjesto (USB, oblak). U njoj su svi podaci.
- **Nova verzija:** preuzmi novi ZIP, kopiraj u njega svoje mape `data`, `config`, `brand`,
  `seed` i datoteku `.env`, pa pokreni `up -d --build`.

## Postavke

- Poslovna pravila: `config/settings.yaml`, `config/labour.yaml`, `config/opening_hours.yaml`.
- AI model, e-mail i ostalo: datoteka `.env` (otvori je u Bilježnici / TextEditu).
- Nakon izmjene postavki: `docker compose -f docker-compose.local.yml up -d --force-recreate`.

## Ako nešto ne radi

- `docker compose -f docker-compose.local.yml logs web` pokazuje zadnje poruke aplikacije.
- Zaboravljena lozinka: obriši `.env` i ponovno pokreni prvu naredbu (podaci ostaju).
