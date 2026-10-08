# MQ Deviation Management Agent — Project Overview

> Internal reference document. Generated from a repository audit on 2026-10-08, branch `feature-cortex-ingestion`. Numbers reflect the state of the repo at that point in time — re-run the audit before relying on exact counts in the future.

## 1. What this project is

The **Deviation Management Agent** ("Deviation Agent") is a Lilly-internal pilot tool that supports Quality/Manufacturing deviation investigations at two pilot manufacturing sites:

- **IAPI** — Indianapolis API
- **PR05** — Puerto Rico 5

The system of record for deviations remains **Veeva Vault QMS** (internally also called **GDD** — Global Deviation Database, or **OneQMS**); deviations are identified as `DEV-######`. This tool does **not** replace Veeva — it is explicitly scoped to assist the hardest, most time-consuming part of an investigation: the **"Fact Finding"** stage, where investigators currently have to manually gather information scattered across many disconnected systems (procedures, batch/process data, equipment data, historical deviations and CAPAs) before they can write the Investigation Summary and Root Cause fields.

A comparable internal tool reportedly cut this writing time by roughly 75% using structured, guided authoring (not autonomous investigation) — that is the ambition level here: **guided drafting and evidence retrieval**, with a human investigator always in the loop.

### Explicit non-goals (Release 1 / MVP boundary)

Per `backend/AGENTS.md` and `backend/docs/architecture/release-1-fact-finding-architecture.md`, this release explicitly will **not**:
- Write directly into Veeva production
- Replace any existing Veeva process step
- Be a fully operationalized, procedure-integrated system
- Perform READY assessment, Investigation, Root Cause Analysis, CAPA, or Effectiveness Check support
- Make or represent an autonomous Quality decision, classification, approval, or disposition
- Bypass Veeva lifecycle controls, or fabricate evidence/citations

The system is currently a **non-GxP pilot/prototype**, not a validated system. Whether it becomes GxP-validated or stays a prototype is called out internally as the single highest-leverage open governance question.

### Core workflow (current code)

1. User opens the React workbench (`/` or `/deviations` for the official UI board; `/workflow` for the Release 1 Fact Finding workbench — note: the two project READMEs use slightly different route names for this feature, `/workflow` vs `/fact-finding`, and should be reconciled).
2. User either opens an existing Veeva deviation by ID, or starts a **guided intake** (`POST /api/fact-finding/intake/start`), which creates a provisional `INTAKE-*` session with no Veeva record yet.
3. User and an AI assistant (Cortex-backed "agent-turn") work through field drafting, evidence retrieval (procedure/process-evidence lookups), and fact confirmation.
4. The intake can be disposed as "no deviation required" (writes nothing), or — if ready — pushed via `POST /intake/{id}/create-in-veeva`. This path is gated behind explicit config/approval flags, routes through a MuleSoft SAPI write path into a **Veeva sandbox only**, and is fully disabled in production.
5. All actions, drafts, confirmations, and provenance are persisted and audited in Postgres.

### Users

Quality investigators, deviation mentors, and QA Compliance staff at IAPI and PR05, accessing a CATS-hosted web workbench. Access is currently internal/pilot-only; non-SSO fallback identities (`pilot_anonymous` / `cats-pilot-unattributed`) are used pending full platform SSO integration.

---

## 2. Tech stack

### Backend

| Layer | Choice | Version |
|---|---|---|
| Language | Python | 3.14 |
| Web framework | FastAPI | 0.115.6 |
| ASGI server | uvicorn[standard] | 0.34.0 |
| Validation / settings | pydantic / pydantic-settings | 2.13.5 / 2.15.0 |
| HTTP client | httpx (async) | 0.27.2 |
| ORM | SQLAlchemy | 2.0.36 |
| Migrations | Alembic | 1.14.0 |
| DB driver | psycopg[binary] | 3.2.13 |
| Test framework | pytest | 8.4.2 |
| Config parsing | PyYAML | 6.0.2 |

- **8** pinned production dependencies, **+2** dev-only (pytest, PyYAML) → 10 total in a dev install.
- Single FastAPI app (`app/main.py`), one mounted router (`app/routers/fact_finding.py`). The `backend/services/` subfolders for other services (`deviation-intake-service`, `evidence-service`, `workbench-api`, `workflow-orchestrator`) are **reserved/empty boundary folders** for a future microservice split — today everything runs as one FastAPI process plus the `fact-finding-service` business-logic package.
- No lint/type-check tool is yet "approved" per repo convention.

