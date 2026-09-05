# Account Research — Project Status

> Last updated: 2026-09-05
> Updated by: Claude
> Current phase: Production parity; cost accounting live; awaiting the first instrumented run
> Latest change: engine `77049ac`, CRM `0756f48` — both deployed and verified
> Overall status: OPERATIONAL — all three DashScope models verified **available** 2026-09-03

---

## 0. MODEL STATUS — verified live 2026-09-03

| Model | State |
|---|---|
| `qwen3.6-flash` | **available** |
| `deepseek-v4-pro` | **available** |
| `deepseek-v4-flash-0731` | **available** |

`GET /api/models/health?probe=1` returned `available` for all three.

> ### ⚠ Research runs are LIVE and cost money
>
> Generate, Regenerate, Refresh and Batch now call the model for real and **may
> consume paid tokens**. Regenerate/Refresh also **replace** the saved report.
> **Do not run them merely to test.** Only on explicit request.

The earlier 403 `AccessDenied.Unpurchased` state is **resolved**. Sections below
that describe it — the ACCEPTED entry in Known Issues, the roadmap line, the
handoff blockers — are kept only as the record of what *was* true and how the
application handles a withdrawal of access. The bilingual *Model unavailable —
activation/payment required* string remains in the UI as the **guard** for that
case; it is not a statement about today.

---

## 0a. APOLLO STATUS — verified live 2026-09-03

| | |
|---|---|
| Key source | **`ai_credentials.env`** (local, git-ignored, mode 600) |
| Environment variable | **`APOLLO_API_KEY`** |
| Local status | **configured / tested** |
| Render status | **PENDING** — must be added to the **engine** service's environment |

`ai_credentials.env` is **not** deployed. On Render the same key has to be set as
an environment variable on the engine service, exactly as `DASHSCOPE_API_KEY` is.
Setting it on the CRM service does nothing: Apollo is called from the engine.

**How the key resolves.** `apollo_service.api_key(cfg)` reads `cfg["APOLLO_API_KEY"]`
first, then `os.environ`. `research_service.load_config()` merges every key from
`ai_credentials.env` over the environment, so the file wins locally and real env
vars work in production. Verified: `apollo.configured(cfg)` is `True`.

**Connectivity check, 2026-09-03.** One `/organizations/enrich` call — no people
search, no contact enrichment, no research run, no model call:

| Check | Result |
|---|---|
| `configured` | ✅ true |
| Auth | ✅ accepted, no 401/403 |
| Resolution | ✅ `skeqi.com` → **SKEQI Intelligent Equipment**, matched by domain |
| API calls | 1 |
| Rate headers | `x-minute-requests-left: 999` of 1000 |
| Errors | none |
| Key in output | ✅ none — never printed, never logged, never persisted |

**Status changed from `not_configured` to configured/available.** The earlier
Manz AG run reported `not_configured` correctly: `ai_credentials.env` was modified
at **19:18:21**, three minutes *after* that run finished at 19:15.

**Enrichment path.** `research_service` calls `apollo.configured(cfg)` and, when
true, runs search → rank → enrich, all with the same `cfg`, so the path is wired
to this key. Note the enrichment cap is **`DEFAULT_ENRICH_LIMIT = 12`**, not 20 —
search returns up to `PER_PAGE = 100` candidates, ranking keeps the relevant ones,
and only the top 12 are enriched, because enrichment is the credit spend. Override
with `APOLLO_ENRICH_LIMIT`. **Not changed here** — say so if you want 20.

**Secret hygiene.** The key is sent only as the `x-api-key` request header, which
`_request` marks "never logged". It is absent from the frontend, from reports and
PDFs, from Neon records, from `STATUS.md` and from every commit.
`ai_credentials.env` remains git-ignored.

---

## 0b. Unsupported claims are omitted, not announced — 2026-09-03

Per instruction: a claim without evidence should **not appear**, rather than
appear as a *Not enough evidence / 证据不足* line. `confidence.py` already filtered
these; two gaps let some through.

1. **Malformed badges survived.** Models emit unclosed or doubled tags such as
   `**Not enough evidence / 证据不足. **Not enough evidence**`. The stripper matched
   only the exact spelling and left `Not enough evidence**` visible.
   `_TAG_LOOSE` now sweeps any remnant and tidies the stray asterisks.
2. **Long absence prose survived.** `is_placeholder` dropped a line only when its
   residue was under 30 characters, so *"Direct competitor names, product ranges,
   capacity comparisons … are not disclosed in the provided evidence"* was kept
   for being wordy. `is_absence_only` now tests what the words **mean**.
3. **Absence written as prose, mid-line.** Some lines mix an absence clause with
   real sourced findings. `prune_absence_sentences` removes the unit of meaning —
   the **sentence** — never the whole line.

**The guard against over-deletion:** a sentence is always kept when it carries a
citation `[n]` or a *Verified / 已验证* or *Likely / 可能* badge. A caveat inside a
sourced finding is not filler: *"Revenue was EUR 200m, though the split is not
disclosed [3]"* survives intact.

Display and PDF share this path (`pdf_service` calls `conf.strip_unsupported`),
so both agree. **The stored record is untouched** — the tags remain in
`research_result` and in the `confidence` counts; only the reading view drops them.

**Verified against all 30 stored reports:** tagged occurrences **2,288 → 0**, no
section lost in any report, and Eclipse Automation keeps its sourced findings and
badges. Prompt, retrieval and model routing were not changed.

---

## 0c. Contacts — bug fixes, CRM-first reuse, buying-centre ranking (2026-09-04)

### Two bugs that made Apollo return nobody

**1. `UnboundLocalError` on every POST.** `apollo_service._request` had
`import urllib.parse` *inside* the function while `urllib.request` was imported at
module level, which makes `urllib` a **local** name for the whole body. Every path
that skips the `params` branch — i.e. **every POST: people search and bulk
enrichment** — raised `UnboundLocalError` before a request was sent. The generic
handler reported it as "0 candidates". Organisation lookup kept working because it
is a GET with params, which is why a connectivity check on that path alone proved
nothing about search.

Fixed: `urllib.parse` is imported at module level, and the local import is gone.
**Never re-add it** — the shadowing is silent and total.

**2. The people-search endpoint is retired.** With (1) fixed Apollo answered
**422**: *use `mixed_people/api_search`*. `search_people` now walks
`PEOPLE_SEARCH_PATHS` — the new path first, the two older ones as fallbacks —
treating **404 and 422** alike as "not on this tenant".

Live check after both fixes, one search, **0 credits**:
`status ok · 100 returned · 94 after ranking`.

### CRM contacts are used before Apollo

Source priority is **CRM → official/web → Apollo**. Apollo fills gaps.

