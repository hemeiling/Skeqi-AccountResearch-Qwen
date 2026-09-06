"""Provider representation: what retrieval verified, preserved through the report.

Retrieval now distinguishes a named organisation from a category, an external
provider from the account's own engineering, and CONFIRMED from STRONG_INDICATION
from MARKET_ONLY. Synthesis used to flatten all of that: the model was asked for
a Company column and would fill it with "MES provider" or 系统集成商 when it had
no name, which reads as a verified supplier and is worse than an empty table.

Two halves, and the second is the one that matters:

  INPUT   the verified rows are handed to the model as structured facts, so it
          is describing what we proved rather than reconstructing it from prose.

  OUTPUT  the rendered report is then CORRECTED against those rows. Prompt
          instructions are not a guarantee - a category name in the Company
          column is removed from it whatever the model was told, and moved to
          the field it belongs in rather than silently deleted.

No network, no model call. Import-safe.
"""
import re

import research_service as rs

# The exact sentence when nothing survived verification. Never a filler category.
NO_PROVIDER_EN = ("No verified named automation provider was identified from the "
                  "available evidence.")
NO_PROVIDER_ZH = "根据现有证据，尚未确认具体的自动化供应商。"
NO_PROVIDER = "{}\n{}".format(NO_PROVIDER_EN, NO_PROVIDER_ZH)

CAPABILITY_LABEL = "Capability Observed / 观察到的能力"
INTERNAL_LABEL = "Internal Capability / 内部能力"
MARKET_LABEL = "Market context / 市场参考"

# Sections whose tables carry provider claims. Others are left completely alone.
PROVIDER_HEADINGS = ("existing automation providers", "competitor analysis",
                     "现有自动化供应商", "竞争对手分析")

RELATIONSHIP_LABEL = {
    rs.REL_CONFIRMED: "CONFIRMED / 已确认",
    rs.REL_STRONG: "STRONG INDICATION / 强相关",
    rs.REL_MARKET: "MARKET ONLY / 仅市场参考",
}

# Words that must never describe a MARKET_ONLY organisation. Being in this market
# is not being in this account.
# The incumbency GRADE is a cell of its own. An earlier version matched the
# surrounding pipes and replaced them too, merging two cells and corrupting the
# row - so the pipes are preserved and only the cell body is rewritten.
_INCUMBENCY_GRADE = re.compile(r"(?<=\|)(\s*)(?:Very High|High|Medium|Moderate|Low/Med)(\s*)(?=\|)", re.I)

_INCUMBENT_WORDS = re.compile(
    r"\b(incumbent|installed base|current supplier|existing supplier|"
    r"supplies the account|in use at|deployed at)\b|现有供应商|在用供应商", re.I)


def provider_prompt_block(providers, account):
    """The verified rows, as facts. Deterministic: no model wrote any of this."""
    rows = [p for p in (providers or []) if p.get("name")]
    if not rows:
        return ("\n\n---\n\nVERIFIED PROVIDERS / 已核实供应商\n"
                "None. " + NO_PROVIDER + "\n"
                "Do NOT place a category, a capability or a role in a Company "
                "column. If no organisation is named, state the sentence above "
                "verbatim.\n")
    seen, out = set(), []
    for p in rows:
        key = (p["name"].lower(), p.get("category"))
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    lines = ["\n\n---\n\nVERIFIED PROVIDERS / 已核实供应商",
             "Each row was verified against the account by the retrieval pipeline.",
             "Relationship and provenance are SEPARATE: provenance is how the SOURCE",
             "relates to the account, relationship is what the source ASSERTS.",
             "A MARKET ONLY organisation may be named as market context and must NEVER",
             "be described as an incumbent, current supplier or installed provider.",
             ""]
    for p in out:
        lines.append("- {name} | category: {cat} | relationship: {rel} | "
                     "provenance: {prov} | source: {src}".format(
                         name=p["name"], cat=p.get("category") or "unclassified",
                         rel=RELATIONSHIP_LABEL.get(p.get("relationship"),
                                                    p.get("relationship") or "?"),
                         prov=p.get("provenance") or "?",
                         src=p.get("source_domain") or p.get("source_url") or "?"))
    lines.append("")
    lines.append("Only these organisations may appear in a Company or Provider column.")
    return "\n".join(lines) + "\n"


NO_COMPETITOR_EN = ("No sufficiently verified target-account competitors were "
                    "identified from the available evidence.")
