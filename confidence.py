"""
Confidence-tag normalisation.

Models emit the same verdict in many shapes: **Verified**, **[Verified]**,
[Verified], plain Verified, 已验证, "Likely / partially supported", 证据不足, and
combinations. Programmatic counting must not depend on Markdown formatting, so
every variant is mapped to a canonical value and a single display form.

Canonical values:  verified | likely | not_enough_evidence
"""

import re

VERIFIED = "verified"
LIKELY = "likely"
NOT_ENOUGH = "not_enough_evidence"

CANONICAL_ORDER = (VERIFIED, LIKELY, NOT_ENOUGH)

DISPLAY = {
    VERIFIED: "Verified / 已验证",
    LIKELY: "Likely / 可能",
    NOT_ENOUGH: "Not enough evidence / 证据不足",
}

# Order matters: "not enough" is checked before "likely"/"verified" because a
# phrase such as "not enough evidence to verify" contains both signals.
_KEYWORDS = (
    (NOT_ENOUGH, re.compile(
        r"not\s+enough\s+evidence|insufficient\s+evidence|no\s+evidence|证据不足|无足够证据|缺乏证据",
        re.I)),
    (LIKELY, re.compile(
        r"likely|partially\s+supported|partial\s+support|可能|部分支持|部分证实", re.I)),
    (VERIFIED, re.compile(r"verified|confirmed|已验证|已证实", re.I)),
)

# A tag "container": bolded and/or bracketed, short, containing letters or CJK.
# Citation markers like [3] never match because a keyword must be present.
_CONTAINER = re.compile(
    r"""(?P<all>
          \*\*\s*\[(?P<b1>[^\]\n]{2,90})\]\s*\*\*     # **[Verified]**
        | \*\*(?P<b2>[^*\n]{2,90})\*\*                 # **Verified**
        | \[(?P<b3>[^\]\n]{2,90})\]                    # [Verified]
        )""",
    re.X)

# Bare phrases outside any markup. Multi-word phrases are unambiguous anywhere;
# single words (Verified / 已验证 / 可能) are only treated as a tag when they end a
# clause, so ordinary prose such as "we verified the figure with" is left alone.
_BARE_PHRASE = re.compile(
    r"(?<![\w\[*])(not\s+enough\s+evidence|insufficient\s+evidence|"
    r"likely\s*/\s*partially\s+supported|partially\s+supported|证据不足)(?![\w\]*])",
    re.I)
_BARE_WORD = re.compile(
    r"(?<![\w\[*])(Verified|Confirmed|已验证|已证实|可能|部分支持)"
    r"(?=\s*(?:\[\d+\]\s*)*[\s]*(?:[.。;；,，)\]]|$))",
    re.M)

_MARKER = "⁦"          # invisible sentinel so a pass never re-processes its own output


def classify(text):
    """Return the canonical value for a fragment, or None if it is not a tag."""
    if not text:
        return None
    for value, pattern in _KEYWORDS:
        if pattern.search(text):
            return value
    return None


def normalize_text(text):
    """Rewrite every confidence variant to one canonical, bolded display form."""
    if not text:
        return text or ""

    def sub_container(m):
        inner = m.group("b1") or m.group("b2") or m.group("b3") or ""
        value = classify(inner)
        if not value:
            return m.group("all")            # ordinary bold/bracket text, leave alone
        return "%s**%s**" % (_MARKER, DISPLAY[value])

    out = _CONTAINER.sub(sub_container, text)

    def sub_bare(m):
        value = classify(m.group(1))
        return "%s**%s**" % (_MARKER, DISPLAY[value]) if value else m.group(0)

    out = _BARE_PHRASE.sub(sub_bare, out)
    out = _BARE_WORD.sub(sub_bare, out)
    return out.replace(_MARKER, "")


def extract(text):
    """Structured counts. Operates on canonical values, not Markdown."""
    counts = {v: 0 for v in CANONICAL_ORDER}
    if not text:
        return {**counts, "total": 0}
    for m in _CONTAINER.finditer(normalize_text(text)):
        inner = m.group("b1") or m.group("b2") or m.group("b3") or ""
        value = classify(inner)
        if value:
            counts[value] += 1
    counts["total"] = sum(counts[v] for v in CANONICAL_ORDER)
    return counts


# ---------------------------------------------------------------------------
# Display filtering
# ---------------------------------------------------------------------------
# "Not enough evidence" is useful as internal signal and useless as reading
# material: a briefing full of placeholder bullets buries the findings that do
# exist. The tags stay in the stored report and in extract() counts; they are
# removed only where a human reads the output.

LIMITED_EN = "Limited public information available for this section."
LIMITED_CN = "本部分公开信息有限。"

_HEADING = re.compile(r"^#{1,3}\s+")
_LANG_EN = re.compile(r"^\*\*English[:：]?\*\*[:：]?\s*$", re.I)
_LANG_CN = re.compile(r"^\*\*中文[:：]?\*\*[:：]?\s*$")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_SEP = re.compile(r"^[\s:\-|]+$")


