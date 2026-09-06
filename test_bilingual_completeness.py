# -*- coding: utf-8 -*-
"""Either language on its own has to be a complete report.

A production run wrote `2. 表格：(同上英文表格)` where a Chinese table belonged -
"same as the English table above". The English reader sees a complete report and
never learns that the Chinese one is hollow. Nothing in the codebase writes that
phrase, and it appeared on one run out of three, so it is the model choosing
brevity over completeness.

The prompt now forbids it. This suite covers the check that catches it when the
prompt does not, and pins the contract text so a future edit cannot quietly drop
it.

The check is structural on purpose: does a table exist on both sides, do the row
counts agree, does either block point at the other. Whether the translation is
GOOD is the model's job, and this suite does not pretend otherwise.

No network, no model call.  .venv/bin/python test_bilingual_completeness.py
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import app                                                        # noqa: E402
import bilingual_check as bc                                      # noqa: E402
import research_service as rs                                     # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (" - " + detail if detail else ""))


def report(en, zh, heading="Existing Automation Providers / 现有自动化供应商"):
    return "## {}\n**English:**\n{}\n\n**中文：**\n{}\n".format(heading, en, zh)


EN_TABLE = """1. MANUFACTURING MODEL: internal.
2. TABLE:
| Process | Provider | Where it sits | Evidence | Confidence |
|---|---|---|---|---|
| Laser welding | AMADA WELD TECH (in-house) | Internal | [1] | Verified |
| Process monitoring | AMADA WELD TECH (in-house) | Internal | [2] | Verified |
| Machine vision | Third party assumed | Partner | [6] | Likely |
3. OPPORTUNITY: laser energy control."""

ZH_TABLE = """1. 制造模式：内部。
2. 表格：
| 工艺 | 供应商 | 归属位置 | 证据 | 置信度 |
|---|---|---|---|---|
| 激光焊接 | AMADA WELD TECH（内部） | 内部 | [1] | 已验证 |
| 过程监控 | AMADA WELD TECH（内部） | 内部 | [2] | 已验证 |
| 机器视觉 | 推测第三方 | 合作伙伴 | [6] | 可能 |
3. 机会：激光能量控制。"""

print("\n[1] The case that started this")
collapsed = report(EN_TABLE, "1. 制造模式：内部。\n2. 表格：(同上英文表格)\n3. 机会：激光能量控制。")
f = bc.check(collapsed)
check("a collapsed Chinese table is caught", len(f) >= 1, str(len(f)))
check("the cross-reference is named",
      any(x["reason"] == "chinese_cross_reference" for x in f))
check("and so is the missing table",
      any(x["reason"] == "missing_chinese_table" for x in f))
check("the finding identifies the section",
      all("Existing Automation Providers" in x["section"] for x in f))

print("\n[2] Complete on both sides is silent")
f = bc.check(report(EN_TABLE, ZH_TABLE))
check("a fully translated table produces no warning", f == [], str(f))
prose = report("1. The account designs its own equipment [1].\n2. No supplier is named.",
               "1. 该客户自行设计设备 [1]。\n2. 未提及供应商。")
check("ordinary bilingual prose produces no warning", bc.check(prose) == [], str(bc.check(prose)))
check("an empty report produces no warning", bc.check("") == [])
check("a section with no language blocks is skipped",
      bc.check("## Sources / 信息来源\n- https://example.com\n") == [])

print("\n[3] A table on one side only")
check("English table, no Chinese table",
      any(x["reason"] == "missing_chinese_table"
          for x in bc.check(report(EN_TABLE, "1. 制造模式：内部。\n2. 机会：激光能量控制。"))))
check("Chinese table, no English table",
      any(x["reason"] == "missing_english_table"
          for x in bc.check(report("1. Internal manufacturing.\n2. Opportunity.", ZH_TABLE))))

print("\n[4] Every cross-reference variant")
for variant in ("表格同上", "同上", "见英文版", "见英文部分", "与英文相同",
                "同上英文表格", "详见英文", "同英文"):
    body = "1. 制造模式：内部。\n2. 表格：{}\n".format(variant)
    f = bc.check(report(EN_TABLE, body))
    check("caught: %s" % variant,
          any(x["reason"] == "chinese_cross_reference" for x in f))
en_ref = report("1. Internal.\n2. TABLE: same as above.", ZH_TABLE)
check("an English block pointing at the Chinese one is caught",
      any(x["reason"] == "english_cross_reference" for x in bc.check(en_ref)))

print("\n[5] A reference inside one language is not a cross-language pointer")
# "see above" pointing at an earlier paragraph in the SAME block is ordinary
# prose. It only counts when this block is the one missing the other's table.
within = report(EN_TABLE + "\n4. See above for the incumbency detail.",
                ZH_TABLE + "\n4. 详见上文的在位供应商说明。")
f = bc.check(within)
check("'see above' beside a complete English table is not flagged",
      not any(x["reason"] == "english_cross_reference" for x in f), str(f))
check("and the Chinese equivalent is not either", f == [], str(f))

print("\n[6] Row counts")
short_zh = """1. 制造模式：内部。
2. 表格：
| 工艺 | 供应商 | 归属位置 | 证据 | 置信度 |
|---|---|---|---|---|
| 激光焊接 | AMADA WELD TECH（内部） | 内部 | [1] | 已验证 |
3. 机会：激光能量控制。"""
f = bc.check(report(EN_TABLE, short_zh))
check("a Chinese table missing rows is caught",
      any(x["reason"] == "table_row_mismatch" for x in f), str(f))
check("and the counts are in the detail",
      any("3 rows" in x["detail"] and "1" in x["detail"]
          for x in f if x["reason"] == "table_row_mismatch"))
one_off = ZH_TABLE + "\n| 备件 | 未披露 | 未知 | [7] | 可能 |"
check("one row of difference is tolerated",
      not any(x["reason"] == "table_row_mismatch"
              for x in bc.check(report(EN_TABLE, one_off))),
      "headers and separators vary between markdown and plain text")
plain = """2. 表格：
   工艺 | 供应商 | 证据
   激光焊接 | 内部 | [1]
   过程监控 | 内部 | [2]
   机器视觉 | 第三方 | [6]"""
check("a bare pipe table counts as a table", bc.has_table(plain))
check("and its rows are counted", bc.table_rows(plain) == 3, str(bc.table_rows(plain)))
check("a separator rule is not a row",
      bc.table_rows("| a | b |\n|---|---|\n| 1 | 2 |") == 1)
check("prose with one pipe is not a table",
      not bc.has_table("Confidence is Verified / 已验证 for this row."))

print("\n[7] It reports, and only reports")
SRC = io.open(os.path.join(HERE, "bilingual_check.py"), encoding="utf-8").read()
for forbidden in ("translate(", "def translate", "copy_table", "requests", "urllib",
                  "post_json", "ask_model"):
    check("the check does not %s" % forbidden.rstrip("("), forbidden not in SRC)
check("it imports nothing from the pipeline",
      "import research_service" not in SRC and "import app" not in SRC)
check("it returns findings rather than a report", isinstance(bc.check(collapsed), list))
check("the report itself is never modified", (lambda r: (bc.check(r), r)[1] == collapsed)(collapsed))

print("\n[8] The run survives it")
APP = io.open(os.path.join(HERE, "app.py"), encoding="utf-8").read()
region = APP[APP.index('ok = run["status"] == 200'):APP.index("saved = save_run")]
check("the check runs before the report is saved", "bc.check(" in region)
check("it is wrapped, so a broken check cannot fail a run", "except Exception" in region)
check("findings become WARN lines the run already publishes", "bc.warnings(" in region)
check("nothing in the path can stop persistence",
      "return" not in region.split("bc.check(")[1].split("saved =")[0],
      "no early exit between the check and the save")
check("a failing check is logged, not swallowed", "_log_bilingual_failure" in APP)
bad = bc.check(collapsed)
check("warning lines name the section and the reason",
      all(w.startswith("WARN Bilingual completeness") for w in bc.warnings(bad))
      and any("Existing Automation Providers" in w for w in bc.warnings(bad)))

print("\n[8b] It is a diagnostic, never a gate")
# Six things a warning must not do. The check reports; nothing downstream reads
# its result to decide anything.
_after = APP[APP.index("bilingual = bc.check"):]
_upto_save = APP[APP.index("bilingual = []"):APP.index("saved = save_run")]
check("no terminal status is derived from it",
      not any(w in _upto_save for w in ('status="', "outcome=", "synthesis_failed")),
      "the run's outcome is decided by synthesis, not by translation structure")
check("it cannot suppress the callback",
      "notify_crm" not in _upto_save)
check("it cannot suppress persistence",
      "save_run" not in _upto_save.replace("saved = save_run", ""))
check("it triggers no second synthesis",
      "synthesize" not in _upto_save and "synthesize_with_fallback" not in _upto_save)
check("it triggers no retrieval",
      not any(w in _upto_save for w in ("build_shared_evidence", "search(", "fetch_page_text")))
check("it never writes back into the report",
      'run["report"] =' not in APP and "run.update(report" not in APP)
check("accounting is untouched by it",
      "ai_attempts" not in _upto_save and "ai_usage" not in _upto_save)
_facts = APP[APP.index("def execution_facts"):APP.index("def _payload_facts")]
check("the manifest reads the findings, it does not act on them",
      "bc.summary(bilingual)" in _facts and "if bilingual" not in _facts)
check("findings are a list the caller may ignore",
      isinstance(bc.check(collapsed), list) and bc.check(collapsed) is not None)
check("a check that raises cannot reach the caller",
      "except Exception" in _upto_save and "_log_bilingual_failure" in _upto_save)


def _boom(_r):
    raise RuntimeError("check exploded")


_real = bc.check
try:
    bc.check = _boom
    # The same guard the run uses: a broken check must leave `bilingual` empty
    # and let everything downstream proceed.
    findings = []
    try:
        findings = bc.check("anything")
    except Exception:
        findings = []
    check("a broken check yields no findings rather than an error", findings == [])
finally:
    bc.check = _real
check("and the summary of nothing is still well formed",
      app.execution_facts({"evidence": [], "ai_usage": []}, [], None,
                          bilingual=[])["bilingual"]["warnings"] == 0)

print("\n[9] The manifest carries counts, never content")
summary = app.execution_facts({"evidence": [], "ai_usage": []}, [], None,
                              bilingual=bad)["bilingual"]
check("it is checked", summary["checked"] is True)
check("the count is right", summary["warnings"] == len(bad), str(summary))
check("sections are named", summary["sections"] and
      all("Providers" in s or "Competitor" in s for s in summary["sections"]))
check("reasons are named", "missing_chinese_table" in summary["reasons"])
check("no report text reaches the manifest",
      not any(isinstance(v, str) and len(v) > 120 for v in summary.values())
      and not any(len(x) > 120 for x in summary["sections"]))
check("a clean run still records that it was checked",
      app.execution_facts({"evidence": [], "ai_usage": []}, [], None)["bilingual"]
      == {"checked": True, "warnings": 0, "sections": [], "reasons": []})

print("\n[10] The contract is pinned in the prompt")
INSTR = rs.INSTRUCTION
check("independent completeness is stated", "INDEPENDENT COMPLETENESS" in INSTR)
check("the deletion test is stated",
      "If either language were deleted, the other must still be a COMPLETE" in INSTR)
check("both directions are required",
      "each block reproduces every substantive thing the other says" in INSTR,
      "one clause, symmetric, rather than the same rule written twice")
check("rows, fields, citations, confidence and conclusions are enumerated",
      all(w in INSTR for w in ("rows,", "logical fields, evidence citations [n]",
                               "confidence values, qualifications, warnings and",
                               "conclusions all appear in BOTH")))
check("adaptation is permitted and omission is not",
      "Wording may adapt naturally; nothing substantive may be dropped." in INSTR)
check("tables are called out explicitly", "TABLES." in INSTR)
for phrase in ("表格同上", "同上", "见英文版", "见英文部分", "与英文相同",
               "same as above", "see above", "same table as above"):
    check("the prompt forbids %s" % phrase.replace("\n", " "), phrase in INSTR)
check("concision is scoped to one language",
      "Be concise WITHIN each language block" in INSTR)
check("and cannot be read as licence to omit",
      "never whether you say it at all in the\nother one." in INSTR)

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
