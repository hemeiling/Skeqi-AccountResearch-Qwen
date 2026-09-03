"""
Display-language selection for an already-generated bilingual report.

PRESENTATION ONLY. Research runs once and is stored bilingual; this module
chooses what to show. It never calls a model, never touches retrieval, and
never rewrites content — it selects between blocks the report already contains.

The report shape it selects over:

    ## English Heading / 中文标题
    **English:**
    - content [1] **Verified / 已验证**

    **中文：**
    - 内容 [1] **Verified / 已验证**

What is split:
  * section headings on " / "        -> "English Heading" or "中文标题"
  * language markers                 -> dropped in single-language mode
  * body blocks                      -> only the selected language is kept
  * canonical confidence tags        -> "Verified" or "已验证"

What is deliberately NOT split: ordinary body text. The research prompt requires
brand names to stay in their original language in BOTH blocks (KEPAILE / 科派乐,
琦航数字工厂系统), so splitting arbitrary "A / B" text inside a sentence would
destroy real content. Only the structures above are unambiguous.

static/app.js mirrors these rules for on-screen rendering; keep the two in step.
"""

import re

EN, ZH, BILINGUAL = "en", "zh", "bilingual"
LANGUAGES = (EN, ZH, BILINGUAL)

# Filename / UI token per language.
SUFFIX = {EN: "EN", ZH: "ZH", BILINGUAL: "Bilingual"}

_CJK = re.compile(r"[一-鿿]")
_HEADING = re.compile(r"^(#{1,4})\s+(.*)$")
_LANG_EN = re.compile(r"^\*\*English[:：]?\*\*[:：]?\s*$", re.I)
_LANG_ZH = re.compile(r"^\*\*中文[:：]?\*\*[:：]?\s*$")

# Canonical confidence tags produced by confidence.normalize_text().
_TAGS = (("Verified / 已验证", "Verified", "已验证"),
         ("Likely / 可能", "Likely", "可能"),
         ("Not enough evidence / 证据不足", "Not enough evidence", "证据不足"))


def normalize(lang):
    lang = (lang or "").strip().lower()
    if lang in ("en", "english"):
        return EN
    if lang in ("zh", "cn", "chinese", "中文"):
        return ZH
    return BILINGUAL


def has_cjk(text):
    return bool(_CJK.search(text or ""))


def split_heading(text, lang):
    """'Company Overview / 公司概况' -> one side, chosen by script not position."""
    if lang == BILINGUAL or " / " not in text:
        return text
    parts = [p.strip() for p in text.split(" / ")]
    cjk = [p for p in parts if has_cjk(p)]
    latin = [p for p in parts if not has_cjk(p)]
    if not cjk or not latin:
        return text                       # nothing to choose between
    return " / ".join(cjk if lang == ZH else latin)


def strip_tags(line, lang):
    if lang == BILINGUAL:
        return line
    for both, en, zh in _TAGS:
        line = line.replace(both, en if lang == EN else zh)
    return line


def select(markdown, lang):
    """Return the report rendered for one language. Bilingual passes through."""
    lang = normalize(lang)
    if lang == BILINGUAL or not markdown:
        return markdown or ""
    out, block = [], None          # block: None (shared), "en" or "zh"
    for raw in str(markdown).split("\n"):
        line = raw.rstrip()
        heading = _HEADING.match(line)
        if heading:
            block = None           # a new section resets the language context
            out.append("{} {}".format(heading.group(1),
                                      split_heading(heading.group(2).strip(), lang)))
            continue
        stripped = line.strip()
        if _LANG_EN.match(stripped):
            block = EN
            continue               # the marker itself is redundant in single-language mode
        if _LANG_ZH.match(stripped):
            block = ZH
            continue
        if block is not None and block != lang:
            continue
        out.append(strip_tags(line, lang))

    # Collapse the blank runs left behind by the removed block.
    cleaned, blanks = [], 0
    for line in out:
        if line.strip():
            blanks = 0
            cleaned.append(line)
        else:
            blanks += 1
            if blanks < 2:
                cleaned.append(line)
    return "\n".join(cleaned).strip() + "\n"


# ---------------------------------------------------------------------------
# Labels for the parts the app renders itself (PDF furniture, generated tables)
# ---------------------------------------------------------------------------

LABELS = {
    "report_title":   {EN: "Account Research Report", ZH: "客户研究报告",
                       BILINGUAL: "Account Research Report / 客户研究报告"},
    "portfolio_title": {EN: "Account Research Portfolio", ZH: "客户研究汇总报告",
                        BILINGUAL: "Account Research Portfolio / 客户研究汇总报告"},
    "contents":       {EN: "Table of Contents", ZH: "目录",
                       BILINGUAL: "Table of Contents / 目录"},
    "sources":        {EN: "Sources", ZH: "信息来源", BILINGUAL: "Sources / 信息来源"},
    "metadata":       {EN: "Research Metadata", ZH: "研究元数据",
                       BILINGUAL: "Research Metadata / 研究元数据"},
    "contacts":       {EN: "Key Contacts & Decision Makers", ZH: "关键联系人与决策者",
                       BILINGUAL: "Key Contacts & Decision Makers / 关键联系人与决策者"},
    "company":        {EN: "Company", ZH: "公司", BILINGUAL: "Company / 公司"},
    "website":        {EN: "Website", ZH: "官网", BILINGUAL: "Website / 官网"},
    "date":           {EN: "Research Date", ZH: "研究日期", BILINGUAL: "Research Date / 研究日期"},
    "model":          {EN: "Model", ZH: "模型", BILINGUAL: "Model / 模型"},
    "queries":        {EN: "Search queries", ZH: "搜索查询",
                       BILINGUAL: "Search queries / 搜索查询"},
    "name":           {EN: "Name", ZH: "姓名", BILINGUAL: "Name / 姓名"},
    "title":          {EN: "Title", ZH: "职务", BILINGUAL: "Title / 职务"},
    "no_evidence":    {EN: "Not enough evidence", ZH: "证据不足",
                       BILINGUAL: "Not enough evidence / 证据不足"},
}


def label(key, lang):
    entry = LABELS.get(key)
    if not entry:
        return key
    return entry.get(normalize(lang), entry[BILINGUAL])
