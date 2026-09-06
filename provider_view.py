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


# --------------------------------------------------------------------------
# Output correction
# --------------------------------------------------------------------------

_EMPH = re.compile(r"[*_`]+")
_CITE = re.compile(r"\[\d+(?:\s*,\s*\d+)*\]")


def _cell_text(cell):
    return _CITE.sub("", _EMPH.sub("", cell or "")).strip()


def _is_separator(row):
    return bool(re.fullmatch(r"[\s|:\-]+", row or ""))


def _account_owned(text, account, aliases=()):
    """The account's own engineering is not an external provider."""
    t = (text or "").lower()
    if re.search(r"\b(internal|in-house|inhouse|own team|own engineering)\b", t) \
            or any(k in text for k in ("内部", "自有", "自研")):
        return True
    names = [account] + list(aliases or [])
    core = rs.core_name(account)
    if core:
        names.append(core)
    return any(n and (rs._mentions(n, text) if n.isascii() else n in text)
               for n in names if n)


def enforce(report, providers=None, account="", aliases=()):
    """Correct the rendered report so a category can never read as a supplier.

    Conservative on purpose. Only tables inside provider sections are touched,
    only their FIRST column is judged, and nothing is deleted - a rejected cell
    is moved to the field it belongs in and listed under the table, so the
    information survives and only the CLAIM changes.
    """
    if not report:
        return report, {"moved_capability": [], "moved_internal": [], "empty": False}
    verified = {p["name"].lower(): p for p in (providers or []) if p.get("name")}
    market_only = {n for n, p in verified.items()
                   if p.get("relationship") == rs.REL_MARKET}

    out, moved_cap, moved_int = [], [], []
    # The per-section buffers are cleared when they are flushed under a table, so
    # the caller needs its own record or the report of what moved comes back empty.
    all_cap, all_int = [], []
    in_provider_section = False
    kept_rows_in_table = 0
    table_open = False
    emptied_tables = 0

    def flush_notes(buf):
        if moved_cap:
            buf.append("")
            buf.append("**{}**: {}".format(CAPABILITY_LABEL,
                                           "; ".join(dict.fromkeys(moved_cap))))
        if moved_int:
            buf.append("")
            buf.append("**{}**: {}".format(INTERNAL_LABEL,
                                           "; ".join(dict.fromkeys(moved_int))))

    lines = report.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("#"):
            # Leaving a provider section: attach whatever was moved out of it.
            if in_provider_section and (moved_cap or moved_int):
                flush_notes(out)
                moved_cap, moved_int = [], []
            head = _cell_text(line).lower()
            in_provider_section = any(h in head for h in PROVIDER_HEADINGS)
            table_open = False
            out.append(line)
            continue
        if not in_provider_section or not line.strip().startswith("|"):
            if table_open and not line.strip().startswith("|"):
                if kept_rows_in_table == 0 and not verified:
                    out.append(NO_PROVIDER)
                    emptied_tables += 1
                table_open = False
            out.append(line)
            continue

        cells = line.split("|")
        if _is_separator(line):
            out.append(line)
            continue
        first = _cell_text(cells[1] if len(cells) > 1 else "")
        low = first.lower()
        # Header rows and empty rows pass through untouched.
        if not first or low in ("company", "provider", "organization", "organisation",
                                "process or capability", "公司", "供应商"):
            out.append(line)
            table_open = True
            continue

        if rs.is_named_organization(first) and not _account_owned(first, account, aliases):
            kept_rows_in_table += 1
            if low in market_only and _INCUMBENT_WORDS.search(line):
                # Named, but only market context. Strip the incumbency claim
                # rather than the row: the organisation is real, the claim is not.
                line = _INCUMBENT_WORDS.sub("market context / 市场参考", line)
            out.append(line)
            table_open = True
            continue

        # Rejected as a company. Keep the content, move the claim.
        if _account_owned(first, account, aliases):
            moved_int.append(first); all_int.append(first)
        else:
            moved_cap.append(first); all_cap.append(first)
        table_open = True

    if in_provider_section and (moved_cap or moved_int):
        flush_notes(out)
    text = "\n".join(out)
    if not verified and NO_PROVIDER_EN not in text:
        # Nothing verified anywhere: say so once, in the provider section.
        text = re.sub(r"(?im)^(#{1,6}\s*Existing Automation Providers[^\n]*)$",
                      lambda m: m.group(1) + "\n\n" + NO_PROVIDER, text, count=1)
    return text, {"moved_capability": list(dict.fromkeys(all_cap)),
                  "moved_internal": list(dict.fromkeys(all_int)),
                  "empty": emptied_tables > 0 or not verified}
