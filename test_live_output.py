#!/usr/bin/env python
"""Regression tests for durable incremental research output (see CLAUDE.md).

Long-running research must show meaningful partial results before it finishes,
and that output must survive leaving the page. These cover the engine half:
splitting a bilingual report into sections, publishing them as they are written,
and never writing once per token.

No network, no model call, no cost.  .venv/bin/python test_live_output.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import research_service as rs                                    # noqa: E402
import app                                                       # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  {} {}{}".format("PASS" if cond else "FAIL", name,
                             "" if cond or not detail else "  <- " + detail))


# The real report shape: bilingual blocks under each heading, exactly as the
# synthesis prompt specifies. language_view splits on these markers.
REPORT = """## Executive Summary / 执行摘要
**English:**
Tesla builds electric vehicles.

**中文：**
特斯拉制造电动汽车。

## Company Overview / 公司概况
**English:**
Founded in 2003.

**中文：**
成立于2003年。

## Manufacturing Challenges / 制造挑战
**English:**
Ramp rates remain the constraint.

**中文：**
产能爬坡仍是瓶颈。
"""

print("\n[A] Splitting a bilingual report into sections\n")
secs = rs.split_report_sections(REPORT)
check("every section is found", len(secs) == 3, "got %d" % len(secs))
check("keys are stable slugs",
      [s["key"] for s in secs] == ["executive-summary", "company-overview",
                                   "manufacturing-challenges"],
      str([s["key"] for s in secs]))
check("English and Chinese titles are split",
      secs[0]["title_en"] == "Executive Summary" and secs[0]["title_zh"] == "执行摘要")
check("order is preserved", [s["position"] for s in secs] == [0, 1, 2])
check("bodies carry the text", all(s["body"] for s in secs))
check("a heading-less string yields nothing", rs.split_report_sections("no headings") == [])
check("empty input is safe", rs.split_report_sections("") == [])

print("\n[B] Sections publish AS THEY ARE WRITTEN, not at the end\n")
rows = []
st = rs.SectionStreamer(rows.extend, flush_seconds=0)
half = REPORT.index("## Manufacturing")
st.feed(REPORT[:half])
first = [(r["section_key"], r["status"]) for r in rows]
check("a finished section is published before the run ends",
      ("executive-summary", "complete") in first, str(first))
check("the section still being written is published as partial",
      any(k == "company-overview" and s == "partial" for k, s in first), str(first))
check("a section not yet started is NOT published",
      not any(k == "manufacturing-challenges" for k, _ in first), str(first))

rows.clear()
st.finish(REPORT)
final = {r["section_key"]: r["status"] for r in rows}
check("every section is complete at the end",
      set(final) == {"executive-summary", "company-overview", "manufacturing-challenges"}
      and set(final.values()) == {"complete"}, str(final))

print("\n[C] Writes are buffered — never one per token\n")
rows.clear()
st2 = rs.SectionStreamer(rows.extend, flush_seconds=999)   # no partial flush in this window
for i in range(40, len(REPORT), 5):                        # 200+ token-sized feeds
    st2.feed(REPORT[:i])
check("a partial is not written on every chunk", len(rows) <= 3, "wrote %d rows" % len(rows))
check("finished sections still published promptly",
      any(r["section_key"] == "executive-summary" for r in rows), str(len(rows)))

print("\n[D] Language views are stored per section\n")
rows.clear()
rs.SectionStreamer(rows.extend, flush_seconds=0).finish(REPORT)
one = rows[0]
check("an English view is stored", "Executive Summary" in (one["content_en"] or ""))
check("a Chinese view is stored", "执行摘要" in (one["content_zh"] or ""))
check("the English view drops the Chinese line",
      "特斯拉制造电动汽车" not in (one["content_en"] or ""), one["content_en"][:60])
check("the Chinese view drops the English line",
      "Tesla builds electric vehicles" not in (one["content_zh"] or ""), one["content_zh"][:60])

print("\n[E] Retrieval evidence is published before synthesis\n")
pkg = {"evidence": [{"url": "https://reuters.com/a", "title": "Reuters on Tesla"},
                    {"url": "https://www.tesla.com/ir", "title": "Tesla IR"}],
       "website": "https://www.tesla.com",
       "quality": {"limitations": [{"message": "Official website blocked automated access"}]}}
ev = app.evidence_sections(pkg)[0]
check("evidence is one complete section", ev["status"] == "complete")
check("it sorts before the report body", ev["position"] < 0)
check("it counts the sources", "2 source(s)" in ev["content_en"], ev["content_en"][:60])
check("it separates third-party sources",
      "1 independent third-party" in ev["content_en"], ev["content_en"][:120])
check("it lists what was found", "Reuters on Tesla" in ev["content_en"])
check("it carries limitations", "blocked automated access" in ev["content_en"])
check("it is bilingual", "已收集证据" in ev["content_zh"])
check("it is labelled evidence, not conclusions",
      ev["section_title_en"] == "Research Evidence")

print("\n[F] Streaming is never load-bearing\n")
check("synthesize accepts an on_section callback",
      "on_section" in rs.synthesize.__code__.co_varnames)
check("the fallback path threads it through",
      "on_section" in rs.synthesize_with_fallback.__code__.co_varnames)
check("a streaming helper exists", callable(rs.post_json_stream))
src = open("research_service.py", encoding="utf-8").read()
i = src.index("def synthesize(")
check("a streaming failure falls back to the blocking request",
      "post_json_stream" in src[i:i + 3000] and "return post_json(url, payload" in src[i:i + 3000])

print("\n{} passed, {} failed".format(len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