def verdicts(line):
    """Every canonical confidence value tagged on one line."""
    found = set()
    for m in _CONTAINER.finditer(line or ""):
        inner = m.group("b1") or m.group("b2") or m.group("b3") or ""
        value = classify(inner)
        if value:
            found.add(value)
    return found


# A tagged line is only a placeholder if nothing survives once the tag, the
# citations and the list markup are removed. Real reports mostly read
# "<useful finding> [2]. **Not enough evidence**", and deleting those would
# throw away the findings the salesperson actually wants.
_SUBSTANCE_MIN = 30

_TAG_ONLY = re.compile(
    r"\*\*\s*(?:Not enough evidence / 证据不足|Likely / 可能|Verified / 已验证)\s*\*\*")


def residue(line):
    """What is left of a line once tags, citations and markup are removed."""
    s = _TAG_ONLY.sub(" ", line or "")
    s = re.sub(r"\[\d+\]", " ", s)                     # citation markers
    s = re.sub(r"^\s*(?:[-*+]|\d+\.)\s*", " ", s)       # bullet / number marker
    s = re.sub(r"[|*`>#]", " ", s)                      # table pipes, bold, quotes
    s = re.sub(r"[\s:：./、,，。;；()（）\-]+", " ", s)     # punctuation and labels
    return s.strip()


def is_placeholder(line):
    """A line whose only message is 'we found nothing'."""
    tags = verdicts(line)
    if NOT_ENOUGH not in tags or (tags & {VERIFIED, LIKELY}):
        return False
    return len(residue(line)) < _SUBSTANCE_MIN


def _strip_unsupported_tag(line):
    """Remove only the 'not enough evidence' badge; verified/likely stay.

    Removing a badge can leave orphan punctuation behind - "Expansion: ** ** / **
    **. Historical..." must not render as "Expansion:. Historical...". Separators
    stranded straight after a colon are cleaned up, and a label left with nothing
    after it at all is dropped.
    """
    if NOT_ENOUGH not in verdicts(line):
        return line
    out = re.sub(r"\*\*\s*Not enough evidence / 证据不足\s*\*\*", "", line)
    for _ in range(2):
        out = re.sub(r"([:：])\s*(?:[/、]\s*)+", r"\1 ", out)
        out = re.sub(r"([:：])\s*[.。;；,，]+\s*", r"\1 ", out)
    out = re.sub(r"[;；,，.。]?\s*[^\s。;；,，|]{1,14}[:：]\s*$", "", out)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([.。;；,，])", r"\1", out)
    return out.rstrip(" /、").rstrip()


def _drop(line):
    return is_placeholder(line)


def _filter_block(lines):
    """Filter one language block, tables included. Returns (kept, had_content)."""
    out, table, content = [], [], False
    def flush():
        nonlocal content
        if not table:
            return
        head = [r for r in table[:2]]
        body = [_strip_unsupported_tag(r) for r in table[2:] if not _drop(r)]
        # A header with every data row removed is noise, not a table.
        if body:
            out.extend(head + body)
            content = True
        table.clear()

    for line in lines:
        if _TABLE_ROW.match(line):
            table.append(line)
            continue
        flush()
        if not line.strip():
            out.append(line)
            continue
        if _drop(line):
            continue
        out.append(_strip_unsupported_tag(line))
        if re.match(r"^\s*([-*+]|\d+\.)\s+\S", line) or line.strip():
            content = True
    flush()
    return out, content


def strip_unsupported(text):
    """Report with 'not enough evidence' items removed, for display and PDFs.

    Sections left with nothing get one short line instead of a wall of
    placeholders. The stored report is untouched.
    """
    if not text:
        return text or ""
    lines = normalize_text(text).split("\n")
    out = []
    # (start index into `out`, language) for each block currently open
    block, lang = [], None

    def close():
        if lang is None and not block:
            return
        kept, had = _filter_block(block)
        if had:
            out.extend(kept)
        else:
            out.append("- " + (LIMITED_CN if lang == "cn" else LIMITED_EN))
        block.clear()

    for line in lines:
        if _HEADING.match(line):
            close()
            lang = None
            out.append(line)
            continue
        if _LANG_EN.match(line.strip()):
            close()
            lang = "en"
            out.append(line)
            continue
        if _LANG_CN.match(line.strip()):
            close()
            lang = "cn"
            out.append(line)
            continue
        block.append(line)
    close()
    # Collapse the blank-line runs that removed bullets leave behind, but keep a
    # separator before each heading and language marker so Markdown still parses.
    cleaned, blanks = [], 0
    for line in out:
        starts_block = bool(_HEADING.match(line) or _LANG_EN.match(line.strip())
                            or _LANG_CN.match(line.strip()))
        if line.strip():
            if starts_block and cleaned and cleaned[-1].strip():
                cleaned.append("")
            blanks = 0
            cleaned.append(line)
        else:
            blanks += 1
            if blanks < 2:
                cleaned.append(line)
    return "\n".join(cleaned).strip() + "\n"
