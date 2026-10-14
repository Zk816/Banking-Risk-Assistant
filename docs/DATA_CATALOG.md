# Data catalog

Every dataset in one place (the dataset name = `documents.source` in the database = the folder under `data/raw/`): what it is, where it comes from, what the raw file looks like, what we store.
Current totals: `uv run python scripts/data_summary.py`. The meaning of every number: table `metric_info`.

**Three kinds of data:**
- **Numbers** → table `metrics` (entity, metric, date, value, unit, source). Answered by exact lookup + rules. No LLM.
- **Ratings** → table `ratings` (entity, agency, rating, outlook, date). Letters, not numbers.
- **Text** → table `chunks` (document, page/article/sheet, text, embedding). Answered by search + reranker + LLM.
  Every document has `bin` (which company, if any) and `doc_type` (audit, rating, stability_report, …),
  filled by `scripts/link_documents.py`, so search can filter by company and type.

**Refresh everything:** `scripts/refresh.sh --full` (daily: `scripts/refresh.sh`, exchange rates only).

Folder layout: `data/raw/<source>/...` (downloaded files, never edited) → parser → database.

---

## Numbers

### 1. `bank_statistics`: monthly figures of every bank (National Bank, main source)
- **What:** monthly tables, one row per bank, every bank in Kazakhstan, 2005–2026.
- **Where:** nationalbank.kz/ru/news/banks-performance → 22 year pages → Excel files.
- **How:** `scripts/ingest_nbk_stats.py` (download all: `--download-all`).
- **File types (raw names `data/raw/bank_statistics/<type>_<id>.xls[x]`):**

| type | contains | parsed? |
|---|---|---|
| prudential | capital, k1, k1-2, k2, k4 | yes |
| capital_assets | assets, loans, overdue 7/30/90+, stage 3, provisions, deposits, equity, profit | yes |
| margin | interest income/expense, net interest margin, spread | yes |
| balances | balance of ~130 account groups (cash, loans, deposits, equity…) | yes |
| liquidity | sector liquid assets vs liabilities | not yet (sector level) |
| loan_quality / deposits / funding | sector overview tables | not yet (sector level) |
| asset_class, overdue_loans | loan classification, older years | not yet |

- **Raw example** (margin file, sheet "01.08.2026", thousands of tenge):
  `2 | АО "Kaspi Bank" | 7969954027 | ... | 0.0904 | 0.0637`
- **Cleaned example** (Kaspi, 31.07.2026):

| metric | value | meaning (from `metric_info`) |
|---|---|---|
| nbk_interest_margin | 0.0921 | net interest margin 9.2% |
| nbk_net_interest_income_ytd | 810 bn KZT | net interest income since 1 January |
| nbk_bal_1400 | 7.38 trln KZT | Требования к клиентам (loans to customers) |
| nbk_bal_2200 | 8.73 trln KZT | Обязательства перед клиентами (customer deposits) |

### 2. `bank_balance_sheets`: quarterly form 700-N of listed banks (KASE, cross-check)
- **What:** quarterly regulatory balance sheet, every account, 13 listed banks, 2006–2026.
- **Where:** kase.kz/ru/issuers/<CODE>/ (document list embedded in the page as JSON).
- **How:** `scripts/ingest_kase.py`. Raw: `data/raw/bank_balance_sheets/<CODE>/`.
- **Use:** proves the NBK numbers are read correctly (assets match 0.00% for 11 banks).

### 3. `country_indicators`: banking and macro indicators by country (World Bank)
- **What:** annual country-level banking and macro indicators, Kazakhstan + 10 peers (Uzbekistan, Kyrgyzstan, Russia, Azerbaijan, Georgia, Armenia, Turkey, Poland, Mongolia, World), 1990–2025.
- **Where:** api.worldbank.org (official JSON API, no key).
- **How:** `scripts/ingest_worldbank.py`. Entities `WB:KAZ`, `WB:UZB`, …
- **Indicators:** NPL ratio, capital/assets, regulatory capital/RWA, Z-score, ROA, ROE, net interest margin, credit/deposits, liquid reserves, private credit/GDP, inflation, GDP growth, GDP per capita, USD rate, unemployment, government debt.
- **Cleaned example** (NPL ratio 2024): Kyrgyzstan 10.3%, Uzbekistan 3.9%, **Kazakhstan 3.1%**, Georgia 2.5%, Poland 1.8%.

### 4. `exchange_rates`: official daily exchange rates (National Bank)
- **What:** daily official rates of ~40 currencies. **Where:** nationalbank.kz RSS feed + archive by date.
- **How:** `scripts/ingest_rates.py [--since YYYY-MM-DD]`.

### 5. `bank_ratings`: Moody's / S&P / Fitch rating of every bank (KDIF)
- **What:** current Moody's / S&P / Fitch rating, previous rating and outlook for **every bank** (31 ratings).
- **Where:** kdif.kz/ru/banks-rating (deposit insurance fund; HTML tables). **How:** `scripts/ingest_kdif.py` → table `ratings`.
- **Cleaned example:** Halyk: Moody's Baa1 (stable, 12.09.2024), S&P BBB- (26.05.2026), Fitch BBB- (17.09.2025).