NO_COMPETITOR_ZH = "根据现有证据，尚未确认足够可靠的目标客户竞争对手。"
NO_COMPETITOR = "{}\n{}".format(NO_COMPETITOR_EN, NO_COMPETITOR_ZH)


def competitor_prompt_block(competitors, profile=None):
    """Verified competitors as FACTS. The model describes them; it does not add
    to them, and an empty set stays empty."""
    rows = [c for c in (competitors or []) if c.get("organization_name")]
    if not rows:
        why = (profile or {}).get("competitor_skip_reason")
        return ("\n\n---\n\nVERIFIED TARGET COMPETITORS / 已核实的目标客户竞争对手\n"
                "None.\n" + NO_COMPETITOR + "\n"
                + ("Reason: " + why + "\n" if why else "")
                + "State the sentence above verbatim. Do NOT supply competitor "
                  "names from general knowledge, and do NOT list the account's "
                  "own suppliers, partners or customers as competitors.\n")
    lines = ["\n\n---\n\nVERIFIED TARGET COMPETITORS / 已核实的目标客户竞争对手",
             "These compete with the ACCOUNT for customers, projects and market share.",
             "Each was verified as a real organisation AND scored for competitive",
             "overlap. Only these may appear as competitors.",
             ""]
    for c in rows:
        lines.append(
            "- Organization: {}\n"
            "  Competition type: {}\n"
            "  Competitive overlap: {}\n"
            "  Offering overlap: {} | Industry/customer overlap: {} | Geography: {}\n"
            "  Evidence: {}\n"
            "  Confidence: {}".format(
                c["organization_name"], c["competition_type"],
                c.get("competitive_rationale") or "-",
                c.get("offering_overlap"), c.get("customer_or_industry_overlap"),
                c.get("geographic_or_market_overlap"),
                ", ".join(c.get("source_domains") or []) or "-",
                c.get("confidence") or "-"))
    return "\n".join(lines) + "\n"


NO_CHANNEL_EN = ("No verified distributors, representatives or resellers were "
                 "identified for this account.")
NO_CHANNEL_ZH = "未发现可验证的分销商、代理商或经销商。"
NO_CHANNEL = "{}\n{}".format(NO_CHANNEL_EN, NO_CHANNEL_ZH)

_GTM_LABEL = {
    "DIRECT": "Direct sales / 直销",
    "DISTRIBUTOR_LED": "Distributor-led / 经销商主导",
    "REPRESENTATIVE_LED": "Representative-led / 代表处主导",
    "MIXED": "Mixed direct and channel / 直销与渠道并行",
    "UNKNOWN": "Not established from the evidence / 现有证据未能确认",
}


def channel_prompt_block(channels, profile=None):
    """Go-to-market and verified channel entities as FACTS.

    The confidence travels with the model on purpose. "Direct" asserted from one
    sales-team mention is a different claim from "direct" asserted from an
    explicit statement, and the report must not present them identically.
    """
    prof = profile or {}
    model = prof.get("go_to_market_model") or "UNKNOWN"
    conf = prof.get("go_to_market_confidence") or "none"
    rows = [c for c in (channels or []) if c.get("organization_name")]
    reps = [c for c in rows if c.get("is_representation")]
    partners = [c for c in rows if not c.get("is_representation")]

    lines = ["\n\n---\n\nGO-TO-MARKET AND VERIFIED CHANNEL / 销售模式与已核实渠道",
             "Go-to-market model: {} (confidence: {})".format(
                 _GTM_LABEL.get(model, model), conf)]
    if conf in ("low", "medium", "none"):
        lines.append("This model is NOT firmly established. Say so; do not present "
                     "it as settled, and do not state that the account has no "
                     "channel merely because none was found.")
    if not reps:
        lines += ["Verified distributors, representatives or resellers: None.",
                  NO_CHANNEL,
                  "State the sentence above verbatim. Do NOT name distributors "
                  "from general knowledge, and do NOT present a supplier, "
                  "customer, integrator or technology partner as a distributor."]
        why = prof.get("channel_skip_reason")
        if why:
            lines.append("Reason: " + why)
    else:
        lines.append("Verified channel entities. Each was verified as a real "
                     "organisation AND found stating that it represents this "
                     "account. Only these may be listed as channel:")
        for c in reps:
            lines.append(
                "- Organization: {}\n  Role: {}{}\n  Territory: {}\n"
                "  Stated: \"{}\"\n  Evidence: {}\n  Confidence: {}".format(
                    c["organization_name"], c["role"],
                    " (authorized)" if c.get("authorized") else "",
                    c.get("territory") or "not stated",
                    (c.get("evidence_quote") or "")[:200],
                    ", ".join(c.get("source_domains") or []) or "-",
                    c.get("confidence") or "-"))
    if partners:
        lines.append("Related organisations that are NOT channel. They integrate, "
                     "partner with or service this account, which is not "
                     "representation. List them as partners, never as distributors:")
        for c in partners:
            lines.append("- {} | {} | {}".format(
                c["organization_name"], c["role"],
                ", ".join(c.get("source_domains") or []) or "-"))
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Output correction
# --------------------------------------------------------------------------

