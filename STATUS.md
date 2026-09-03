# Account Research — Project Status

> Last updated: 2026-09-02
> Updated by: Claude
> Current phase: CRM integration COMPLETE and pre-push verified; awaiting model activation for live research
> Overall status: DEGRADED — DashScope account has no model entitlement (403 AccessDenied)

---

## 1. Current Objective

Build a practical standalone Account Research application that rapidly researches a list of
target companies for SKEQI and produces evidence-backed bilingual English/Chinese reports.

Current priority is making the standalone research tool useful immediately, with emphasis on
**retrieval robustness before synthesis** — never letting a retrieval failure become a
"no information" report.

CRM / EmailDrafter integration is NOT the current priority and must not be worked on unless
explicitly requested.

---

## 2. Current Application

| Item | Value |
|---|---|
| Entry point | `app.py` (Flask) |
| Local URL | `http://127.0.0.1:5057` (override with `PORT=5058 python3 app.py`) |
| Retrieval / synthesis | `research_service.py` |
| Financial sourcing | `finance_service.py` |
| Apollo client | `apollo_service.py` |
| People ranking / merge | `people_service.py` |
| Confidence tags + display filter | `confidence.py` |
| Display-language selection | `language_view.py` |
| Batch queue, CSV/XLSX, compile | `batch_service.py` |
| PDF rendering | `pdf_service.py` |
| Frontend | `templates/index.html`, `static/app.js`, `static/style.css` |
| Brand colour | Kellogg / Northwestern digital purple `#4F2582` |
| Access gate | `access.py` (login form + signed cookie; off unless configured) |
| Deployment | `render.yaml`, `DEPLOY.md` |
| Credentials | `ai_credentials.env` (git-ignored, mode 600) |
| Report storage | `reports/<Company>/` (JSON + PDF), combined PDFs in `reports/` |
| Evidence cache | `evidence_cache/` (also holds `_sec_company_tickers.json`) |
| Run history | `test_results/*.json` |

**Supported models** (DashScope native; routing verified, do not change):

| Model | Endpoint |
|---|---|
| `qwen3.6-flash` | `multimodal-generation` |
| `deepseek-v4-pro` | `text-generation` |
| `deepseek-v4-flash-0731` | `text-generation` |

Search provider is **Alibaba Bailian / DashScope web search** (`enable_search`,
`search_strategy=turbo`). It is **not** Google. All three models share one search backend.

---

## 3. Current User Workflow

**Single Company**
Company → website optional → Research → progressive stage log → sources → bilingual report
→ merged decision-maker roster → Display Language toggle → View PDF / Download PDF.

**Batch**
CSV/XLSX upload → column mapping → company list → website provided or auto-discovered →
sequential per-company research → save JSON + PDF immediately → continue → compile/download.

Batch behaviour that works today:

- **Incremental saving** — each company's JSON and PDF are written as it finishes; downloadable
  while the rest of the batch is still running.
- **Resume** — batch state saved to `reports/_batches/<id>.json` after every company.
- **Existing Report** handling — skipped with no AI call unless "Skip companies that already
  have a report" is cleared.
- **Regenerate**, **Delete Report**, **Edit URL**, **Edit Company**, **Retry failed**,
  **Retry Without Website**.
- **ZIP download** of completed PDFs and **combined portfolio PDF** (zero AI tokens).
- **View Report / View PDF / Download PDF** per completed row (View PDF opens the embedded viewer).
- **Report Language** selector (English / 中文 / Bilingual) applying to individual PDFs, ZIP
  downloads and both compile actions.

---

## 4. Research Report Structure

**CURRENT REPORT FORMAT: 19 sections** (prompt lives in `research_service.INSTRUCTION`):

1. Executive Summary / 执行摘要
2. Company Overview / 公司概况
3. Strategic Initiatives / 战略举措
4. Industry Trends / 行业趋势
5. SWOT Analysis / SWOT分析
6. Competitor Analysis / 竞争对手分析
7. Existing Automation Providers / 现有自动化供应商
8. Key Decision Makers / 关键决策人
9. Upcoming Projects / 未来项目
10. Latest News / 最新动态
11. Financial Information / 财务信息
12. Manufacturing Challenges / 制造挑战
13. Sustainability Objectives / 可持续发展目标
14. Relevant SKEQI Solutions / 思客琦相关解决方案
15. Sales Strategies / 销售策略
16. Strategic Objectives / 战略目标
17. Potential SKEQI Use Cases / 潜在思客琦应用场景
18. Acronyms & Business Terms / 术语与缩写
19. Sources / 信息来源

**OLD REPORT FORMAT: 12 sections.** Reports saved before the expansion have fewer sections and
fewer sources. They are not equivalent to newly generated reports.

Do NOT revert to the 12-section prompt.

### Display language

Research is generated and stored **once, bilingual**. A display-language toggle
(English / 中文 / Bilingual, default Bilingual) selects what is shown and exported.
It never re-runs research and never calls a model.

`language_view.select()` splits on structures that are unambiguous:
section headings on `" / "`, the `**English:**` / `**中文：**` block markers, and the canonical
confidence tags. Ordinary body text is deliberately left alone, because the prompt requires brand
names to stay in their original language in both blocks (KEPAILE / 科派乐, 琦航数字工厂系统) and
splitting those would destroy content.

Source titles, publications and URLs are never translated. Only UI/PDF labels change.

`static/app.js` mirrors these rules for on-screen rendering — **keep the two in step.**

---

## 4b. User Interface

Enterprise UI, redesigned 2026-09-02. Vanilla HTML/CSS/JS — no framework was introduced.

**Brand:** Kellogg / Northwestern digital purple **`#4F2582`** (`--brand`), reserved for primary
buttons, active tabs, active segmented options, the brand mark, links and progress accents.
The workspace itself stays light (`--bg:#f7f8fa`). Purple is never used for status semantics.

