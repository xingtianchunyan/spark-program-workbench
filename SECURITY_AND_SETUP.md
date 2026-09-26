# Spark Program Workbench · Security and Setup

## What changed

- API keys are no longer stored in `spark_task_data.json`.
- On the first launch, keys from an older JSON file are migrated automatically to `.env` and removed from JSON.
- Nervos Talk username/password storage has been removed.
- JSON saves use an in-process lock plus atomic file replacement to avoid partial writes.
- WebBridge is disabled by default and only accepts a fixed action allowlist from the workbench client.
- Notion tasks are completed only after the corresponding Notion records have been read back and verified.
- Every new In-Progress project still creates all four workflow tasks. Project List and Fund Pool are verification-gated now; Dashboard and Spark Contacts remain queued for their later automation design.

## Local `.env`

The real `.env` stays on the computer running the workbench. Do not upload it. See `.env.example` for available settings.

The workbench creates `.env` automatically when it detects keys in an older JSON file. On Unix-like systems it applies mode `0600`; on Windows, keep the workbench folder inside the signed-in user's private profile and do not share the folder.

## Translation and WebBridge

The browser workbench does not use the old local WebBridge. Translation sends the current draft directly to the AI provider selected in Settings (Kimi or SiliconFlow), using its HTTPS API, and replaces the editor text only after a successful response. Nothing is sent to Immersive Translate.

## Notion access

The automation target is the copied Dashboard in the user's own workspace. Interactive work may still use hosted Notion MCP, but unattended local automation uses the official Notion REST API because hosted MCP currently requires an interactive OAuth flow and does not yet support unattended authorization.

Create a Notion Personal Access Token, then run `Setup-Notion.bat`. The setup program verifies access to all four data sources before saving the token to `.env`. Never put the token in source code or `spark_task_data.json`.

## Browser service

- The service is hard-coded to listen on `127.0.0.1`.
- State-changing requests require a CSRF token.
- SQLite uses WAL and immediate transactions to prevent simultaneous browser requests from losing updates.
- Verification-gated tasks cannot be checked manually.
- Team mode stores password hashes, short-lived session hashes and audit events; it never stores Nervos Talk passwords.
- Do not expose the development server directly to a LAN or the Internet. Team deployment requires TLS plus an authenticated private proxy.
