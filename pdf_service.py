"""
Bilingual PDF report generation.

Renders a saved Account Research record into a professional A4 report.
Chinese uses ReportLab's built-in Adobe CJK CID font (STSong-Light), so there is
no font file to ship; Latin text stays on Helvetica and CJK runs are switched
inline, which keeps English crisp instead of rendering it through a CJK face.

No credentials are ever written into a PDF.
"""

import re
from datetime import datetime
from pathlib import Path

import confidence as conf
import language_view as lv
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (BaseDocTemplate, Flowable, Frame, KeepTogether,
                                PageBreak, PageTemplate, Paragraph, Spacer, Table,
                                TableStyle)

CJK_FONT = "STSong-Light"
BASE_FONT = "Helvetica"
BOLD_FONT = "Helvetica-Bold"
_registered = False

ACCENT = colors.HexColor("#1f6feb")
INK = colors.HexColor("#16191d")
MUTED = colors.HexColor("#666e7a")
LINE = colors.HexColor("#d8dde3")
BAND = colors.HexColor("#f4f6f8")

CJK_RE = re.compile(r"([\u2e80-\u9fff\u3000-\u303f\uff00-\uffef]+)")


def _register():
    global _registered
    if not _registered:
        pdfmetrics.registerFont(UnicodeCIDFont(CJK_FONT))
        _registered = True


def esc(text):
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def mixed(text):
    """Escape, then switch CJK runs to the CJK font so mixed lines render correctly."""
    return CJK_RE.sub(lambda m: '<font name="%s">%s</font>' % (CJK_FONT, m.group(1)), esc(text))


def inline(text):
    """Markdown inline -> ReportLab markup, with CJK font switching."""
    out = mixed(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"`([^`]+)`", r"<font face='Courier'>\1</font>", out)
    return out


# ---------------------------------------------------------------- styles
def build_styles():
    _register()
    ss = getSampleStyleSheet()
    def mk(name, **kw):
        base = dict(fontName=BASE_FONT, fontSize=9.5, leading=14, textColor=INK,
                    alignment=TA_LEFT, spaceAfter=4)
        base.update(kw)
        return ParagraphStyle(name, parent=ss["Normal"], **base)
    return {
        "title": mk("t", fontName=BOLD_FONT, fontSize=19, leading=24, spaceAfter=2),
        "subtitle": mk("st", fontSize=11.5, leading=16, textColor=MUTED, spaceAfter=14),
        "h2": mk("h2", fontName=BOLD_FONT, fontSize=12, leading=16,
                 textColor=ACCENT, spaceBefore=13, spaceAfter=5),
        "lang": mk("lg", fontName=BOLD_FONT, fontSize=8.5, leading=12,
                   textColor=MUTED, spaceBefore=4, spaceAfter=2),
        "body": mk("b"),
        "bullet": mk("bu", leftIndent=11, bulletIndent=2, spaceAfter=2.5),
        "cell": mk("c", fontSize=8.5, leading=12, spaceAfter=0),
        "cellhead": mk("ch", fontName=BOLD_FONT, fontSize=8.5, leading=12,
                       textColor=MUTED, spaceAfter=0),
        "small": mk("sm", fontSize=8, leading=11, textColor=MUTED),
    }


# ---------------------------------------------------------------- parsing
def parse_sections(markdown):
    """Split the model's bilingual report into ordered sections with EN/ZH blocks."""
    sections, current = [], None
    for raw in str(markdown or "").split("\n"):
        line = raw.rstrip()
        h = re.match(r"^#{1,3}\s+(.*)$", line)
        if h:
            current = {"title": h.group(1).strip(), "blocks": []}
            sections.append(current)
            continue
        if current is None:
            current = {"title": "", "blocks": []}
            sections.append(current)
        current["blocks"].append(line)
    return sections


