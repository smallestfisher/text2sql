# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Text2SQL turns Chinese natural-language business questions into read-only Oracle SQL, validates and executes it, and persists product state and run evidence to a local SQLite runtime store. The authoritative design doc is [docs/PROJECT_GUIDE.md](docs/PROJECT_GUIDE.md) — read it before non-trivial work; it covers the full pipeline, API surface, debugging playbook, and semantic-asset rules. [README.md](README.md) covers startup and config.

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
docker compose up -d oracle              # business DB for local dev
docker compose up -d --build             # full stack (frontend, backend, oracle)
```

Do NOT run `docker compose down -v` as a restart — it destroys the Oracle and runtime data volumes.

Tests and lint (use `.venv/bin/python` — deps like `pydantic` live in the project venv, not system `python3`):

```bash
.venv/bin/python -m unittest discover -s tests             # all tests
.venv/bin/python -m unittest tests.test_retrieval_eval.RetrievalEvalTests   # single test class
python3 backend/domain_config_lint.py                      # validate the offline schema-boundary fixture
```

Retrieval scoring probe (read-only, hits the real embedding API, needs `VECTOR_API_KEY` in `.env`):

```bash
.venv/bin/python scripts/probe_retrieval_scores.py
```

Frontend (`frontend/`): `npm install`, `npm run dev`, `npm run build` (build also runs `tsc --noEmit`). Override backend origin with `VITE_API_ORIGIN=...`.

## Architecture

Two persistence roles are fixed: **Oracle** (`BUSINESS_DATABASE_URL`) is the business data source and only ever runs read-only business SQL; **SQLite** (`RUNTIME_DATABASE_URL`) stores users, sessions, messages, state, semantic releases, traces, query logs, SQL audit, feedback, and eval. Embedding vectors are rebuildable files under `VECTOR_CACHE_DIR`. All SQL generation, repair, AST parsing, and validation follow Oracle rules.

The front door is `POST /api/chat/query/stream`. `release_aware_orchestrator.py` resolves the session-bound release and constructs its immutable runtime before delegating to `orchestrator.py`:

```
session state → QuestionContext (admit/rewrite question) → terminal gate →
retrieval (tables, business knowledge, examples, join patterns, vector) →
evidence closure → ContextSummary → SQL prompt → LLM → SqlValidator (+ repair) →
Oracle execute → AnswerBuilder → persist snapshot/trace/query-log/audit → Workspace
```

Every query gets a `trace_id` that links trace steps, query log, retrieval log, and SQL audit. The frontend rehydrates entirely from `GET /api/chat/sessions/{session_id}/workspace`.

Layer map: `backend/app/api/routes` (HTTP), `backend/app/core` (container/settings/exceptions/cancellation), `backend/app/models` (typed request/response/session/retrieval/trace/workspace models), `backend/app/repositories` (runtime DB + metadata), `backend/app/services` (the pipeline services above).

## Critical conventions

**Business facts live in the published semantic release, not in Python.** Code only loads, retrieves, ranks, trims, renders, assembles, validates, and audits. Never encode business rules or per-table scenarios as `if/else` on table names. Edit the corresponding draft in the admin center, then publish a new release:

- `tables_metadata` — real tables, fields, relations, and `time_fields` (physical time-field storage format).
- `business_knowledge` — reusable rules, formulas, default definitions, taboos (entry + notes).
- `join_patterns` — stable join paths.
- `examples_template` — human-confirmed NL2SQL few-shot.

**Asset storage:** the four drafts and immutable release snapshots live in the runtime SQLite store and are edited through the admin center. `ReleaseRuntimeManager` builds every query runtime from the session-bound release snapshot. There is no production file fallback. Business-domain values are release-defined strings; do not add frontend enums, labels, or table-name inference. `tests/fixtures/` and `eval/retrieval_cases.json` are offline test inputs and are not copied into the production image. Admin evaluation cases live in runtime SQLite.

After changing a draft, publish it through the admin center and verify the new release with a replay/eval run. `domain_config_lint.py` only checks the offline schema-boundary fixture.

**Retrieval degradation:** keyword BM25 is always available. Vector failure or missing embedding credentials must degrade visibly to BM25 with a warning; it must not make the release load from another source.

**Deployment config:** all settings come from environment variables or deployment secrets and are read by `Settings.build()`. The admin center's **System Settings** page and `GET /api/admin/config` are read-only; changing configuration requires updating the deployment and restarting the backend. The field registry is `FIELD_SPECS` in `core/settings.py`. Runtime product state must not become a configuration override layer.

**Time fields:** release `tables_metadata.time_fields.format` is the physical storage encoding (`YYYYMM`, `YYYYMMDD`, `YYYY-MM`, `YYYY-MM-DD`), not an input format. String-encoded date/month fields must use string expressions or format-matched literal ranges — never Oracle date functions like `TO_CHAR`/`TRUNC` on them. `SqlValidator` enforces this. If a physical column ever changes to real `DATE/TIMESTAMP`, update the draft and its tests first, then prompt/validator behavior.

**SqlValidator is the hard pre-execution boundary** (single read-only statement, tables/fields must exist in semantic assets, source table must be in the prompt's allowed set, Oracle syntax + time-format checks). Errors block execution; warnings don't but flow into trace/query-log/audit and roll up to `risk_level`/`risk_flags`. SQL repair only fixes validator/execution errors — business correctness comes from assets, retrieval, and prompt quality.

**Debugging a wrong answer:** reproduce, capture `session_id`/`trace_id`, open the workspace endpoint, then localize by layer (QuestionContext → terminal gate → retrieval → SQL prompt/assets → validator → Oracle data → workspace restore) before changing any long-lived asset. Full playbook in PROJECT_GUIDE.md §8. Retrieval coverage gaps go into `eval/retrieval_cases.json` (verified by `tests.test_retrieval_eval` without LLM/execution).

## Project context

This is a simulated-stage project: current data and user questions are simulated, not real production usage, and it does not need complex permission/governance design right now.
