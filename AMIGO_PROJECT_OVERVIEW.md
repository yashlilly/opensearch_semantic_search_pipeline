# AMIGO — AI Monitoring Platform

**Full project overview covering all four repositories, tech stacks, data sources, and operational scale.**

> Source repositories analyzed:
> - `EliLillyCo/gis-amigo-ai-monitoring-core` (this repo)
> - `EliLillyCo/gis-amigo-ai-monitoring-agents`
> - `EliLillyCo/gis-amigo-ai-monitoring-data-ingestion`
> - `EliLillyCo/gis-amigo-ai-monitoring-predictive-tools`

---

## 1. What AMIGO Is

AMIGO (AI Monitoring for Industrial & General Operations) is an AI-enabled monitoring and operational-support platform for production-line engineers at Eli Lilly manufacturing sites. It combines:

- Conversational access to production and engineering knowledge (chat / "Easy Search")
- Structured KPI/FAQ query handling
- Intervention recommendation workflows
- Anomaly-detection use cases (ALMD)
- Work-order and spare-parts intelligence through **GMARS**
- User feedback capture and conversational memory

It is domain-aware: behavior changes by **site and production line** rather than being one generic chatbot. Three production tools sit at the center of the platform: **Intervention Recommendation**, **Lane Restoppering**, and **Anomalous Lane Monitoring Detection (ALMD)**.

### Sites and lines currently onboarded (7 sites, 28 lines)

| Site | Lines |
|------|-------|
| Fegersheim (FEG) | CGV3 |
| Sesto | PFS2, PFS ASIM, A4 Wet, A4 Pack, R2, R3 |
| RTP | R6, R7, R11 (PFS4/PFS5 also present in predictive-tools config) |
| IPM | PFS3, B105A, B105B |
| IDAP | A1, A2, A5, R1, R4, R5, R4Pack, R5Pack |
| Concord | R8W, R9W, R10W (Veeva ingestion also covers PFS6/PFS7/PFS8) |
| RAMP | PFS |

The core repo's CloudFormation template (`template.yaml`) deploys **28 nested per-site-line serverless application stacks** (`LillyServerlessApplicationResource*`), one for each site-line combination above.

---

## 2. Repository Map

| Repository | Role |
|---|---|
| **gis-amigo-ai-monitoring-core** | Backend-for-frontend / core microservice. Auth & entitlements, dashboard aggregation, site-line onboarding, cached-data retrieval, service discovery to the predictive-tools and agents microservices. Owns 28 per-site-line CloudFormation stacks. |
| **gis-amigo-ai-monitoring-agents** | GMARS (work-order Q&A) and RAG/CORTEX conversational agents — the "Easy Search" chatbot backend. LangGraph-orchestrated multi-agent pipeline. |
| **gis-amigo-ai-monitoring-data-ingestion** | Event-driven ETL: pulls GMARS work orders, Veeva quality documents, and SharePoint/OneNote content; enriches with Azure OpenAI; indexes into OpenSearch Serverless for hybrid search. |
| **gis-amigo-ai-monitoring-predictive-tools** | Domain logic & ML/analytics microservice for Intervention Recommendation, Lane Restoppering, and ALMD anomaly detection, plus Teams notifications. Integrates directly with SEEQ, PMX, Denodo, OSI PI, Pharmasuite, Databricks. |

All four repos share environment promotion via GitHub branches: `develop → dev`, `staging → qa`, `uat → uat`, `main → prd` (prod).

---

## 3. Technology Stack

### 3.1 Shared across all repos
- **Language**: Python 3.12+ (Glue jobs in data-ingestion run Python 3.9 PythonShell)
- **Web framework**: FastAPI (ASGI, Uvicorn/Gunicorn workers)
- **Validation**: Pydantic v2 / pydantic-settings
- **Package manager**: `uv` (core, agents); `pip` + `requirements.txt` (predictive-tools, data-ingestion/Glue)
- **Cloud**: AWS (ECS Fargate, Lambda, Glue, Step Functions, EventBridge, S3, DynamoDB, RDS/PostgreSQL, Redshift, OpenSearch Serverless, Secrets Manager, SQS, SNS/Teams webhooks)
- **IaC**: AWS CloudFormation / SAM (`template.yaml`, `ecr-template.yaml`, per-repo `params.<env>.json`)
- **CI/CD**: GitHub Actions (`aws-code-deployment.yaml`, `lint-pr.yml`, `checkmarkx-one.yaml` for static/security scanning)
- **Containerization**: Docker, built on `amazonlinux:2023`, non-root `ec2-user` (uid/gid 1001)
- **Testing**: pytest, pytest-asyncio, pytest-cov, moto (AWS mocking), freezegun

