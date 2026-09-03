# SKEQI Account Research — v2 (evidence-first retrieval)

Run 2026-09-02. Same three models, same company, **new retrieval architecture**.
v1 (`../skeqi/`) sent the whole research instruction to the search backend.
Code: `research/`. Raw: `<model>.json` / `.md`, `_evidence.json`, `_evidence_merged.json`.

## Pipeline

```
company profile -> 6 short queries -> web search (x6) -> disambiguation
   -> dedupe + tier ranking -> 3-tier page extraction -> evidence set
   -> closed-book synthesis -> cited claims
```

Search is **on** during retrieval and **off** during synthesis, so the model cannot
produce a claim and then look for support: it only ever sees the evidence set.

| Stage | v1 | v2 |
|---|---|---|
| Search queries | 1 (the ~1900-char instruction) | 6 short queries, template-generated |
| Raw results | 1 | **46** |
| Rejected as wrong entity | 0 (Skechers reached the model) | **20** |
| Evidence items with real page text | 0 | **9** |
| Official website | unreachable (SPA) | **retrieved** via script-bundle tier |

### Components
- `queryPlanner.js` — deterministic, no LLM. Instruction ≠ query.
- `searchClient.js` — Bailian has no standalone search API, so the cheapest model is used
  purely as a retrieval vehicle with `max_tokens: 48`; the prose is discarded, the
  `search_results` kept.
- `disambiguate.js` — rejects a result carrying an unrelated-entity signal
  (skechers/斯凯奇/运动鞋…) unless 思客琦 also appears. The Latin name alone is never
  sufficient — that is the collision. **All 4 Skechers results were rejected.**
- `rank.js` — 6 tiers (official site → product pages → partner refs → gov/established
  media → industry pubs → other). Job boards and registries are matched *before* the
  gov/media list, because many live on `.edu.cn` hosts and were otherwise promoted to
  tier 4. Tier-6 items are capped at 4 so job ads cannot crowd out real sources.
- `fetchPage.js` — standard fetch → script-bundle extraction → headless Chrome, escalating
  only on insufficient text. Also decodes GB2312/GBK (Chinese media served mojibake as UTF-8).
- `pipeline.js` — per-tier character budgets (official 5000, job board 900), post-fetch
  re-validation on full text, and `verifyEntity()` for named relationships.

**On the JS-rendered site:** `www.skeqi.com` ships a 1.35 KB shell. Tier 2 (script-bundle)
recovers 2102 chars — the whole company profile — without launching a browser. Chrome
tier 3 did fire for `qcc.com` registry pages, so all three tiers are exercised in this run.

## The three previously-missed areas

| Area | Ground truth | v1 (all 3 models) | qwen3.6-flash | deepseek-v4-pro | deepseek-v4-flash-0731 |
|---|---|---|---|---|---|
| Intelligent logistics | CONFIRMED | **all missed** | CONFIRMED [1][9] | CONFIRMED [1] | CONFIRMED [1][9] |
| Industrial X-ray / CT | PARTIAL (2D/3D X-ray yes; CT never stated) | **all missed** | CONFIRMED — over-claims CT | NOT ENOUGH EVIDENCE — *"CT not explicitly stated"* | **LIKELY** — *"未直接出现CT字样"* |
| Precision manufacturing | CONFIRMED | **all missed** | CONFIRMED [1][2][9] | CONFIRMED [1] | CONFIRMED [1] |

All three now find logistics and precision manufacturing. On X-ray/CT — the one item
where ground truth is genuinely partial — `deepseek-v4-flash-0731` is best calibrated
(LIKELY, with the reason), `deepseek-v4-pro` is defensibly strict, `qwen3.6-flash`
over-claims by folding CT into a confirmed verdict.

## Siemens — verified separately

`verifyEntity()` ran its own queries and required **both** 思客琦 and the entity on the
same page. **CONFIRMED**, two independent sources:
- https://www.jfdaily.com.cn/wx/detail.do?id=424816 (解放日报)
- https://www.chinaaet.com/article/3000140882

Substance: strategic cooperation agreement signed at the 4th CIIE; Siemens MCD software
cut project delivery time by ~30%. **v1 found no Siemens evidence at all.** All three
models picked it up in v2 and cited it correctly.

## Model comparison

| Metric | qwen3.6-flash | deepseek-v4-pro | deepseek-v4-flash-0731 |
|---|---|---|---|
| Product coverage | 11/11 verdicts, 1 over-claim (CT) | 10 CONFIRMED + 1 correctly withheld | 10 CONFIRMED + 1 LIKELY (best calibrated) |
| Customer/partner coverage | 9+ named, first/third-party split | **16 named**, first/third-party split | 15+ named, explicit source-nature note |
| Accuracy vs ground truth | high, one over-claim | **highest** | high |
| Source quality used | tiers 1,2,5,9 | tiers 1,2,5,8,9 | tiers 1,2,4–9 (broadest) |
| **Citation correctness** (quoted text present in cited item) | 21/23 = **91%** | 12/12 = **100%** | 22/22 = **100%** |
| Invalid citation ids | none | none | none |
| Research completeness | high | moderate (terse, 4076 chars) | **highest** (8822 chars) |
| Latency | **44.8 s** | 86.9 s | 182.7 s |
| Tokens in / out / total | 6840 / 7380 / 14220 | 6974 / 5222 / **12196** | 7053 / 18228 / **25281** |

Plus a shared retrieval cost of **25 252 tokens** for 6 search calls (search results are
injected into each retrieval call's input, which dominates that figure).

### Scores (1–10)

| Criterion | qwen3.6-flash | deepseek-v4-pro | deepseek-v4-flash-0731 |
|---|---|---|---|
| Accuracy | 8 | 9 | 9 |
| Research depth | 8 | 7 | 9 |
| Source quality | 8 | 8 | 9 |
| Citation correctness | 8 | 10 | 10 |
| Business understanding | 8 | 8 | 9 |
| Speed | 9 | 6 | 3 |
| Cost efficiency | 7 | 9 | 4 |
| **Mean** | **8.0** | **8.1** | **7.6** |

v1 means were 7.6 / 6.3 / 6.3 — the whole field moved up, and the spread narrowed.
**That is the point: the retrieval architecture, not the model, was the binding constraint.**

## Recommendation for Account Research

- **Primary: `deepseek-v4-pro`.** 100% citation correctness, most named customers,
  correctly withholds a verdict when evidence is partial, lowest synthesis token count.
- **Fast tier: `qwen3.6-flash`.** 2× faster, 8.0 mean; use when latency matters and accept
  occasional over-claiming — pair with a verdict-strictness check.
- **Fallback: `deepseek-v4-flash-0731`.** Deepest and perfectly cited, but 182 s and 25 k
  tokens make it a poor default. Good for a one-off deep dive on a priority account.
- Keep `deepseek-v4-flash-0731` as the **retrieval driver** (`searchClient.js`) — capped at
  48 output tokens, its speed matters and its prose is discarded.

## Known limits before integration

1. Some high-value hosts block automation (`baike.baidu.com` returns a verification page;
   `qcc.com` needs the Chrome tier). Budget for partial coverage.
2. Search results vary run to run — the Siemens article surfaced from a different query in
   each run. `verifyEntity()` exists precisely to make named relationships deterministic.
3. Retrieval costs ~25 k tokens per company. Cache the evidence set per company; do not
   re-retrieve on every synthesis.
4. Official-site coverage is the homepage only. Per-section subpage crawling would raise
   product-portfolio precision further.
5. Customer claims from tier-1 remain **first-party assertions**. Only Siemens and the CATL
   award currently have third-party corroboration.