### 6. `base_rate`: base rate decisions (National Bank)
- **What:** every base rate decision 2015–2026 (79). **How:** `scripts/ingest_base_rate.py`.

---

## Text

### 7. `laws`: laws and the Civil Code
- **What:** 10 laws: banks, National Bank, financial market regulation, deposit guarantee, credit bureaus, currency regulation, payments, bankruptcy, Civil Code (general + special part). Missing: microfinance, collection activity.
- **Where:** adilet.zan.kz, saved by hand (the site shows programs an empty page). **How:** `scripts/ingest.py --source laws`. Chunk = one article ("Статья 43").

### 8. `nbk_regulations`: National Bank Board resolutions
- **What:** 70 Board resolutions: reporting rules and deadlines, how ratios are calculated, chart of accounts, currency rules.
- **Where:** nationalbank.kz/ru/npa (robots.txt forbids crawling → pages saved by hand, then documents downloaded from their links).
- **How:** `scripts/ingest.py --source nbk_regulations`. Chunk = one numbered point ("пункт 11").

### 9. `stability_reports`: Financial Stability Reports (National Bank)
- **What:** the regulator's yearly analysis of risks in the financial system (loan quality, consumer credit, liquidity, dollarisation), 2006–2024. Answers "why" questions.
- **Where:** nationalbank.kz/ru/news/otchet-o-finansovoy-stabilnosti. PDF.
- **How:** `scripts/ingest.py --source stability_reports`. Chunk = part of one page ("стр. 45").

### 10. `company_reports`: IFRS statements and rating actions of banks and companies (KASE)
- **What:** for 13 listed banks since 2018: audited IFRS annual statements (`audit`), auditor-reviewed interim statements (`review`), quarterly statements (`fs`), rating agency actions from S&P / Fitch / Moody's (`rating`).
- **Where:** same KASE issuer pages as form 700-N. PDF.
  Plus **~270 other Kazakh issuers** (industrial companies, microfinance, leasing, state holdings): audited statements and rating actions (`--all-issuers`). Names in `data/raw/company_reports/issuers.tsv`.
- **How:** `scripts/download_kase_docs.py [--all-issuers]` → `scripts/ingest.py --source company_reports --ocr`. Raw: `data/raw/company_reports/<CODE>/<type>_<date>_<file>.pdf`.
- **OCR:** about half of these PDFs have no text layer (image scans, or text turned into vector outlines). PyMuPDF renders each page to PNG and Qwen3-VL reads it (≈5 s/page, 8 pages in parallel). Result cached in `data/processed/ocr/`, so each PDF is read once.

### 11. `basel_standards`: Basel Committee standards (BIS)
- **What:** the international rulebook behind k1/k2/k4: Basel III capital, LCR, NSFR, leverage, non-performing exposures, IFRS 9 provisions, IRRBB, large exposures, Pillar 3, Core Principles.
- **Where:** bis.org. **How:** `scripts/download_bis.py` → `scripts/ingest.py --source basel_standards`.

### 12. `nbk_publications`: reviews, bulletins, statistical tables (National Bank)
- **What:** inflation reviews, economic reviews, annual reports, analytics, statistical bulletin, loans to the economy by industry / region / consumer / mortgage / SME with rates, banking conglomerates, insurance groups, mortgage organisations, pension fund.
- **Where:** nationalbank.kz/ru/news/<section>. PDF + Excel. File titles in `data/raw/nbk_publications/<section>/titles.tsv`.
- **How:** `scripts/download_nbk_pub.py` → PDFs: `scripts/ingest.py --source nbk_publications`. Excel tables (~540): `scripts/ingest.py --source nbk_publications --tables` — each sheet becomes text chunks "a | b | c" with the title, sheet name and header repeated, so the right table can be found (exact numbers from them: not parsed yet).

### 13. `deposit_market`: deposit market reviews and deposit rates (KDIF)
- **What:** quarterly reviews of the household deposit market, monthly deposit rates of every bank, KDIF reports (108 PDFs).
- **Where:** kdif.kz/ru/<section>. **How:** `scripts/download_kdif_docs.py` → `scripts/ingest.py --source deposit_market`.

### 14. `worldbank_reports`: FSAP and economic reports on Kazakhstan (World Bank)
- **What:** 81 reports: FSAP (financial sector assessment, 2004, 2009, 2024 + technical notes), Kazakhstan Economic Updates, Country Economic Memoranda, pension, payments, consumer protection in finance. Off-topic sector projects (roads, power, health) filtered out.
- **Where:** search.worldbank.org/api/v2/wds (official API). **How:** `scripts/download_wb_docs.py` → `scripts/ingest.py --source worldbank_reports`.

---

## Not available (checked)
| site | why |
|---|---|
| finreg.kz (ARDFM) | not reachable from our server |
| imf.org, data.imf.org | blocks programs (403) |
| 1cb.kz (credit bureau) | not reachable from our server |
| stats.bis.org | no Kazakhstan series |
| taldau.stat.gov.kz | no open API; World Bank + NBK cover the macro numbers |
| ifrs.org | times out |
| adilet.zan.kz | empty page for programs, robots.txt forbids; save laws by hand |
| data.egov.kz | needs a digital-signature key |
| dfo.kz | report forms are empty |