_CLEARED = {"\u2014", "-", "\u2013", "n/a", "N/A", "none", "None", ""}
_EMPH = re.compile(r"[*_`]+")
_CITE = re.compile(r"\[\d+(?:\s*,\s*\d+)*\]")


def _cell_text(cell):
    return _CITE.sub("", _EMPH.sub("", cell or "")).strip()


def _is_separator(row):
    return bool(re.fullmatch(r"[\s|:\-]+", row or ""))


# Which column actually names the organisation. Position is not a reliable
# answer: the Competitor table starts with Company, while the Provider table
# starts with "Process or capability" and puts the provider SECOND. Judging
# column one there inspected capability phrases and never looked at the column
# that carries supplier claims - which is how "Undisclosed Tier-1s" survived a
# production run.
_PROVIDER_HEADER = re.compile(
    r"(company|provider|organi[sz]ation|supplier|vendor|integrator|"
    r"\u516c\u53f8|\u4f9b\u5e94\u5546|\u5382\u5546|\u63d0\u4f9b\u5546|\u96c6\u6210\u5546)", re.I)
# A header that merely describes work, never an organisation.
_NON_PROVIDER_HEADER = re.compile(
    r"(process|capability|capabilities|\u5de5\u827a|\u80fd\u529b)", re.I)


def provider_column(header_line):
    """Index of the organisation column, by header semantics. None if absent.

    "Provider or internal capability" names a provider AND a capability; it is
    still the provider column, so a provider word wins over a capability word in
    the same header. A header that is only a capability word is not.
    """
    cells = [_cell_text(c) for c in (header_line or "").split("|")]
    best = None
    for i, c in enumerate(cells):
        if not c:
            continue
        if _PROVIDER_HEADER.search(c):
            if best is None:
                best = i
        elif _NON_PROVIDER_HEADER.search(c):
            continue
    return best


def _split_entries(cell):
    """One cell can list several things: "ABB Robotics, Kuka/Fanuc (market)"."""
    parts = re.split(r"\s*(?:,|;|/|\u3001|\uff0c)\s*", cell or "")
    return [p.strip() for p in parts if p.strip()]


def _account_owned(text, account, aliases=()):
    """The account's own engineering is not an external provider.

    Two things this has to get right, both learned from "ACRO (In-house)"
    surviving a production run as a competitor row.

    The ownership marker often lives in the PARENTHETICAL - "(In-house)",
    "(内部)" - so this must be asked BEFORE any parenthetical is stripped.
    Callers pass the full entry for exactly that reason.

    And the cell is often a SHORTER form of the account name: "ACRO" for "ACRO
    Automation Systems". Asking whether the account name appears in the cell has
    the direction backwards. The short form is accepted, but only as an EXACT
    match of a distinctive token - never a substring, or Acromag, ACROBAT
    Automation, Macro Automation and Acro-Tech Welding would all be swallowed as
    the account's own capability.
    """
    t = (text or "").lower()
    if re.search(r"\b(internal|in-house|inhouse|own team|own engineering)\b", t) \
            or any(k in text for k in ("内部", "自有", "自研")):
        return True
    names = [account] + list(aliases or [])
    core = rs.core_name(account)
    if core:
        names.append(core)
    if any(n and (rs._mentions(n, text) if n.isascii() else n in text)
           for n in names if n):
        return True
    # Short-name ownership. Exact equality only, and only on a token distinctive
    # enough to identify the company rather than its industry.
    bare = _EMPH.sub("", re.sub(r"\s*\([^)]*\)\s*", " ", text or "")).strip().lower()
    return bool(bare) and bare in {tok for tok in rs.distinctive_tokens(account)
                                   if len(tok) >= 3}


