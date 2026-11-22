# Architecture

```
agent (Phase 2+) ──read-only tools──▶ database
      │
      └── propose_action ──▶ validators ──▶ actions (proposed)
                                              │  owner approves in the UI
                                              ▼
                                   re-validate ──▶ executor ──▶ executed
                                                                 │
                                                       compensate (rollback)
```

* **Propose, then approve.** The agent can only call `ducan.actions.service.propose`.
  Execution happens only via `approve`, which a human triggers in the UI.
* **Validators** (`ducan.validation`) reject proposals that reference unknown SKUs or
  suppliers, claim values that differ from (or are missing in) the database, mention
  unreferenced products, exceed spending caps or stock limits. Rejected proposals are
  not queued; the rejection is audited.
* **Re-validation at execution.** Caps and stock are checked again when an action is
  approved, so several proposals cannot jointly exceed a cap.
* **Audit log.** Every state change writes an `audit_log` row (actor, event, summary,
  details). From Phase 2, every LLM call is logged there with token counts.
* **Exact numbers.** Money and quantities are `Decimal`, stored as text in SQLite.
  Never compare or sum those columns in SQL.
* **Brand.** Only files listed in `brand/brand.yaml` (with sha256) may be used.
* **AI disclosure** is added by templates (`ducan.comms.disclosure`), never by the model.
* **Processes.** `web` (FastAPI) and `worker` (APScheduler) are separate processes, so
  scheduled jobs never run twice.