**Shell:** sticky app bar (AR mark, "Account Research 客户研究", bilingual tagline, System Status)
over a sticky tab bar with three tabs — Single Company, Batch Research, Reports. No new features
were invented for the navigation; Reports simply hosts the report library that already existed.

**Key decisions**
- Engineering details ("test app", the DashScope/retrieval strapline) moved out of the header into
  a **System Status** popover backed by the existing `/api/models/health`.
- Batch actions gained hierarchy: primary Generate Selected, secondary Generate All / Retry Failed,
  text-only Select All / Clear Selection, restrained red Delete Selected Reports.
- Batch progress became four KPI cards plus one secondary metrics line.
- Row actions collapsed from up to seven inline links into `[View] [PDF] [⋯]`, the overflow being a
  native `<details>` so no extra state has to survive polling. Failure recovery stays inline
  because it is specific to that row, and a low-source legacy report still promotes Regenerate.
- Language pickers are segmented controls sharing one `localStorage` value across all three places.

**Bilingual chrome (2026-09-02).** The whole application shell and workflow follow one convention:

- **Slash form** for short labels, buttons and table headers — `Status / 状态`, `Sources / 来源`,
  `Generate All / 生成全部`.
- **Two-line form** for titles and helper text, Chinese on its own line in `.cn`
  (lighter, `0.88em`) so English stays visually primary.
- **Never translated:** company names, model names, URLs, source titles, provider/API names and
  brand names without an official Chinese form.

Status **values stay English** because they are compared as data and drive the CSS classes
(`s-Completed`); `STATUS_ZH` supplies the Chinese for display only. Extending that map is the
correct way to add a status label — never change the value.

Server-side progress messages (`Reading 14 source pages…`) remain English: they are emitted by
`research_service`, which is out of scope for a presentation change.

**Preserved deliberately:** `renderBatchTable`'s in-place row diffing and its editing guard (the
focus fix), `CELLS = 9`, every element id, every fetch, and all Python behaviour.

---

## 4c. Deployment & CRM Embedding

The app runs as its **own Render service**. The Skeqi-EmailDrafter CRM embeds it
in an iframe under `Account Research → Current Account Research (Qwen-based)`.
**Navigation only** — no database, API, session, report, contact, token counter or
credential is shared in either direction.

```
Skeqi-EmailDrafter (CRM)
└── Account Research  (one sidebar item)
    ├── Current Account Research (Qwen-based)   ← default
    │      ↓ iframe, cross-origin
    │   THIS standalone app on Render
    └── Previous Account Research (Claude-based)
           ↓ /account-research/  (unchanged)
```

**Access gate** (`access.py`). A single shared credential in
`APP_ACCESS_USERNAME` / `APP_ACCESS_PASSWORD`, exchanged for an HMAC-signed
cookie. **Not HTTP Basic**: browsers suppress Basic-auth prompts inside a
cross-origin iframe, so the frame would be unusable. The cookie is
`SameSite=None; Secure; Partitioned` so it survives as a third-party cookie
under CHIPS. Unset both variables and the gate is off, so local dev is unchanged.

**Embedding policy.** `ALLOWED_FRAME_ANCESTORS` drives
`Content-Security-Policy: frame-ancestors 'self' <origin>`. Never `*`. With no
value it is `'self'` only, i.e. embedding is refused.

**Configuration source.** `load_config()` reads the environment first, then
`ai_credentials.env` on top when the file exists. Render has no file, so env
vars are the only source there; locally the file still wins.

**Server.** `gunicorn --workers 1 --threads 8`. One worker is mandatory: job and
batch state is in-memory and owned by background threads.

### ⚠ Storage is ephemeral on Render

Research JSON, PDFs, compiled portfolios, batch state, run history and the
evidence cache are all local files and **do not survive a restart or redeploy**.
The free plan also spins down when idle. Documented in `DEPLOY.md`; deliberately
not redesigned for this first live test.

---

## 4d. CRM Integration — phased

Target: **one CRM shell with two Account Research engines**, not one merged engine.
Qwen and Claude stay logically separate: separate prompts, models, tables and code.

| Phase | Scope | Status |
|---|---|---|
| 0 | Two tabs behind one nav item, Current default, Qwen shown in an iframe | **Done** |
| 1 | Neon schema + migration of existing reports | **Done, verified** |
| 2 | Express proxy + Neon persistence, reads served from the database | **Done, verified** |
| 3 | Native CRM workspace replacing the iframe | **Done, verified** |

CRM repo: `hemeiling/Skeqi-EmailDrafter`, branch **`account-research-qwen`**
(branched from `main`; `main` untouched, nothing pushed).

### Phase 1 result — ONE CURRENT REPORT PER COMPANY

Two tables in the CRM's **existing** Neon database — no second connection, no
migration framework, idempotent DDL inside `initDb()` exactly like the rest:

- `account_research_qwen_reports` — the canonical bilingual record in
  `research_data` (JSONB), plus `sources` / `contacts` / `usage` JSONB and
  scalar projections for listing. English-only, Chinese-only and bilingual views
  and PDFs all render **from that one record**.
- `account_research_qwen_batches` — durable batch history.

**No version history.** A company has exactly one report; researching it again
replaces that report. Identity is `company_key` (a normalised company name)
under a **UNIQUE index**. `company_id` would be the better key but is NULL
whenever the CRM does not yet know the company, and NULLs do not conflict.
The constraint is in the database, not just in application code, because two
simultaneous runs would otherwise both insert.

`version` survives only as a **regeneration counter** — how many times the
company has been researched. There is no version selector and no historical UI.

**Saving is a single upsert.** Nothing is deleted first, so a failed model call
or a failed write leaves the previous report exactly where it was. That is what
makes regenerate safe.