def enforce(report, providers=None, account="", aliases=()):
    """Correct the rendered report so a category can never read as a supplier.

    Conservative on purpose. Only tables inside provider sections are touched,
    only the column whose HEADER names an organisation is judged, and nothing is
    deleted - a rejected entry is moved to the field it belongs in and listed
    under the table, so the information survives and only the CLAIM changes.
    """
    if not report:
        return report, {"moved_capability": [], "moved_internal": [], "empty": False}
    verified = {p["name"].lower(): p for p in (providers or []) if p.get("name")}
    market_only = {n for n, p in verified.items()
                   if p.get("relationship") == rs.REL_MARKET}

    out, all_cap, all_int = [], [], []
    moved_cap, moved_int = [], []
    in_section = False
    col = None                    # provider column for the table being read
    kept_rows = 0
    emptied = 0

    def flush(buf):
        if moved_cap:
            buf.append("")
            buf.append("**{}**: {}".format(CAPABILITY_LABEL,
                                           "; ".join(dict.fromkeys(moved_cap))))
        if moved_int:
            buf.append("")
            buf.append("**{}**: {}".format(INTERNAL_LABEL,
                                           "; ".join(dict.fromkeys(moved_int))))

    for line in report.split("\n"):
        if line.startswith("#"):
            if in_section and (moved_cap or moved_int):
                flush(out); moved_cap, moved_int = [], []
            head = _cell_text(line).lower()
            in_section = any(h in head for h in PROVIDER_HEADINGS)
            col, kept_rows = None, 0
            out.append(line)
            continue
        stripped = line.strip()
        if not in_section or not stripped.startswith("|"):
            if col is not None and not stripped.startswith("|"):
                if kept_rows == 0 and not verified:
                    out.append(NO_PROVIDER); emptied += 1
                col, kept_rows = None, 0
            out.append(line)
            continue
        if _is_separator(line):
            out.append(line)
            continue

        cells = line.split("|")
        if col is None:
            # First non-separator row of a table is its header.
            col = provider_column(line)
            out.append(line)
            continue
        if col >= len(cells):
            out.append(line)
            continue

        raw = _cell_text(cells[col])
        # A cell this corrector already cleared is not a fresh category to file
        # again - without this, a second pass appends the placeholder itself to
        # Capability Observed and correction stops being idempotent.
        if not raw or raw in _CLEARED:
            out.append(line)
            continue

        keep, dropped_cap, dropped_int = [], [], []
        for entry in _split_entries(raw):
            bare = re.sub(r"\s*\([^)]*\)\s*", " ", entry).strip()
            # The FULL entry, not `bare`: the ownership marker is usually the
            # parenthetical, and stripping it first destroys the evidence.
            if _account_owned(entry, account, aliases):
                dropped_int.append(entry)
            elif rs.is_named_organization(bare):
                keep.append(entry)
            else:
                dropped_cap.append(entry)
        moved_cap.extend(dropped_cap); all_cap.extend(dropped_cap)
        moved_int.extend(dropped_int); all_int.extend(dropped_int)

        if not keep:
            # No organisation left in the provider column. The row's other
            # columns still describe real work, so the row stays and only the
            # unsupported claim is removed.
            cells[col] = " \u2014 "
            line = "|".join(cells)
            line = _INCUMBENT_WORDS.sub("market context / \u5e02\u573a\u53c2\u8003", line)
            line = _INCUMBENCY_GRADE.sub("\\1—\\2", line)
        else:
            kept_rows += 1
            cells[col] = " " + ", ".join(keep) + " "
            line = "|".join(cells)
            # A row whose only named organisations are market-only cannot carry
            # an incumbency claim, whatever the model wrote.
            names = [re.sub(r"\s*\([^)]*\)\s*", " ", k).strip().lower() for k in keep]
            unverified = all(n not in verified or n in market_only for n in names)
            flagged = any("(market" in k.lower() for k in keep)
            if unverified or flagged:
                line = _INCUMBENT_WORDS.sub("market context / \u5e02\u573a\u53c2\u8003", line)
                line = _INCUMBENCY_GRADE.sub("\\1market context\\2", line)
        out.append(line)

    if in_section and (moved_cap or moved_int):
        flush(out)
    text = "\n".join(out)
    if not verified and NO_PROVIDER_EN not in text:
        text = re.sub(r"(?im)^(#{1,6}\s*Existing Automation Providers[^\n]*)$",
                      lambda m: m.group(1) + "\n\n" + NO_PROVIDER, text, count=1)
    return text, {"moved_capability": list(dict.fromkeys(all_cap)),
                  "moved_internal": list(dict.fromkeys(all_int)),
                  "empty": emptied > 0 or not verified}
