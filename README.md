# Dućan AI

Back-office system for a small shop in Mali Lošinj (unit P4, Vladimira Gortana 9).
An AI agent plans, orders, prices, schedules and drafts communication; **a human approves
every action that spends money or speaks to the public.** The agent never executes side
effects itself.

All user-facing text is Croatian. Code, comments and docs are English, except
`docs/compliance-checklist.md`, which is written for the owner and the accountant.

Status: **Phase 3 (staff scheduling)**. See "What is built" below.

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

```bash
make replenish    # nightly replenishment now: proposes purchase orders
make pricing      # weekly pricing now: proposes price changes
make rota         # propose next week's rota now
.venv/bin/python -m ducan.jobs.cli rota-deadline   # run the Thursday check now
```

Scheduled in the worker: `import_sales` (every `pos.poll_minutes`), `replenishment`
(daily 02:30), `check_stock` (daily 03:10), `pricing` (Mondays 03:30), `rota`
(Mondays 06:15, for the following week) and `rota_deadline` (Thursday 12:00, from
`config/opening_hours.yaml`). Jobs only ever
**propose**; every result lands in **Odobrenja**, and each run's notes for the owner
("Treba tvoj unos") are shown on the dashboard.

## Replenishment (nightly)

Per active product: sales rate = 0.6 × 14-day average + 0.4 × 28-day average (divided by the
days the shop has actually been selling), scaled by the monthly seasonal factor of the
delivery date. Until `min_history_days` of history exist, an estimate from the product's
share of sales and `demand_prior.expected_daily_revenue_gross` is blended in; with neither,
the product is flagged, not guessed.

Per supplier: the next delivery is the first delivery day after the lead time; stock has to
last until the delivery after that (`horizon`). Order if
`stock + open orders < rate × horizon + min_stock`, up to
`min_stock + rate × (horizon + cover_days)`, capped by `max_stock` and by what can be sold
within the shelf life. Lines are sorted by urgency; the least urgent are dropped until the
order fits the per-order and weekly caps. Orders under the supplier's minimum are not
proposed. Unknown lead times, delivery days, prices or max stock become flags.

Each proposal carries a Croatian email (with the AI disclosure), a PDF and a CSV order sheet
(download on the approval card). The email's numbers are validated against the database.
After approval the email is sent with SMTP if `SMTP_HOST`/`SMTP_FROM` are set; otherwise
the order stays "approved" and is sent by hand. When goods arrive, book them with
**Primka** on the order card.

## Pricing (weekly)

Only when a floor margin is configured (`pricing.default_floor_margin_pct` or per category):

1. margin below the floor -> raise to the lowest price point that restores it;
2. a competitor price **you entered** (Konkurencija page, last 30 days) is more than
   `competitor_gap_pct` below ours -> lower towards it, never below the floor.

New prices are rounded to `pricing.price_points`. The validator rejects any price change
below the floor or off a price point. Nothing is scraped from the web.

## Staff scheduling

* **Osoblje**: name, email, role, optional personal weekly maximum and a weekly
  availability grid (one window per weekday). Nothing else is stored about staff; there is
  no monitoring of work or behaviour, and cameras are out of scope.
* `config/opening_hours.yaml`: seasonal opening hours, people needed, peak windows.
* `config/labour.yaml`: max hours per day and week, daily and weekly rest, breaks.
  **All values are left empty for the owner to fill in after checking the law.** While any
  is empty, no rota is generated and the Thursday check raises the alert.
* The generator splits each opening day into the fewest shifts no longer than the daily
  maximum, adds peak shifts, and fills each place with the available person with the fewest
  hours whose week still meets every rule (incl. rest against the previous week's shifts).
  Every shift says who and why; unfilled places are listed with the reason.
* The rota for week N+1 is proposed on Monday of week N; if none exists on Thursday 12:00 the
  owner gets an email (`OWNER_EMAIL`) and a banner on the dashboard.
* Approving publishes the shifts and emails each person their shifts with a personal link
  (`PUBLIC_BASE_URL/smjene/<token>`, no login) where they can ask a colleague to take a
  shift. The owner approves or rejects swaps on **Raspored**; a swap that breaks a rule cannot
  be approved. Rolling back a rota withdraws its shifts and tells the people affected.
* Note: during a single person's break the shop is not covered; the owner decides how to
  handle that (the generator does not plan break cover).

## The AI agent

The numbers (quantities, prices, totals) are always computed by code. The model
(`ANTHROPIC_MODEL`, never hard-coded) currently writes only the short justification for
price proposals, using read-only tools (product facts, owner-entered competitor prices,
sales). Its text must pass the reference validator; otherwise the template text is used.
Every model request is written to the audit log with the input summary, tool calls,
output, stop reason and token counts (`llm.call`). Without `ANTHROPIC_MODEL` no model is
called.

## Rolling back an executed action

Open **Odobrenja → Izvršeno**, enter a reason on the action and press **Poništi**.
Rollback is a *compensating action*, not a deletion:

* `purchase_order`: an email already sent cannot be recalled; the rollback is refused and
  you contact the supplier directly.
* `shift_schedule`: the rota's shifts are withdrawn and the staff are told by email.
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

## What is built (Phase 2)

* Sales analytics, demand forecast with 14/28-day averages, seasonal factor and demand prior.
* Nightly replenishment per supplier with caps, minimum orders, lead times, delivery days,
  shelf life; Croatian email + PDF/CSV order sheet; SMTP sending after approval; receiving.
* Weekly pricing with floor margins, price points and manual competitor prices.
* LLM agent wrapper (manual tool-use loop, audit logging with token counts, optional
  server-side refusal fallback and effort from `.env`), used for price reasons.
* Dashboard: sales by day (chart + table), category margin, stock cover, expiring stock
  (FIFO estimate from receipts), what to order today, notes for the owner.

## What is built (Phase 3)

* Staff with weekly availability grid; seasonal opening hours and peaks from config.
* Labour-rule checks per person (daily/weekly maximum, personal maximum, daily rest incl.
  previous week, weekly rest, breaks, availability) shared by generator and validator.
* Explainable rota generator, weekly proposal job, Thursday deadline alert.
* Approval publishes shifts and emails staff (notifier interface; Telegram/WhatsApp later).
* Shift-swap flow through a personal link, owner decision re-checked against the rules.

Phase 4 tables (content calendar, reviews, FAQ) are added with that phase.