| Behaviour | Implementation |
|---|---|
| New research completes | `saveQwenReport` → `INSERT … ON CONFLICT (company_key) DO UPDATE` |
| Regenerate | Same upsert; old report only replaced once the new one exists |
| Regenerate fails | Nothing written; existing report intact |
| Delete Report | `deleteQwenReportsByCompany` → company reads as Pending |
| Existing-report detection | `hasQwenReport(company)` → boolean, no version scan |

**Migration.** `scripts/import-qwen-reports.js --dir <reports>` imports each
company's **current** report only. Archived runs under `history/` are counted
and left on disk as your backup. Local files are read-only.

---

### Phase 2 result — proxy + persistence

**Neon is the system of record.** The CRM lists, reads and deletes reports
directly from the database; the engine is not consulted for any read. Research
itself still runs in the standalone Python service, reached through a thin proxy
in `qwenResearch.js`. No retrieval, model-routing, Apollo or Yahoo Finance logic
was reimplemented in Node.

| Route | Purpose | Touches the engine? |
|---|---|---|
| `GET /api/aresearch/reports` | Report library, optional `?q=` | No |
| `GET /api/aresearch/reports/:id` | One stored report | No |
| `GET /api/aresearch/company/:company` | Current report, 404 if none | No |
| `GET /api/aresearch/exists?companies=a\|\|b` | Existing Report vs Generate | No |
| `POST /api/aresearch/reports/delete` | Remove a company's report | No |
| `GET /api/aresearch/render?…&lang=&format=` | Language view or PDF from stored data | Renderer only, no model |
| `GET /api/aresearch/models/health` | Activation state | Yes |
| `POST /api/aresearch/research`, `GET …/job/:id` | Run research, persist on completion | Yes |
| `POST /api/aresearch/batch/*` | Batch passthrough | Yes |

The engine gained one stateless endpoint, `POST /api/render`, which turns a
stored record into a language-selected view or a PDF. Storage belongs in the
database; rendering stays with the code that produced the record. **No model is
called on that path.**

**Persistence is best-effort on top of the engine's own result.** If Neon is
unreachable the finished research is still returned to the browser and the
engine still holds it — losing the save must not lose the report. Only runs with
`status === 200` and a research result are saved, so a failed regenerate leaves
the previous report untouched.

**Server-to-server auth** uses `X-AR-Service-Key` (`ACCOUNT_RESEARCH_SERVICE_KEY`
in the CRM = `APP_SERVICE_KEY` on the engine). A browser login form is
meaningless between two backends.

**Model access** is surfaced as *"Model unavailable — activation/payment
required / 模型暂不可用 — 需要开通/付费"*, never as a retrieval failure.

---

### Phase 3 result — native CRM workspace

The Qwen tab is no longer an iframe. It is built from the CRM's own cards,
tables, buttons, typography and `--color-primary` purple, and begins directly at
**Single Company / 单个公司 · Batch Research / 批量研究 · Reports / 报告库**.
None of the standalone chrome appears: no AR mark, no app bar, no standalone
navigation, no System Status header.

- **Reads come from Neon** via the Phase 2 API. The engine is consulted only to
  RUN research and to render a stored record, so the library, saved reports,
  language switching and PDF export work while the models await activation.
- **Language selection stays server-side.** The standalone build mirrored the
  rules in JavaScript; here the view is requested per language and rendered from
  the one stored record, so there is no second copy to drift.
- **PDF preview is a PDF in a frame**, never an application in a frame.
- **Batch rows keep the in-place updater.** A row being edited is not touched, so
  polling cannot move the caret, clear a selection or overwrite unsaved text.
- **Generate / Regenerate check model availability first** and answer with
  *"Model unavailable — activation/payment required / 模型暂不可用 — 需要开通/付费"*,
  never as a retrieval failure.
- **Combined PDF and ZIP build from Neon records**, through two stateless engine
  render endpoints, so an export never depends on the engine's filesystem.

Files: `public/qwen-research.js` (new, 633 lines), `public/index.html`,
`public/app.js`, `server.js`, `qwenResearch.js`.

---

---

## 5. SKEQI Research Objective

For every target company the report answers:

> "What is happening at this company, where could SKEQI realistically help, who should we
> engage, and what should we talk to them about?"

Recommendations may only use the approved SKEQI capability areas in
`research_service.SKEQI_CAPABILITIES` (battery cell equipment, module/PACK lines, ESS assembly,
laser welding, 2D/3D X-ray NDT, KEPAILE intelligent logistics, QIHANG digital factory,
automotive body/BIW lines, precision manufacturing, battery recycling).

Never invent SKEQI products or capabilities.

---

## 6. Retrieval Architecture

```
Company name (+ optional website)
  ↓
Validate supplied website  ──► if blocked/wrong, auto-discover
  ↓                              (guessed domains must be corroborated on-page)
Public-listing lookup (SEC EDGAR → Yahoo quote validation)   [parallel]
  ↓
DIRECT OFFICIAL-SITE CRAWL: homepage + about + products + industries
                            + leadership + news + contact
  ↓
Retrieval ladder (sequential, cost-aware, access-aware):
    Wave 1  Qwen 3.6 Flash          → 403? skip, try next.  else assess evidence
    Wave 2  DeepSeek V4 Flash       → only if still weak, or previous denied
    Wave 3  DeepSeek V4 Pro         → only if still weak, or previous denied
    Wave 4+ AI_MODEL_FALLBACKS ids  → same rules
  ↓
Merge raw results across all waves
  ↓
Identity filtering (rejects unrelated entities, e.g. SKEQI vs Skechers)
  ↓
Deduplicate on canonical URL key (scheme / www. / trailing slash ignored)
  ↓
Tier ranking  ──► fetch page text
  ↓
Merge official-site evidence + web evidence + Yahoo Finance block
  ↓
Apollo people enrichment (optional)
  ↓
Evidence assessment → quality gate
  ↓
Synthesis (closed-book, search OFF) → bilingual 19-section report
```

