# SKEQI / 思客琦 — three-model Account Research trial

Run 2026-09-02 · DashScope native · `enable_search=true` · `turbo` · `enable_source` + `enable_citation`
Identical prompt to all three models. Not a benchmark rerun.
Raw outputs: `<model>.json` / `<model>.md`. Ground truth: `_ground_truth.md`.

## The headline finding is about retrieval, not the models

All three models received **exactly one source** — a Dalian Polytechnic University
job-board profile page — and none reached `www.skeqi.com`. Two causes, both fixable:

1. **The long prompt is used verbatim as the search query.** Probe (`_retrieval_probe.js`):

   | Query | Chars | Sources |
   |---|---|---|
   | full research prompt | ~1900 | **1** |
   | `思客琦 智能装备 公司简介 产品 客户` | 19 | **9** |
   | `SKEQI 思客琦 skeqi.com company products customers` | 46 | **7** |
   | `思客琦 官网 skeqi.com 新能源智能装备 模组 PACK 整线` | 35 | **9** |

   Short queries also returned far better sources: 百度百科, 天眼查, 企查查,
   a 腾讯网 digital-transformation case study, a 搜狐 report on a **Siemens strategic
   partnership**, and a 央广网 investigative piece questioning SKEQI's supplier
   relationships and subsidiary headcount ahead of its IPO. None of the three models
   saw any of this.

2. **`www.skeqi.com` is a JS-rendered SPA.** The served HTML is a 1.35 KB shell; all
   content sits in a CDN JS bundle. The crawler cannot read it, so the official site
   is effectively invisible to this pipeline. Any "official website" requirement must
   be met by fetching and rendering the bundle separately.

3. **Name collision.** The backend also injected **Skechers** (footwear) content.
   `deepseek-v4-flash-0731` and `qwen3.6-flash` both explicitly detected and rejected it;
   `deepseek-v4-pro` did not mention it.

**Source-verification caveat:** the single cited URL, when fetched directly today, contains
only basic registration data — not the rich profile all three models quoted. The quoted
boilerplate does match SKEQI's official 关于我们 text verbatim, so the injected snippet was
genuine, but **the citation cannot be verified at the URL given**. Claims resting solely on
it (GGII #1 2023 share, Busbar first-set award, IPO 过会, four production bases, and the
customers beyond those on the official site) remain unverified.

## Comparison

| Model | Completeness | Accuracy | Product ID | Customer ID | Source quality | Citation quality | Latency | In | Out | Total | Relative cost |
|---|---|---|---|---|---|---|---|---|---|---|---|
| deepseek-v4-pro | Moderate — 5 of 11 confirmed, 2 outright missed | High; no fabrication, states limits plainly | 5 CONFIRMED, 2 LIKELY, 4 under-called | CATL + Hithium/Jinko/GoodWe | 1 source (backend-limited) | `[1]` on most claims | 65.6 s | 2915 | 3894 | 6809 | 1.00× |
| deepseek-v4-flash-0731 | **Highest** — IPO, 4 bases, awards, 11 customers | Good, but all detail rests on one unverifiable source | 5 CONFIRMED, 2 LIKELY, 4 missed | **11 named**, split by sector | 1 source | `[1]` per table row + contamination note | 63.2 s | 2994 | 6536 | 9530 | 1.40× |
| qwen3.6-flash | High, best-structured | Good; one inference over-called as CONFIRMED (MES) | 6 CONFIRMED, 2 LIKELY, 3 missed | **11 named**, categorised, B2B framing | 1 source | `[1]` throughout + prominent contamination note | **32.6 s** | 2941 | 4655 | 7596 | 1.12× |

Estimated cost: **not computable** — no price-per-token data exists in this project.
The relative column is a total-token index. Confirm against Bailian billing before use.

Against ground truth (10 CONFIRMED + 1 PARTIAL), every model **under-reported**: all three
missed intelligent logistics, industrial X-ray, and precision manufacturing, which are named
business segments on the official site. Nobody over-claimed CT. The under-reporting is a
direct consequence of the 1-source retrieval, not model weakness.

## Scores (1–10)

| Criterion | deepseek-v4-pro | deepseek-v4-flash-0731 | qwen3.6-flash |
|---|---|---|---|
| Accuracy | 8 | 7 | 8 |
| Research depth | 6 | 8 | 8 |
| Source quality | 3 | 3 | 3 |
| Citation usefulness | 7 | 8 | 8 |
| Business understanding | 7 | 8 | 9 |
| Speed | 5 | 5 | 9 |
| Cost efficiency | 8 | 5 | 8 |
| **Mean** | **6.3** | **6.3** | **7.6** |

Source quality is identical by construction — one shared retrieval backend, same single
result. It scores the pipeline, not the model.

## Recommendations

- **Best for Account Research: `qwen3.6-flash`.** Fastest by ~2×, best-structured output,
  honoured the English instruction, caught the Skechers contamination, and made the most
  correct product calls. Requires the `multimodal-generation` endpoint (already routed).
- **Best fast / low-cost: `qwen3.6-flash`** — it is both. If a *second* cheap option is
  needed, `deepseek-v4-pro` used the fewest output tokens (3894).
- **Best fallback: `deepseek-v4-pro`.** Different model family, most conservative, never
  fabricated, and flags its own limits — the right behaviour when retrieval degrades.
- **Avoid `deepseek-v4-flash-0731` as primary**: richest output but 1.40× the tokens, 2× the
  latency of qwen3.6-flash, and it replied in Chinese to an English prompt.

## Before integrating into Account Research

1. Build a **short keyword search query** separately from the long analysis prompt. This is
   worth more than any model swap: 1 source → 7–9.
2. **Fetch the official site directly** (render or read the CDN bundle); do not expect the
   search backend to reach an SPA.
3. **Disambiguate the company name** — pin 思客琦 / skeqi.com to suppress Skechers.
4. **Verify cited URLs** before presenting a claim; one citation here did not support its content.
5. Consider two-stage: cheap retrieval + `qwen3.6-flash` synthesis, escalating to
   `deepseek-v4-pro` when sources conflict.
