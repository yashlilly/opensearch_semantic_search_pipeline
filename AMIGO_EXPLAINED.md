# Explaining AMIGO — A Walkthrough

Use this as a script for talking someone through the project. It builds up from "what problem does this solve" to "how does it actually work" to "what's under the hood."

---

## 1. The elevator pitch

Eli Lilly runs manufacturing lines across sites in Fegersheim, Sesto, RTP, IPM, IDAP, Concord, and RAMP — 7 sites, 28 lines in total. Engineers standing on those lines constantly need two things:

1. **Answers** — "What does this alarm code mean?" "What's the SOP for this changeover?" "What happened on this work order last month?"
2. **Decisions** — "Should I intervene on this batch right now?" "Is this lane showing an anomaly?" "What's the right re-stoppering move here?"

Today those answers live scattered across SOPs, work-order systems, plant historians, and tribal knowledge. AMIGO is the AI layer that sits on top of all of that and gives engineers one place to ask questions and get recommendations — tailored to the exact site and line they're standing on.

It's not a generic chatbot. The system changes its behavior depending on which site/line you're logged in for, because every line has different sensors, different failure modes, and different data sources behind it.

---

## 2. What it feels like to use

Picture an engineer on the floor at RTP, line R6. They open AMIGO and can do three things:

- **Ask a question in plain English** — "Why did we get so many rejects on batch 4521?" The system searches work orders, SOPs, and quality documents, and comes back with a cited answer: here's the work order, here's the SOP page, here's the OneNote entry — click through to the source.
- **Check the dashboard** — a live view of the three production tools: Intervention Recommendation, Lane Restoppering, and Anomaly Detection (ALMD), each scoped to R6 specifically.
- **Get pushed a Teams notification** — the system can proactively ping a Teams channel: "Batch X is showing a plunger-depth anomaly" or "Line R7 restoppering recommendation ready."

Under the hood those three experiences are powered by very different machinery, which is why the project is split into four repositories instead of one monolith.

---

## 3. The four repos — think of them as four departments

### Core (the front door / coordinator)
This is the repo you're sitting in. It's the thing the frontend actually talks to. It does three jobs:
- **Lets people in** — validates the login token, checks which AD group the person belongs to, and figures out which sites/lines/tools they're entitled to see.
- **Serves the dashboard** — pulls cached results out of its own Postgres tables so the dashboard loads fast, instead of hitting live plant systems every time.
- **Routes deeper requests** — when something needs a live answer (an ongoing batch, a chat question), it calls out to the other two microservices.
- **Onboards new lines** — when a new site/line gets added to AMIGO, this is where that configuration (which AD group owns it, which tools it has) gets registered.

### Agents (the chatbot brain)
This repo answers natural-language questions. It runs two agents side by side:
- **GMARS agent** — answers questions about work orders ("what happened on this machine last week"). It's built as a multi-step AI pipeline (using LangGraph): first it breaks your question down into a structured query, then an LLM decides what to search for, then it runs a hybrid keyword+semantic search against an index of work orders, then another LLM call writes a cited answer.
- **RAG/CORTEX agent** — answers questions that need SOPs, quality documents, or OneNote knowledge. It fans the question out in parallel to Lilly's internal CORTEX AI platform across three knowledge sources (Veeva quality docs, SharePoint, OneNote) and stitches the answers together with citations.
- There's also a lightweight **router** that reads your question first and decides: is this a GMARS question, a RAG question, or does it need both?

### Data Ingestion (the plumbing that feeds the chatbot)
Before the agents repo can answer a question about a work order, that work order has to get out of Redshift, be understood by an AI model, and land in a searchable index. That's this repo's whole job:
- Every 30 minutes, it pulls new/changed work orders out of Redshift.
- It also pulls Veeva quality documents and SharePoint/OneNote content.
- For every work order, it asks Azure OpenAI to pull out 9 structured fields from the messy free-text description, and generates a 3072-dimension embedding vector.
- It writes all of that into OpenSearch so it can be found later by keyword *or* by meaning.
- It's fully event-driven and self-healing: if something fails to upload, it's marked and automatically retried next run; nothing gets silently dropped.

### Predictive Tools (the engineering/ML brain)
This is where the actual plant-floor intelligence lives — not chat, but recommendations:
- **Anomaly Detection (ALMD)** — watches sensor signals in near-real-time for 7 production lines across 4 failure modes (plunger depth, TRR, insertion rod, vacuum performance) and flags anomalies on the latest batch.
- **Intervention Recommendation** — pulls together batch targets, rejects, and alarms for 18 different site/line configurations and tells an engineer whether (and how) they should intervene on a running batch.
- **Lane Restoppering** — recommends how to restart/re-stopper a line after a stop.
- **Notifications** — pushes all of the above into Teams channels on a schedule.

This repo is the one that talks directly to the actual plant systems: SEEQ (which sits on top of OSI PI), PMX, Denodo, Pharmasuite, and Databricks. It's the only repo that touches raw sensor/batch data live.

---