**Important:** the retrieval ladder is for RETRIEVAL ONLY. It does not produce three reports.
One synthesis run happens per model the user selected. "Run All Three" remains a separate,
deliberate comparison feature.

**Evidence thresholds** (`research_service.assess_evidence`):

| Unique sources | Level |
|---|---|
| 8+ | strong |
| 5–7 | acceptable |
| 2–4 | weak → trigger fallback |
| 0–1 | insufficient → must trigger fallback |

Count alone is not sufficient. Fallback also triggers when company overview or products/services
evidence is missing. The **hard blocking guard** fires when identity is unverified AND no
official/company-specific source was retrieved.

### Known limitations

- The DashScope search backend indexes the Chinese web far better than the English web. Western
  companies frequently return few usable English sources; the direct site crawl is what
  compensates.
- `search_options.freshness` collapses results from 10 to 0–1. Leave unset.
- `search_strategy=agent` fails in non-streaming mode.
- This venv's Python links **LibreSSL 2.8.3** and cannot complete TLS handshakes with some
  modern hosts. `fetch_url` falls back to the system `curl` on TLS failure.
- Some official sites (e.g. `te.com`) block automated access entirely; evidence then comes from
  search plus any alternate regional domain.
- A model returning 403 `AccessDenied` is skipped, recorded, and reported as an access failure.
  It is never counted as a retrieval wave and never presented as "not enough evidence".

---

## 7. Source Strategy

General priority: official website > official product pages > official partner references >
government / reputable media > industry publications > other web sources.

**Financial ladder** (implemented in `research_service.classify` / `subrank`):

| Rung | Source | Tier |
|---|---|---|
| 1 | Official Investor Relations / annual reports | 2 |
| 2 | Regulatory filings (SEC, HKEX, SSE, SZSE, CNINFO) | 3 |
| 3 | Yahoo Finance | 4 (subrank 1) |
| 4 | Reputable financial / business publications | 4 (subrank 2) |

### Yahoo Finance status: IMPLEMENTED and TESTED

Verified working. Evidence:

- **Before this work it was not used at all.** Every cached evidence package contained zero
  `finance.yahoo.com` sources despite a query mentioning Yahoo Finance.
- **Measured cause:** the DashScope search backend does not surface `finance.yahoo.com`.
  `"TE Connectivity" Yahoo Finance` returned Zhihu and a personal blog; `TEL Yahoo Finance`
  returned pages *about* Yahoo Finance. Search alone cannot deliver Yahoo Finance evidence.
- **Current approach:** ticker resolved deterministically from SEC EDGAR `company_tickers.json`
  (free, no key), validated against the Yahoo quote page, then the quote page fetched **directly**
  and parsed for real figures (market cap, PE, EPS, earnings date, sector, industry, employees).
- The four requested Yahoo search queries also run, but for **public companies only**.
- `financial_sources.yahoo_finance_used` reports **Yes/No based on whether a Yahoo Finance URL
  actually landed in the evidence**, never on whether a query mentioned it.
- Private/unlisted companies are never forced through Yahoo.

---

## 8. People / Decision-Maker Research

Web research extracts decision makers from official leadership pages and public web sources.
Apollo supplements this with structured contact data for roles that are hard to find publicly.

### Apollo integration: IMPLEMENTED — BLOCKED on a key for live verification

`APOLLO_API_KEY` is **not present in `ai_credentials.env`** as of this update, so the live API
has still never been called. Everything below is implemented and tested with the network hop
mocked at `apollo_service._request`.

### Endpoints used

| Stage | Endpoint | Method | Credit cost |
|---|---|---|---|
| Resolve org by domain | `/api/v1/organizations/enrich?domain=<domain>` | GET | none |
| Resolve org by name (fallback) | `/api/v1/mixed_companies/search` | POST | none |
| Stage 1 — candidate search | `/api/v1/mixed_people/search` | POST | none |
| Stage 1 fallback | `/api/v1/people/search` | POST | none |
| Stage 2 — contact enrichment | `/api/v1/people/bulk_match` | POST | **one credit per contact revealed** |

Auth header `x-api-key`. The key is read server-side only and never logged, returned to the
browser, or written into a report.

### Credit-saving strategy

1. **Search first, enrich last.** Search returns up to 100 candidates and costs nothing. It never
   returns a usable email — Apollo substitutes the placeholder `email_not_unlocked@domain.com`,
   which `clean_email()` rejects so it can never reach a report.
2. **Rank before spending.** `people_service.from_apollo()` scores every candidate against SKEQI's
   sales motion and drops anyone scoring zero (assistants, HR, payroll, recruiting, comms).
3. **Enrich only the top N.** `APOLLO_ENRICH_LIMIT` (default 12, max 25) contacts reach
   `bulk_match`, batched 10 per call. A company returning 20 candidates costs ~12 credits, not 20.
4. **No personal data requested.** `reveal_personal_emails` and `reveal_phone_number` are both
   `false` — cheaper, and it keeps personal contact details out of report files.
5. **Cached companies cost nothing.** The Apollo result is stored in the cached evidence package,
   so re-running a cached company makes zero Apollo calls.

### Fields captured

Name · Current Title · Department/Function · Seniority · Company · Location · Business Email ·
Email Status · LinkedIn URL · Why Relevant to SKEQI · Source.

Email status is normalised and never upgraded: `Verified`, `Unverified (guessed)`, `Unverified`,
`Unavailable`, `Unavailable (bounced)`, `Pending`, `Not available`. **An address is never invented
or inferred from a domain pattern.** A blank email means Apollo held none.

