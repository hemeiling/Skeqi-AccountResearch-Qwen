# -*- coding: utf-8 -*-
"""The bounded representation of evidence that synthesis is allowed to see.

    rich durable evidence  ->  bounded synthesis representation  ->  safe request

AMADA WELD TECH retrieved sixteen good sources and produced no report: the
provider answered HTTP 413 before counting a single input token. Retrieval was
not at fault and is not touched here. What was missing is a ceiling on the
REQUEST, measured the way the provider measures it.

Two measurement traps this module exists to avoid.

BYTES, NOT CHARACTERS. Every cap elsewhere in the pipeline counts characters. A
Chinese character is three bytes of UTF-8, so a 900-character cap is 900 bytes of
English and 2,700 of Chinese. Budgeting in characters silently gives a CJK-heavy
account three times the payload for the same nominal limit.

THE SERIALIZED REQUEST, NOT THE PROMPT. The request is posted as JSON with
ensure_ascii on, which escapes every non-ASCII character to \\uXXXX - six bytes
per Chinese character. On the AMADA payload that escaping added 19.8%: a 76,940
byte prompt became a 97,986 byte request. A budget that measures the prompt is
measuring the wrong thing.

Compaction is diversity-aware on purpose. Repetition is removed before sources
are, because sixteen compact sources answer more questions than four long ones,
and because dropping a source destroys a citation the report may already need.

No network, no model call, no imports from the pipeline: this module is pure
measurement and text selection, which is what makes it testable without a run.
"""
import json
import re

# Per-tier ceiling for one evidence item's text, in BYTES of the final request.
# Tier 1 is the account's own site and earns the most room; tier 6 is a weak
# third-party mention and earns the least.
ITEM_BYTES = {1: 6000, 2: 4500, 3: 3000, 4: 2400, 5: 2000, 6: 1200}
ITEM_BYTES_DEFAULT = 1200

# No item is compacted below this. Reaching the floor everywhere is the signal
# that dropping a source is finally justified.
ITEM_FLOOR = 400
EMERGENCY_ITEM_FLOOR = 300

# Never leave synthesis with fewer than this many sources, unless retrieval
# genuinely found fewer. Breadth of citation is the last thing to go.
MIN_SOURCES = 6

_CJK = re.compile(r"[一-鿿]")
# Passages carrying figures, dates or company suffixes are the ones a sales
# briefing needs; they survive compaction ahead of prose.
_FACTUAL = re.compile(
    r"\d|%|\$|€|¥|USD|RMB|GmbH|\bInc\b|\bLtd\b|\bCo\.|\bCorp\b|\bAG\b|\bS\.p\.A",
    re.I)
# Industry-neutral vocabulary, both languages. Normalisation only: it decides
# which passage of a page is kept, never what the page is taken to mean.
_RELEVANT = (
    "weld", "laser", "vision", "assembly", "automation", "robot", "inspection",
    "test", "manufactur", "production", "factory", "plant", "supplier",
    "distributor", "customer", "partner", "acquisition", "revenue", "capacity",
    "焊接", "激光", "视觉", "装配", "自动化", "机器人", "检测", "测试",
    "制造", "生产", "工厂", "供应商", "经销商", "客户", "合作", "产能",
)


def utf8(text):
    return len((text or "").encode("utf-8"))


def serialized_bytes(payload):
    """Exactly what post_json puts on the wire, escaping included."""
    return len(json.dumps(payload).encode("utf-8"))


def estimate_tokens(text):
    """Rough and labelled as such: one token per CJK character, 3.6 bytes per
    token elsewhere. Used for diagnostics, never for a limit."""
    text = text or ""
    cjk = len(_CJK.findall(text))
    return int(cjk + max(0, len(text) - cjk) / 3.6)


def split_passages(text):
    parts = [p.strip() for p in
             re.split(r"\n{2,}|(?<=[.!?。！？])[ \t]+", text or "") if p.strip()]
    return [p for p in parts if len(p) > 20]


def _shingle(passage):
    return frozenset(re.findall(r"\w{4,}", (passage or "").lower())[:14])


def _score(passage, account, aliases):
    """What makes a passage worth its bytes."""
    low = passage.lower()
    s = min(len(passage), 400) / 400.0
    names = [n for n in [account] + list(aliases or []) if n]
    if any(n.split()[0].lower() in low for n in names if n.split()):
        s += 3.0
    s += sum(1.0 for w in _RELEVANT if w in low)
    if _FACTUAL.search(passage):
        s += 1.5
    return s