- The CRM proxy attaches its own contacts to `POST /api/research` as
  `known_contacts`; the engine threads them through `worker()` into
  `build_shared_evidence(..., known_contacts=...)`.
- `people_service.from_crm()` scores them with the same ruleset used for Apollo.
- **Apollo is skipped entirely** when the CRM already yields `enrich_limit`
  relevant contacts (`apollo_usage.status = "skipped_crm_sufficient"`).
- Running the engine standalone simply receives none and behaves as before.

### Dedupe by real identity

`identity_keys()` returns every key a person can be recognised by, strongest
first, and two records merge if **any** key matches:

1. `email:` exact, lowercased
2. `li:` LinkedIn profile, protocol/`www.`/query stripped
3. `name:` normalised first+last **plus normalised company**

The company is part of the name key on purpose: *Jim Farley* at two different
companies is two people. Legal suffixes are stripped, so *Ford Motor Company* and
*Ford Motor Co., Ltd.* are the same company.

**A CRM email always wins over Apollo**, and a CRM title beats an Apollo one.
**No email is ever invented**: a contact without one keeps an empty address and
the CRM's own `not_checked` / `not_available` status.

### Ranking: a buying centre, not a list of manufacturing titles

The ruleset kept its **high-priority band unchanged** — manufacturing, operations,
plant leadership, procurement, strategic sourcing, supply chain, automation and
controls, equipment and process engineering, battery/EV/energy storage, digital
manufacturing all still score 69–100.

What changed is that roles which **influence, specify, evaluate, fund or approve**
a purchase are no longer scored 0 and dropped. A **second band at 40–58** was
added *below* every core role, so they rank lower unless the title says otherwise:

| Band | Score | Examples |
|---|---|---|
| Program leadership | 58 | launch / industrialisation programme owners |
| Business-unit leadership | 56 | GM, division, segment, country manager |
| Finance leadership | 55 | CFO, Finance Director, VP Finance, Controller |
| Innovation / R&D | 52 | R&D Director, Head of Innovation |
| Engineering (IC) | 50 | engineers not already matched higher |
| Quality | 46 | quality/QA/QC roles |
| Technology | 44 | technology, digital, controls, IT leads |
| Maintenance / Reliability | 42 | uptime and spare-part owners |
| Supply chain (broad) | 40 | supplier, vendor, sourcing, purchasing |

Two matching fixes went with it: `engineer` now matches alongside `engineering`
(so *Battery Manufacturing Engineer* scores 72, not 0), and the finance rule
handles both word orders (*Finance Director* and *Director of Finance*).

**Exclusions are unchanged.** Assistants, recruiters, HR, comms, reception and
similar still score 0 and never reach the roster.

**Roster diversity.** Ford's CRM record holds eighteen battery manufacturing
engineers; on score alone they filled all twenty slots and pushed out the plant,
finance and programme people. `merge()` now applies `_PER_DEPARTMENT_CAP = 4` on a
first pass and backfills the remaining slots by score, then re-sorts. A company
with only one kind of contact still returns a full list.

Real Ford CRM data, Top 20: Executive Leadership 2 · Battery Engineering 8 ·
Digital Manufacturing 4 · Finance Leadership 4 · Engineering 1 · Innovation 1.
Under the old ruleset only **6 of 40** rows survived at all.

A CRM placeholder (`N/A`, `-`, `none`) is treated as an empty field, so the
ruleset's own department is used instead of printing "N/A" in the roster.

### Verified 2026-09-04 — no paid research run

| Check | Result |
|---|---|
| Both Apollo call shapes reach the network | ✓ no `UnboundLocalError` |
| Live people search | ✓ 100 returned, 94 ranked, **0 credits** |
| Core roles keep their scores | ✓ 69–100 unchanged |
| Second band no longer scores 0 | ✓ CFO 55, R&D 52→55, Program 58, IC engineer 50 |
| Exclusions still dropped | ✓ assistant, recruiter, social media, reception = 0 |
| Dedupe by email / LinkedIn / name+company | ✓ incl. same name at different companies kept apart |
| CRM email and title beat Apollo | ✓ |
| No invented emails | ✓ |
| Roster is a buying centre | ✓ 6 departments in the Top 20 |
| All 30 stored reports still parse and merge | ✓ 0 failures |

---

## 0d. Contact identity and provenance — two fixes (2026-09-04)

### The Verkor evidence

Verkor ran with 40 CRM contacts supplied, 39 retained. Its CRM holds exactly
**one** contact carrying an email and a LinkedIn URL. In the saved roster that
person appeared **twice**:

| Row | Sources | Company | Email | LinkedIn | CRM id |
|---|---|---|---|---|---|
| 7 | `Apollo` | *(blank)* | — | — | — |
| 8 | `CRM` | Verkor | present | present | 5805 |

The CRM data was never lost. The two rows simply never merged, so a reader could
meet the empty one first.

### Fix 1 — identity that tolerates a missing company

`identity_keys` keyed a name as `name|company`. That stops *Jim Farley* at two
companies merging, and it was **my regression**: rows parsed out of the model's
own report carry **no** company, so they could never match the CRM row.

Matching order is now:

1. same normalised **email**
2. same normalised **LinkedIn**
3. same normalised **name + same company**
4. same normalised **name** where **either side states no company**

A company only disambiguates when **both** sides claim one, and they differ.
Within a single company's research run everyone belongs to that company.

**CRM keeps precedence** for the structured fields — email, email status,
LinkedIn, company, location, seniority, contact id — and a blank never
overwrites a CRM value, whichever order the records arrive in. Nothing is
invented.

A second bug surfaced while testing: `dict(person)` is shallow, so the canonical
record **shared the caller's `sources` list** and merge appended provenance onto
its own input. Re-merging accumulated badges that never applied.

### Fix 2 — provenance comes from the pipeline, not the model's prose

`parse_report_people` read the report's own "Source" column and stamped
`Apollo` when the model wrote it. Verkor carried an Apollo badge on a run where
**Apollo was never called** (`calls: 0`, `status: skipped_crm_sufficient`).

Rows parsed from a report are now **Web** or **Official**. Only records that
actually came back from Apollo, via `from_apollo()`, carry `Apollo`. Merged
contacts show every real provenance: `CRM + Web`, `CRM + Apollo`.

### Verified — saved data only, no paid call

| Check | Result |
|---|---|
| Margot Cussigh: duplicate rows | **2 → 1** |
| CRM email / status / LinkedIn / id / company / location | ✓ all preserved |
| Blank never overwrites CRM, either arrival order | ✓ |
| False Apollo badge from prose | ✓ gone — reads `CRM + Web` |
| Genuine Apollo record | ✓ still `CRM + Apollo` |
| Caller's input no longer mutated | ✓ |
| Same name, different companies | ✓ stay apart |
| Same company written differently | ✓ merges |
| All 31 stored reports parse and merge | ✓ 0 failures |
| Duplicate-name rows across every saved roster | ✓ 1 → 0 |

