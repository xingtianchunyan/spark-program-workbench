# Spark Program Workbench · Browser and Team Architecture

## Target

A local-first workbench opened in a browser, with a clean path to a private team deployment. The desktop Tkinter version remains usable during migration, but the browser service becomes the single writer once database migration begins.

## Components

1. **Web UI** — task board, project detail, sync status, approval dialogs, audit history.
2. **Local API** — Python standard-library HTTP service bound to `127.0.0.1`; no package installation required and never exposed to the LAN.
3. **SQLite store** — projects, tasks, Notion jobs, step attempts, external record IDs, users and audit events.
4. **Nervos Talk adapter** — read-only polling first; later per-user scoped Discourse API authorization for posting.
5. **Notion adapter** — official REST API token for unattended work plus hosted MCP configuration for interactive use; every write follows query → create/update → fetch → verify.
6. **Worker** — durable jobs with retry/backoff; uncertain network results return to query, never directly repeat create.

## Core tables

- `projects`: internal ID, Nervos topic ID, title, lifecycle state, source URL.
- `tasks`: project ID, task type, status, completion source, timestamps.
- `sync_jobs`: idempotency key, workflow version, overall status, requested/approved actor.
- `sync_steps`: job ID, step name, status, attempt count, verified Notion page IDs, last error.
- `external_records`: system, object type, natural key, remote page ID, checksum.
- `audit_events`: actor, action, target, before/after checksum, timestamp.
- `user_credentials`: credential reference only; never plaintext tokens or passwords.

## Migration phases

### Phase 1 — Current desktop hardening

- Move API keys to `.env`.
- Remove Nervos Talk password storage.
- Add atomic JSON writes.
- Separate queued Notion work from verified completion.
- Add idempotency keys and verification gates.
- Preserve all four In-Progress workflow tasks; implement Project List/Fund Pool first while keeping Dashboard/Contacts as planned steps.

Status: implemented.

### Phase 2 — SQLite service layer

- Status: implemented in `spark_web/storage.py`.
- Imports JSON once, uses WAL + immediate transactions, mirrors verified core steps back to legacy JSON.

### Phase 3 — Local browser UI

- Status: implemented and launched by `Launch-Web.vbs`.
- Loopback-only service with CSRF protection, task/job views, manual correction and audit history.
- New-project, weekly-update and completion/closure workflows are implemented.

### Phase 4 — Team mode

- Local role/session foundation implemented; production deployment still requires organization SSO or a private identity proxy plus TLS.
- Roles: viewer, operator, approver, administrator.
- Each Nervos Talk user supplies their own scoped Discourse User API key through `.env`; passwords are never accepted.
- A formal post requires the logged-in account bound to that forum identity and the explicit phrase `正式发布`.
- Record the drafting actor, approving actor, selected forum identity and returned post ID.

## Safety invariants

- Never share or store another member's forum password.
- Never mark a Notion task complete before read-back verification.
- Never retry a create after an uncertain response without querying first.
- Never overwrite a conflicting Notion row automatically.
- Never expose the local service beyond `127.0.0.1` merely to make it reachable by teammates.
- Team access requires authentication, TLS and audit logging.

## First browser milestone acceptance criteria

- Opens from a new `Launch-Web.bat` in the default browser.
- Imports all current tasks and preserves their completion state.
- Displays Tranfr's two Notion steps as pending/partial/verified.
- Prevents manual completion of verification-gated tasks.
- Survives two simultaneous browser requests without lost updates.
- Produces an audit record for every state change.
