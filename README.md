# Account Research — test app

A small Flask test app that researches a company from live web sources and returns a
bilingual (English + 中文), evidence-grounded report with citations, token usage and latency.

This is a **test utility**, not the production Account Research product.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

Then open <http://127.0.0.1:5057>.

Dependencies: **Flask** (web), **reportlab** (PDF), **openpyxl** (.xlsx upload).
Chinese PDF text uses ReportLab's built-in Adobe CJK font, so there is no font file to install.

## Credentials

Copy `ai_credentials.env.example` to `ai_credentials.env` and fill in the real values.

```
DASHSCOPE_API_KEY=sk-ws-...        # pay-as-you-go key — REQUIRED for DashScope native
DASHSCOPE_WORKSPACE_ID=ws-...
```

`ai_credentials.env` is git-ignored and should stay mode `600`. Credentials are read on
the backend only and are never sent to the browser. A Token Plan key (`sk-sp-`) returns
401 on the native endpoint — the pay-as-you-go key is required.

## Models and routing

Three models are supported. Their DashScope routing is verified and must not be changed:

| Model | Protocol | Endpoint |
|---|---|---|
| `qwen3.6-flash` | DashScope native | `multimodal-generation` |
| `deepseek-v4-pro` | DashScope native | `text-generation` |
| `deepseek-v4-flash-0731` | DashScope native | `text-generation` |

Calling the wrong endpoint returns `400 url error, please check url`. The multimodal
endpoint also needs array-form message content and no `result_format`; `research_service.py`
handles both shapes.

Search configuration: `enable_search=true`, `search_strategy=turbo`, `enable_source=true`,
`enable_citation=true`, `citation_format=[<number>]`. **Do not set `freshness`** — it
collapsed retrieval from 10 sources to 0–1 in testing.

## How it works

```
Company + URL
  -> discover the company's local-language name from its own website
  -> 5 short deterministic search queries   (never the research prompt)
  -> web searches, run concurrently
  -> disambiguate, dedupe, rank by source tier
  -> fetch real page text (standard -> JS-bundle -> bounded)
  -> shared evidence package  (cached per company)
  -> all selected models synthesise CONCURRENTLY, closed-book over that evidence
  -> each result renders and saves the moment it finishes
```

Two design points worth knowing:

- **The research instruction is not a search query.** Sending the full prompt to the
  search backend collapses retrieval to ~1 result; short keyword queries return 7–9.
- **Retrieval runs once per company**, shared by every model, so "Run All Three" is a fair
  comparison and does not triple the search cost. The evidence package is cached, and the
  UI offers *Use cached research / Refresh web research*.

Source priority: official website > official product pages > official partner references >
government / reputable media > industry publications > other. Company-name disambiguation
rejects unrelated entities (e.g. SKEQI must not pick up Skechers content).

## Batch research

The **Batch Research / 批量研究** tab takes a `.csv` or `.xlsx` list with at least a
company-name and a website column (column mapping is adjustable in the UI).

Companies are processed **one at a time** through a controlled queue. After each company
finishes, its JSON and bilingual PDF are written immediately, so reports are downloadable
while the rest of the list is still running — verified: with a 3-company batch, SKEQI's PDF
was available at t+100s while CATL was still searching and EVE Energy was still pending.

Batch state is saved to `reports/_batches/<id>.json` after every company, so an interrupted
run resumes rather than restarting. A company that already has a report is marked
**Skipped** and consumes no tokens unless you clear "Skip companies that already have a
report". A failure is recorded and the batch continues; **Retry failed** re-queues them.

Within one company, "Run All Three" keeps the shared-retrieval + parallel-synthesis
behaviour — retrieval is never repeated per model.

## PDF output

Each completed run produces a professional A4 PDF under `reports/<Company>/`:

```
reports/
├── SKEQI/
│   ├── research.json
│   ├── research_qwen3.6-flash.json
│   ├── _meta.json
│   └── SKEQI_Account_Research_Qwen3.6Flash_2026-09-01.pdf
└── CATL/ ...
```

Every PDF carries the company, website, date, model, both languages, cited sources with
clickable URLs, confidence tags, token usage and latency. Credentials never appear in a PDF.

Two combined outputs are available at any time, both built **only from saved data with zero
AI tokens**:

- **Download Completed Reports (ZIP)** — the individual PDFs already on disk.
- **Compile Completed Reports / 汇总已完成报告** — one portfolio PDF with a cover, a summary
  table, a clickable table of contents, PDF bookmarks, and each company's full bilingual
  report starting on its own page. Compile all or only selected companies, ordered by upload
  order (default), company name, or completion time. Output:
  `reports/Account_Research_Portfolio_<date>.pdf` or
  `reports/Selected_Companies_Account_Research_<date>.pdf`.

You do not need to wait for a batch to finish — compiling 20 of 75 completed companies works,
and you can recompile later as more finish.

### Optional cost estimates

Cost is reported only when pricing is configured. Add to `ai_credentials.env`:

```
AI_PRICE_INPUT_PER_1K=0.0
AI_PRICE_OUTPUT_PER_1K=0.0
AI_PRICE_CURRENCY=CNY
```

Without these, the app reports token counts and shows "pricing not configured" rather than
inventing a number.

## Output

Every report has 12 bilingual sections (Company Overview / 公司概况 … Sources / 信息来源),
produced in a **single** model response so both languages rest on the same evidence and
the same `[n]` citations. Each claim is tagged **Verified**, **Likely / partially
supported**, or **Not enough evidence**.

## Files

```
app.py                  Flask routes, background jobs, per-model sessions, history, batch
research_service.py     retrieval, disambiguation, ranking, routing, synthesis
pdf_service.py          bilingual PDF rendering (single report + combined portfolio)
batch_service.py        CSV/XLSX parsing, batch queue, per-company save, compilation
templates/index.html    single-page UI
static/app.js           progressive rendering, tabs, polling
static/style.css        styling
test_results/           one JSON per completed model run (git-ignored)
reports/                per-company research JSON + PDFs, and combined PDFs (git-ignored)
evidence_cache/         cached evidence package per company (git-ignored)
archive/                historical benchmark evidence and superseded tools
```

`test_results/*.json` records the company, website, model, search queries, sources,
full report, token usage, latency and timestamp — no credentials. Past runs reload from
disk in the **Recent Research** panel without calling a model.

## Archive

`archive/benchmark_reference/` holds the evidence behind the three-model choice
(72-model benchmark, gap-fill results, SKEQI comparisons and hand-verified ground truth).
`archive/test_results_history/` holds superseded runs. `archive/test_account_research.py`
is the standalone CLI this app replaced.