### Frontend

| Layer | Choice | Version |
|---|---|---|
| UI framework | React / react-dom | 19.0.0 |
| Routing | react-router-dom | ^7.1.1 |
| Build tool | Vite + @vitejs/plugin-react | ^8.0.0 / ^6.0.0 |
| Styling | Tailwind CSS (+ postcss) | ^4.0.0 |
| HTTP client | axios | ^1.7.9 |
| Icons | lucide-react | ^0.469.0 |
| Diagrams/graphs | reactflow + dagre | ^11.11.4 / ^0.8.5 |
| Charts | recharts | ^2.15.0 |

- Node engine requirement: **>=20.19.0** (`.nvmrc`, `package.json`).
- npm workspaces monorepo layout: `frontend/apps/deviation-workbench` (the one real app) + `frontend/packages/{api-client, domain-types}` (real) + `{config, observability, test-utils, ui}` (reserved placeholders, `.gitkeep` only).
- **8** runtime dependencies, **5** devDependencies (13 declared, excluding transitive).
- Package registry: Lilly JFrog Artifactory (`frontend/.npmrc`).
- Dev server proxies `/api` → `localhost:8000`.
- Production build: static `dist/`, served by a minimal dependency-free Node server (`server.mjs`) on port 3000.
- **No frontend tests exist yet** — `frontend/tests/{accessibility,e2e,visual}` contain only `.gitkeep` placeholders.

### Database

- **PostgreSQL 17.8** (AWS RDS in CATS; local via pgAdmin for development).
- Database name: `deviationagent`; app role `deviationagent_app`.
- Exactly **1 Alembic migration** so far (`20260925_0001_fact_finding_persistence.py`), creating **10 tables**:
  1. `fact_finding_sessions`
  2. `fact_finding_turns`
  3. `fact_finding_facts`
  4. `fact_finding_evidence_items`
  5. `fact_finding_field_states`
  6. `fact_finding_proposals`
  7. `fact_finding_confirmations`
  8. `fact_finding_composed_outputs`
  9. `fact_finding_audit_events`
  10. `fact_finding_exports`

### Infrastructure / deployment

- **Platform**: CATS = *Cloud Applications and Technology as a Service*, Lilly's Kubernetes/EKS PaaS. GitOps (ArgoCD/Flux-style), Azure AD/Entra SSO at the ingress, GxP-qualified ("RED Data Ready").
- **Environments**: Dev / QA / Prod / Sandbox, each its own AWS account/VPC (us-east-2):
  - dev: `408787358807`, qa: `474366589702`, prd: `283234040926`
  - K8s namespaces: `mq-deviationagent-dev` / `-qa` / `-prd`
  - Ingress hosts: `deviationagent.{dev,qa}.mq.lilly.com`, `deviationagent.mq.lilly.com` (prod)
- Both backend and frontend build to Docker images in a shared ECR repo `mq-deviation-management-agent` (tags `<env>-backend-sha-*` / `<env>-frontend-sha-*`).
- **Secrets**: AWS Secrets Manager per environment, synced via Kubernetes `ExternalSecret` — includes Cortex cookies/user, Veeva OAuth client/secret/token URL/profile ID, MuleSoft client/secret.
- **Database infra**: Crossplane-provisioned AWS RDS Postgres 17.8, `db.t3.small`, 20 GiB per environment; 35-day backup retention; deletion protection enabled only in prod.
- Backend deployment runs a **migration init container** (`python scripts/run_migrations.py`, Postgres advisory lock, fails the pod on migration failure) before the API container starts.
- Ingress is fail-closed except `/api/health` and `/health`; all other routes require an environment-specific AD group, with trusted identity headers (`X-USER-NAME/EMAIL/GROUPS/ID`) injected by the CATS ingress layer.
- **CI/CD**: GitHub Actions, **7 workflows** — `branch-guard`, `backend-build`, `frontend-build`, `backend-push-to-ecr`, `frontend-push-to-ecr`, `sync-manifests` (opens a PR of rendered `cats-deployment/*.yaml` into a separate CATS infra repo per branch), `refresh-credentials`.
  - Branch → environment mapping: `develop`→dev, `qa`→qa, `prod`→prd (per backend docs; the frontend README states `develop`→dev, `staging`→qa, `main`→prd — this naming is inconsistent between the two READMEs and should be reconciled).
