# COTS Impact Analyzer — What It Is & How It Works

*A walkthrough for explaining this project to someone new.*

---

## In one sentence

When a vendor (Waters, LabVantage, etc.) releases a patch for software Lilly uses, this tool reads the vendor's changelog and tells you, line by line, whether that change actually affects how Lilly uses the product — using AI, instead of a person manually reading a 40-page PDF.

---

## The problem it solves

Before this existed, when a COTS (commercial-off-the-shelf) vendor shipped a patch, someone on the TEA or M&Q team had to:

1. Open the vendor's release notes — often a dense Excel sheet or a 30+ page PDF full of bug fixes, known issues, and feature notes.
2. Go through it line by line, cross-referencing it against what Lilly actually uses and how.
3. Decide which changes matter, whether existing regression tests still cover them, and whether new tests are needed.
4. Write all of that up for every single changelog entry.

That's slow, repetitive, and easy to get wrong when a document has 100+ entries. This tool automates steps 2–4: upload the vendor document, pick which "analyzers" (impact lenses — e.g. "SOP impact", "requirements impact") should look at it, and get back a structured spreadsheet with an AI-generated impact assessment for every row.

---

## Who it's for

Built collaboratively by Lilly's **TEA** and **M&Q** teams. Two kinds of users:

- **Regular users** — upload a changelog, pick a product and analyzers, submit, download the result.
- **Admins** — configure which products exist, which AI "agents" and prompts are available, and which analyzers combine them.

---

## Walking through a real example

Say someone uploads a 36-page Waters NuGenesis LMS release-notes PDF.

1. **Upload** — the PDF is saved, and immediately a preview extraction runs: the app reads the PDF, splits it into chunks (because a 36-page document is too much to hand an AI in one shot), sends those chunks to Cortex (Lilly's internal AI gateway) in parallel, and gets back a clean table of rows like:

   | functional_area | bug_id | description |
   |---|---|---|
   | LMS | INFLMS-2499 | Previously, if the recent list of Sample Templates... |
   | SmartBuilder | INFLMS-27323 | Previously, while editing the Excel sections... |

   If the AI extraction ever fails, there's a non-AI backup parser (reads bold text and font sizes to guess structure) so the user never gets stuck with nothing.

2. **Submit** — the user picks a product (e.g. "LabVantage") and the analyzers they want (e.g. "SOP impact," "Requirements impact"). A job is created and handed to a background worker — the user doesn't sit and wait.

3. **Behind the scenes**, three stages run in order:
   - A **pre-analyzer** checks each row against the actual modules Lilly uses, and flags irrelevant rows to skip (no point analyzing a fix for a module nobody at Lilly even has turned on).
   - Each selected **analyzer** sends every remaining row to its own AI agent + prompt — one agent might check SOP relevance, another checks test-coverage impact — all running concurrently.
   - A **post-analyzer** looks at everything every analyzer said about a row and writes one final, holistic verdict.

4. **Download** — the user gets back an Excel file (or JSON), one row per changelog entry, with every analyzer's findings as columns.

---

## The moving parts

```
   Streamlit (what the user sees)
        |
        v
   FastAPI (the API — upload, submit, check status, download)
        |
        v
   Celery + Redis (background job queue — so the user isn't stuck waiting)
        |
        v
   Cortex (Lilly's internal AI gateway — does the actual "thinking")
        |
        v
   PostgreSQL (stores jobs, results, config — no fancy ORM, just plain SQL)
```

Three of these — FastAPI, the Celery worker, and (separately) Streamlit — run as two Docker containers in production, with Redis as a sidecar. Nothing here is a single monolith; each piece has one clear job.

**One deliberate security decision worth knowing**: the AI calls to Cortex always happen from the backend (FastAPI/Celery), never from the Streamlit frontend. That's because the backend holds the real Cortex credentials, and we don't want those sitting in the browser-facing container.

---

## Why these particular technologies

- **FastAPI** — modern, async, auto-generates API docs, plays well with background job dispatch.
- **Celery + Redis** — the AI calls can take minutes for a big document; a web request shouldn't block that long, so the real work happens in a background worker.
- **PostgreSQL via raw asyncpg (no ORM)** — the team chose to write SQL directly rather than add an ORM layer; keeps the data model simple and the queries explicit.
- **Streamlit** — fast to build internal admin/analyst tools in pure Python, no separate frontend framework needed.
- **Cortex** — Lilly's own internal AI/LLM gateway, so the app never talks to an external AI vendor directly; auth, logging, and model access are all centralized there.

---

## The project, by the numbers

| | |
|---|---|
| Python files | 89 |
| Lines of Python | ~8,600 |
| API endpoints | 31 |
| Background job types (Celery tasks) | 4 |
| Screens in the UI | 7 |
| Database tables | 7 |
| File types it can read | 3 — `.xlsx`, `.xls`, `.pdf` |

---

## What data it works with

- **Input**: vendor changelog files — Excel (most common) or PDF — plus an optional "which modules does Lilly actually use" Excel file to filter out irrelevant entries.
- **The AI's knowledge**: Cortex agents answer using a RAG (retrieval-augmented) setup — they can pull in reference material like Veeva SOPs or LabVantage regression-test documents when forming their verdict. That reference library lives on the Cortex platform itself, outside this app.
- **Output**: an Excel or JSON report, one row per changelog entry, with every analyzer's assessment as a column.

**One honest caveat**: there's an admin screen that looks like it lets you "refresh" that RAG reference library from inside this app — but today that button is a placeholder. It logs that it was clicked and returns success, but doesn't actually trigger anything on Cortex yet. Keeping that reference library up to date is still a manual, outside-this-app process.

---

## Where it runs

Three environments — **dev**, **qa**, and **prod** — each a separate deployment, with its own database and Redis. Code automatically builds and deploys to dev/qa whenever it's merged; **database schema changes always need a manual step** after deploying, so a release isn't fully "done" until someone runs that migration.

---

## The short version, if someone only has 30 seconds

*"Vendor patches a product → their changelog goes in → AI reads every line and tells us what actually matters to Lilly, with impact details and test-coverage recommendations → we get back a spreadsheet instead of someone spending a day reading a PDF."*