**Ford Motor Company**, matched by company id 7 (`ford motor`), not the empty
short-name `ford` record: 55 contacts, **12 with email, 0 with LinkedIn**, 43
with neither. LinkedIn is not being lost — it was never captured. All 12 emails
survive ranking; 8 reach the Top 20, the other 4 ranking below the cut. No email
is invented.

The `crmContactsFor` cap stays at **40 of 55** for now, deliberately, pending
Top 20 quality review.

---

## 0e. Retrieval quality — Tier B verification and adaptive fetching (2026-09-04)

Committed as `f560b63`. Measured on Verkor, Manz AG and ACRO Automation Systems.

### The problem

Third-party evidence had collapsed to almost nothing. The identity filter was
rejecting 97–100% of every non-official candidate, so reports were built almost
entirely from the company's own website. Source counts looked acceptable; source
*independence* did not exist.

### Cause 1 — `\b` does not fire against CJK (a regression, not a gap)

`re.search(r"\bacro\b", text)` never matches inside Chinese text, because `\b`
requires a word/non-word transition and both a Latin letter and a CJK character
are "word" characters. Every Chinese-language page was silently rejected. Since
the DashScope backend indexes the Chinese web far better than the English web,
this removed most of the usable pool.

Fix: `_mentions()` uses explicit ASCII edges instead.

```python
re.search(r"(?<![A-Za-z0-9])" + re.escape(needle) + r"(?![A-Za-z0-9])", text, re.I)
```

### Cause 2 — matching was too literal, then too loose

- `core_name()` strips legal suffixes (ag, gmbh, inc, llc, ltd, sas, bv, …), so
  "Manz" matches a page that says "Manz AG".
- `distinctive_tokens()` filters generic industry words (auto, motor, energy,
  battery, systems, automation, …). A two-token match now requires at least one
  distinctive token; a single distinctive token is accepted with **no length
  floor**, so short real names are not penalised.
- Token matching uses `_mentions()`, not substring containment. Previously
  `"acro" in "macro"` was a match.

### Cause 3 — second-tier candidates were discarded unread

Tier B candidates are now fetched and verified against actual page text rather
than judged on URL and snippet alone.

### Measured category yield — order is evidence, not assumption

| Category | Tier B candidates fetched | Verified | Rate |
|---|---|---|---|
| Financial | 24 | 13 | **54%** |
| News | 20 | 3 | 15% |
| Other | 41 | 1 | 2% |
| Government / regulatory | 5 | 0 | **0%** |

Government sources are *not* prioritised merely because the category sounds
authoritative; measured, they verified nothing. Order is `financial → news →
other → government`, configurable via `TIER_B_CATEGORY_ORDER`.

### Result — verified third-party sources at 10 fetches

| Company | Before | After |
|---|---|---|
| Verkor | 6 / 14 | **10 / 14** |
| Manz AG | 0 / 14 | **8 / 14** |
| ACRO Automation Systems | 0 / 14 | **6 / 14** |

### Adaptive fetching and the sufficiency rule

Tier B fetches in batches of `TIER_B_FETCH_LIMIT` (10) up to
`TIER_B_MAX_FETCHES` (30), stopping on **evidence sufficient**, **candidate pool
exhausted**, or **safety ceiling reached**.

**Policy A**, judged on the set that will actually be *retained after caps* —
not on candidates merely verified during Tier B:

| Condition | Threshold |
|---|---|
| Third-party sources | ≥ 4 |
| Distinct third-party hosts | ≥ 3 |
| Categories represented | ≥ 2 |

The official domain is excluded from all three counts. An earlier version
counted pre-cap candidates and declared sufficiency for evidence that was then
discarded; that was wrong in both directions and is fixed.

### Corrected three-company run

| Company | Fetched | Retained | Third-party | Hosts | Cats | Sufficient | Stop reason |
|---|---|---|---|---|---|---|---|
| Verkor | 10 | 11 | 4 | 3 | 2 | **yes** | evidence sufficient |
| Manz AG | 30 | 8 | 3 | 3 | 2 | no | safety ceiling |
| ACRO | 30 | 9 | 2 | 1 | 1 | no | safety ceiling |

### Performance

Tier A page text is cached back onto the candidate record, so it is fetched once
instead of re-fetched on every adaptive batch. Manz: **197s → 164s**.

### Topic attribution

Search results carry the query label that found them. Candidates and evidence
records carry `topics` and `category`. This is pipeline metadata; **no prompt
was changed to obtain it**.

### Narrow collision entry, not a general rule

`COLLISIONS` gained an ACRO entry for the unrelated Suzhou companies (ACRO
Biosystems / 苏州爱克罗). A page is **not** rejected merely for containing
"Suzhou".

### ⚠ Open limitation — retention is now the binding constraint

`apply_evidence_caps` collapses the low-tier budget from 6 to **2** when five or
more strong sources are present. Chinese financial hosts classify as **tier 6**.
So the retention rule discards exactly the category that retrieval works hardest
to find.

Manz discards 4 verified financial sources. Each one is a **new host, a new
category, and carries topics the retained set lacks** (competitors, projects,
strategy, products):

- `gelonghui.com/p/1462071` — products, projects, competitors
- `caifuhao.eastmoney.com/news/20220609193426889597990` — strategy
- `gelonghui.com/p/1575897` — competitors
- `gelonghui.com/p/1781012` — projects, competitors

With them Manz would reach 7 third-party sources across 5 hosts in 3 categories
and satisfy Policy A on the first batch. Without them it can never satisfy its
own stopping rule, burns all 30 fetches, and ends with less evidence than it
gathered. Verkor and ACRO discarded **no** third-party sources; ACRO is thinly
covered at source, which is a different problem.

**RESOLVED 2026-09-04 in `7f39b83`.** A third-party source that passed
page-content identity verification is no longer discarded for its tier alone.
The exemption requires BOTH third-party AND `content_verified`; no domain, host
or category is promoted. `build_evidence` now decides identity before applying
the budget and records `content_verified` so the production rule can see it.

Measured, retrieval only:

| Company | Fetches | Third-party | Hosts | Cats | Sufficient | Discarded |
|---|---|---|---|---|---|---|
| Manz before | 30 | 3 | 3 | 2 | no | 4 |
| **Manz after** | **10** | **10** | **7** | **3** | **yes** | **0** |
| Verkor before | 10 | 4 | 3 | 2 | yes | 0 |
| Verkor after | 30 | 4 | 4 | 2 | yes | 0 |
| ACRO before | 30 | 2 | 1 | 1 | no | 0 |
| ACRO after | 30 | 3 | 2 | 1 | no | 0 |