def compact_text(text, budget, account, aliases, seen):
    """The strongest passages that fit, re-emitted in their original order.

    Not the first N characters: the top of a page is a navigation menu and a
    cookie banner, and truncation reliably keeps those and discards the facts.
    Passages that substantially repeat something another source already said are
    skipped, which is how eight near-identical boilerplate blocks become one.
    """
    text = text or ""
    if utf8(text) <= budget:
        return text, 0
    parts = split_passages(text)
    if not parts:
        # No sentence structure to work with - a table dump or extracted PDF
        # text. Cut on a character boundary that still fits in bytes.
        return _clip(text, budget), 0
    order = {id(p): i for i, p in enumerate(parts)}
    picked, used, skipped = [], 0, 0
    for p in sorted(parts, key=lambda x: -_score(x, account, aliases)):
        sh = _shingle(p)
        if sh and any(len(sh & s) >= 8 for s in seen):
            skipped += 1
            continue
        cost = utf8(p) + 1
        if used + cost > budget:
            continue
        picked.append(p)
        used += cost
        seen.append(sh)
    if not picked:
        return _clip(text, budget), skipped
    picked.sort(key=lambda p: order.get(id(p), 0))
    return "\n".join(picked), skipped


def _clip(text, budget):
    """Byte-exact clip that never splits a character."""
    raw = (text or "").encode("utf-8")[:max(0, budget)]
    return raw.decode("utf-8", "ignore")


def empty_stats():
    return {"budget_bytes": 0, "payload_bytes": 0, "estimated_input_tokens": 0,
            "compaction_applied": False, "emergency_compaction": False,
            "evidence_items_before": 0, "evidence_items_after": 0,
            "evidence_bytes_before": 0, "evidence_bytes_after": 0,
            "sources_preserved": 0, "domains_preserved": 0,
            "passages_deduplicated": 0}


def compact(evidence, account, available, render, aliases=(),
            ceilings=None, floor=ITEM_FLOOR, min_sources=MIN_SOURCES):
    """Three passes, in order of what they cost the report.

      1. bound each item to its tier's ceiling, by passage selection
      2. shrink the largest item repeatedly, down to the floor
      3. only then drop, duplicate domains and weakest tiers first

    `render` is the caller's evidence renderer, so this measures the real block
    rather than an approximation of it. Returns (items, stats).
    """
    ceilings = ceilings or ITEM_BYTES
    items = [dict(e) for e in (evidence or [])]
    stats = empty_stats()
    stats["evidence_items_before"] = len(items)
    stats["evidence_bytes_before"] = utf8(render(items)) if items else 0
    if not items:
        return items, stats

    seen = []
    for it in items:
        cap = ceilings.get(it.get("tier"), ITEM_BYTES_DEFAULT)
        it["text"], skipped = compact_text(it.get("text"), cap, account, aliases, seen)
        stats["passages_deduplicated"] += skipped

    # Pass 2: the largest item pays first, so breadth survives depth.
    guard = 0
    while utf8(render(items)) > available and guard < 200:
        guard += 1
        biggest = max(items, key=lambda e: utf8(e.get("text")))
        size = utf8(biggest.get("text"))
        if size <= floor:
            break
        target = max(floor, int(size * 0.75))
        biggest["text"], _ = compact_text(biggest.get("text"), target,
                                          account, aliases, [])

    # Pass 3: dropping is the last resort, and duplicate domains go first.
    while utf8(render(items)) > available and len(items) > min(min_sources, len(items)):
        by_domain = {}
        for e in items:
            by_domain.setdefault(e.get("domain") or e.get("url"), []).append(e)
        duplicates = [e for e in items
                      if len(by_domain.get(e.get("domain") or e.get("url"), [])) > 1]
        pool = duplicates or items
        victim = max(pool, key=lambda e: (e.get("tier") or 6, -utf8(e.get("text"))))
        items.remove(victim)
        if len(items) <= min_sources:
            break

    stats["evidence_items_after"] = len(items)
    stats["evidence_bytes_after"] = utf8(render(items))
    stats["sources_preserved"] = len(items)
    stats["domains_preserved"] = len({e.get("domain") for e in items if e.get("domain")})
    stats["compaction_applied"] = (
        stats["evidence_bytes_after"] < stats["evidence_bytes_before"])
    return items, stats