## 4. How a question actually flows through the system (concrete example)

Say the engineer types: *"Why are we getting rejects on R6 today?"*

1. Frontend sends the question to **Core**, with the user's auth token.
2. Core checks the token, confirms the user is entitled to RTP/R6, and forwards the question to **Agents**.
3. The **Intelligent Router** inside Agents reads the question and decides this sounds like a GMARS (work-order) question, maybe hybrid.
4. The **GMARS pipeline** kicks in:
   - An LLM call decomposes "rejects on R6 today" into a structured search (line=R6, time=today, topic=rejects).
   - A planner LLM decides which tools to call — hybrid search, direct work-order lookup, or both.
   - It queries **OpenSearch**, which was populated hours earlier by the **Data Ingestion** pipeline pulling from Redshift.
   - A final LLM call writes a human-readable, cited answer, pulling in real work-order numbers.
5. Citations get resolved back to actual documents in **DynamoDB** and presigned S3 URLs so the user can click through to the source PDF.
6. The answer streams back through Core to the frontend, and the conversation gets saved to DynamoDB so follow-up questions have context.

Meanwhile, totally independently, **Predictive Tools** is running its own loop: polling SEEQ/PMX/Denodo for R6's live batch data, checking it against anomaly thresholds, and — if something's off — pushing a Teams alert without anyone having asked a question at all.

Those are two separate "experiences" (reactive chat vs. proactive monitoring) stitched together by Core.

---

## 5. Why these particular technology choices

- **FastAPI everywhere** — all four services are Python/FastAPI microservices. Consistent, async-friendly, easy to containerize identically.
- **LangGraph for GMARS** — the work-order question-answering isn't a single LLM call, it's a multi-step reasoning process (decompose → plan → search → summarize). LangGraph lets that be modeled explicitly as a graph with branching paths (a simple question takes a short path; a "compare these two batches" question takes a longer, more careful path through a bigger reasoning model).
- **Two different LLM "sizes"** — a cheaper/faster model handles routine planning and summarization; a more expensive reasoning model (o4-mini) only gets invoked for genuinely hard comparison questions. That keeps cost and latency down for the 90% of questions that are simple.
- **CORTEX instead of a custom RAG stack for SOPs** — Lilly already has an internal AI platform for document Q&A, so AMIGO doesn't reinvent that; it calls out to it and focuses its own AI investment on the manufacturing-specific GMARS problem.
- **OpenSearch Serverless with hybrid search** — work-order descriptions are often short and jargon-heavy, so pure semantic search misses exact part numbers/codes, and pure keyword search misses paraphrased questions. Combining both (BM25 + k-NN) covers both cases.
- **SEEQ instead of querying OSI PI directly** — OSI PI is a raw historian; SEEQ is the analytics layer plant engineers already use to define "what is a batch" and "what is a signal." Reusing SEEQ's workbook/worksheet definitions means AMIGO's anomaly detection matches what engineers would see if they opened SEEQ themselves.
- **Everything behind AD-group entitlements** — because different engineers at different sites should only see their own lines, access control is baked into the data model from the start (site-line configs carry an AD group ID), not bolted on later.
- **Event-driven ingestion over polling** — new documents trigger S3 events → SQS → Lambda, instead of a service constantly asking "is there anything new yet?" Cheaper, faster, and it naturally handles bursts.

---

## 6. The scale, in plain numbers

- **7 sites, 28 production lines** currently live on the platform
- **4 microservices / repos**, each independently deployable
- **18** distinct intervention-recommendation configurations (one per site/line combo)
- **7 lines × 4 failure modes** = anomaly detection covers 28 distinct detection models, each with a "trend analysis" second phase
- **Every 30 minutes**, new work orders get pulled and indexed; **every 5 minutes**, the search index gets reconciled
- **9 fields** get extracted by AI from every single work order description
- **3072-dimension** embedding vectors power the semantic search
- **3 knowledge sources** (Veeva, SharePoint, OneNote) get searched in parallel for every SOP-style question
- Roughly **300+ commits** of iteration on the core repo alone — this has been actively developed and refined, not a one-shot build

---

## 7. The one-sentence summary for each repo

- **Core**: "The gatekeeper and dashboard — checks who you are, shows you what you're allowed to see, and calls the specialists when it needs a real answer."
- **Agents**: "The conversational brain — turns a plain-English question into a cited answer by orchestrating LLM calls against work-order search and Lilly's CORTEX platform."
- **Data Ingestion**: "The plumbing — continuously pulls raw manufacturing and quality data out of source systems, has AI tag and embed it, and loads it into a searchable index."
- **Predictive Tools**: "The engineer's second opinion — talks directly to plant systems (SEEQ, PMX, Denodo) to recommend interventions, flag anomalies, and tell Teams channels when something needs attention."

Put together: data flows in from the plant and from documents (Data Ingestion) → gets stored and indexed → gets reasoned over by AI when someone asks a question (Agents) or watched continuously for problems (Predictive Tools) → and all of it is gated, aggregated, and served to the actual user through one front door (Core).