def _flow_lines(lines, st):
    """Turn a section's raw lines into flowables (headings, bullets, tables, text)."""
    flow, table_rows = [], []

    def flush_table():
        if not table_rows:
            return
        cols = max(len(r) for r in table_rows)
        data = []
        for i, row in enumerate(table_rows):
            row = row + [""] * (cols - len(row))
            style = st["cellhead"] if i == 0 else st["cell"]
            data.append([Paragraph(inline(c), style) for c in row])
        width = 170 * mm
        t = Table(data, colWidths=[width / cols] * cols, hAlign="LEFT")
        t.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, LINE),
            ("BACKGROUND", (0, 0), (-1, 0), BAND),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        flow.append(t)
        flow.append(Spacer(1, 5))
        table_rows.clear()

    for line in lines:
        s = line.strip()
        if re.match(r"^\|.*\|$", s):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if re.match(r"^[\s:\-|]+$", s.replace("|", "")):
                continue                      # markdown separator row
            table_rows.append(cells)
            continue
        flush_table()
        if not s:
            continue
        m = re.match(r"^\*\*(English|中文)[:：]?\*\*[:：]?\s*$", s)
        if m:
            label = "ENGLISH" if m.group(1) == "English" else "中文"
            flow.append(Paragraph(mixed(label), st["lang"]))
            continue
        b = re.match(r"^[-*+]\s+(.*)$", s) or re.match(r"^\d+\.\s+(.*)$", s)
        if b:
            flow.append(Paragraph(inline(b.group(1)), st["bullet"], bulletText="•"))
            continue
        flow.append(Paragraph(inline(s), st["body"]))
    flush_table()
    return flow


# ---------------------------------------------------------------- document
def _decorate(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.4)
    canvas.line(20 * mm, 16 * mm, A4[0] - 20 * mm, 16 * mm)
    canvas.setFont(BASE_FONT, 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(20 * mm, 11 * mm, doc.footer_left)
    canvas.drawRightString(A4[0] - 20 * mm, 11 * mm, "Page %d" % doc.page)
    canvas.restoreState()


def _kv_table(rows, st, widths=(45 * mm, 125 * mm)):
    data = [[Paragraph(mixed(k), st["cellhead"]), Paragraph(mixed(v), st["cell"])]
            for k, v in rows]
    t = Table(data, colWidths=list(widths), hAlign="LEFT")
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.3, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]))
    return t