Manz satisfies all three predicates on the first batch and uses a third of the
budget. ACRO was the regression that mattered — it must not be pushed over the
line by readmitted weak sources — and it correctly still fails the rule.

Verkor needed three batches where it previously needed one, at the same final
counts. Retention can only ADD items and tier ordering prevents displacement, so
this is search-backend variance: at 10 fetches this run had 3 third-party
candidates where the earlier run had 4. Confirmed from the per-batch checkpoints.

---

## 0f. Tesla — blocked official site misreported (2026-09-04)

Committed as `0c99f98`. Tesla returned "Broader web research produced
insufficient reliable evidence" after 25 queries. Two independent bugs.

**1. A blocked official site was reported as missing evidence.**
`validate_website` correctly accepts a host that blocks automation when the
domain distinctively encodes the company name — tesla.com IS Tesla's site. It
returns `reason="site_blocked"` with empty text, and `resolve_website` then
labelled the run `provided` and dropped the distinction. The official crawl
contributed nothing and the run ended on the generic `insufficient` branch.
`FAILURE_REASONS` already carried an accurate `site_blocked` message that
nothing ever raised.

Now: `resolve_website` reports `blocked`, `build_shared_evidence` derives a
`site_blocked` state, warns through the existing WARN channel, and puts the flag
on both the package and `quality`. Blocked is not invalid and not unresearchable.

**2. Financial retrieval ran after the empty-evidence guard.**
A public company whose website blocks crawlers was failed while its financial
evidence was one call away, uncollected. Yahoo Finance now runs before the guard.

Verified on non-model paths only — no search, no synthesis, nothing billable:

| Check | Result |
|---|---|
| Domain validates | ok, `reason=site_blocked`, 0 chars |
| Blocked state recognised | `status=provided`, `blocked=True` |
| TSLA listing resolves | public, TSLA, "Tesla, Inc.", SEC EDGAR |
| Yahoo evidence collected | `finance.yahoo.com/quote/TSLA/`, 2000 chars |
| Guard fires? | 0 items before Yahoo, 1 after → no raise |

Tesla now proceeds to synthesis. `quality["blocking"]` needs both unverified
identity and no official source; identity still holds on the domain match.

**Diagnostic note for future sessions:** the engine assigns its own `job_id`,
unrelated to the CRM's. A completed run's retrieval funnel lives only in the
serving process's memory, and `evidence_cache` is written on success only. If a
run must be diagnosed after the fact, capture the funnel while the job is live.

**`/healthz` now returns a commit.** It answered `{"ok": true}` with no way to
tell which build was serving. It reports `RENDER_GIT_COMMIT` when present, else
git, cached and never raising. `verify_deployment.sh` prints it.

---

## 0g. Best-effort continuation — IMPLEMENTED (2026-09-04)

Committed as `0e62d05`. The principle is in `CLAUDE.md`; this is what changed.

**Every recoverable branch that used to end a run now continues.**

| Branch | Was | Now |
|---|---|---|
| `model_access_denied` | raise | warning, continue to financial/contacts/synthesis |
| `search_unavailable` | raise | warning, continue |
| `no_company_match` | raise | warning, continue |
| `site_unverified` | raise | warning, continue |
| `site_blocked` | raise | warning, continue |
| `insufficient` | raise | warning, continue |
| CRM contact lookup throws | killed the run | warning, continue without CRM contacts |
| Apollo throws | killed the run | warning, keep CRM contacts, continue |
| Contact merge throws | killed the run | keep the unmerged list |
| Tier B verification throws | killed the run | keep confirmed sources, stop verifying |

`build_shared_evidence` no longer contains a single `raise RetrievalError`.

**Structured limitations.** Each degraded stage appends
`{stage, status, message}` to a `limitations` list carried on the package and on
`quality`, so the UI shows per-stage degradation instead of one opaque failure.

### ZERO-GROUNDING MODE

Continuation is unconditional; invention is not. With no surviving evidence the
run still attempts financial lookup, CRM contacts and Apollo, and only then
synthesises with an explicit notice that it has no grounding. Factual sections
must read **"Insufficient verified public evidence / 缺乏足够的已验证公开信息"**
rather than infer. The notice is derived from the evidence set itself, so it can
never disagree with what was actually retrieved. Contact directories and SKEQI's
own capabilities are still written normally: neither depends on researching the
company.

### Terminal states

The generation-blocking quality gate is gone, and with it "Generate Anyway" —
researching anyway is the default. Runs report `completed`, or
`completed_with_limitations` when anything degraded.

**Inverse violation fixed.** If every synthesis model failed, the job was marked
`done` with no report. It is now `synthesis_failed`, the one genuinely fatal
execution outcome, and the retrieval package is preserved so synthesis can be
retried **without paying for research again**.

`needs_review` is no longer produced.

### Regression coverage — `test_continuation.py`

28 checks, no network and no model call: `.venv/bin/python test_continuation.py`.
Covers every converted branch, zero-grounding prompt behaviour, the retention
policy, and the three terminal states. Includes **static guards** that fail if
`raise RetrievalError` returns to the pipeline or the blocking gate returns to
`app.py`.

---

## 0h. Research Sessions — CRM (2026-09-04)

CRM commit `5bb0494` on `account-research-qwen`. Reuses the existing durable job
table; no second, browser-only tracker was built.

- `db.listRecentQwenJobs` — live jobs plus the last 24 hours, with staleness and
  elapsed computed in SQL.
- `GET /api/aresearch/sessions` — those rows with a **server-derived `state`**, so
  every client shares one vocabulary: `queued`, `researching`, `generating`,
  `completed`, `completed_with_limitations`, `synthesis_failed`, `interrupted`,
  `failed`.
- A **Research Sessions / 研究任务** list above the existing detailed progress
  panel, which is unchanged and now driven by the selected session. Multiple
  concurrent sessions are listed independently.
- Re-read from Neon on every entry to the tab; polled every 3s only while
  something is live.

**Nothing in the sessions UI can start a job.** Selecting only reads; Regenerate
goes through `startOrAttachJob`, which attaches to a live run, and the partial
unique index in Neon refuses a duplicate behind it.

Verified in headless Chromium against live Neon, 12 checks: the list renders,
selecting opens the detailed panel, sessions survive a full reload, and neither
selecting nor reloading issues a research POST.

---

## 0i. Display-filter sentence truncation — CLOSED (2026-09-05)

Fixed in `5dc764f`, deployed and validated in production.

### What was wrong