### 3.2 Core repo (`gis-amigo-ai-monitoring-core`)
- `fastapi>=0.115`, `uvicorn[standard]`, `gunicorn`
- `sqlalchemy>=2.0.29` + `psycopg2-binary` → PostgreSQL (AWS RDS)
- `boto3` → DynamoDB, S3, Secrets Manager, OpenSearch
- `opensearch-py`, `requests-aws4auth` → OpenSearch Serverless (AOSS)
- `PyJWT`, `cryptography` → JWT auth / entitlements
- `python-ulid` → ULID-based sort keys for chat history pagination
- Served on container port **8081**, health-checked via `/health`

### 3.3 Agents repo (`gis-amigo-ai-monitoring-agents`)
- **LangChain 0.3.30**, `langchain-community`, `langchain-core`, `langchain-aws`, `langchain-text-splitters`
- **LangGraph ≥1.0.1** — orchestrates the GMARS multi-agent pipeline as a graph
- **Azure OpenAI** via `langchain-openai` + `azure-identity` (Azure AD token auth, auto-refreshing)
  - "Thinking" deployment: **o4-mini** (temperature=1, used for decomposition / enhanced comparison planning)
  - Standard chat deployment (planner + summarization + answer generation, `reasoning_effort: low` for final answers)
  - `AzureOpenAIEmbeddings` for vector search queries
- `openai>=2.17.0` SDK
- Custom internal `light-client` wheel package for authenticated CORTEX API calls
- `opensearch-py>=3.1.0` for hybrid BM25 + kNN retrieval
- Served on container port **8081/8083** depending on deployment context

### 3.4 Predictive-tools repo (`gis-amigo-ai-monitoring-predictive-tools`)
- `fastapi==0.129.0`, `uvicorn==0.41.0`
- **SEEQ SDK**: `seeq==66.12.2.20250313`, `seeq_spy==193.22` — analytical layer over OSI PI
- **Denodo**: `denodo-sqlalchemy==2.0.4` — federated SQL access to GMDF data
- **Office365-REST-Python-Client==2.5.2** — Pharmasuite/SharePoint-adjacent integration
- Data science: `pandas==2.2.3`, `numpy==2.2.3`, `scipy==1.15.3`, `matplotlib==3.10.8`, `seaborn==0.13.2`
- LangChain stack again present (`langchain==0.3.20`, `langchain-openai`) + `azure-identity` — AI-assisted analysis/report narration
- `sqlalchemy==2.0.46` + `psycopg2-binary` → RDS caching tables
- `boto3==1.42.52`
- Runs background **SQS pollers** inside the FastAPI app lifecycle for: notifications, data ingestion, and bent-plunger-rod processing

### 3.5 Data-ingestion repo (`gis-amigo-ai-monitoring-data-ingestion`)
- **AWS Glue (PythonShell 3.9)** — core ETL job runtime
- `pandas`, `numpy`, `psycopg2-binary`, `fpdf2`, `pypdf`, `beautifulsoup4`, `cryptography`
- `Office365-REST-Python-Client>=2.4.0` — SharePoint/OneNote extraction
- **Azure OpenAI GPT** (metadata extraction) + embedding model (3072-dim vectors)
- Lambda runtime deps are intentionally minimal (`sqlalchemy`, `psycopg2-binary` — boto3 is pre-installed)
- Custom Lambda layers: `fpdf2-layer`, `gmars-dependencies-layer`, `gmars-pipeline-layer`, `light-client-layer`

---

## 4. AI / LLM Model Usage