### Report section

`Key Contacts & Decision Makers / 关键联系人与决策者` is rendered in the UI and the PDF from the
merged structured roster, **not** from model output. The Apollo directory handed to the model
carries only Name/Title/Department/Seniority — no emails — so the model cannot restate or
fabricate an address.

Departments covered: Executive Leadership, Operations, Technology, Digital/IT, Supply Chain,
Procurement, Strategic Sourcing, Manufacturing, Plant Leadership, Engineering, Automation,
Battery Engineering, Energy Storage, Digital Manufacturing, Quality, Sales/Business Leadership.

## 9. Credentials / Environment Variables

Loaded server-side only, from `ai_credentials.env` (searched beside the app, then the parent
directory), with `os.environ` as a fallback for `APOLLO_API_KEY`. Never sent to the browser,
never written into a report or PDF.

Variable NAMES in use:

```
DASHSCOPE_API_KEY
DASHSCOPE_WORKSPACE_ID
DASHSCOPE_BASE_URL
DASHSCOPE_REGION
TOKEN_PLAN_API_KEY
TOKEN_PLAN_BASE_URL
AI_MODEL_QUALITY
AI_MODEL_FAST
AI_MODEL_CITATION
DASHSCOPE_PATH_TEXT
DASHSCOPE_PATH_MULTIMODAL
DASHSCOPE_MULTIMODAL_MODELS
AI_SEARCH_STRATEGY
AI_SEARCH_ENABLED
AI_SOURCE_ENABLED
AI_CITATION_ENABLED
AI_CITATION_FORMAT
AI_REQUEST_TIMEOUT_MS
AI_PRICE_INPUT_PER_1K      (optional)
AI_PRICE_OUTPUT_PER_1K     (optional)
AI_PRICE_CURRENCY          (optional)
APOLLO_API_KEY             (optional — NOT YET PRESENT in ai_credentials.env)
APOLLO_ENRICH_LIMIT        (optional — contacts enriched per company, default 12)
AI_MODEL_FALLBACKS         (optional — extra model ids to try on 403 AccessDenied)
APP_ACCESS_USERNAME        (access gate; gate is OFF unless both are set)
APP_ACCESS_PASSWORD
APP_ACCESS_SECRET          (optional — defaults to a value derived from the password)
ALLOWED_FRAME_ANCESTORS    (CRM origin permitted to iframe this app; never "*")
PORT / HOST                (deployment; HOST defaults to 127.0.0.1 locally)
```

NEVER put actual key values in this file.

---

## 10. Implemented Features

- [x] Single-company research
- [x] Batch research (sequential queue, resume after interruption)
- [x] Bilingual EN/ZH output, 19 sections
- [x] PDF generation
- [x] Combined portfolio PDF
- [x] ZIP download
- [x] Existing-report detection
- [x] Regenerate existing report
- [x] Delete existing report
- [x] Edit company / Edit URL
- [x] Optional company website
- [x] Website auto-discovery
- [x] Progressive status display
- [x] Token tracking
- [x] Source tracking
- [x] **Verify Yahoo Finance usage** — confirmed unused before; now genuinely used and reported
- [x] **Stronger financial-source retrieval** — 4-rung ladder, IR/filings/Yahoo/financial press
- [x] **Public vs private detection** — SEC EDGAR + Yahoo quote validation
- [x] **Apollo enrichment** — two-stage search→enrich complete; live API call still unverified (no key)
- [x] **Decision-maker merge + source badges** (Apollo / Official / Web)
- [x] **Apollo cost tracking separate from LLM tokens** (calls, returned, retained, enriched, emails)
- [x] **Business-email capture with honest status** (verified / unverified / unavailable / not available)
- [x] **`Key Contacts & Decision Makers / 关键联系人与决策者` section** in UI and PDF
- [x] **Direct official-site crawl** (homepage, about, products, industries, leadership, news, contact)
- [x] **Multi-model retrieval fallback** (Qwen → DeepSeek Flash → DeepSeek Pro, sequential)
- [x] **Evidence threshold assessment + visible fallback status**
- [x] **Hard quality guard** with Retry Expanded Search / Edit URL / Generate Anyway
- [x] **"Not enough evidence" filtered from report display and both PDFs**
- [x] **Embedded PDF viewer** (browser-native iframe, non-blocking panel)
- [x] **curl fallback for hosts this Python's LibreSSL cannot reach**
- [ ] Improve source diversity for companies still returning <5 sources
- [x] **Model-access detection and graceful fallback** (retrieval and synthesis, separately)
- [x] **`/api/models/health`** diagnostic endpoint (ids, status, provider code/message; no key)
- [x] **Endpoint auto-detection** for model ids not in `DASHSCOPE_MULTIMODAL_MODELS`
- [ ] Live Apollo API verification against a real key
- [x] **Display-language toggle** (English / 中文 / Bilingual) — display, PDF, ZIP, portfolio
- [x] **Language-suffixed PDF filenames** (`_EN_`, `_ZH_`, `_Bilingual_`)
- [x] **On-demand language PDFs** rendered from saved data, cached on disk
- [x] **Language preference persisted** in `localStorage`, shared by both tabs
- [x] **Enterprise UI redesign** (app shell, 3 tabs, KPI cards, data grid, report library)
- [x] **Kellogg purple `#4F2582` design system** with consistent controls and spacing
- [x] **Plain-language model-activation messaging** in pickers, reports and System Status
- [x] **Consistently bilingual UI chrome** (tabs, forms, buttons, grid, badges, library, viewer)
- [x] **Render-ready**: gunicorn, `PORT`/`HOST`, `/healthz`, env-var config, `render.yaml`
- [x] **Access gate** — login form + signed partitioned cookie, env-var credentials
- [x] **CSP `frame-ancestors`** restricted to the approved CRM origin
- [x] **CRM tab integration** — two tabs, Current default, Previous unchanged
- [ ] Deploy to Render and point `CURRENT_ACCOUNT_RESEARCH_URL` at it
- [ ] Durable storage (Render Disk or object storage) — documented, not built
- [ ] DashScope model activation — accepted as blocked, no action planned