Reported as a bilingual bug: Existing Automation Providers rendered the Chinese as
`设备/自动化供应商公开证据（西门子、ABB、先` — cut mid-word — and the English lost
everything after `FANUC`.

Neither the model nor the database was at fault. The raw model output is complete
and correctly parallel, and the stored `research_result` is intact. The damage was
done on the way out, by the last line of `_strip_unsupported_tag`:

```python
r"[;；,，.。]?\s*[^\s。;；,，|]{1,14}[:：]\s*$"
```

It drops a label left with nothing after it once a "not enough evidence" badge is
removed, and the 1-14 limit was meant to catch SHORT labels only. But nothing
anchored its left edge, so instead of failing to match a long label it matched
that label's **last fourteen characters**. Chinese has no spaces to stop the
window, so it consumed real words. English slash-lists went the same way.

**28 of the 37 badge-carrying lines** in the Tesla report were cut. The filter
runs at RENDER time, so this corrupted every markdown view and every PDF while
storage stayed clean — which is why the fix repaired every existing report with
no regeneration and no migration.

### The rule now

A label is stripped only when it is the **entire remaining content** of the
bullet, and then the whole bullet is dropped rather than left as an orphan `-`.
Anchoring alone was tried and rejected: it still took `projects:` off
`Major upcoming projects:`.

| Case | Behaviour |
|---|---|
| `…（西门子、ABB、先导、海目星、库卡、发那科等）：` | preserved |
| `Divisions/products/services:` | preserved |
| `Major upcoming projects:` | preserved |
| `Expansion: Historical growth [2].` | preserved |
| `Expansion:` alone | dropped |

### Verified in production, no regeneration

Engine `5dc764f7398c`, CRM `2027d9c`. Rendering the stored Tesla report through
the production CRM render route:

- supplier list complete in English, Chinese and bilingual;
- no bullet ends on a dangling separator; no orphan bullet markers;
- PDFs valid in all three languages.

Of the 28 damaged lines: **19 return byte-for-byte**, **9 are dropped** as bare
labels, **0 remain cut mid-content**.

`test_bilingual_display.py` covers it: Chinese no-space sentences, English slash-
and space-separated labels, punctuation before colons, dangling labels, badge
removal inside valid sentences, all three bullet markers, and the PDF path. It
fails 19 of 31 against the old rule. The real-report check is opt-in through
`AR_TESLA_FIXTURE` so no customer report is committed.

### NOT part of this defect — still open

1. **Language purity.** The English view still carries ~187 CJK characters in its
   body, from two causes: the model occasionally writing Chinese prose inside the
   `**English:**` block, and the app-generated SKEQI capability table whose cells
   are bilingual and never language-selected.
2. **Placeholder asymmetry.** `is_placeholder` treats the two halves of one bullet
   differently — it keeps `Location/timing/capacity/investment relevance to SKEQI:`
   in English but drops the Chinese `地点/时间表/产能/投资价值及对思客琦的意义：` from
   the same source pair. Four fragments are absent from the render for this
   reason; they were absent BEFORE the fix too, so this is not a regression.
3. **Report architecture.** The saved report is one markdown document with
   `**English:**` / `**中文：**` blocks, language-selected at render time. That is
   the "derive one language from the other" pattern the bilingual invariant rules
   out. Live sections already store `content_en` / `content_zh` separately; the
   saved report does not.

---

## 0j. Since the truncation fix — what shipped (2026-09-05/06)

Production: **engine `77049ac`**, **CRM `0756f48`**. Both verified live.

| Commit | Repo | What |
|---|---|---|
| `e933467` | engine | Competitor Analysis + Existing Automation Providers derive their subject |
| `c0ccd13` | engine | Evidence provenance: target / ecosystem / market |
| `d92acea` | engine | Chinese ecosystem entities need an organisation, not a fragment |
| `c47a258` | engine | Streaming fallback instrumented instead of swallowed |
| `77049ac` | engine | Retrieval tokens counted; second price card retired |
| `3492dbb` | CRM | Terminal states, canonical identity, save_failed recovery |
| `f2a9a08` | CRM | Identity key stays on the existing join surface |
| `f3b6a54`/`ef9f59a`/`be8d540` | CRM | Cost accounting through the platform system |
| `0756f48` | CRM | Sections panel stops repeating each section's heading |

### The 红旗 incident, and what it taught

A real run finished, generated a report and a PDF, then failed to save with
`cannot derive an identity for "红旗"`. Three distinct defects came out of it.

**1. `normalizeNameKey` stripped CJK.** 红旗, 宁德时代 and 中创新航 all normalised to
the empty string, so `saveQwenReport` threw. Neon held **zero** reports with a
Chinese name; every CJK account had silently failed. Fixed, and identity is now
established ONCE at claim and carried on the job.

The key stays the **normalised name** even when a CRM company exists. That is not
a claim that names beat domains — it is that the key is the JOIN SURFACE. All 42
reports are name-keyed, `companies.name_key` is name-keyed, and lookup starts
from a name. A `crm:<id>` key would have orphaned **33 of 42** reports and turned
`ON CONFLICT` into duplicate rows. Stronger anchors live beside the key as
`company_id` and `identity_source`.

**`record.website` is NEVER an identity source.** 红旗 supplied
`hongqi-auto.com`; retrieval resolved `pcauto.com.cn`, a car portal. A
record-derived key would have filed the account under "pcauto".

**2. A late callback resurrected a terminal job.** `failQwenJob` ran at
18:12:28.511; section callbacks until 18:12:28.871 each set `status:'running'`.
The row ended 85% running WITH a completion time and an error — which is exactly
why Sessions said 85% Generating while Progress said 100% completed. Terminal
states are now frozen against heartbeats; explicit transitions still work, which
is how a save retry completes.

**3. Save failure is not research failure.** `save_failed` is its own state, and
`POST /api/aresearch/job/:id/retry-save` replays the existing generated record.
No model call, no retrieval, no regeneration.

**Recovered without rerunning.** The engine still held the record in memory. It
was backed up first (99,947 bytes, also in the gitignored `reports/_recovery_hongqi/`),
then replayed. Report `红旗__qwen3-6-flash__20260905181227`: 20,622 chars, 16
sources, renders and exports a PDF.

### Cost accounting — reusing the platform, not rebuilding it

The CRM already had everything: a versioned per-(provider, model) pricing table
with effective dates, `recordAiEvent`, and `account_research` as a registered
feature. **qwen3.6-flash was already priced and already flagged estimated.** The
Qwen path simply never called any of it.

- `run_search` was reading the provider's usage block and **discarding it**. It is
  a real model call per query, so a 25-query run reported synthesis tokens only.
  Now captured. Accounting only — queries, models, fallbacks and evidence are
  untouched.
