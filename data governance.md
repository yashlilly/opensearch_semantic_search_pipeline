# Explaining this project

A plain-language walkthrough of the AI Agents for Data Governance
platform — written the way you'd actually explain it to someone over a
call or in a meeting, not as a technical reference. Use this as a script.

---

## The one-sentence version

It's a platform that uses AI agents to automatically figure out where a
company's data comes from, how clean it is, what it means in business
terms, and whether anyone's actually allowed to call it "governed" —
across a messy real-world estate of databases, data lakes, and legacy
systems that nobody has fully mapped out by hand.

---

## The problem it solves

Any large company that's been around for decades ends up with data
scattered across dozens of systems: a data lake, a few data warehouses, an
ERP system, planning tools, legacy databases that predate half the
current engineering team. Nobody has a complete, trustworthy picture of:

- **Where does this specific column of data actually come from?** (lineage)
- **Is this data any good — complete, unique, consistent, valid?** (data quality)
- **What does this field actually mean in business terms, and who owns it?** (governance)
- **Is this a "Critical Data Element" that regulators or auditors care about?** (criticality)
- **If I need data for a specific business purpose, what's the right, approved source to use?** (data product discovery)

Doing this by hand — a human going table by table, column by column,
system by system — simply doesn't scale. There are tens of thousands of
tables and hundreds of thousands of columns involved. This project
automates that work using a set of specialized AI agents, each focused on
one piece of the problem, all working against the company's real data
systems rather than a mocked-up sandbox.

---

## How it works — the agents

Think of it as five (plus a couple of newer, supporting) specialists,
each doing one job well:

1. **Lineage Agent** — "Where did this data come from, and where does it
   go?" It traces a table or column backward and forward through the
   systems it passes through, builds an interactive graph you can click
   through, and can even read raw SQL to figure out lineage that was
   never explicitly declared anywhere.

2. **Profiling Agent** — "What does this data actually look like?" It
   runs statistics against real tables: how many nulls, how unique are
   the values, do foreign keys actually point to real rows, do values
   obey basic business rules. From that it automatically flags which
   columns are "Critical Data Elements" (the ones that really matter) and
   tags each column with the business domain it belongs to.

3. **Governance Agent** — "What does this mean, in plain English?" Given
   a raw table full of cryptic column names, it uses an LLM to generate
   human-readable descriptions and classification tags (e.g., "this looks
   like PII", "this is a date field representing a shipment date") — the
   kind of documentation work a data steward would otherwise have to do
   by hand, table by table.

4. **Data Quality Agent** — "Can I trust this data?" It runs a
   configurable set of rules — completeness, validity, consistency,
   uniqueness — against the data the Profiling Agent already looked at,
   and produces a scored report plus AI-written suggestions for how to
   fix what's broken.

5. **Product Agent** — "I need data for X — where do I get it?" You
   describe a business goal in plain language ("I need supplier quality
   metrics by region"), and it searches the governance catalog, breaks
   your request down into the right underlying concepts, and recommends
   the actual governed data product(s) that answer it.

There are also two newer, supporting pieces: a **Business/Domain layer**
that gives non-technical business users a simplified dashboard view (per
business domain: how many fields are governed, data quality scores,
compliance checklists) and a **Catalog layer** that unifies everything
into one live, consistent inventory — resolving the fact that different
underlying systems disagree about how many tables actually exist.

---

## How it's built

A normal web application shape, nothing exotic on purpose — a Python
backend (FastAPI) and a React frontend, because the hard part here is the
data problem, not the plumbing.

- **Backend**: Python 3.12 + FastAPI. Each agent is its own self-contained
  module that gets automatically wired into the app from a config file —
  adding a new agent doesn't require touching the app's core routing.
- **Frontend**: React + Vite, with an interactive graph visualization
  library for the lineage views and charting for dashboards.
- **AI**: every agent that needs "understanding," not just arithmetic
  (writing descriptions, classifying data, parsing a natural-language
  business goal), goes through an internal enterprise LLM gateway — never
  a public API directly, for obvious data-sensitivity reasons.
- **Data access**: real connections into Redshift, Databricks, an SAP-side
  database, and a transactional Postgres database that stores the
  platform's own session state, run history, and the lineage graph itself.
  There's also a protocol (MCP) that lets any agent reach out to an
  external live data source on demand, without that source needing to be
  pre-configured — useful for ad-hoc exploration.
- **Everything is config-driven**: thresholds, table names, storage
  paths — all live in one config file plus environment variables, not
  buried in code, so behavior can be tuned without a redeploy.
- **It runs on real infrastructure**: containerized, deployed to a
  managed Kubernetes platform via CI/CD, with a real database backing it,
  not a demo running off a laptop.

---

## The data it actually works with

This is the part that makes it credible rather than a toy: it's not
pointed at sample data. It's reading real production metadata from real
systems — a data lake, multiple data warehouses, planning systems,
regulatory/compliance systems — each with its own quirks (different
naming conventions, different casing rules, tables that exist under
multiple spellings in different places). A lot of the engineering effort
actually goes into reconciling those inconsistencies so the platform can
say, with confidence, "this is the same table" even when three different
systems refer to it three different ways.

---

## The scale — why this isn't a toy project

Some real numbers that give a sense of what "production scale" means
here:

- **Nearly 14,000 real tables** are touched by at least one tracked data
  lineage connection.
- A single real data-ingestion run processed **over 61,000 raw lineage
  records**, resulting in roughly **7,700 persisted connections** between
  tables — and correctly retired a similar number of stale ones.
- One data-quality evidence survey found that only about **1 in 5 tables**
  has any kind of automatically-discovered column-level mapping evidence
  at all — a real, measured gap, not a guess, and useful ammunition for
  "we need to invest more in data discovery" conversations.
- An automated system has independently fingerprinted and matched **over
  28,000 columns** with a **0% false-positive rate** — meaning when it says
  two columns are the same piece of data, it's right.
- The codebase itself is substantial: tens of thousands of lines of
  backend and frontend code, with close to **5,000 automated tests**, built
  by a team of around 20 engineers over roughly six months, with over
  450 completed code changes merged into the product.
- **Seven major business domains** are modeled end-to-end (things like
  Supply Chain Planning, Materials, Supplier Quality) — each with its own
  governance maturity tracking, data quality rules, and dashboard.

---

## Why it matters

Without this, "do we trust our data" is a question that gets answered by
institutional memory and manual spreadsheets — slow, inconsistent, and it
doesn't scale as the company's systems keep multiplying. This platform
turns that into something closer to a continuously-running, queryable
system: ask "where does this column come from," "is this data clean,"
"what does this field mean," or "where do I get governed data for this
business need," and get a real, evidence-backed answer in seconds instead
of a multi-week audit.

It's also honest about its own limitations by design — it explicitly
tracks and surfaces "we don't know" as a distinct answer from "this
doesn't exist," rather than quietly guessing, which matters a lot in a
regulated environment where a wrong confident answer is worse than an
honest unknown.