| Use case | Model / Platform | Notes |
|---|---|---|
| GMARS decomposition & enhanced comparison planning | **Azure OpenAI o4-mini** ("thinking" deployment) | `temperature=1` (required by o4-mini); structured output via `.with_structured_output()` |
| GMARS planning, standard chat, summarization | Azure OpenAI standard chat deployment | Also used for final answer generation (180s timeout, `reasoning_effort: low`) |
| Query embeddings for OpenSearch kNN search | **AzureOpenAIEmbeddings** | 3072-dimensional vectors |
| Intelligent Router (question classifier) | Azure OpenAI (via `AzureChatOpenAI`) | Classifies query → `gmars` / `rag_cortex` / `hybrid` with confidence score |
| SOP / document Q&A (Easy Search second agent) | **CORTEX** (Lilly's internal AI platform) | Reached via internal `light-client` SDK, parallel calls per source (Veeva, OneNote, SharePoint) |
| Data-ingestion metadata enrichment | **Azure OpenAI GPT** | Extracts **9 structured metadata fields** per work order (`wonum`) |
| Data-ingestion embeddings | Azure OpenAI embedding model | 3072-dim, same model family as GMARS retrieval embeddings |
| Core repo (per its own README) | **OpenAI** + **CORTEX** | README states the platform's AI/ML models are developed by OpenAI and Lilly's CORTEX platform; hallucination risk mitigated via curated knowledge-base context and structured error handling |

Authentication to Azure OpenAI uses **Azure AD token provider** (auto-refreshing bearer tokens), not static API keys.

---

## 5. Data Sources — What Feeds AMIGO

### 5.1 Manufacturing / OT data (consumed by predictive-tools)

| Source | What it provides | Where used |
|---|---|---|
| **SEEQ** (`seeq`, `seeq_spy` SDKs) | Analytical layer over OSI PI time-series: batch info, start/end times, total good units, use-case-specific sensor tags (Plunger Depth, TRR, Vacuum Performance, Insertion Rod) | Anomaly Detection (all 4 use cases), Intervention Recommendation, Lane Restoppering |
| **OSI PI** | Raw plant time-series historian, fetched indirectly through SEEQ workbooks/worksheets | Batch timing, total good counts |
| **PMX** | Batch/work-order metadata (`raw_pmx_auftrag`, `raw_pmx_arbgang`, `raw_pmx_TEILESTAMM`, `raw_pmx_bo_choicelists`, `raw_pmx_arbpl` tables in **GMDF** raw schema) | Target units, batch codes, dose quantities, line codes — RTP, IDAP, Concord, CGV3 |
| **Denodo** | Federated SQL virtualization layer for alarms/stoppages (e.g., `con.r8w_paw_noncritical_alarm`, `con.r8w_paw_critical_alarm`, `con.r9w_paw_*`, `con.r10w_paw_*`; `indypar_gmdm.idap_traksys19a3_events_reachpack_r4`) | Alarms, stoppage duration/count (top 5), target units |
| **Pharmasuite** | Target unit data | Intervention Recommendation |
| **Databricks** | Alarms, critical operations data | Anomaly Detection |
| **RDS (PostgreSQL)** | Cached SEEQ time-series data, tool-output caching tables | All predictive tools |

Example concrete tag/workbook identifiers captured in the predictive-tools README (illustrating the granularity of integration):
- RTP R6/R7/R6A: tags like `R6W_M40_MD41.Total_Count_Devices.PV`, `R6W_M40_MD41.PMX_Batch_Confirm_Batch_ID.BAT`
- Sesto PFS2 use-case signals: `SIRM_Plunger_height_01..10.P10.8.PV`, `SIRM_TRR_fault.P10.8.PV`, `DBA-SBA_PT-070-421-01/02.P10.8.PV`, `H6_TR1_T{X,Y,Z}{1,2}_AxisServo.Actual Position.P10.8.PV`
- Workbook/Worksheet GUIDs are configured per site-line (CGV3, IDAP A1/A2/A5/R4PACK/R5PACK, Concord R8W/R9W, IPM B105B, Sesto PFS2)

### 5.2 Knowledge / document data (consumed by data-ingestion → agents)

| Source | What it provides | Ingestion mechanism |
|---|---|---|
| **Amazon Redshift (GMARS)** | Work-order, work-log, long-description tables — the manufacturing maintenance system of record | AWS Glue PythonShell job, incremental via watermark, every 30 min (core pipeline) / every 5 min (index reconciliation) |
| **Veeva Quality Docs** (API v21.2) | SOPs and quality documents (PDF/CSV/DOCX, max 200 pages/split) | Glue job via VQL queries + CSV manifests, tracked in RDS `veeva_documents` table |
| **SharePoint / OneNote** | Site-specific knowledge extracts (`amigo-rtp-centralized-one-note-share-point-1`, etc.) | `Office365-REST-Python-Client`-based Lambda/Glue extraction |
| **CSV pre-staged sources** | `augmented_a1_workorders.csv`, `augmented_a2_workorders.csv`, `augmented_a5_workorders.csv`, etc. | Fallback/legacy load path per line |

### 5.3 Downstream data stores
- **Amazon OpenSearch Serverless (AOSS)** — hybrid BM25 keyword + k-NN (3072-dim) vector index, one index per site-line (e.g., `OPENSEARCH_INDEX_A1`)
- **DynamoDB** — chat history (conversation + message, ULID-sorted), agent processing events, site-line onboarding config
- **S3** — raw/processed/failed document staging, generated PDFs, embeddings, watermark files, session/follow-up artifacts, upload logs
- **RDS PostgreSQL** — document metadata, upload/retry tracking (GMARS + Veeva), historical batch data for Intervention/Lane Restoppering/ALMD/Notifications

---

## 6. Numeric Facts & Scale

| Metric | Count | Source |
|---|---|---|
| Sites onboarded | **7** (Fegersheim, Sesto, RTP, IPM, IDAP, Concord, RAMP) | core README |
| Production lines onboarded | **28** | core `template.yaml` (28 `LillyServerlessApplicationResource*` nested stacks) |
| Core repo REST API endpoints | **~20 documented** (auth, dashboard ×3, intervention ×3, lane-restoppering ×3, almd ×3, easy-search ×8, onboarding ×1) | core README API table |
| Core repo route handler functions (`@router.*`) | **31** | `app/routes/**/*.py` |
| DynamoDB tables (core repo) | **3** (`ConfigSiteLine`, `AgentEventsTable`, `EasySearchChatHistoryTable`) | core `template.yaml` |
| Agents repo API endpoints | **3** functional (`/api/v1/agents/gmars`, `/api/v1/agents/rag-cortex`, `/api/v1/agents/smart`) + 3 health probes | agents repo routes |
| GMARS pipeline nodes | **5–6** (decompose, gmars_plan / gmars_enhanced_planner, execute_tools / enhanced_executor, summarize / enhanced_summarization) | agents `CLAUDE.md` graph description |
| GMARS retrieval tools | **3** (`search.py`, `fetch.py`, `analytics.py`) | `app/modules/gmars/tools/` |
| RAG/CORTEX knowledge sources fanned out per query | **3** (Veeva, OneNote, SharePoint) | agents README |
| Intervention Recommendation site/line configs | **18** JSON configs | predictive-tools `config/intervention_recommendation/` |
| Anomaly Detection equipment lines with full use-case code trees | **7** (PFS2, PFS3, PFS4, PFS5, PFS6, PFS7, PFS10) | predictive-tools `usecase_codes/` |
| Anomaly Detection use cases per line | **4** (Plunger Depth, TRR, Insertion Rod, Vacuum Performance), each with Phase 1 (detection) + Phase 2 (trend analysis, `ta_*`) | predictive-tools modules |
| Anomaly Detection notification configs | **4** (IPM PFS3, RTP PFS4, RTP PFS5, Sesto PFS2) | predictive-tools config |
| Intervention notification configs | **18** (one per intervention site/line) | predictive-tools config |
| Predictive-tools POST endpoints | **9** (4 anomaly-detection use cases + 1 intervention + 1 lane-restoppering + 3 notification channels) | predictive-tools README |
| Data-ingestion AWS resource types in template | 9 distinct types, **≈40 resources** (9 IAM policies, 7 Secrets, 6 Serverless Functions, 6 SQS queues, 4 EventBridge Schedules, 4 Lambda layers, 3 SQS queue policies, 3 Events Rules, 3 DynamoDB tables, 2 OpenSearch security policies, 1 Step Functions state machine, 1 Glue job) | data-ingestion `template.yaml` |
| GMARS metadata fields extracted by LLM per work order | **9 structured fields** | data-ingestion README/CLAUDE.md |
| Embedding vector dimensionality | **3072** | data-ingestion + agents repos |
| GMARS Glue scheduler frequency | Every **30 minutes** (data extraction), every **5 minutes** (index reconciliation / Step Functions trigger) | data-ingestion README |
| Glue batch size | **100 rows/batch** | data-ingestion README |
| SQS batch size (event-driven Lambda triggers) | **10 messages/batch** | data-ingestion README |
| Hybrid search weighting options | 40/60, 50/50, 60/40 (keyword/semantic) | data-ingestion README |
| Veeva document split limit | **200 pages/split** | data-ingestion README |
| Core repo total commits (at time of writing) | **305** | `git log` |
| GitHub repos comprising AMIGO | **4** | core README |

---

## 7. Architecture Pattern (Core Repo)

Strict layered, inward-only dependency flow:

```
HTTP Request
  → routes/        (HTTP concerns, request parsing, status codes)
  → services/       (business logic orchestration)
  → repository/     (data access abstraction)
  → models/         (SQLAlchemy ORM / DynamoDB models)

services/ also reaches into:
  → modules/         (reusable feature logic)
  → infrastructure/  (AWS/Azure clients)

Cross-cutting: core/config.py, core/logging.py, core/exceptions.py, schemas/
```

Rule enforced project-wide: `routes/` and `repository/`/`models/` never import from each other directly — everything funnels through `services/`.

The **agents** repo follows the same layered pattern:
```
Routes → Services → Modules (domain logic incl. LangGraph pipeline) → Infrastructure (AWS clients)
```

The **predictive-tools** repo organizes by feature module instead (`anomaly_detection/`, `intervention_recommendation/`, `lane_restoppering/`, `notification/`, `chat_history/`, `site_config/`, `data_ingestion/`), each with its own executor, connector, config, and repository layer, plus three long-running **SQS pollers** attached to the FastAPI app lifespan.

---

## 8. Security / Auth

- JWT-based authentication validated against an OIDC issuer (`auth_issuer`, `auth_audience`, `auth_scope`), with a Basic Auth fallback path (`basic_auth_secret_name`)
- **Azure AD (AD groups)** drive entitlements: each site-line config stores an `ad_group_name` / `ad_group_id`; `EntitlementService.get_entitlements()` resolves a user's AD group IDs to the site-lines/tools they're allowed to see
- Secrets (RDS creds, OpenSearch creds, Basic Auth, Easy Search, Amigo Auth) are stored in **AWS Secrets Manager**, one secret per concern, referenced by name in app config
- CI includes a dedicated **Checkmarx One** static/security-scanning GitHub Actions workflow (`checkmarkx-one.yaml`) across repos
- All containers run as non-root `ec2-user` (uid/gid 1001) on Amazon Linux 2023 base images

---

## 9. Deployment

- **Compute**: AWS ECS Fargate (ALB-fronted services), AWS Glue (ETL), AWS Lambda (event-driven processors + index reconciliation), Step Functions (data-ingestion orchestration)
- **Environments**: `dev → qa → uat → prod`, mapped 1:1 from GitHub branches (`develop`, `staging`, `uat`, `main`)
- **IaC**: CloudFormation/SAM templates per repo, environment-specific parameter files (`params.dev.json`, `params.qa.json`, `params.uat.json`, `params.prd.json`/`params.prod.json`)
- **CI/CD**: GitHub Actions roles assume per-environment IAM roles (ARNs stored as GitHub Actions secrets) to deploy both application code and infrastructure on merge/promotion
- Naming convention across resources: `{env}-mq-ai-monitoring-*`

---

## 10. Document Provenance

This document was compiled by directly inspecting:
- All README.md and CLAUDE.md files in the four repositories
- `pyproject.toml` / `requirements.txt` / `glue-requirements.txt` dependency manifests
- `template.yaml` / `ecr-template.yaml` CloudFormation/SAM resource definitions
- Route, service, module, and config file trees (direct file reads, not just listings)
- Representative source files: `app/core/config.py` (core), `app/modules/gmars/llm.py` and `app/modules/intelligent_router/*.py` (agents)

No numbers in Section 6 are estimates unless explicitly marked "≈"; all are derived from counting actual files/resources/config entries in each repository at the time of this review.