- Both Dockerfiles pull dependencies from Lilly's internal JFrog Artifactory using BuildKit `--mount=type=secret` (never baked into the image).

---

## 3. Data sources & integrations

### A. Veeva Vault QMS (system of record — read, and gated optional write)
- **Reader**: `backend/app/sources/veeva.py` (289 lines) — authenticates via Vault REST `sessionId`, runs VQL queries, maps Vault fields into the app's deviation shape.
- **Writer**: `backend/app/sources/veeva_writer.py` (750 lines) — deliberately separate from the reader, **disabled by default**, **blocked entirely in production**.
- Writes do **not** go direct to Vault REST — they route through **MuleSoft's "deviations SAPI"** (`mqqms-veeva-deviations-sapi`), per ADR-006.
- Fixtures: `backend/integrations/veeva/fixtures/` (4 sample JSON payloads).

### B. Cortex — Lilly's internal GenAI platform (active focus of this feature branch)
Cortex is Lilly's own internally built, SPE-managed enterprise GenAI platform — **not** a third-party product (not Databricks Cortex). It provides:
- A unified model library (Claude / GPT / Bedrock / Gemini access)
- RAG via `doc-chain` / `hybrid-doc-chain`
- Multi-agent orchestration (`agent-chain` = supervisor + worker agents)
- Tool integration via gRPC + MCP Gateway
- "Cortex Guard" content filtering (prompt-injection, PII, jailbreak, toxicity)

**Backend integration surface**: `backend/integrations/cortex/` (per-env placeholder dirs + 72 fixture files), `backend/scripts/cortex/` (15 CLI helper scripts), and the transport/provider layer in `backend/services/fact-finding-service/fact_finding_service/providers/` (`cortex.py`, `procedure.py`, `evidence.py`, `process_team.py`, `snapshot.py`).

**Auth model**: cookie-based (`CORTEX_USER`, `CORTEX_COOKIE_0/1`) — explicitly documented as a temporary core-team pilot exception, not viable long-term since cookies expire and are tied to one person's identity.

**RAG data configs** (current/staged):
- `deviation-management-sops-{dev}` (multimodal) and `-text-dev` (text-first/contextual chunking) — SOP corpus comparison profiles
- `mq-devagent-deviation-sop-guidance-dev` — staged SOP-guidance corpus
- `mq-devagent-process-evidence-{iapi,pr05}-dev` — site-separated current-process evidence (PFDs, cause-and-effect matrices, study reports, hold-time data), deliberately split per site to prevent cross-site retrieval errors
- `mq-devagent-historical-deviations-{iapi,pr05}-dev` — historical deviation examples only (explicitly **not** process authority)
- `mq-devagent-sharepoint-sync-test-dev` — smoke-test config for the SharePoint sync pipeline (the two new fixture files on this branch)

**Agent/model configs**: a supervisor `agent-chain` (`mq-devagent-fact-finding-assistant-dev` / `-supervisor-v2-dev`) routes to child doc-chains (SOP guidance, IAPI/PR05 process evidence, IAPI/PR05 historical deviations) to produce one user-facing Fact Finding narrator; plus a bounded fast-narrator config and a project-forked prompt template (staged, not yet wired to the live runtime).

**Ingestion paths**:
1. **Push-based upload** — `scripts/cortex/cortex_data_cli.py` calling `POST /data/upload/{name}`.
2. **Pull-based SharePoint sync** (the focus of this branch) — `scripts/cortex/cortex_sync_cli.py` calling `/sync`, `/sync/trigger/{name}`, `/sync/{name}`, `/sync/metrics/{name}`, pointed at the `MQDeviationData / Shared Documents / mq_deviation` SharePoint folder. Currently manual-smoke-test only; production/scheduled use will need service auth (LIGHTClient + Secrets Manager) instead of cookie auth.

**Vector stores**: Pinecone (recommended for dev) and Elasticsearch, both in active use. Embeddings tested include `text-embedding-3-small/large`, `text-embedding-ada-002` (Azure/OpenAI), `multimodalembedding` (Vertex), plus Cohere and Titan embeddings.

