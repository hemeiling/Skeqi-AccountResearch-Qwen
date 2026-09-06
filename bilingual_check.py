# -*- coding: utf-8 -*-
"""Did the report say the same thing twice, or did it say it once and point?

The report is bilingual: every section carries an English block and a Chinese
block, and either language on its own has to be a complete report. A production
run collapsed a Chinese table to `2. 表格：(同上英文表格)` - "same as the English
table above" - which is invisible to an English reader and useless to a Chinese
one.

Nothing in the codebase writes that phrase. The model produced it, and it did so
on one run out of three, so this is a probabilistic failure that a prompt alone
cannot close. Hence a check.

DELIBERATELY SHALLOW. It compares structure, not meaning: does a table exist on
both sides, do the row counts match, does either block point at the other
instead of carrying content. Translation quality and semantic equivalence belong
to the model, and a second model call to police the first is not worth its cost
or its latency.

It only ever REPORTS. It never copies a table across, never translates, never
rewrites the report and never fails a run - an imperfect report beats no report,
which is the same rule the rest of this pipeline follows.

No imports from the pipeline; no network; no model call.
"""
import re

# Pointing at the other language instead of carrying the content. These are
# unambiguous in Chinese: they exist only to refer across the language boundary.
_ZH_CROSSREF = (
    "表格同上", "同上表", "同上英文", "同上", "见英文", "参见英文", "如英文",
    "与英文相同", "同英文", "英文版相同", "详见英文", "同前", "略，同",
)
# In English the same words can legitimately refer WITHIN a block ("see above"
# pointing at an earlier paragraph), so these only count when this block is the
# one missing the table the other block has. Checked by the caller below.
_EN_CROSSREF = (
    "same as above", "see above", "same table as above", "as above",
    "see the english", "same as the english", "per the english",
)

_LANG_EN = re.compile(r"^\*\*English[:：]?\*\*", re.I)
_LANG_ZH = re.compile(r"^\*\*中文[:：]?\*\*")
_HEADING = re.compile(r"^##\s+(.+?)\s*$", re.M)
_SEPARATOR = re.compile(r"^[\s|:\-—–]+$")


def split_sections(report):
    """`## Heading` to the next `## Heading`. Returns [(heading, body)]."""
    out = []
    marks = list(_HEADING.finditer(report or ""))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(report)
        out.append((m.group(1).strip(), report[m.end():end]))
    return out


def split_languages(body):
    """The English and Chinese halves of one section, by their markers."""
    en, zh, current = [], [], None
    for line in (body or "").split("\n"):
        if _LANG_EN.match(line.strip()):
            current = en
            continue
        if _LANG_ZH.match(line.strip()):
            current = zh
            continue
        if current is not None:
            current.append(line)
    return "\n".join(en), "\n".join(zh)


def table_rows(block):
    """Data rows of any table in this block.

    Counts markdown pipe tables and the bare `a | b | c` form the model also
    produces, ignoring the header and any separator rule. Two separators is the
    threshold: one pipe appears in ordinary prose and in this report's own
    `Verified / 已验证` tags.
    """
    rows = [ln.strip() for ln in (block or "").split("\n")
            if ln.count("|") >= 2 and not _SEPARATOR.match(ln.strip())]
    return max(0, len(rows) - 1) if rows else 0


def has_table(block):
    return table_rows(block) > 0 or any(
        ln.count("|") >= 2 and not _SEPARATOR.match(ln.strip())
        for ln in (block or "").split("\n"))


def _crossref(block, needles):
    low = (block or "").lower()
    return [n for n in needles if n in low or n in (block or "")]


def check(report, row_tolerance=1):
    """Structural completeness, section by section.

    Returns a list of findings: {section, reason, detail}. An empty list means
    nothing structural is missing - it does not mean the translation is good.
    """
    findings = []
    for heading, body in split_sections(report or ""):
        en, zh = split_languages(body)
        if not en.strip() and not zh.strip():
            continue                       # not a bilingual section; nothing to compare
        en_table, zh_table = has_table(en), has_table(zh)

        hits = _crossref(zh, _ZH_CROSSREF)
        if hits:
            findings.append({
                "section": heading, "reason": "chinese_cross_reference",
                "detail": "Chinese block points at the English one ({}) instead "
                          "of carrying the content".format(", ".join(hits[:3]))})
        # English shorthand only counts when this block is the one missing what
        # the other has - "see above" inside a complete block is ordinary prose.
        if en_table is False and zh_table is True:
            en_hits = _crossref(en, _EN_CROSSREF)
            if en_hits:
                findings.append({
                    "section": heading, "reason": "english_cross_reference",
                    "detail": "English block points at the Chinese one ({}) "
                              "instead of carrying the content"
                              .format(", ".join(en_hits[:3]))})

        if en_table and not zh_table:
            findings.append({
                "section": heading, "reason": "missing_chinese_table",
                "detail": "the English block has a table and the Chinese block "
                          "has none"})
        elif zh_table and not en_table:
            findings.append({
                "section": heading, "reason": "missing_english_table",
                "detail": "the Chinese block has a table and the English block "
                          "has none"})
        elif en_table and zh_table:
            en_rows, zh_rows = table_rows(en), table_rows(zh)
            if abs(en_rows - zh_rows) > row_tolerance:
                findings.append({
                    "section": heading, "reason": "table_row_mismatch",
                    "detail": "English table has {} rows, Chinese has {}".format(
                        en_rows, zh_rows)})
    return findings


def summary(findings):
    """Counts and section names only. The report body never goes in a manifest."""
    findings = findings or []
    return {"checked": True, "warnings": len(findings),
            "sections": sorted({f["section"] for f in findings})[:12],
            "reasons": sorted({f["reason"] for f in findings})}


def warnings(findings):
    """One WARN line per finding, in the form the run already publishes."""
    return ["WARN Bilingual completeness - {}: {}".format(f["section"], f["detail"])
            for f in (findings or [])]
