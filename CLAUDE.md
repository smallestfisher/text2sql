# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Text2SQL turns Chinese natural-language business questions into read-only Oracle SQL, validates and executes it, and persists run evidence to a MySQL runtime store. The authoritative design doc is [docs/PROJECT_GUIDE.md](docs/PROJECT_GUIDE.md) — read it before non-trivial work; it covers the full pipeline, API surface, debugging playbook, and semantic-asset rules. [README.md](README.md) covers startup and config.

## Commands

Backend (from repo root):

```bash
cp env.example .env
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --reload --app-dir .   # or: scripts/devctl.sh start backend
```

`scripts/devctl.sh <start|stop|restart|status|logs> [all|backend|frontend]` manages local dev processes (PIDs/logs under `.runtime/`).

Local databases:

```bash
docker compose up -d oracle mysql        # just the DBs for local dev
docker compose up -d --build             # full stack (frontend, backend, oracle, mysql)
```

Do NOT run `docker compose down -v` as a restart — it destroys the Oracle and MySQL data volumes.

Tests and lint (use `.venv/bin/python` — deps like `pydantic` live in the project venv, not system `python3`):

```bash
.venv/bin/python -m unittest discover -s tests             # all tests
.venv/bin/python -m unittest tests.test_retrieval_eval.RetrievalEvalTests   # single test class
python3 backend/domain_config_lint.py                      # validate semantic assets (stdlib only; run after editing semantic/, examples/, eval/)
```

Retrieval scoring probe (read-only, hits the real embedding API, needs `VECTOR_API_KEY` in `.env`):

```bash
.venv/bin/python scripts/probe_retrieval_scores.py
```

Frontend (`frontend/`): `npm install`, `npm run dev`, `npm run build` (build also runs `tsc --noEmit`). Override backend origin with `VITE_API_ORIGIN=...`.

## Architecture

Two databases with fixed roles: **Oracle** (`BUSINESS_DATABASE_URL`) is the business data source and only ever runs read-only business SQL; **MySQL** (`RUNTIME_DATABASE_URL`) is the runtime store for users, sessions, messages, state, traces, query logs, SQL audit, feedback, eval, and vector corpus. All SQL generation, repair, AST parsing, and validation follow Oracle rules.

The main query pipeline (front door: `POST /api/chat/query/stream`) is orchestrated in `backend/app/services/orchestrator.py`:

```
session state → QuestionContext (admit/rewrite question) → terminal gate →
retrieval (tables, business knowledge, examples, join patterns, vector) →
evidence closure → ContextSummary → SQL prompt → LLM → SqlValidator (+ repair) →
Oracle execute → AnswerBuilder → persist snapshot/trace/query-log/audit → Workspace
```

Every query gets a `trace_id` that links trace steps, query log, retrieval log, and SQL audit. The frontend rehydrates entirely from `GET /api/chat/sessions/{session_id}/workspace`.

Layer map: `backend/app/api/routes` (HTTP), `backend/app/core` (container/settings/exceptions/cancellation), `backend/app/models` (typed request/response/session/retrieval/trace/workspace models), `backend/app/repositories` (runtime DB + metadata), `backend/app/services` (the pipeline services above).

## Critical conventions

**Business facts live in semantic assets, not in Python.** Code only loads, retrieves, ranks, trims, renders, assembles, validates, and audits. Never encode business rules or per-table scenarios as `if/else` on table names. To fix correctness, edit the assets, not the pipeline:

- `semantic/tables.json` — real tables, fields, relations, and `time_fields` (physical time-field storage format).
- `semantic/business_knowledge.json` — reusable rules, formulas, default definitions, taboos (entry + notes).
- `semantic/join_patterns.json` — stable join paths.
- `examples/nl2sql_examples.template.json` — human-confirmed NL2SQL few-shot.
- `eval/retrieval_cases.json` / `eval/evaluation_cases.json` — regression samples.

**Asset storage (`SEMANTIC_ASSET_STORE`, default `db`):** the first four assets are stored as whole-document rows in the runtime MySQL `semantic_assets` table (`name` PK, `content_json`). The JSON files under `semantic/`/`examples/` seed the table once when it is empty (`semantic_asset_seeder`), then act only as a fallback for a missing asset. At runtime the source of truth is the DB, edited through the admin center's Semantic Studio UI (backed by `GET/PUT /api/admin/metadata/documents/{name}` and `/api/admin/examples`); saves go to the DB and trigger a retrieval reload. Set `SEMANTIC_ASSET_STORE=file` to keep reading/writing the JSON files directly instead. `eval/*.json` always stay file-based. `MetadataRegistry` is the single read hub (all consumers read through it); `FileMetadataRepository` is the single write path. Tests and `domain_config_lint.py` run in file mode (no DB dependency).

After editing assets (file mode) or seed files, run `domain_config_lint.py`, then restart or `POST /api/admin/metadata/reload`. DB-mode edits via the UI reload automatically.

**Time fields:** `time_fields.format` is the physical storage encoding (`YYYYMM`, `YYYYMMDD`, `YYYY-MM`, `YYYY-MM-DD`), not an input format. String-encoded date/month fields must use string expressions or format-matched literal ranges (e.g. `SUBSTR(work_date, 1, 6)` for month grain) — never Oracle date functions like `TO_CHAR`/`TRUNC` on them. `SqlValidator` enforces this. If a physical column ever changes to real `DATE/TIMESTAMP`, update `time_fields` and its tests first, then prompt/validator behavior.

**SqlValidator is the hard pre-execution boundary** (single read-only statement, tables/fields must exist in semantic assets, source table must be in the prompt's allowed set, Oracle syntax + time-format checks). Errors block execution; warnings don't but flow into trace/query-log/audit and roll up to `risk_level`/`risk_flags`. SQL repair only fixes validator/execution errors — business correctness comes from assets, retrieval, and prompt quality.

**Debugging a wrong answer:** reproduce, capture `session_id`/`trace_id`, open the workspace endpoint, then localize by layer (QuestionContext → terminal gate → retrieval → SQL prompt/assets → validator → Oracle data → workspace restore) before changing any long-lived asset. Full playbook in PROJECT_GUIDE.md §8. Retrieval coverage gaps go into `eval/retrieval_cases.json` (verified by `tests.test_retrieval_eval` without LLM/execution).

## Project context

This is a simulated-stage project: current data and user questions are simulated, not real production usage, and it does not need complex permission/governance design right now.
