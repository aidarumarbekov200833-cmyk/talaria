# Zephyr Finpay Redesign and Safe Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use subagent-driven-development.
> Work task-by-task with tests first. This project intentionally has no Git;
> use the sibling baseline archive and SHA-256 checkpoints instead of commits.

**Goal:** Deliver the approved Finpay-like visual system and compatibility-first
frontend, FastAPI, and GramJS refactors without breaking public contracts.

**Architecture:** Contract snapshots guard the current surfaces. Frontend work
uses CSS/ES-module facades, backend work uses router-service-repository slices,
and Telegram delivery policy remains durable in Python while GramJS stays a
thin transport.

**Tech stack:** HTML/CSS, vanilla JavaScript ES modules, Node 20, Playwright,
Prettier, Python 3.12, FastAPI, SQLAlchemy, Alembic, pytest, Ruff, mypy, GramJS.

**Spec:** `docs/superpowers/specs/2026-08-21-zephyr-finpay-design.md`

## Global constraints

- Preserve all original DOM contract attributes and named required nodes.
- Preserve OpenAPI, error envelope, localStorage keys, payment flow, and
  Telegram matching semantics.
- Russian production copy only; no placeholders or invented limits.
- No runtime dependencies; no Git initialization.
- TDD for every behavior or refactor; verification must run after the last edit.

## Task 1: Baseline and contract gates

- Save the approved spec and plan, create the SDD ledger, and record preflight
  rulings.
- Create an external source ZIP, per-file SHA-256 manifest, and ZIP hash while
  excluding caches, virtual environments, `node_modules`, `.git`, and `.env`.
- Add a Python-stdlib DOM contract capture/check tool and capture the original
  HTML contract before markup changes.
- Add normalized OpenAPI, Alembic head/schema, and frontend action baselines.
- Add frontend dev-only Prettier/Playwright scaffolding and root CHANGELOG.
- Establish backend, frontend, and gateway baseline commands and record any
  environment/pre-existing failures separately.

## Task 2: Finpay frontend and safe module boundaries

- Write failing contract/unit/browser tests for tokens, promo secrecy, quote
  tweening, stats fallback, reduced motion, dialogs, FAQ, storage, XSS, and CSV.
- Split CSS behind `styles.css`, implement the approved Finpay-like landing and
  workspace, and retain all original DOM contract attributes.
- Split JS into landing/FAQ/tariff/dialog and shared helper modules with
  compatibility facades; keep the unified `data-act` dispatcher.
- Implement the hidden promo flow, rAF price/metric tweens, accessible dialogs
  and FAQ, safe localStorage, escaped rendering, and Excel-safe CSV.
- Run DOM contract, unit, Playwright, format, syntax, responsive/theme, console,
  overflow, and reduced-motion gates. Write stage checkpoint and CHANGELOG.

## Task 3: FastAPI services, repositories, errors, queries, and indexes

- Add characterization tests before each extraction.
- Convert `services.py` to a compatibility package, add repositories, and move
  domains in this order: tariff/settings/offers; leads/search;
  campaigns/replies; warmup/admin; AI.
- Add DomainError mapping without changing wire responses, preserve transaction
  and background-job boundaries, and redact secrets from logs/responses.
- Add query-count tests and additive indexes on
  `campaign_sends(owner_id, account_id, status, sent_at)` and
  `leads(tg_verified_by_account_id, tg_checked_at)`.
- Run Ruff, format check, compileall/import, mypy report, pytest, OpenAPI parity,
  Alembic single-head/upgrade, query gates, Docker build. Checkpoint and log.

## Task 4: Delivery policy, GramJS modules, CI, and final integration

- Characterize delivery and Node behavior before moving code.
- Consolidate durable queue/retry/quarantine/reason policy in
  `backend/app/services/delivery.py`; keep old Python modules as facades.
- Split GramJS internals into errors, registry, sessions, transport, reply
  watches, and HTTP entrypoint while keeping `server.js` and HTTP payloads.
- Add `node:test` coverage for FloodWait, registry dedup, watcher expiry,
  one-shot callbacks, filters, and handler binding.
- Expand CI with all frontend/backend/gateway contract, quality, migration,
  browser, Docker, and health gates.
- Run full verification after the final edit, complete CHANGELOG, create final
  SHA-256 checkpoint, and perform independent whole-project review.