---

## 11. Recent Test Results

| Company | Sources | Level | Tokens | Time | Result |
|---|---|---|---|---|---|
| ACRO Automation Systems | 9 (7 official) | strong | 22,736 | 103s | Fixed. Was "zero information"; now full 19 sections, 0 thin |
| TE Connectivity | 14 | strong | 23,965 | 82s | Yahoo Finance used: Yes (`finance.yahoo.com/quote/TEL/`) |
| ATC Automation | 6 | — | 16,849 | — | Private company; Yahoo correctly not forced |
| SKEQI | 8 | — | 20,565 | 99s | Baseline, unchanged |
| Zzqx Nonexistent Widgets Gmbh | 0 | insufficient | 0 | 155s | Guard fired: `needs_review`, all 3 waves tried, no tokens spent |

Apollo (mocked transport, fixtures):

| Company | Apollo returned | Retained | Web people | Final roster | Departments |
|---|---|---|---|---|---|
| TE Connectivity | 11 | 7 | 3 | 10 | Executive Leadership, Operations, Plant Leadership, Strategic Sourcing, Digital Manufacturing |
| ATC Automation | 8 | 5 | 0 | 5 | Strategic Sourcing, Manufacturing, Automation, Battery Engineering, Quality |

Two-stage enrichment (mocked transport, 20 candidates including back-office noise):

| Measure | Value |
|---|---|
| Candidates returned by search | 20 (no credit cost) |
| Relevant after ranking | 15 |
| Sent to `bulk_match` | 12 (2 batches: 10 + 2) |
| Matched / credits consumed | 11 |
| Credits avoided vs enriching everything | 9 |
| Emails found / verified | 9 / 6 |
| Placeholder emails leaked into the report | 0 |
| Dropped as irrelevant | Executive Assistant, Talent Acquisition, Payroll, Safety Coordinator, Social Media |

Departments covered in that run: Executive Leadership, Operations, Procurement, Engineering,
Supply Chain, Strategic Sourcing, Automation, Plant Leadership, Digital Manufacturing,
Manufacturing, Battery Engineering, Energy Storage, Quality, Sales/Business Leadership.

Live app with **no** key configured: 0 Apollo calls, 5 web-sourced contacts, 0 emails,
0 invented addresses — research completed normally.

---

## 12. Known Issues

### ACCEPTED — DashScope models require paid activation
All three configured models return HTTP **403 `AccessDenied.Unpurchased`**,
*"Access to model denied. Please make sure you are eligible for using the model."*

| Field | Value |
|---|---|
| Provider | Alibaba Bailian / DashScope, native protocol |
| Workspace | `ws-nd2t772oy7jnpwaz` |
| Affected model IDs | `qwen3.6-flash`, `deepseek-v4-pro`, `deepseek-v4-flash-0731` |
| HTTP status / code | 403 · `AccessDenied.Unpurchased` |

