"""Who competes with the TARGET ACCOUNT.

Not who competes with SKEQI at the account - that question is Existing
Automation Providers, and conflating the two is why ACRO's competitor table
listed ACRO's own robot suppliers.

Two rules do the work.

Search concepts are RANKED, not enumerated. ACRO's profile yields fourteen
capabilities, and templating all of them would spend the budget on "assembly"
and "automation" - words shared by every automation company alive, which return
the same large vendors for every account. Specific offerings come first, generic
capability words are used only as qualifiers, and never alone.

Competition is scored on three INDEPENDENT dimensions before any label is
derived, so "operates in the same industry" can never by itself produce a
competitor. That is the single most common false positive in this domain and the
one the classifier is built to refuse.

No network here: the caller supplies a search client and the existing
verification pipeline. Import-safe.
"""
import re
from collections import OrderedDict

import research_service as rs

DIRECT, PARTIAL, ADJACENT = "DIRECT", "PARTIAL", "ADJACENT"
NOT_A_COMPETITOR = None

MAX_COMPETITOR_SEARCHES = 6
BATCH_1, BATCH_2 = 3, 3

# Words that describe the whole industry rather than a company's distinctive
# offering. Useful as QUALIFIERS, useless as a search subject: every automation
# firm on earth matches "automation" and "assembly".
_GENERIC = {"assembly", "automation", "automated", "engineering", "engineered",
            "manufacturing", "manufacture", "industrial", "systems", "system",
            "solutions", "solution", "equipment", "technology", "technologies",
            "services", "products", "custom", "integration", "integrated"}


def _specificity(concept):
    """How much a concept narrows the field. Two informative words beat one."""
    words = [w for w in re.split(r"[\s/&,\-]+", (concept or "").lower()) if w]
    if not words:
        return 0
    informative = [w for w in words if w not in _GENERIC]
    if not informative:
        return 0                       # purely generic: never a search subject
    return len(informative) * 2 + (1 if len(words) > 1 else 0)


def rank_concepts(profile, limit=4):
    """The few concepts worth spending a search on, most specific first.

    Order follows how much each narrows the field: named offerings, then project
    or product types, then distinctive capabilities. Industries and geography are
    returned separately because they QUALIFY a search rather than being one.
    """
    ev = (profile or {}).get("supporting_evidence") or {}
    seen, scored = set(), []

    def consider(concept, kind, weight):
        c = (concept or "").strip().lower()
        if not c or c in seen:
            return
        spec = _specificity(c)
        if not spec:
            return
        seen.add(c)
        # Evidence breadth breaks ties: a concept on three pages is more likely
        # to be what the company actually leads with than one on a single page.
        pages = len(set((ev.get(kind) or {}).get(concept, [])))
        scored.append({"concept": concept, "kind": kind, "specificity": spec,
                       "pages": pages, "rank": weight})

    for c in (profile or {}).get("offerings", []):
        consider(c, "offerings", 30)
    for c in (profile or {}).get("project_types", []):
        consider(c, "project_types", 20)
    for c in (profile or {}).get("capabilities", []):
        consider(c, "capabilities", 10)

    # Kind FIRST, then specificity, then evidence breadth. Blending them into one
    # score let a two-word capability outrank a named offering, which inverts the
    # order that matters: an offering is what the company says it sells, and a
    # capability is a property of the whole industry.
    scored.sort(key=lambda x: (-x["rank"], -x["specificity"], -min(x["pages"], 3),
                               x["concept"]))
    return {
        "concepts": scored[:limit],
        "qualifiers": {
            "industries": (profile or {}).get("industries", [])[:2],
            "geography": (profile or {}).get("geography", [])[:1],
        },
        "rejected_generic": sorted(c for c in (profile or {}).get("capabilities", [])
                                   if not _specificity(c)),
    }