- Every synthesis attempt that executed is recorded with ITS OWN model.
- **One accounting boundary**: the completion callback. `request_id` is
  deterministic per (job, kind, index) and the table already had a unique index,
  so a retried callback, a replay or a retry-save records nothing new.
- Cost is stored at generation time and never repriced.
- `batch_service.pricing()` is neutered (returns None) so there is one price card.
  Its three call sites are untouched and already handled None; `AI_PRICE_*` was
  never set in production, so no displayed number changed.

**Two cost labels, deliberately:**
`Est. AI Cost / 预估 AI 成本` for a fully instrumented run;
`Est. Synthesis Cost / 预估生成成本` for a historical one, with
"Retrieval cost was not captured for this historical run." 红旗 shows $0.0237
under the synthesis label. A historical figure necessarily uses the CURRENT price
row, because none was stored then.

### Section headings were doubling

Every stored section begins with its own title — the engine writes
`## <title>\n\n<body>` so a section is self-contained, which is right. The panel
then added its own title. Measured: 19 of 19 sections, both languages.

Fixed at the DISPLAY layer only; nothing stored was rewritten. `stripLeadingTitle`
removes the first line only when it EQUALS the title after normalising heading
marks, emphasis, whitespace and trailing punctuation in either script. Equality,
never "contains". Stripping happens per language before the bilingual join.

### Verified in production, no paid run

16 checks across English, Chinese and bilingual: no section repeats its heading,
Executive Summary and the following sections carry real content, the saved report
still has its own `## ` headings, and the stored content still begins with
`## Executive Summary` — proof the data was not touched.

### NOT done, deliberately

Workstream 2 (process taxonomy / archetype hypothesis expansion) was rejected as
**NOT WORTH THE ADDED RETRIEVAL** and is not in either repository. Product and
industry classification worked; public retrieval does not reliably expose
account ↔ process ↔ provider relationships. Kept only as documentation.

### Open

1. **No instrumented run has happened yet.** The next Account Research run is the
   first real test of three things at once: retrieval tokens making total usage
   exceed synthesis alone, `stream_diagnostics` saying why SSE does or does not
   engage, and the save-failure path under live conditions.
2. Section publishing is still not progressive — 红旗 published all 19 sections in
   a 1.08s burst after synthesis, the signature of the streaming fallback.
3. Engine auto-deploy on Render is unreliable; always confirm `/healthz`.
4. Language purity, placeholder asymmetry and the bilingual report architecture
   (§0i) remain open.

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

### UI organization pass — 2026-09-02 (presentation only)

Applies to the **standalone engine UI** (`templates/index.html`, `static/style.css`,
`static/app.js`). The CRM copy of this workspace is a separate repository and is
**not present on this machine**, so none of the following is in the CRM yet.

**One report-language control.** The app bar owns the only `[data-langgroup]` on the
page: label, muted helper line and the segmented control grouped in `.langpick`.
The four duplicates are gone — single-company form, batch form, the Reports
sidebar's "Export Language", and the PDF viewer bar. Behaviour is unchanged: one
`localStorage` value, delegated `.langbtn` click handler, no per-language storage.
**Do not reintroduce a second language control.**

**Tabs.** `.mode.active` is now a filled brand-purple pill with white text; inactive
tabs are muted with a brand-soft hover. The active `.cn` half is `#e5dcf3` so it
reads on purple.

**Aligned two-up forms.** Single Company and Batch Setup share `.form-aligned
.form-2up`: a **fixed 17px label row** above a control of `--ctl-h:36px`, two equal
columns from 721px, one column below. The action column has no label, so it carries
an empty 17px `.lblspacer` — that spacer is what makes the Generate button start at
the same y as the inputs. `optional / 可选` is a muted inline badge, not a second
label line. Measured: Company Name and Company Website share one top; Model and
Generate share the next; all four are 36px.

**Three status cards** (`.statcards`) head the Single Company tab: Model /
Connection, Saved Reports, Last Generated. Every value is derived from responses
the page already makes — `/api/config`, `/api/models/health`, `/api/reports`.
**No new endpoint and no AI call.** `renderStatusCards()` re-runs on model change,
on health refresh and after `loadDoneList()`.

**Batch Research** is three labelled areas: Setup (Company List | Model, then
Company Column | Website Column, then the skip-existing option), Generation
(Generate Selected / All / Retry Failed / Stop), and Selection & Management
(Select All / Clear Selection, count, then Delete Selected pushed right).

**Reports** gained its own management: a checkbox per report, Select All / Clear
Selection over the *filtered* rows, a live count, Compile Selected, Delete Selected,
and per-row `View | PDF | ⋯` where ⋯ holds Download PDF, Refresh and Delete.
View opens the saved `research.json`; PDF opens the embedded viewer.

**Bug fixed by this pass:** "Compile Selected" used to compile the **batch table's**
selection, which the Reports tab never displayed — clicking it there compiled
whatever happened to be ticked in Batch Research. It now reads `librarySelection`,
the report library's own set, and reports back beside its own button (`#libout`).

**Selection is synced in place.** `syncLibrarySelectionUI()` toggles the checkbox
and row class without rebuilding the list — same reason the batch table diffs rows
rather than re-rendering. Do not replace it with a `renderLibrary()` call.

**`--appbar-h`.** The tab bar sticks under the app bar, whose height now depends on
the language picker. `syncAppbarHeight()` measures it on load and resize and writes
the CSS variable. The old hard-coded `top:59px` is gone.

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
  language switching and PDF export keep working if model access is ever lost.
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

### Render deployment — prepared, NOT deployed

**I cannot perform the deployment.** There is no Render API key, no `render`
CLI and no dashboard session available here, so creating services and entering
secrets is yours to do. Everything that can be prepared and proven in advance
has been.

**Blocker resolved:** the engine was not in a git repository and Render deploys
from one. It is now initialised and committed locally (`ai_credentials.env`,
`reports/`, `evidence_cache/`, `test_results/` all ignored). **It still needs a
GitHub remote and a push** — that requires your account.

**Pre-flight verified by simulating Render exactly** — credentials file moved
away, configuration from environment variables only, `gunicorn --workers 1
--threads 8`:

| Check | Result |
|---|---|
| Boots with no `ai_credentials.env` | ✓ config source = environment |
| `/healthz` | ✓ 200 |
| Anonymous app page | ✓ 302 → login |
| Anonymous API | ✓ 401 |
| Service-key call | ✓ 200 |
| `frame-ancestors` | ✓ set to the approved origin, no wildcard |
| Language views from a posted record | ✓ 19 sections in en / zh / bilingual |
| `/api/render`, `/api/render-portfolio`, `/api/render-zip` | ✓ 30.8 KB / 29.2 KB / 22.0 KB |

