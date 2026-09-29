# Kazakhstan Banking Intelligence

A cited, numbers-first AI assistant for Kazakhstan's financial sector.

Ask it "What was Bank CenterCredit's NPL ratio in Q2 2026?" and it returns the exact number, where it came from (document, date, line item), and how fresh the data is. If the data isn't published yet, it says so instead of guessing.

This project has two goals, in this order:

1. **Learn how RAG and modern LLM systems actually work** by building one by hand, step by step.
2. **Turn it into a production-ready system** with an API, scheduled ingestion, tests, and monitoring.

---

## Core Idea

A general LLM can't answer these questions reliably, because the answers are recent, local, and numeric:

- "What was Bank CenterCredit's non-performing loan ratio in Q2 2026?"
- "Compare the capital adequacy (k2) of Halyk Bank and Kaspi Bank over the last four quarters."
- "According to Freedom Bank's 2025 audited statements, what was the total loan portfolio?"
- "Did Bank X meet the minimum capital requirement under the current ARDFM resolution?"

The system splits the work in two:

| Question type | Where the data lives | How it's retrieved |
|---|---|---|
| **Numbers** (ratios, line items, amounts) | Postgres tables | LLM calls SQL tools and gets exact rows |
| **Text** (laws, regulations, audit notes) | Postgres + pgvector | Hybrid search (keyword + vector) with reranking |
| **Both** ("did Bank X meet the requirement?") | Both | A router combines the two paths |

**Rule #1:** the LLM never produces a number from memory. It retrieves it or says it can't.

---

## Data Sources

**MVP: banking**

| Source | What we take | Notes |
|---|---|---|
| ARDFM (finreg.kz) | Monthly prudential indicators for all second-tier banks | Primary source for ratios (k1, k2, NPL, etc.) |
| opi.dfo.kz | Audited financial statements (PDF) | Needs table-aware extraction |
| KASE (kase.kz) | Quarterly reports from listed banks | Cross-check source |
| adilet.zan.kz | Banking laws, ARDFM / NBK regulations | Keep edition history |
| data.nationalbank.kz | Base rate, exchange rates, macro stats | Context, not bank-level data |

**Phase 2 and later:** goszakup.gov.kz (state procurement API), the Samruk-Kazyna procurement portal, and selected data.egov.kz datasets (only the ones that are actually updated).

> Endpoints, formats, and access rules must be checked against the real sites before we write any ingestion code. Don't assume an API exists; verify it.

---

## Tech Stack (kept small on purpose)

| Purpose | Tool | Why |
|---|---|---|
| Language | Python 3.12 | |
| Package manager | `uv` | Fast, one tool for venv and dependencies |
| Database | Postgres + `pgvector` | One DB for both SQL rows and vectors |
| Embeddings | `bge-m3` (sentence-transformers) | Multilingual: Russian, Kazakh, English |
| Keyword search | Postgres full-text search | Learn BM25-style search without an extra service |
| Reranker | `bge-reranker-v2-m3` | Cross-encoder, multilingual |
| PDF extraction | `docling` (fallback: `pdfplumber`) | Layout- and table-aware |
| LLM | Claude API (`anthropic` SDK) | Tool calling for the SQL path |
| API (Phase 2) | FastAPI | |
| Scheduling (Phase 2) | APScheduler or cron | Keep it boring |
| Monitoring (Phase 3) | Langfuse | Traces, costs, eval scores |
| Local infra | Docker Compose | Postgres + app + Langfuse |

**No LangChain or LlamaIndex in Phase 1.** We build the retrieval, prompts, and tool loop by hand so it's clear what those frameworks do under the hood. We can evaluate them later with that knowledge.

---

## Project Structure