def build_pdf(record, out_path, lang=None):
    """record: a saved research JSON dict. Returns the written Path.

    `lang` selects EN / ZH / bilingual from the SAME stored bilingual report.
    No model is called; this is a rendering choice, not a research choice.
    """
    lang = lv.normalize(lang)
    st = build_styles()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    company = record.get("company", "Unknown")
    website = record.get("website", "")
    model_label = record.get("model_label") or record.get("model", "")
    ts = record.get("timestamp") or datetime.now().astimezone().isoformat()
    try:
        when = datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        when = ts[:16]
    usage = record.get("token_usage") or {}
    sources = record.get("sources") or []

    doc = BaseDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=22 * mm,
        title="%s - Account Research" % company, author="Account Research test app")
    doc.footer_left = "%s  ·  %s  ·  %s" % (company, model_label, when)
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=_decorate)])

    flow = [
        Paragraph(mixed(lv.label("report_title", lang)), st["title"]),
        Paragraph(mixed(company), st["subtitle"]),
        _kv_table([
            (lv.label("company", lang), company),
            (lv.label("website", lang), website or "—"),
            (lv.label("date", lang), when),
            (lv.label("model", lang), "%s  (%s · %s)" % (
                model_label, record.get("protocol", "DashScope Native"),
                record.get("endpoint", ""))),
        ], st),
        Spacer(1, 8),
    ]

    # Body sections, in the order the model produced them.
    for sec in parse_sections(
            lv.select(conf.strip_unsupported(record.get("research_result") or ""), lang)):
        block = []
        if sec["title"]:
            block.append(Paragraph(mixed(sec["title"]), st["h2"]))
        body = _flow_lines(sec["blocks"], st)
        if not sec["title"] and not body:
            continue
        block.extend(body)
        # Keep a heading with at least its first lines rather than orphaning it.
        flow.extend([KeepTogether(block[:3])] + block[3:] if len(block) > 3 else block)

    # Key Contacts & Decision Makers, rendered from structured data rather than
    # from model output - an email address must never come from a language model.
    roster = record.get("decision_makers") or []
    if roster:
        flow.append(Paragraph(mixed(lv.label("contacts", lang)), st["h2"]))
        psum = record.get("people_summary") or {}
        if psum:
            flow.append(Paragraph(mixed(
                "%s contacts · %s with a business email (%s verified) · %s confirmed by "
                "more than one source · %s Apollo only · %s web only"
                % (psum.get("total", len(roster)), psum.get("with_email", 0),
                   psum.get("verified_email", 0), psum.get("merged", 0),
                   psum.get("apollo_only", 0), psum.get("web_only", 0))), st["small"]))
        data = [[Paragraph(mixed(h), st["cellhead"]) for h in
                 (lv.label("name", lang), lv.label("title", lang),
                  "Dept · Seniority", "Location", "Contact",
                  "Why Relevant to SKEQI" if lang != lv.ZH else "与思客琦的相关性",
                  "Source" if lang != lv.ZH else "来源")]]
        for person in roster:
            dept = person.get("department") or "—"
            sen = person.get("seniority") or "—"
            contact = person.get("email") or "No email"
            contact = "%s<br/>%s" % (esc(contact), esc(person.get("email_status") or "Not available"))
            if person.get("linkedin_url"):
                contact += '<br/><link href="%s" color="#1f6feb">LinkedIn</link>' % esc(person["linkedin_url"])
            data.append([
                Paragraph(mixed(person.get("name", "")), st["cell"]),
                Paragraph(mixed(person.get("title") or "—"), st["cell"]),
                Paragraph(mixed("%s · %s" % (dept, sen)), st["cell"]),
                Paragraph(mixed(person.get("location") or "—"), st["cell"]),
                Paragraph(contact, st["cell"]),
                Paragraph(mixed(person.get("why") or "—"), st["cell"]),
                Paragraph(mixed(" + ".join(person.get("sources") or [])), st["cell"]),
            ])
        # Contact is the widest column on purpose: an email broken across two
        # lines mid-address is hard to read and easy to mistype.
        t = Table(data, colWidths=[22 * mm, 25 * mm, 21 * mm, 17 * mm, 42 * mm, 28 * mm, 15 * mm],
                  hAlign="LEFT", repeatRows=1)
        t.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, LINE),
            ("BACKGROUND", (0, 0), (-1, 0), BAND),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        flow.append(t)
        flow.append(Paragraph(mixed(
            "Emails come from Apollo enrichment only and are never inferred. "
            "A blank address means Apollo held no business email for that contact."), st["small"]))
        flow.append(Spacer(1, 6))

    # Sources with clickable URLs.
    flow.append(Paragraph(mixed(lv.label("sources", lang)), st["h2"]))
    if sources:
        data = [[Paragraph(mixed(h), st["cellhead"]) for h in
                 ("#", "Title / 标题", "Type / 类型", "URL")]]
        for s in sources:
            url = esc(s.get("url", ""))
            data.append([
                Paragraph(str(s.get("id", "")), st["cell"]),
                Paragraph(mixed(s.get("title") or "(untitled)"), st["cell"]),
                Paragraph(mixed(s.get("source_type", "")), st["cell"]),
                Paragraph('<link href="%s" color="#1f6feb">%s</link>' % (url, url), st["cell"]),
            ])
        t = Table(data, colWidths=[8 * mm, 55 * mm, 33 * mm, 74 * mm], hAlign="LEFT", repeatRows=1)
        t.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, LINE),
            ("BACKGROUND", (0, 0), (-1, 0), BAND),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        flow.append(t)
    else:
        flow.append(Paragraph(mixed(lv.label("no_evidence", lang)), st["body"]))

    # Metadata.
    flow.append(Paragraph(mixed(lv.label("metadata", lang)), st["h2"]))
    queries = record.get("search_queries") or []
    flow.append(_kv_table([
        ("Model / 模型", model_label),
        ("Protocol / 协议", record.get("protocol", "DashScope Native")),
        ("Endpoint / 端点", record.get("endpoint", "")),
        ("Search enabled / 联网搜索", "true (turbo)"),
        ("Search queries / 搜索查询", str(len(queries))),
        ("Sources / 信息来源数", str(len(sources))),
        ("Evidence cached / 使用缓存", "yes" if record.get("evidence_cached") else "no"),
        ("Input tokens / 输入 tokens", "{:,}".format(usage.get("input") or 0)),
        ("Output tokens / 输出 tokens", "{:,}".format(usage.get("output") or 0)),
        ("Total tokens / 总计 tokens", "{:,}".format(usage.get("total") or 0)),
        ("Latency / 耗时", "%s s" % record.get("latency_seconds", "—")),
        ("Generated / 生成时间", when),
    ], st))

    # Financial sourcing and Apollo, both reported honestly: "used" means a source
    # actually landed in the evidence, and Apollo calls are never token counts.
    fin = record.get("financial_sources") or {}
    ap = record.get("apollo_usage") or {}
    extra = []
    if fin:
        extra.append(("Company listing / 上市状态",
                      ("Public · %s%s" % (fin.get("ticker", ""),
                                          " · " + fin["listed_name"] if fin.get("listed_name") else ""))
                      if fin.get("public_company")
                      else (fin.get("listing_status") or "private/unlisted").replace("_", " ")))
        extra.append(("Yahoo Finance used / 使用雅虎财经",
                      "Yes / 是" if fin.get("yahoo_finance_used")
                      else ("No / 否" + ("" if fin.get("public_company")
                                         else " (private/unlisted - not applicable)"))))
        for url in (fin.get("yahoo_finance_urls") or [])[:2]:
            extra.append(("Yahoo Finance URL", url))
    if ap:
        extra.append(("Apollo enrichment / Apollo 补充",
                      (ap.get("status") or "-").replace("_", " ") if ap.get("configured")
                      else "not configured"))
        if ap.get("configured"):
            extra.append(("Apollo API calls / API 调用", str(ap.get("calls", 0))))
            extra.append(("Apollo people returned / 返回人数", str(ap.get("people_returned", 0))))
            extra.append(("Apollo people retained / 保留人数", str(ap.get("people_retained", 0))))
            extra.append(("Apollo people enriched / 补充人数", str(ap.get("people_enriched", 0))))
            extra.append(("Apollo emails found / 邮箱数",
                          "%s (%s verified)" % (ap.get("emails_found", 0),
                                                ap.get("emails_verified", 0))))
    if extra:
        flow.append(Spacer(1, 4))
        flow.append(_kv_table(extra, st))
    if queries:
        flow.append(Spacer(1, 6))
        flow.append(Paragraph(mixed(lv.label("queries", lang)), st["lang"]))
        for i, q in enumerate(queries, 1):
            flow.append(Paragraph("%d. %s" % (i, mixed(q)), st["small"]))

    doc.build(flow)
    return out_path


