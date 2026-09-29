# Kazakh Bank Risk Assistant

A cognitive AI prototype (Practical Assignment 3) that helps companies and investors see which Kazakh banks are healthy and which are becoming risky.

It collects official data, checks it, and answers questions in Russian with a source. If the data isn't there, it says so instead of guessing.

**Data**
- National Bank statistics: every bank, monthly, 2005–2026 (loans, bad loans, capital ratios)
- KASE stock exchange reports of 13 banks, used to cross-check the numbers
- 70 National Bank regulations (Word), searched by meaning
- Official exchange rates

**Four cognitive modules**
- **Perception:** keyword rules turn a question into type, bank, indicator, date
- **Attention:** ranks banks by bad-loan share; scores regulation texts 0–1 (bge-m3 + pgvector, bge-reranker)
- **Memory:** short-term (current conversation) and long-term (PostgreSQL, with consent)
- **Knowledge:** if-then rules (capital minimum, 3% risk, yearly trend, grounding)

Numbers are answered by code from the database. Regulation answers are written by a local LLM (Qwen3-VL-30B-A3B via vLLM) only from the retrieved text.

## Quick start

Requires Python 3.12, [uv](https://docs.astral.sh/uv/), PostgreSQL 16 with pgvector, and an OpenAI-compatible LLM server for regulation answers. No API key is needed.

```bash
cp .env.example .env        # set DATABASE_URL and LLM_BASE_URL
uv sync
uv run python scripts/init_db.py
uv run python scripts/ingest_nbk_stats.py   # NBK statistics
uv run python scripts/ingest_kase.py        # KASE reports
uv run python scripts/ingest.py --source nbk   # regulations -> chunks -> embeddings
uv run python scripts/ingest_rates.py       # exchange rates
```

Then open `notebooks/assignment3.ipynb` and run all cells.