**Root cause:** these models require paid activation on the account. Not an application fault.
Confirmed not Apollo (no DashScope dependency, different host and auth) and not a wrong model id
(all three appear in the account's own catalog).

**Decision: leave the models configured and blocked for now. No further action is required
unless billing / model access is enabled.** Do not spend time trying to repair or replace them.

**How the app behaves meanwhile**
- The model pickers label them `Qwen 3.6 Flash — Unavailable / Requires activation` and disable them.
- Selecting one shows a plain-language message: *"This model is not currently activated for this
  account. Please choose another available model, or enable billing / model access."*
- It is never reported as a retrieval failure, "not enough evidence", or an application error.
- A model proven denied is remembered for the process and is not retried repeatedly.
- **Saved reports, PDFs, language switching, batch history and report viewing all work normally.**

### Live Apollo API unverified — `APOLLO_API_KEY` is still absent
The key is **not** in `ai_credentials.env`. The real endpoints, response shapes and credit
headers have never been exercised. Endpoint paths, the `x-api-key` header and the `bulk_match`
body shape follow Apollo's documented v1 API but are unconfirmed until a key is added.
Expect to check on first live run: the `matches` array key, `email_status` vocabulary, and which
usage headers Apollo actually returns.

### Title-matching bugs found and fixed (watch for regressions)
Two defects were silently dropping target contacts:
1. Patterns assumed "X Director" and missed "Director of X", losing Supply Chain and Automation
   leaders. `title_variants()` now matches both orders.
2. `\b(manufactur|technolog|robotic)\b` required a word boundary after a stem, so "Manufacturing"
   and "Robotics" never matched at all. Stems now use `\w*`.
A 34-case title matrix covers both; re-run it after touching `RELEVANCE_RULES`.

### Some official sites block automated access
`te.com` returns 403 to the fetcher. The pipeline falls back to search and to alternate regional
domains (it resolved `teconnectivity.com.cn`). Evidence quality is lower than a readable site.

### Domain guessing was adopting the wrong company (FIXED, watch for regressions)
`te.com` being blocked caused discovery to guess `connectivity.com` — an unrelated business —
and label it tier-1 "official website". A guessed domain that drops part of the company name must
now be corroborated by the page naming the company. Blocked hosts with a strong distinctive
domain token (e.g. `honda.com`) are still accepted.

### Low source counts for some Western companies
English-web coverage from the DashScope backend is weak. The direct site crawl mitigates this
substantially but companies with thin websites still land at 5–7 sources.

### Historical reports
Reports generated with earlier prompts may contain 12 sections, fewer sources and older retrieval
behaviour. They are not equivalent to newly generated reports. Records saved before 2026-09-02
also lacked `pdf` / `research_result_display`; both are now backfilled on read.

### Residual prose mentions of 证据不足
The display filter removes placeholder *items*, not the phrase appearing inside a substantive
sentence. One such line remains in the ACRO report. This is correct behaviour.

---

## 13. Important Decisions

### Standalone app first
Focus on making the standalone Account Research application useful for immediate company
research. Do NOT work on SKEQI-EmailDrafter integration unless explicitly requested.

### Website is optional
A supplied URL is a hint, not an absolute identity constraint. It is validated and replaced when
it does not hold up.

### Existing reports
Users must be able to View, Regenerate and Delete.

### Retrieval quality over source count
Do not artificially inflate source counts with irrelevant sources. Three strong company-specific
sources beat fifteen unrelated ones. When five or more official sources exist, low-tier trade
portals are capped at two.

### SKEQI grounding
Never invent SKEQI capabilities.

### Deterministic before LLM
Ticker resolution, public/private detection, decision-maker ranking and "why relevant" text are
all rule-based. No LLM call is spent on work a parser or lookup can do reliably.

### Apollo supplements, never replaces
Official leadership pages win for executive titles. Apollo fills procurement, sourcing,
engineering, battery, manufacturing and operations roles that are hard to find publicly.

### An email address never comes from a language model
The Apollo directory passed into the synthesis prompt carries Name/Title/Department/Seniority
only. The contacts table is rendered from structured data in both the UI and the PDF. A model
that has never seen an address cannot invent one.

### Search before enrich
Apollo people search is free; `bulk_match` costs a credit per contact. Candidates are always
ranked before any enrichment call, and only the top `APOLLO_ENRICH_LIMIT` are revealed.

### Retrieval fallback is separate from report generation
The three models build one shared evidence package. They do not produce three reports.

---

## 14. What Was Just Completed

**Phase 3 — native Current Account Research workspace (2026-09-02).**
Branch `account-research-qwen`, commit **`bc928b6`**. Not pushed.

**Files changed**

| Repo | File | Change |
|---|---|---|
| CRM | `public/qwen-research.js` | **New, 633 lines.** The whole workspace |
| CRM | `public/index.html` | Native markup for the Current pane + CRM-token styling |
| CRM | `public/app.js` | Iframe path removed; native init on tab open |
| CRM | `server.js` | Batch-upload proxy, portfolio/ZIP export from Neon |
| CRM | `qwenResearch.js` | `recordsFor()` — canonical records for exports |
| Engine | `app.py` | `POST /api/render-portfolio`, `POST /api/render-zip` (stateless) |

**UI components ported:** single-company form (company, optional URL, model,
language), report view with metadata grid, Key Contacts table, sources list,
batch upload with column mapping, batch action bar, KPI cards, the data grid
with in-place editing, report library with search, portfolio actions, and the
embedded PDF preview. Markdown rendering, citation pills and confidence tags
were ported and restyled onto CRM tokens.

**Neon APIs used:** `GET /reports`, `GET /company/:company`, `GET /exists`,
`POST /reports/delete`, `GET /render` (markdown + PDF),
`POST /export/portfolio`, `POST /export/zip`, `GET /models/health`,
plus the proxied `research`, `job`, `batch/*` routes.

### VERIFIED

| Check | Result |
|---|---|
| Current Account Research loads by default | `current` active on open |
| No nested app chrome | Qwen iframe absent, no standalone appbar, no System Status |
| Sub-tabs | Single Company 单个公司 · Batch Research 批量研究 · Reports 报告库 |
| 29 existing reports visible | Library shows 29 / 29 |
| English-only view | 19 headings, first "Executive Summary", 31 CJK chars |
| Chinese-only view | 19 headings, first "执行摘要", 3,053 CJK chars |
| Bilingual view | 19 headings, first "Executive Summary / 执行摘要" |
| Metadata / contacts / sources | 6 metadata tiles, 4 contact rows, 9 sources |
| PDF preview | Opens inline from `/api/aresearch/render?…&format=pdf` |
| PDF download | Served through the same Neon-backed route |
| Delete removes the intended Neon report | Library 29→28, Torus gone, Neon 28; restored to 29 |
| Existing-report detection from Neon | ACRO / TE = Existing Report, ZZ New Co = Pending |
| **Focus during polling** | Focus, text and caret all intact through re-renders |
| Generate/Regenerate while blocked | "Model unavailable — activation/payment required / 模型暂不可用 — 需要开通/付费" |
| Previous Account Research | Claude iframe loads, content unchanged |
| Tab switching | Current → Previous → Current, no JS errors |
| Claude tables unchanged | `account_reports` 12, `account_research_cache` 45 |

**Bug found and fixed:** the Delete button rendered "Delete 删除 删除" — the CRM's
own i18n already translates "Delete", and a manual `i18n-zh` span duplicated it.
My first duplicate detector missed it because `删除删除` scans as a single CJK
token; a corrected detector now reports zero duplicates across all three tabs.

### Final pre-push verification (2026-09-02)

Both services restarted clean, no startup errors, `APOLLO_API_KEY` absent.

| Area | Check | Result |
|---|---|---|
| Current | Default Account Research tab | ✓ |
| Current | 29 Neon reports visible | ✓ 29 |
| Current | English / 中文 / Bilingual | ✓ 19 sections each; cjk 31 / 3053 / 3295 |
| Current | Report metadata | ✓ 6 tiles |
| Current | Sources | ✓ 9 |
| Current | Apollo / contact rendering from saved reports | ✓ 4 rows, with the Apollo key absent |
| Current | PDF inline viewer | ✓ |
| Current | PDF download | ✓ |
| Current | Delete | ✓ 29→28, retrieve 404, restored to 29 |
| Current | Edit Company / Edit URL | ✓ both save |
| Current | Report library + search | ✓ |
| Current | Combined PDF | ✓ 29-page, 690 KB, built from Neon |
| Current | ZIP | ✓ 29 PDFs, 526 KB, built from Neon |
| Previous | Loads unchanged | ✓ |
| Previous | Still an iframe, untouched | ✓ |
| Previous | Existing reports / tables unchanged | ✓ `account_reports` 12, cache 45 |
| Models | 403 shown as activation/payment required | ✓ bilingual |
| Models | Not misclassified as retrieval failure | ✓ |
| UI | No duplicate bilingual labels | ✓ |
| UI | No nested app shell/header | ✓ |
| UI | No iframe for Current | ✓ |
| UI | No console errors switching tabs | ✓ |
| Data | Exactly one current report per company | ✓ 29 / 29, unique index present |
| Repo | No credentials in code, commits or tracked files | ✓ `.env` ignored, diff clean |

Branch diff vs `main`: **10 files, +1834 / −6**. The six deleted lines are the
old Current-tab iframe path and the replaced script tag — nothing unrelated.

### BLOCKED

**Live new research.** DashScope returns 403 `AccessDenied.Unpurchased` on all
three configured models. **Reason: model access requires paid activation.**
Not a Phase 3 failure — everything was validated against the 29 existing Neon
reports, and no research tokens were spent.

---

## 15. Current Work In Progress

**Phases 1–3 — CRM integration**
Status: **COMPLETE and verified.** Neon persistence, proxy, and a native
workspace. Awaiting your approval before pushing the branch.

**Render deployment**
Status: PREPARED, NOT DEPLOYED. See `DEPLOY.md`. Note Phase 1 removes the worst
consequence of ephemeral disk for *completed* reports once Phase 2 writes new
runs to Neon; today only the migrated history is durable.

**DashScope model activation** — accepted as blocked, requires paid activation.
**Apollo** — implemented, `APOLLO_API_KEY` still absent.

---

## 16. NEXT ACTIONS

1. **Review the pushed branch** and open a PR when ready
   (`account-research-qwen`, 5 commits, `main` untouched).
2. When model access is activated, run one live company end-to-end to confirm
   new research → Neon upsert and regenerate → safe replacement.
3. Archive `reports/*/history/` (35 JSON + 35 PDFs, 3.7 MB) — superseded runs,
   deliberately not in Neon.
4. Deploy both services to Render per `DEPLOY.md`, setting `APP_SERVICE_KEY` on
   the engine and `ACCOUNT_RESEARCH_SERVICE_KEY` on the CRM to the same value.
5. Add `APOLLO_API_KEY` and verify Apollo live.

---

## 17. Do Not Accidentally Change

- Do not integrate into EmailDrafter unless requested.
- Do not replace the standalone application.
- Do not revert the 19-section SKEQI prompt to the old 12-section prompt.
- Do not expose credentials to the frontend, in logs, or in reports/PDFs.
- Do not label DashScope/Bailian search as Google.
- Do not set `search_options.freshness` (collapses results to 0–1).
- Do not change the DashScope endpoint routing per model.
- Do not invent financial information; keep "Not publicly available / 未公开" for private companies.
- Do not invent SKEQI capabilities.
- Do not regenerate existing reports without user intent.
- Do not delete reports without explicit user action.
- Do not sacrifice research accuracy merely to increase source count.
- Do not let Apollo replace web research, and do not force Yahoo Finance for private companies.
- Do not remove the curl fallback in `fetch_url` — several hosts are unreachable without it.
- Do not report "Yahoo Finance used: Yes" based on a query; it must reflect a retained URL.
- Do not re-run research to change display language; render EN/ZH from the saved bilingual result.
- Do not store per-language research results — one bilingual record is canonical.
- Do not machine-translate source titles, publications or URLs.
- Do not translate batch STATUS VALUES; they are data. Add to `STATUS_ZH` for display instead.
- Do not translate company names, model names or provider/API names in the UI.
- Do not run gunicorn with more than ONE worker; job/batch state is in-memory.
- Do not set `frame-ancestors *`; keep it to the approved CRM origin.
- Do not connect this app's data to the CRM. The integration is navigation only.
- Do not split arbitrary "A / B" text in report bodies; brand names legitimately contain it.

---

## 18. Session Handoff

**Last successful operation:**
Verified the native Current Account Research workspace in a real browser: default
tab, 29 reports from Neon, all three language views, PDF preview and download,
delete and restore, existing-report detection, focus retention during polling,
the activation message on Generate, and the Claude tab unchanged.

**Current stopping point:**
**Phases 1–3 complete, pre-push verified, and PUSHED** to
`origin/account-research-qwen`. `main` untouched, no merge, no force-push.

**Recommended next command/action:**
Open a pull request when ready, or deploy per `DEPLOY.md`. Render deployment has
NOT been started.

**Uncommitted changes:**
CRM: none — branch `account-research-qwen`, five commits
(`790bb6a`, `fcdd036`, `3c66d49`, `d4536a5`, `bc928b6`). `main` untouched.
Engine: `app.py`, `access.py`, `research_service.py`,
`ai_credentials.env.example`, `STATUS.md` — not a git repository.

**Application currently runnable:** Yes. Engine on 5057, CRM on 3000 with
`CURRENT_ACCOUNT_RESEARCH_URL` and `ACCOUNT_RESEARCH_SERVICE_KEY` set.

**Known blockers / limitations:**
1. Live research blocked — DashScope models require paid activation.
2. In-flight batches live in the engine's memory; a restart loses progress but
   never a completed report, which is already in Neon.
3. Render deployment not performed.
4. `APOLLO_API_KEY` absent.
