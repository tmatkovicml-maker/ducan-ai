# Dućan AI

Back-office system for a small shop in Mali Lošinj (unit P4, Vladimira Gortana 9).
An AI agent plans, orders, prices, schedules and drafts communication; **a human approves
every action that spends money or speaks to the public.** The agent never executes side
effects itself.

All user-facing text is Croatian. Code, comments and docs are English, except
`docs/compliance-checklist.md`, which is written for the owner and the accountant.

Status: **Phase 1 (foundations)**. See "What is built" below.

## Setup (local)

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
make install                 # creates .venv and installs dependencies
cp .env.example .env
make hash-password           # paste the output into ADMIN_PASSWORD_HASH
python -c "import secrets; print(secrets.token_urlsafe(48))"   # -> SECRET_KEY
# for local http only: COOKIE_SECURE=false in .env
make seed                    # runs migrations, then loads seed/*.csv
make dev                     # http://127.0.0.1:8000
make worker                  # scheduled jobs, in a second terminal
```

`make test` runs the tests, `make lint` runs ruff.

## Deployment (VPS)

```bash
cp .env.example .env         # fill in; set DOMAIN; escape "$" in the hash as "$$"
docker compose up -d --build
docker compose exec web python -m ducan.jobs.cli seed
```

Services: `web` (FastAPI, runs migrations on start), `worker` (APScheduler) and `caddy`
(automatic HTTPS for `DOMAIN`). Data lives in `./data` (SQLite database, POS folders).

**Backups:** copy `data/ducan.sqlite3` regularly, e.g. with
`sqlite3 data/ducan.sqlite3 ".backup 'backup-$(date +%F).sqlite3'"` from cron, and keep
copies off the server. (Backup target and schedule are not decided yet.)

## Configuration

* `.env`: secrets and deployment settings (never committed).
* `config/settings.yaml`: spending caps (EUR net), low-risk thresholds, accepted VAT rates,
  POS polling interval. **Caps start at 0, which blocks every purchase order** until the
  owner sets them.
* `brand/brand.yaml`: the only allowed brand assets, each with its sha256. The app refuses
  to start if a listed file is missing or changed. No logo exists yet; the UI shows text.

## Seed data and the assortment spreadsheet

`seed/products.csv` is a draft list of 35 typical products with **no prices, VAT,
suppliers or stock limits**, all inactive (see `seed/README.md`). Fill it in, or import
the owner's spreadsheet:

```bash
make import-assortment XLSX=P4_ducan_model.xlsx   # merges sheet "Asortiman" into products.csv
make seed
```

The importer matches Croatian or English headers (category, product, unit, purchase price
excl. VAT, VAT rate, retail price incl. VAT, share of sales), keeps the owner's extra
columns for products with the same name, and gives new products the next `P4-NNNN` SKU.

## POS sales import (CSV)

Until the POS vendor is known, the system defines the format:

| column | example | notes |
|---|---|---|
| `timestamp` | `2026-07-15T10:15:00` | ISO 8601; without an offset it is shop local time |
| `sku` | `P4-0001` | must exist; unknown SKUs are rejected per row |
| `quantity` | `2` | negative = refund |
| `gross_amount` | `2,40` | EUR incl. VAT; comma or point |
| `payment_method` | `card` | `cash`/`card`/`other` or `gotovina`/`kartica`/`ostalo` |

UTF-8, `,` or `;` delimited. Other columns are ignored and never stored. Drop files into
`data/pos_inbox/` (processed every 15 minutes, then moved to `pos_archive/` or
`pos_rejected/`) or upload them on the "Prodaja" page. The same file is never imported
twice (sha256).

## Running jobs manually

```bash
.venv/bin/python -m ducan.jobs.cli import-sales    # process data/pos_inbox now
.venv/bin/python -m ducan.jobs.cli check-stock     # stock levels vs. movements (exit 1 on mismatch)
.venv/bin/python -m ducan.jobs.cli seed
# in Docker: docker compose exec web python -m ducan.jobs.cli <command>
```

Scheduled in the worker: `import_sales` (every `pos.poll_minutes`), `check_stock`
(daily 03:10). Nightly replenishment and weekly pricing arrive in Phase 2.

## Rolling back an executed action

Open **Odobrenja → Izvršeno**, enter a reason on the action and press **Poništi**.
Rollback is a *compensating action*, not a deletion:

* `price_change`: the old price is restored and a new price-history row is written. If the
  price was changed again in the meantime, the rollback is refused (to avoid overwriting
  a newer decision).
* Messages already sent (emails, posts, replies) **cannot be undone**; the rollback is
  refused and the refusal is recorded in the audit log.

From code: `ducan.actions.service.rollback(session, action_id, actor, reason)`.
Every rollback and refusal appears under **Dnevnik**.

## What is built (Phase 1)

* Data model + Alembic migration: products, suppliers, price history, stock levels and
  movements, sales and sales imports, actions (approval queue), audit log, settings.
* Admin UI (mobile-first, installable PWA): products, suppliers, stock (counts and manual
  corrections), sales imports, approval queue, audit log.
* Approval queue: state machine, validators (references, caps, stock, price), low-risk
  bulk approval, re-validation at approval, rollback. Executor: `price_change` only.
* Single-user login (argon2), signed session cookie, CSRF on every POST, login throttling,
  security headers.
* Tests: approval state machine, reference validator, order caps, CSV import, stock
  reconciliation, low-risk rules, brand assets, importers, web/auth, migrations.

Phase 2+ tables (staff, shifts, content calendar, reviews) are added with the phases that
use them.