class Anchor(Flowable):
    """Zero-height destination: creates a PDF bookmark and an internal link target."""

    def __init__(self, key, title, level=0):
        Flowable.__init__(self)
        self.key, self.title, self.level = key, title, level
        self.width = self.height = 0

    def draw(self):
        self.canv.bookmarkPage(self.key)
        self.canv.addOutlineEntry(self.title, self.key, level=self.level, closed=False)


def _report_body(record, st, heading_style=None, lang=None):
    """Section flowables for one research record - shared by single and combined PDFs."""
    lang = lv.normalize(lang)
    h2 = heading_style or st["h2"]
    flow = []
    for sec in parse_sections(
            lv.select(conf.strip_unsupported(record.get("research_result") or ""), lang)):
        block = []
        if sec["title"]:
            block.append(Paragraph(mixed(sec["title"]), h2))
        body = _flow_lines(sec["blocks"], st)
        if not sec["title"] and not body:
            continue
        block.extend(body)
        flow.extend([KeepTogether(block[:3])] + block[3:] if len(block) > 3 else block)
    return flow


def _sources_table(sources, st):
    if not sources:
        return [Paragraph(mixed(lv.label("no_evidence", lang)), st["body"])]
    data = [[Paragraph(mixed(h), st["cellhead"]) for h in
             ("#", "Title / 标题", "Type / 类型", "URL")]]
    for s in sources:
        url = esc(s.get("url", ""))
        data.append([
            Paragraph(str(s.get("id", "")), st["cell"]),
            Paragraph(mixed(s.get("title") or "(untitled)"), st["cell"]),
            Paragraph(mixed(s.get("source_type", "")), st["cell"]),
            Paragraph('<link href="%s" color="#1f6feb">%s</link>' % (url, url), st["cell"]),
        ])
    t = Table(data, colWidths=[8 * mm, 55 * mm, 33 * mm, 74 * mm], hAlign="LEFT", repeatRows=1)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("BACKGROUND", (0, 0), (-1, 0), BAND),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return [t]