### C. MuleSoft (integration layer for Veeva writes)
"SAPI" (`mqqms-veeva-deviations-sapi`) is Lilly's preferred real-time integration platform for the Veeva write path. Config via `MULESOFT_SAPI_BASE_URL`, `MULESOFT_SAPI_ALLOWED_HOSTS`, `MULESOFT_SAPI_TARGET_VAULT_DNS`, `MULESOFT_CLIENT_ID/SECRET`, idempotency flags.

### D. Other systems referenced in requirements/discovery (not yet wired into code)
These appear only in `backend/docs/requirements/baseline/*` as candidate/future data sources, not as live integrations: SAP (one-way GDD batch-impact notification), QDocs/eDMS/QualityDocs (controlled documents, "Yellow"/"Orange" classification), GMARS (IBM Maximo CMMS), TrackWise (maintenance audits, not deviations), GMDF/GMDF-DP (Global Manufacturing Data Fabric), Syncade, eLogs, eTickets, DeltaV, OSI PI/PI Vision/PI Historian, LIMS/Darwin, MODA, Denodo, QBDVision, PMX, TrakSys, MES. `backend/integrations/upstream-data/` exists as an empty reserved placeholder for these.

### E. Naming clarifications
- **CATS** = "Cloud Applications and Technology as a Service" — Lilly's Kubernetes/EKS PaaS.
- **"MQ"** in the repo name, ingress hosts (`*.mq.lilly.com`), and AD groups (`mqqms_ods_developer`) most plausibly stands for **"Manufacturing Quality"** (inferred from the `mqqms` naming of the Veeva SAPI route and AD groups) — this is **inferred, not explicitly confirmed** anywhere in the repo.
- There is **no message-queue technology** (no IBM MQ, RabbitMQ, Kafka, AMQP) anywhere in this codebase. All integration is synchronous HTTP/REST. So "MQ" is not "Message Queue" in this project.

---

## 4. By the numbers

| Metric | Count |
|---|---|
| Backend Python files (excl. venv) | 109 |
| Backend LOC (Python) | ~39,369 |
| Frontend JS/JSX/TS/TSX files | 28 |
| Frontend LOC | ~4,395 |
| Backend API endpoints | 22 (2 health + 20 Fact Finding routes) |
| Database tables | 10 (in 1 Alembic migration) |
| Backend dependencies (prod / dev-added) | 8 / +2 |
| Frontend dependencies (runtime / dev) | 8 / 5 |
| Backend test files / test functions | 14 / 223 |
| Frontend test files | 0 |
| CI/CD workflows | 7 |
| Cortex fixture files | 72 |
| Cortex CLI helper scripts | 15 |
| ADRs (architecture decisions) | 3 |
| Markdown docs repo-wide | 74 |
| `backend/docs` files | 54 |

**Caveat**: figures like "152 equipment deviations" or "14.2-day average cycle time" that appear in `backend/docs/requirements/baseline/01_project_charter.md` are explicitly marked `[UNVERIFIED]` in that source document and are **not** a formally approved baseline — do not treat them as confirmed facts.

### Backend API endpoints (all under `/api/fact-finding` unless noted)

- `GET /health`, `GET /api/health`
- `GET /demo-cases`, `/source-records`, `/intake/sites`, `/me`, `/intake/drafts`
- `POST /intake/start`, `/intake/{session_id}/create-in-veeva`, `/intake/{session_id}/dispose`
- `POST /session/start`, `GET /session/{session_id}`, `GET /session/{session_id}/audit`
- `POST /fields/{field_id}/draft`, `/fields/draft`, `/fields/{field_id}/confirm`
- `POST /process-team/resolve`
- `POST /handoff/preview`
- `POST /assistant/chat`, `/assistant/chat/jobs`, `GET /assistant/chat/jobs/{job_id}`

> Note: the root `README.md` references a `GET /api/deviations` endpoint reading live Veeva data — this route was **not found** registered in `app/main.py` or `app/routers/fact_finding.py` as of this audit. Treat it as legacy/aspirational documentation pending verification, not a currently live endpoint.

---

## 5. Architecture at a glance

### Backend (`backend/`)