**Blueprint gap found and fixed:** `APP_SERVICE_KEY` was missing from the
engine's `render.yaml`, so a blueprint deploy would have started the engine with
no server-to-server credential and rejected every CRM call.
`ACCOUNT_RESEARCH_TIMEOUT_MS` was likewise missing from the CRM's. Both
blueprints now declare every variable their code reads.

`verify_deployment.sh <engine-url> <crm-url>` runs the full post-deploy
validation read-only, with no model call.

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
  ↓         ASCII-edge matching, legal-suffix stripping, distinctive tokens,
  ↓         narrow per-company collision map.  See §0e.
Deduplicate on canonical URL key (scheme / www. / trailing slash ignored)
  ↓
Split Tier A (accepted on URL + snippet) / Tier B (needs proof)
  ↓
Tier A ──► fetch page text (cached on the candidate, fetched once)
  ↓
Tier B ──► adaptive loop, batches of 10 up to 30, ordered
  ↓         financial → news → other → government (TIER_B_CATEGORY_ORDER):
  ↓             fetch → verify against page text → apply caps
  ↓             → evaluate Policy A on the RETAINED set
  ↓             → stop: sufficient | pool exhausted | ceiling
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
- **Evidence retention, not retrieval, is now the binding constraint.** The low-tier budget drops
  to 2 when 5+ strong sources exist, and Chinese financial hosts are tier 6, so verified
  third-party financial evidence is discarded. Quantified in §0e.

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

### Apollo integration: IMPLEMENTED and LIVE locally (2026-09-03)

`APOLLO_API_KEY` **is present in `ai_credentials.env`** and the live API has been called
successfully — see §0a for the connectivity check. The description below was written while
the key was absent and the network hop was mocked at `apollo_service._request`; the mechanics
it documents are unchanged, only the "never called" status is superseded. **Render still needs
the env var on the engine service.**

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
APOLLO_API_KEY             (optional — PRESENT locally; add to the Render ENGINE service)
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
- [x] DashScope model activation — RESOLVED, all three models available 2026-09-03

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

### RESOLVED 2026-09-03 — was: DashScope models require paid activation
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

**Outcome: access was activated; all three models now probe as available. The text below
records the state that HELD UNTIL 2026-09-03 and how the app degrades if access is lost again.**

**Historic decision: leave the models configured and blocked for now. No further action was required
unless billing / model access is enabled.** Do not spend time trying to repair or replace them.

**How the app behaves meanwhile**
- The model pickers label them `Qwen 3.6 Flash — Unavailable / Requires activation` and disable them.
- Selecting one shows a plain-language message: *"This model is not currently activated for this
  account. Please choose another available model, or enable billing / model access."*
- It is never reported as a retrieval failure, "not enough evidence", or an application error.
- A model proven denied is remembered for the process and is not retried repeatedly.
- **Saved reports, PDFs, language switching, batch history and report viewing all work normally.**

### Evidence caps discard verified third-party sources (OPEN, 2026-09-04)
Severity: **high** — it is the current ceiling on report quality.
`apply_evidence_caps` cuts the low-tier budget to 2 when 5+ strong sources exist; Chinese
financial hosts are tier 6. Manz AG loses 4 verified financial sources, each a new host, new
category and new topics, and can therefore never satisfy Policy A at any fetch budget.
Fully quantified in §0e. Caps deliberately left unchanged pending your decision.

### ACRO Automation Systems is thinly covered at source (OPEN)
Distinct from the caps issue: ACRO discards nothing, but only 2 third-party sources from a single
host verify at all. It runs the full 30 fetches and still fails Policy A. More fetching will not
help; this is a limit of the index, not of the filter.

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

### Best-effort continuation — the governing invariant (2026-09-04)
**A backend-stage failure degrades the report. It does NOT terminate the research
session. partial evidence > no report.** Full statement at the top of `CLAUDE.md`;
it is non-negotiable and applies to all Account Research work.

Every independent capability is attempted even when an earlier one fails: a blocked
official site, an unavailable search wave, a failed financial lookup, missing CRM
contacts, Apollo being down and evidence below sufficiency are all warnings that
continue, never stops.

Evidence sufficiency decides whether to keep retrieving, and the confidence and
warnings on the report. It must never by itself decide whether a report is
generated. Hitting the adaptive ceiling with thin evidence means stop retrieving,
record the limitation, continue to synthesis.

Errors are classified RECOVERABLE or FATAL, defaulting to RECOVERABLE. FATAL is
only for an invalid request with no identifiable company, or total failure of the
synthesis service after fallbacks. Users should not have to click "Research
Anyway" for ordinary evidence limitations; researching anyway is the default.

Premature termination from a recoverable failure is a regression bug.

Status: **principle recorded; pipeline NOT yet compliant.** The audit of violating
branches is in §12.


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

**Amended 2026-09-04.** The principle stands; the *implementation* now works against it. The cap
was written when "low tier" meant an unverified trade portal. Tier B candidates are now fetched
and verified against page text, so a tier-6 host can be proven to be about the company — and the
cap still discards it. Independence of evidence is part of quality, not a trade against it. See
§0e for the four Manz sources this loses and the recommended fix.

### Sufficiency is judged on retained evidence, not on candidates
A candidate that passes verification but is then dropped by the caps must never count toward the
stopping rule. Counting pre-cap candidates makes the pipeline stop early on evidence it will not
keep.

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

**Production parity on both services, and the truncation defect closed.**

| Commit | Repo | What |
|---|---|---|
| `9955236` | engine | Live incremental research output, durable against the job id |
| `5dc764f` | engine | Display-filter truncation fix (§0i) |
| `2027d9c` | CRM | Live Research panel, read from Neon |

Deployed and verified: engine `5dc764f7398c`, CRM `2027d9c`.

**Suites:** bilingual display 31/31, continuation 28/28, live output 29/29,
CRM section API 15/15, headless browser 17/17. No model call was spent on any of
this, and Tesla was never regenerated.

---

## 15. Current Work In Progress

Nothing in flight. Both services are deployed and match their branches.

**Next up, agreed:** Tavily / provider redundancy for retrieval. Deliberately held
until the production baseline was clean, which it now is.

---

## 16. NEXT ACTIONS

1. **Tavily / provider redundancy** — the agreed next retrieval improvement.
2. **Language purity** (§0i, item 1): stop Chinese prose appearing in the English
   view. Two causes, one prompt-side and one in the generated capability table.
3. **Placeholder asymmetry** (§0i, item 2): `is_placeholder` treats the two halves
   of a bullet differently.
4. **Bilingual invariant** (§0i, item 3): the saved report should carry
   independent `content_en` / `content_zh` per section rather than being filtered
   out of one document at render time.