def _fmt_when(ts):
    try:
        return datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts or "")[:16]


def build_portfolio_pdf(records, out_path, title=None, price=None, lang=None):
    """Combine already-saved research records into one portfolio PDF.

    Pure re-use of stored data: no model is called and no tokens are spent.
    `lang` renders EN / ZH / bilingual from the same stored bilingual reports.
    """
    lang = lv.normalize(lang)
    st = build_styles()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    title = title or lv.label("portfolio_title", lang)

    dates = sorted(r.get("timestamp", "") for r in records if r.get("timestamp"))
    models = sorted({(r.get("model_label") or r.get("model") or "") for r in records})
    tot_sources = sum(len(r.get("sources") or []) for r in records)
    tot_in = sum((r.get("token_usage") or {}).get("input") or 0 for r in records)
    tot_out = sum((r.get("token_usage") or {}).get("output") or 0 for r in records)
    tot_tok = sum((r.get("token_usage") or {}).get("total") or 0 for r in records)

    doc = BaseDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=22 * mm,
        title="Account Research Portfolio", author="Account Research test app")
    doc.footer_left = "Account Research Portfolio  ·  %d companies" % len(records)
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=_decorate)])

    # ---- cover ----
    cover_rows = [
        ("Companies / 公司数量", str(len(records))),
        ("Research date range / 研究日期范围",
         ("%s  –  %s" % (_fmt_when(dates[0]), _fmt_when(dates[-1]))) if dates else "—"),
        ("Models used / 使用模型", ", ".join(m for m in models if m) or "—"),
        ("Total sources / 信息来源总数", str(tot_sources)),
        ("Total tokens / Token 总计",
         "{:,}  (in {:,} / out {:,})".format(tot_tok, tot_in, tot_out)),
    ]
    if price:
        cost = round(tot_in / 1000.0 * price["input_per_1k"]
                     + tot_out / 1000.0 * price["output_per_1k"], 2)
        cover_rows.append(("Estimated cost / 预估成本",
                           "%s %s" % (price.get("currency", "CNY"), cost)))
    else:
        cover_rows.append(("Estimated cost / 预估成本", "pricing not configured / 未配置价格"))

    flow = [
        Spacer(1, 26 * mm),
        Paragraph(mixed(title), st["title"]),
        Paragraph(mixed("Generated %s" % datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")),
                  st["subtitle"]),
        _kv_table(cover_rows, st),
        Spacer(1, 10),
        Paragraph(mixed("Compiled from saved research data. No AI generation was performed "
                        "for this document. / 本汇总由已保存的研究结果编制，未调用模型。"),
                  st["small"]),
        PageBreak(),
    ]

    # ---- summary table ----
    flow.append(Paragraph(mixed("Summary" if lang == lv.EN else "汇总" if lang == lv.ZH
                                    else "Summary / 汇总"), st["h2"]))
    head = ("Company / 公司", "Website / 官网", "Model / 模型",
            "Sources / 来源", "Tokens / Token数", "Date / 日期")
    data = [[Paragraph(mixed(h), st["cellhead"]) for h in head]]
    for r in records:
        u = r.get("token_usage") or {}
        site = esc(r.get("website") or "")
        data.append([
            Paragraph(mixed(r.get("company", "")), st["cell"]),
            Paragraph('<link href="%s" color="#1f6feb">%s</link>' % (site, site.replace("https://", ""))
                      if site else "—", st["cell"]),
            Paragraph(mixed(r.get("model_label") or r.get("model") or ""), st["cell"]),
            Paragraph(str(len(r.get("sources") or [])), st["cell"]),
            Paragraph("{:,}".format(u.get("total") or 0), st["cell"]),
            Paragraph(_fmt_when(r.get("timestamp"))[:10], st["cell"]),
        ])
    t = Table(data, colWidths=[34 * mm, 42 * mm, 30 * mm, 18 * mm, 24 * mm, 22 * mm],
              hAlign="LEFT", repeatRows=1)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("BACKGROUND", (0, 0), (-1, 0), BAND),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    flow.append(t)

    # ---- table of contents (clickable) ----
    flow.append(Paragraph(mixed(lv.label("contents", lang)), st["h2"]))
    for i, r in enumerate(records, 1):
        key = "company_%d" % i
        label = r.get("company", "")
        flow.append(Paragraph(
            '<link href="#%s" color="#1f6feb">%d. %s</link>' % (key, i, mixed(label)),
            st["body"]))
    flow.append(PageBreak())

    # ---- one section per company, each starting on a new page ----
    for i, r in enumerate(records, 1):
        key = "company_%d" % i
        flow.append(Anchor(key, "%d. %s" % (i, r.get("company", "")), level=0))
        flow.append(Paragraph('<a name="%s"/>%s' % (key, mixed("%d. %s" % (i, r.get("company", "")))),
                              st["title"]))
        u = r.get("token_usage") or {}
        flow.append(_kv_table([
            ("Company / 公司", r.get("company", "")),
            ("Website / 官网", r.get("website") or "—"),
            ("Model / 模型", "%s  (%s)" % (r.get("model_label") or r.get("model") or "",
                                           r.get("endpoint", ""))),
            ("Research date / 研究日期", _fmt_when(r.get("timestamp"))),
            ("Sources / 来源", str(len(r.get("sources") or []))),
            ("Tokens", "{:,} (in {:,} / out {:,})".format(
                u.get("total") or 0, u.get("input") or 0, u.get("output") or 0)),
            ("Latency / 耗时", "%s s" % r.get("latency_seconds", "—")),
        ], st))
        flow.append(Spacer(1, 6))
        flow.extend(_report_body(r, st, lang=lang))
        flow.append(Paragraph(mixed(lv.label("sources", lang)), st["h2"]))
        flow.extend(_sources_table(r.get("sources") or [], st))
        if i < len(records):
            flow.append(PageBreak())

    doc.build(flow)
    return out_path


def safe_name(text, limit=48):
    """Filesystem-safe company token: SKEQI, EVE_Energy, 思客琦 -> 思客琦."""
    cleaned = re.sub(r'[\\/:*?"<>|]+', " ", str(text or "")).strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    return (cleaned[:limit] or "Company")


def pdf_filename(company, model_label=None, when=None, multi_model=False, lang=None):
    """Language is part of the name so EN/ZH/bilingual exports never overwrite
    each other: ACRO_Account_Research_Qwen3.6Flash_EN_2026-09-02.pdf"""
    date = (when or datetime.now().astimezone()).strftime("%Y-%m-%d")
    parts = [safe_name(company), "Account_Research"]
    if multi_model and model_label:
        parts.append(re.sub(r"[^A-Za-z0-9.]+", "", model_label))
    parts.append(lv.SUFFIX[lv.normalize(lang)])
    parts.append(date)
    return "_".join(parts) + ".pdf"