def plan_intents(profile, batch=1, covered=()):
    """Queries built from the ranked concepts. The account NAME is deliberately
    absent: a competitor's own page never mentions the account it competes with.
    """
    plan = rank_concepts(profile, limit=BATCH_1 + BATCH_2)
    inds = plan["qualifiers"]["industries"]
    geo = plan["qualifiers"]["geography"]
    pool = plan["concepts"]
    out = []
    start = 0 if batch == 1 else BATCH_1
    for c in pool[start:start + (BATCH_1 if batch == 1 else BATCH_2)]:
        key = c["concept"]
        if key in covered:
            continue
        # One qualifier per query: two narrows it to nothing, none returns the
        # whole industry.
        qual = inds[0] if (batch == 1 and inds) else (geo[0] if geo else (inds[0] if inds else ""))
        q = "{} companies{}".format(key, (" " + qual) if qual else "")
        out.append((key, q))
    return out


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def score_overlap(text, profile, account, aliases=()):
    """Three dimensions, measured independently and never collapsed early.

    Kept apart on purpose: collapsing them is what lets "also does automation"
    become "competitor". The label is derived afterwards from which dimensions
    actually hold.
    """
    body = (text or "")[:20000]
    low = body.lower()

    offerings = [c for c in (profile.get("offerings") or []) if _specificity(c)]
    caps = [c for c in (profile.get("capabilities") or []) if _specificity(c)]
    inds = profile.get("industries") or []
    geo = profile.get("geography") or []

    def hit(items):
        return [i for i in items if i and i.lower() in low]

    off_hits = hit(offerings) + hit(caps)
    ind_hits = hit(inds)
    geo_hits = hit(geo)

    return {
        "offering_overlap": bool(off_hits),
        "offering_matches": off_hits[:5],
        "customer_or_industry_overlap": bool(ind_hits),
        "industry_matches": ind_hits[:3],
        "geographic_or_market_overlap": bool(geo_hits),
        "geography_matches": geo_hits[:2],
        # A page that names the account AND a competitive verb is the strongest
        # signal there is, but it is recorded, never required.
        "named_together": bool(re.search(
            r"(compet\w+|rival|alternativ\w+|versus|vs\.)", low)) and any(
                (rs._mentions(n, body) if n.isascii() else n in body)
                for n in [account] + list(aliases or []) if n),
    }


def classify(dims):
    """DIRECT / PARTIAL / ADJACENT, or not a competitor at all.

    Industry membership alone returns None. That is the rule the whole module
    exists to enforce: sharing a market is not competing for the same work.
    """
    off = dims.get("offering_overlap")
    ind = dims.get("customer_or_industry_overlap")
    geo = dims.get("geographic_or_market_overlap")
    if not off:
        return NOT_A_COMPETITOR          # no offering overlap -> not a competitor
    if off and ind and geo:
        return DIRECT
    if off and (ind or geo):
        return PARTIAL
    return ADJACENT                      # offering overlap only


def empty_coverage():
    return {"used": False, "search_count": 0, "batches": 0, "candidates": 0,
            "verified": 0, "retained": 0, "distinct_domains": 0,
            "contribution_count": 0, "direct": 0, "partial": 0, "adjacent": 0,
            "rejected_same_industry": 0, "skip_reason": None,
            "profile_confidence": None}


def coverage_is_useful(cov):
    """Breadth of VERIFIED competitors, not volume of results."""
    return (cov["contribution_count"] >= 3 and cov["direct"] >= 1
            and cov["distinct_domains"] >= 2)


def discover(client, profile, verify, progress=None, ceiling=MAX_COMPETITOR_SEARCHES):
    """Two bounded batches. Skips entirely when the profile cannot support it."""
    progress = progress or (lambda *_a, **_k: None)
    cov = empty_coverage()
    cov["profile_confidence"] = (profile or {}).get("confidence")
    if not (profile or {}).get("competitor_discovery_ready"):
        cov["skip_reason"] = (profile or {}).get("competitor_skip_reason") \
            or "profile not ready for competitor discovery"
        progress("competitors", "Competitor discovery skipped - {}".format(cov["skip_reason"]))
        return [], cov

    kept, covered, seen = [], set(), set()
    for batch in (1, 2):
        if cov["search_count"] >= ceiling:
            break
        intents = plan_intents(profile, batch=batch, covered=covered)
        intents = intents[:max(0, ceiling - cov["search_count"])]
        if not intents:
            break
        before = cov["contribution_count"]
        cov["batches"] = batch
        for key, query in intents:
            try:
                hits = client.search(query)
            except Exception as e:
                progress("competitors", "WARN competitor search failed ({}) - continuing"
                                        .format(type(e).__name__))
                continue
            cov["search_count"] += 1
            cov["used"] = True
            fresh = [h for h in hits if h.get("url") not in seen]
            seen.update(h.get("url") for h in hits)
            cov["candidates"] += len(fresh)
            out = verify(fresh, key) or {}
            kept.extend(out.get("evidence") or [])
            cov["verified"] += out.get("verified", 0)
            cov["contribution_count"] += out.get("competitors", 0)
            cov["direct"] += out.get("direct", 0)
            cov["partial"] += out.get("partial", 0)
            cov["adjacent"] += out.get("adjacent", 0)
            cov["rejected_same_industry"] += out.get("rejected_same_industry", 0)
            if out.get("competitors"):
                covered.add(key)
        cov["distinct_domains"] = len({e.get("domain") for e in kept if e.get("domain")})
        cov["retained"] = len(kept)
        gained = cov["contribution_count"] - before
        progress("competitors", "Competitor discovery batch {}: {} search(es), {} verified "
                                "competitor(s)".format(batch, cov["search_count"],
                                                       cov["contribution_count"]))
        if coverage_is_useful(cov):
            progress("competitors", "Competitor coverage sufficient - stopping")
            break
        if gained == 0:
            if batch == 1:
                progress("competitors", "First batch found no verified competitor - stopping")
            break
    return kept, cov