```
kz-banking-intel/
├── README.md
├── CLAUDE.md                  # Instructions for Claude Code
├── PROGRESS.md                # Current step, what's done, what's next
├── pyproject.toml
├── .env.example
├── docker-compose.yml
│
├── src/kzbank/
│   ├── config.py              # Settings loaded from .env (one place)
│   ├── ingest/                # Fetching raw data, one file per source
│   │   ├── adilet.py
│   │   ├── ardfm.py
│   │   ├── dfo.py
│   │   └── nbk.py
│   ├── extract/               # Raw data → clean records
│   │   ├── chunking.py        # Text → chunks (legal structure aware)
│   │   ├── pdf_tables.py      # PDF → table rows
│   │   ├── normalize.py       # Units, periods, entity names → BIN
│   │   └── validate.py        # Accounting identity checks
│   ├── storage/
│   │   ├── schema.sql         # All tables in one readable file
│   │   └── db.py              # Connection + small query helpers
│   ├── retrieval/             # Text path
│   │   ├── embed.py
│   │   ├── vector_search.py
│   │   ├── keyword_search.py
│   │   ├── hybrid.py          # Fusion (RRF)
│   │   └── rerank.py
│   ├── tools/                 # Number path: functions the LLM can call
│   │   └── bank_metrics.py
│   ├── agent/
│   │   ├── prompts.py         # All prompts in one place
│   │   ├── router.py          # Decides: numbers, text, or both
│   │   ├── answer.py          # Main entry: question → cited answer
│   │   └── citations.py
│   ├── api/                   # Phase 2
│   └── monitoring/            # Phase 3
│
├── eval/
│   ├── questions.jsonl        # Golden set: question, expected answer, source
│   ├── run_eval.py
│   └── metrics.py
│
├── scripts/                   # CLI entry points (ingest, index, ask)
├── tests/
├── notebooks/                 # Throwaway experiments only, never imported
├── docs/learning/             # One short note per step: what I learned
└── data/                      # Gitignored: raw/ and processed/
```

Rules: everything importable lives in `src/kzbank/`. No new top-level folders without a good reason. Notebooks never hold logic the app depends on.

---

## Database Shape (core idea)

```
entities         bin | name_ru | name_kz | name_en | type
metrics          bin | metric | period | value | unit | source_doc | page | line_code | published_at | retrieved_at
documents        id | source | title | url | published_at | edition | language
chunks           id | document_id | text | article_ref | embedding | tsv
```

Every number knows where it came from and when. Every chunk knows its document and legal article.

---

## Roadmap

Each step ends with working code, a short note in `docs/learning/`, and an update to `PROGRESS.md`.

### Phase 1: Learn RAG by building it

| # | Step | What you learn |
|---|---|---|
| 0 | Setup: uv, Docker Postgres + pgvector, config | Project hygiene |
| 1 | **Naive RAG** on ~20 Adilet legal texts: fixed-size chunks, embeddings, cosine search, answer | Embeddings, vector similarity, context windows, why naive RAG fails |
| 2 | **Structure-aware chunking** by article/пункт, plus metadata | Chunking is half of RAG quality |
| 3 | **Hybrid search**: Postgres full-text + vectors, fused with RRF | Why keyword search still matters (numbers, legal terms) |
| 4 | **Reranking** with a cross-encoder | Bi-encoder vs cross-encoder; recall vs precision |
| 5 | **Grounded, cited answers** and refusals | Prompting for faithfulness; hallucination control |
| 6 | **Eval v1**: golden set, recall@k, answer correctness | You can't improve what you don't measure |
| 7 | **Structured data**: ARDFM ingestion, BIN entities, unit normalization | Why numbers don't belong in a vector DB |
| 8 | **Tool calling**: LLM queries `bank_metrics` functions | How tool use works at the API level |
| 9 | **PDF tables** from dfo.kz, validated with accounting checks | Document AI; trust-but-verify extraction |
| 10 | **Router**: numbers, text, or both | Simple agentic patterns without a framework |
| 11 | **Eval v2**: numeric exact match, citation accuracy, refusal accuracy | Evaluating a full system |

### Phase 2: Production-ready

| # | Step | What you learn |
|---|---|---|
| 12 | FastAPI service, structured logging, error handling | Serving LLM apps |
| 13 | Scheduled, idempotent ingestion with freshness tracking | Data pipelines that don't duplicate or go stale |
| 14 | Full Docker Compose, tests, CI with eval as a gate | Shipping without breaking quality |
| 15 | Caching, latency and cost optimization | Real-world LLM economics |

### Phase 3: Monitoring

| # | Step | What you learn |
|---|---|---|
| 16 | Tracing with Langfuse: every retrieval, tool call, token, cost | Observability for LLM systems |
| 17 | Online evaluation and regression alerts | Catching quality drops before users do |
| 18 | Data health alerts: failed ingestion, stale sources, extraction errors | Monitoring the data, not just the model |
| 19 | User feedback loop into the golden set | Continuous improvement |

---

## Quick Start

```bash
cp .env.example .env          # add ANTHROPIC_API_KEY
docker compose up -d db
uv sync
uv run python scripts/init_db.py
uv run python scripts/ingest.py --source adilet --limit 20
uv run python scripts/ask.py "Какие требования к капиталу банков?"
```

(Commands are filled in as steps are completed.)