5. Set `CRM_CALLBACK_URL` and `APOLLO_API_KEY` on Render; PowerCo has never run.

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
- Do not add a second report-language control. The app bar owns the only one.
- Do not re-render the whole report library on a checkbox tick; use
  `syncLibrarySelectionUI()`.
- Do not let "Compile Selected" read the batch table again; it is the library's
  own selection.
- Do not hard-code the tab bar's sticky offset; it follows `--appbar-h`.
- Do not give `.liblist` a scroll container again — it clips the per-row ⋯ menu.
- Do not reintroduce `\b` in company-name matching. It cannot fire against CJK and
  silently rejects the entire Chinese-language pool. Use `_mentions()`.
- Do not match company tokens by substring; `"acro"` is inside `"macro"`.
- Do not add a general "reject pages mentioning city X" rule. Use the narrow
  per-company `COLLISIONS` map.
- Do not prioritise government/regulatory sources by category reputation. Measured,
  they verified 0 of 5. Order is `TIER_B_CATEGORY_ORDER`.
- Do not judge evidence sufficiency on verified candidates. It must be the set
  retained after caps, or the pipeline stops on evidence it discards.
- Do not remove the Tier A page-text cache; without it every adaptive batch
  re-downloads the official site.

---

## 18. Session Handoff

**Last successful operation:**
Production validation of the stored Tesla report through the CRM render route.
Supplier list complete in all three languages, PDFs valid, no regeneration.

**Current stopping point:**
The display-filter truncation defect is CLOSED (§0i). Both services are deployed
and match their branches. Nothing is in flight.

**The one thing to know:**
Render's auto-deploy is unreliable on the engine. `9955236` and `c121a60` both
needed manual deploys; `5dc764f` went automatically. Always confirm with
`/healthz`, which reports the serving commit, before validating anything.

**Recommended next command/action:**
Start Tavily / provider redundancy, or take one of the three items §0i leaves
open. Read the top of `CLAUDE.md` first: best-effort continuation, the durability
invariant and the incremental-output requirement all constrain that work.

**Uncommitted changes:** none.

**Application currently runnable:** Yes. `PORT=5062 .venv/bin/python app.py`.
Suites: `test_continuation.py`, `test_live_output.py`, `test_bilingual_display.py`
(the last takes `AR_TESLA_FIXTURE` for the real-report check).

**Known blockers / limitations:**
1. Engine auto-deploy on Render is unreliable; verify `/healthz` every time.
2. Language purity, placeholder asymmetry and the bilingual invariant are open.
3. Live section publishing has not been exercised in production; it needs a real
   run, which is billable. The code is covered locally by 29 checks.
4. `CRM_CALLBACK_URL` and `APOLLO_API_KEY` still unset on Render.

---

## P0-A / P0-B — target integrity and evidence diversity (2026-09-05) — IMPLEMENTED, TESTED, NOT DEPLOYED

Engine commits `599f5ca` (P0-A) and `d34b047` (P0-B). Deliberately isolated.
**Not pushed, not deployed, no paid run.** Awaiting review.

### P0-A `599f5ca` — a validated supplied domain is never silently replaced

Two rules were wrong in opposite directions.

1. Identity was judged over `page_text[:4000]`. A portal that merely covers a
   company passed: pcauto.com.cn first writes 红旗 at char 885. Identity is now
   judged on `identity_region()` -- title, first `IDENTITY_HEAD` (400) chars,
   copyright line.
2. 红旗's own site is served in English and never writes 红旗, so nothing could
   bridge the scripts. `identity_signals(..., supplied=True)` adds
   `domain-self-corroborated`: a domain a PERSON supplied need only show its own
   stem in its own masthead. A DISCOVERED domain gets no such benefit.

`domain-covers-name` (reuses `_domain_covers_name`) and self-corroboration each
add +4, matching the existing `domain~name` weight. Without it hongqi-auto.com
proves its identity and still fails `score >= 5` (it scores 4 on furniture).

`resolve_website` no longer swaps silently: a substitution sets
`supplied_website` + `replaced_supplied`, warns, and files a `limitation`. When
discovery finds nothing the user's domain is kept as **`supplied_unconfirmed`**
(new status) rather than discarded.

> **Measured, do not re-derive.** An earlier candidate rule -- require
> `domain~name` for every discovered domain -- was measured FIRST and rejected:
> it wrongly rejects `te.com` (token under 3 chars) and `dfmc.com.cn` (CJK name,
> acronym domain). Only the identity-region rule is surgical.

Measured over all 41 stored official domains (31 fetchable): **1** loses
discovered-official status (pcauto.com.cn for 红旗, the bug), **0** supplied
domains regress, 2 gain it (imautomation.com, comau.com). Both benign historical
corrections still occur (bwm.com and vw.com are still rejected and corrected).

### P0-B `d34b047` — one publisher cannot consume the evidence set

`apply_evidence_caps` sorted by tier and took the first 16. Anything on the
resolved domain *including subdomains* is tier 1/2, and there was **no**
per-domain ceiling anywhere. Third-party evidence was dropped as `evidence cap`
before the low-tier budget was consulted.

Now two passes: **pass 1** admits in the same tier order but lets no single
registrable domain exceed `DIVERSITY_PER_DOMAIN`; **pass 2** backfills leftover
capacity from deferred items. A single-domain account backfills to an identical
result, so this is never a quota that can fail a run.

`DIVERSITY_PER_DOMAIN = MAX_SITE_PAGES` (8) is **anchored, not picked**: the
official crawl contributes at most 8 pages, so the whole crawl still lands in
pass 1 and only search results piling onto the same domain wait. Caps of 4-6
were measured and disturb the healthy population without reaching more
concentrated reports.

New `registrable_domain()` is the shared boundary. `evidence_sufficiency` had
the same hostname bug -- three pcauto subdomains counted as three independent
hosts -- and now uses it. That makes sufficiency HARDER to reach, i.e. more
retrieval, never fewer reports. It remains a dial (only `suff["enough"]` breaks
the retrieval loop; nothing gates synthesis).

Retained items are renumbered in tier order after both passes, so selection
changed but presentation did not.

**Historical impact** from stored `quality.candidates`: for 7 reports the cap was
binding AND the pool held far more than was retained -- apple (16 from one
domain, pool 105), 东风 (119), 红旗 (106), BMW (77), CALB (52), BYD (44),
Eclipse Automation (22). Pool *composition* is not recoverable because rejected
candidates are not persisted. That is exactly the P1 work.

### Tests

`test_target_domain.py` (43) and `test_evidence_diversity.py` (36), both
network-free. 红旗 is covered but NOT special-cased: the same asserts run over
Cyrillic and Japanese fixtures. All six engine suites green: 28 / 48 / 29 / 25 /
43 / 36.