```
app/                         deployable FastAPI app (official entrypoint)
  main.py                     FastAPI instance, CORS, telemetry middleware, health routes
  routers/fact_finding.py     1,869 lines — all 20 Fact Finding HTTP routes
  sources/                    Veeva read (veeva.py) + write (veeva_writer.py) + auth
  fact_finding/               record adapters, DI container, identity, schemas
services/
  fact-finding-service/       the real business-logic package
    agent_turn/                drafter, parser, validators, question_plan, engine, etc. (12 files)
    providers/                 cortex.py, procedure.py, evidence.py, process_team.py, snapshot.py
    composer.py, persistence.py, audit.py, workbench.py, retrieval_grounding.py, contracts.py, ...
    config/                    16 JSON config files (field matrix/contracts/dependencies, question plan,
                                process-team rules, site profiles, stage policy, veeva-write-{env}.json, ...)
  deviation-intake-service/, evidence-service/, workbench-api/,
  workflow-orchestrator/       reserved/empty boundary folders for future service separation
integrations/
  cortex/{dev,qa,prd}/         per-env placeholders (.gitkeep — real values from Secrets Manager)
  cortex/fixtures/             72 Cortex config/prompt/eval JSON fixtures
  veeva/fixtures/              4 sample Veeva JSON payloads
  upstream-data/               empty reserved placeholder
scripts/cortex/                15 Cortex CLI helper scripts
alembic/versions/              1 migration, 10 tables
docs/                          54 files: adr/, api-contracts/, architecture/, domain/, operations/
                                (incl. cortex/, veeva/), requirements/baseline/ (#00–18), security/
tests/                         14 files, 223 test functions
```

### Frontend (`frontend/`)

```
apps/deviation-workbench/src/
  app/App.jsx                  root component/router
  components/                  Toast.jsx, TopBar.jsx
  features/landing/             LandingPage.jsx, OpenRecordModal.jsx, ReportEventModal.jsx
  features/workbench/           WorkbenchPage.jsx + 7 panels (Activity, AgentRail, ContextSidebar,
                                 Conversation, DeviationDetails, LifecycleNav, Sources) + 3 JS helpers
  features/fact-finding/        reserved, currently empty
  lib/                          apiClient.js, clientLogger.js, deviationId.js, stage.js, useMediaQuery.js
  theme/ThemeContext.jsx
packages/
  api-client/, domain-types/    real shared packages (TS)
  config/, observability/, test-utils/, ui/   reserved/empty placeholders
```

### Authentication / identity

Three layers:
1. **CATS ingress SSO** (Bouncer/Azure AD) injects `X-USER-NAME/EMAIL/GROUPS/ID` headers once enabled for an environment.
2. Backend trusts these headers as ingress-provided but does **not yet cryptographically verify** them (pending a platform decision on `oauth_token_headers`).
3. **Local-only fallback**: `X-Workbench-User` header, accepted only when `APP_ENV=local`.

Current pilot deployments (dev/QA) run in a largely **unattributed identity mode** (`cats-pilot-unattributed` / `pilot_anonymous`); writes additionally require the explicit flag `DEVIATION_WRITE_ALLOW_UNATTRIBUTED=true`. This mode is **blocked entirely in production**.

### Architecture Decision Records (`backend/docs/adr/`)

- **ADR-001**: this repo is the sole home for Fact Finding code (a prior "donor repo" is retired).
- **ADR-002**: orchestration shape proposed, pending Phase 1 scorecard.
- **ADR-006**: single-mode runtime — Veeva reads via OAuth session exchange; Veeva writes via MuleSoft SAPI only (no direct Vault REST writes).

### Governance guardrails

`backend/AGENTS.md` imposes hard "never" rules reflecting GxP/regulated-industry constraints: no autonomous Quality decisions, no classification/approval/disposition, no bypassing Veeva lifecycle controls, no production writes, no fabricated evidence or citations.

---

## 6. Open items worth tracking

- Reconcile the Fact Finding workbench route name across READMEs (`/workflow` vs `/fact-finding`).
- Reconcile the branch→environment naming inconsistency between backend and frontend READMEs (`qa` vs `staging` for the QA environment).
- Verify whether `GET /api/deviations` (referenced in the root README) is a live route or stale documentation.
- `pytest.ini` ignores two test files (`test_fact_finding_intake_guidance.py`, `test_workflow_router.py`) that no longer exist in `backend/tests/` — likely stale entries from a prior refactor.
- The expansion of "MQ" in the project name is inferred ("Manufacturing Quality"), not explicitly documented anywhere in the repo.
- Frontend has zero automated tests; `frontend/tests/{accessibility,e2e,visual}` are placeholder directories only.
