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

# Models sometimes emit a malformed badge — an unclosed bold, a doubled tag, or
# the English half on its own: "**Not enough evidence / 证据不足. **Not enough
# evidence**". Stripping only the well-formed spelling left "Not enough
# evidence**" visible in the report. Anything that reads as the badge counts.
_TAG_LOOSE = re.compile(
    r"\*{0,2}\s*(?:not\s+enough\s+evidence|insufficient\s+evidence)"
    r"(?:\s*[/、]\s*证据不足)?\s*\*{0,2}", re.I)
_TAG_LOOSE_CN = re.compile(r"\*{0,2}\s*证据不足\s*\*{0,2}")

# A line whose whole message is "this information is not available" is not a
# finding, however many words it spends saying so. _SUBSTANCE_MIN alone kept
# these because they are long; the test is what the words MEAN, not how many.
_ABSENCE = re.compile(
    r"^(?:[^.。]*?\b(?:are|is|was|were|has|have|had|could|can)?\s*"
    r"(?:not|no|none)\b[^.。]*?\b(?:disclosed|available|published|reported|"
    r"documented|found|specified|provided|stated|identified|listed|detailed|"
    r"quantified|broken\s+out|given)\b[^.。]*)$"
    r"|^(?:[^.。]*(?:未(?:披露|公开|提及|说明|找到|列出)|无(?:公开|相关|具体|详细)"
    r"[^。]{0,8}(?:信息|数据|资料)|暂无[^。]{0,10})[^.。]*)$", re.I)


def is_absence_only(line):
    """True when the line reports only the ABSENCE of information.

    Citations are the guard: "revenue was X, though the split is not disclosed
    [3]" is a sourced finding and must survive. A line with no citation whose
    residue is a single absence clause has nothing to tell the reader.
    """
    if re.search(r"\[\d+\]", line or ""):
        return False
    body = residue(line)
    if not body:
        return True
    return bool(_ABSENCE.match(body.strip()))


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
    return len(residue(line)) < _SUBSTANCE_MIN or is_absence_only(line)


_BULLET_MARK = re.compile(r"^\s*(?:[-*+]|\d+\.)\s*")
_BARE_LABEL = re.compile(r"[^\s。;；,，|]{1,14}[:：]")


def _strip_unsupported_tag(line):
    """Remove only the 'not enough evidence' badge; verified/likely stay.

    Removing a badge can leave orphan punctuation behind - "Expansion: ** ** / **
    **. Historical..." must not render as "Expansion:. Historical...". Separators
    stranded straight after a colon are cleaned up, and a bullet reduced to
    nothing but a short label is dropped whole rather than left dangling.
    """
    if NOT_ENOUGH not in verdicts(line):
        return line
    out = re.sub(r"\*\*\s*Not enough evidence / 证据不足\s*\*\*", "", line)
    # Sweep any malformed remnant, then tidy the asterisks it leaves behind.
    out = _TAG_LOOSE.sub("", out)
    out = _TAG_LOOSE_CN.sub("", out)
    out = re.sub(r"\*{2,}", "", out)
    for _ in range(2):
        out = re.sub(r"([:：])\s*(?:[/、]\s*)+", r"\1 ", out)
        out = re.sub(r"([:：])\s*[.。;；,，]+\s*", r"\1 ", out)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([.。;；,，])", r"\1", out)
    out = out.rstrip(" /、").rstrip()
    # A bullet whose ONLY remaining content is a short label is filler, and the
    # whole bullet goes: "Expansion:" on its own says nothing.
    #
    # This was a trailing-character rule, r"[^\s。;；,，|]{1,14}[:：]$", with
    # nothing anchoring its left edge. The 1-14 limit was meant to catch short
    # labels only, but unanchored it matched the LAST fourteen characters of a
    # long label instead of failing to match. Chinese has no spaces to stop it,
    # so it ate real words - "（西门子、ABB、先导、海目星、库卡、发那科等）：" lost
    # everything after 先 - and English slash-lists went the same way. 28 of the
    # 37 badge-carrying lines in one report were cut, on every view and PDF,
    # while the stored report was fine. Anchoring alone was not enough either:
    # it still took "projects:" off "Major upcoming projects:". The label must
    # be the WHOLE remaining content, which is what the length limit always
    # meant.
    if _BARE_LABEL.fullmatch(_BULLET_MARK.sub("", out).strip()):
        return ""
    return out


# Some models write the absence as ordinary prose rather than as a badge:
# "关于X的证据不足。" or "…: 所给材料中为Not enough evidence。" Those sentences read
# as filler, but they can sit in the same line as real sourced findings, so the
# unit of removal is the SENTENCE, never the whole line.
_SENT_SPLIT = re.compile(r"(?<=[.。!！?？])\s*")
_ABSENCE_MARK = re.compile(
    r"not\s+enough\s+evidence|insufficient\s+evidence|证据不足|信息不足|"
    r"未(?:披露|公开|提及|说明|找到|列出|核实)|无(?:公开|相关|具体|详细)", re.I)


def prune_absence_sentences(line):
    """Drop sentences that only report an absence; keep everything else.

    A sentence is kept whenever it carries a citation or a Verified/Likely
    badge — that is sourced content, and a caveat inside it is not filler.
    """
    if not _ABSENCE_MARK.search(line or ""):
        return line
    prefix = re.match(r"^\s*(?:[-*+]|\d+\.)\s*", line or "")
    head = prefix.group(0) if prefix else ""
    body = line[len(head):] if head else line
    parts = [p for p in _SENT_SPLIT.split(body) if p.strip()]
    if len(parts) <= 1 and not _ABSENCE_MARK.search(body):
        return line
    kept = []
    for part in parts:
        if (_ABSENCE_MARK.search(part)
                and not re.search(r"\[\d+\]", part)
                and not re.search(r"Verified / 已验证|Likely / 可能", part)):
            continue
        kept.append(part)
    if not kept:
        return ""
    return head + " ".join(k.strip() for k in kept)


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
        cleaned = prune_absence_sentences(_strip_unsupported_tag(line))
        if not cleaned.strip() and line.strip():
            continue                       # every sentence was filler
        out.append(cleaned)
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
