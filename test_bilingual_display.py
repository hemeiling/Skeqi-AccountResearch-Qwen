#!/usr/bin/env python
"""Regression tests for the bilingual display filter.

The bug: after a "not enough evidence" badge was stripped, a trailing-label rule
with no left anchor deleted whichever 1-14 characters preceded the colon. Chinese
has no spaces to stop it, so it ate real words mid-sentence:

    …（西门子、ABB、先导、海目星、库卡、发那科等）：   ->   …（西门子、ABB、先
    Divisions/products/services:                    ->   Divisions/pro

28 of the 37 badge-carrying lines in the Tesla report were damaged. The filter
runs at RENDER time, so this corrupted every markdown view and every PDF while
the stored report stayed intact.

No network, no model call.  .venv/bin/python test_bilingual_display.py
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import confidence as conf                                        # noqa: E402
import language_view as lv                                       # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  {} {}{}".format("PASS" if cond else "FAIL", name,
                             "" if cond or not detail else "  <- " + detail))


def strip(line):
    return conf._strip_unsupported_tag(conf.normalize_text(line))


BADGE = "**Not enough evidence / 证据不足**"

print("\n[A] Chinese has no spaces - the filter must not eat words\n")

ZH = "- 设备/自动化供应商公开证据（西门子、ABB、先导、海目星、库卡、发那科等）：证据不足。"
out = strip(ZH)
check("the supplier list survives in full",
      "西门子、ABB、先导、海目星、库卡、发那科等" in out, out)
check("it is not cut mid-word at 先", not out.rstrip().endswith("先"), out)
check("the badge itself is gone", "证据不足" not in out.replace("证据不足）", ""), out)

for zh, keep in [
    ("- 地点/时间表/产能/投资价值及对思客琦的意义：证据不足。", "投资价值及对思客琦的意义"),
    ("- 碳中和/工厂能效/可再生能源/减废/回收/水资源/材料利用率：证据不足。", "材料利用率"),
    ("- 制造/电池/储能/建厂扩产/自动化/合作/高管变动动态：证据不足。", "高管变动动态"),
]:
    check("kept: %s" % keep, keep in strip(zh), strip(zh))

print("\n[B] English labels, including slash- and space-separated ones\n")

check("slash-separated label survives",
      "Divisions/products/services" in strip("- Divisions/products/services: 证据不足."),
      strip("- Divisions/products/services: 证据不足."))
check("multi-word label survives",
      "Major upcoming projects" in strip("- Major upcoming projects: 证据不足."),
      strip("- Major upcoming projects: 证据不足."))
check("a parenthesised list is not cut at 'etc.'",
      "KUKA, FANUC, etc" in strip(
          "- Suppliers (Siemens, ABB, Lead Intelligent, Hymson, KUKA, FANUC, etc.): 证据不足."),
      strip("- Suppliers (Siemens, ABB, Lead Intelligent, Hymson, KUKA, FANUC, etc.): 证据不足."))
check("a bilingual label survives",
      "Weaknesses" in strip("- Weaknesses / 劣势: 证据不足."),
      strip("- Weaknesses / 劣势: 证据不足."))

print("\n[C] Genuinely empty filler labels are still removed\n")

check("a short bare label is dropped entirely", strip("- Expansion: 证据不足.") == "",
      repr(strip("- Expansion: 证据不足.")))
check("no orphan bullet is left behind",
      strip("- Expansion: 证据不足.").strip() not in ("-", "*", "+"))
check("a numbered bare label is dropped too", strip("1. Revenue: 证据不足.") == "",
      repr(strip("1. Revenue: 证据不足.")))

print("\n[D] Badge removal inside otherwise-valid sentences\n")

line = "- Expansion: %s / %s. Historical growth is documented [2]." % (BADGE, BADGE)
out = strip(line)
check("the surviving sentence is kept", "Historical growth is documented [2]." in out, out)
check("the label in front of it is kept", out.startswith("- Expansion:"), out)
check("no badge text remains", "Not enough evidence" not in out, out)

cited = "- Supplier relationships remain unverified [1] %s." % BADGE
check("a cited sentence keeps its text",
      "Supplier relationships remain unverified [1]" in strip(cited), strip(cited))

print("\n[E] Markdown bullets and punctuation before colons\n")

for src, want in [
    ("* Bullet star label/list/here: 证据不足.", "Bullet star label/list/here"),
    ("+ Bullet plus label/list/here: 证据不足.", "Bullet plus label/list/here"),
    ("2. Numbered label/list/here: 证据不足.", "Numbered label/list/here"),
    ("- Trailing comma, then label/list: 证据不足.", "Trailing comma, then label/list"),
    ("- Semicolon; then label/list/x: 证据不足.", "Semicolon; then label/list/x"),
    ("- 逗号，然后标签列表内容项目：证据不足。", "逗号，然后标签列表内容项目"),
]:
    check("kept: %s" % want[:34], want in strip(src), strip(src))

print("\n[F] The real Tesla report renders intact - no regeneration\n")

# A real report is customer data and does not belong in the repository. Point
# AR_TESLA_FIXTURE at a saved research_data JSON to run this section.
FIX = os.environ.get("AR_TESLA_FIXTURE", "")
if not FIX or not os.path.exists(FIX):
    print("  SKIP  set AR_TESLA_FIXTURE=<research_data.json> to check a real report")
else:
    canonical = json.load(open(FIX, encoding="utf-8"))["research_result"]
    rendered = conf.strip_unsupported(canonical)
    check("the supplier list is intact in the rendered report",
          "西门子、ABB、先导、海目星、库卡、发那科等" in rendered)
    check("the English supplier list is intact",
          "KUKA, FANUC" in rendered and "Divisions/pro\n" not in rendered)
    # Every badge line must either survive with its text or be dropped outright;
    # none may be cut mid-content.
    cut = []
    for src in conf.normalize_text(canonical).split("\n"):
        if conf.NOT_ENOUGH not in conf.verdicts(src):
            continue
        got = conf._strip_unsupported_tag(src)
        if not got.strip():
            continue                                   # dropped: fine
        head = re.sub(r"\s*\*\*.*$", "", src).rstrip(" /、").rstrip()
        if head and not got.startswith(head[:len(got)]):
            cut.append((src, got))
    check("no badge line is cut mid-content", not cut,
          "%d cut, e.g. %s" % (len(cut), cut[0][1][:60] if cut else ""))
    for lang in ("en", "zh", "bilingual"):
        view = lv.select(rendered, lang)
        check("%s view still contains the supplier evidence" % lang,
              ("先导" in view) or ("Lead Intelligent" in view), lang)

print("\n[G] The PDF path shares this filter\n")

BASE = os.path.dirname(os.path.abspath(conf.__file__))
src = open(os.path.join(BASE, "pdf_service.py"), encoding="utf-8").read()
check("build_pdf renders through strip_unsupported",
      src.count("conf.strip_unsupported(record.get(\"research_result\")") >= 1)
try:
    import pdf_service as ps
    import tempfile
    from pathlib import Path
    rec = {"company": "Testco", "model_label": "Test",
           "research_result": "## Existing Automation Providers / 现有自动化供应商\n"
                              "**English:**\n- Suppliers (Siemens, ABB, KUKA, FANUC, etc.): "
                              + BADGE + ".\n\n**中文：**\n- 供应商（西门子、ABB、先导、海目星等）：证据不足。\n",
           "sources": [], "decision_makers": [], "people_summary": {}}
    out = Path(tempfile.gettempdir()) / "bilingual-test.pdf"
    ps.build_pdf(rec, out, lang="bilingual")
    check("a PDF is produced", out.exists() and out.stat().st_size > 1000,
          str(out.stat().st_size if out.exists() else 0))
except Exception as e:
    check("a PDF is produced", False, "%s: %s" % (type(e).__name__, e))

print("\n{} passed, {} failed".format(len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
