# -*- coding: utf-8 -*-
"""Competitors and channel partners, reasoned by the model and proved by Tavily.

    model = semantic reasoning
    Tavily = evidence acquisition
    this module = guardrails, and nothing else

What it replaces is the point. Three deterministic engines used to answer these
questions: a capability lexicon that decided what a company sells, an overlap
scorer that decided who competed with it, and a role-regex table that decided who
distributed for it. On AMADA WELD TECH they produced eighteen "competitors", six
of which were AMADA itself, several of which were news headlines, and one of
which overlapped on the word "Terms" taken from a terms-and-conditions page.

Two passes, because the model must not publish from memory. The first pass is
planning, not an answer: whatever it names becomes a targeted search. The second
pass sees only fetched pages and may cite only those. A relationship Tavily
cannot verify is omitted, however confident the model was about it.

The guardrails here are deliberately mechanical - schema, identity, duplicates,
placeholders, citation validity, budgets. None of them decides whether a company
is a competitor. That question belongs to the model.

No imports from the pipeline: the model call, the search and the page fetch all
arrive as callables, which is what lets this be tested without a network or a
paid call.
"""
import json
import re

COMPETITOR, CHANNEL = "competitor", "channel"

# Budgets, per kind, per run.
MAX_SEARCHES = 4
MAX_FETCHES = 8
MAX_MODEL_CALLS = 2

COMPETITION_TYPES = ("DIRECT", "PARTIAL", "ADJACENT")
CHANNEL_ROLES = ("AUTHORIZED_DISTRIBUTOR", "DISTRIBUTOR", "REPRESENTATIVE", "RESELLER")
GTM_MODELS = ("DIRECT", "DISTRIBUTOR_LED", "REPRESENTATIVE_LED", "MIXED", "UNKNOWN")

# A name that names nothing. Not a taxonomy - a rejection list for the handful of
# strings that keep arriving where an organisation should be.
_PLACEHOLDERS = (
    "undisclosed", "unidentified", "unnamed", "unspecified", "unknown",
    "anonymous", "confidential", "undetermined", "various", "n/a", "none",
    "not applicable", "not disclosed", "tbc", "tbd", "multiple", "several",
    "competitor", "distributor", "reseller", "representative", "supplier",
    "company profile", "about us", "home", "terms", "conditions",
    "directory", "listing", "list", "guide", "overview", "profile", "page",
    "top", "best", "leading", "other", "others",
)
# A headline is not an organisation. These verbs only appear in one.
_HEADLINE = re.compile(
    r"\b(acquires?|acquired|announces?|launches?|reports?|wins?|opens?|expands?|"
    r"names?|appoints?|completes?|introduces?|unveils?)\b", re.I)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

COMPETITOR_PLAN = """Who are the competitors of {company}{site}?

Identify real companies that compete with {company}'s products or solutions for
customers or business.

This is a RESEARCH PLANNING step, not a published answer. Name the companies you
would check. Every name will be verified against public sources before anything
is used, so name only companies you actually believe compete with this exact
company, and reply with an empty list rather than filling one.

Return JSON only:
{{"need_research": true|false,
  "candidates": ["Company A", "Company B"],
  "what_they_sell": "one short phrase describing what {company} sells"}}

Set need_research true when you cannot name candidates with reasonable
confidence. Do not guess."""

COMPETITOR_VERIFY = """Based ONLY on the evidence below, who actually competes with {company}?

{evidence}

For every company you are considering, first decide one thing: does the cited
evidence actually support that this company competes with {company} for
customers, projects, contracts, or the same commercial need?

A source merely mentioning both companies is NOT sufficient.

A supplier, distributor, integrator, service provider, technology partner,
customer, or manufacturer serving {company} is NOT a competitor unless the
evidence also establishes that it sells a competing commercial offering.

Rules:
- Use only these numbered sources. Cite the numbers that support each competitor.
- EXCLUDE {company} itself, its parent, its subsidiaries and its regional
  entities however they are spelled.
- EXCLUDE directories, marketplaces, locations, page titles and news headlines.
- Operating in the same industry is not competition.
- Do not infer competition from co-mention alone.
- If evidence_supports_competition is not clearly true, omit the company.
- If the evidence establishes no real competitor, return an empty list. An empty
  list is a correct answer.

Return JSON only:
{{"competitors": [{{"name": "",
                   "competition_type": "DIRECT|PARTIAL|ADJACENT",
                   "why_it_competes": "one sentence, from the evidence, naming the
                                       competing offering or the shared customers",
                   "evidence_ids": [1],
                   "evidence_supports_competition": true,
                   "confidence": "high|medium|low"}}]}}"""

CHANNEL_PLAN = """Who are the known distributors, authorized distributors, representatives or
resellers for {company}{site}?

This is a RESEARCH PLANNING step, not a published answer. Name the organisations
you would check. Every name will be verified against public sources before
anything is used.

Answer only where you believe the relationship is with this exact company and not
with a similarly named one. Reply with an empty list rather than filling one.

Return JSON only:
{{"need_research": true|false,
  "candidates": ["Distributor A", "Distributor B"]}}

Set need_research true when you cannot name candidates with reasonable
confidence. Do not guess."""

CHANNEL_VERIFY = """Based ONLY on the evidence below, identify actual distributors, representatives
or resellers of {company}.

{evidence}

For every organisation you are considering, first decide one thing: does the
cited evidence establish that it is a distributor, authorized distributor,
representative or reseller OF THIS EXACT COMPANY?

Mere co-mention is not enough.

A technology partner, system integrator, service partner, supplier, customer, or
similarly named company is NOT a channel relationship unless the evidence
explicitly establishes distribution, resale, representation or authorization.

Rules:
- Use only these numbered sources. Cite the numbers that support each entry.
- Be careful about companies with the same or a similar name, and about a
  different company whose name resembles {company}. The evidence must tie the
  relationship to {company} itself.
- Integrating {company}'s equipment, partnering with it or servicing it is NOT
  distribution. Leave those out.
- EXCLUDE {company} itself and its own regional entities.
- If evidence_supports_relationship is not clearly true, omit the organisation.
- If the evidence establishes no relationship, return an empty list and no
  verified channel organisation will be reported.

Also state the go-to-market model the evidence supports, or UNKNOWN.

Return JSON only:
{{"go_to_market": "DIRECT|DISTRIBUTOR_LED|REPRESENTATIVE_LED|MIXED|UNKNOWN",
  "channel": [{{"name": "",
               "role": "AUTHORIZED_DISTRIBUTOR|DISTRIBUTOR|REPRESENTATIVE|RESELLER",
               "territory": "",
               "states": "the phrase in the evidence that establishes it",
               "evidence_ids": [1],
               "evidence_supports_relationship": true,
               "confidence": "high|medium|low"}}]}}"""


# ---------------------------------------------------------------------------
# Queries: targeted when the model named something, generic when it did not
# ---------------------------------------------------------------------------

def plan_queries(kind, company, candidates=()):
    """A named candidate is worth a targeted query; a blank first pass is not.

    Targeted queries ask about a RELATIONSHIP between two named companies, which
    is a far better search than "{company} competitors" and is why the first
    pass exists at all.
    """
    company = (company or "").strip()
    if not company:
        return []
    named = [c.strip() for c in (candidates or []) if c and c.strip()][:MAX_SEARCHES]
    out = []
    if kind == COMPETITOR:
        for c in named:
            out.append(('"{}" "{}" competitor OR alternative'.format(company, c)))
        generic = ['"{}" competitors'.format(company),
                   '"{}" alternatives'.format(company),
                   '"{}" vs competitors comparison'.format(company)]
    else:
        for c in named:
            out.append('"{}" "{}" distributor OR reseller OR representative'
                       .format(company, c))
        generic = ['"{}" authorized distributor'.format(company),
                   '"{}" distributor OR reseller'.format(company),
                   '"{}" sales representative'.format(company),
                   '"{}" "where to buy"'.format(company)]
    # Generic queries fill whatever the targeted ones left, and are the whole
    # plan when the model had nothing.
    for q in generic:
        if len(out) >= MAX_SEARCHES:
            break
        out.append(q)
    return out[:MAX_SEARCHES]


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------

def parse_json(text):
    """The model answers in JSON. Tolerate a code fence and surrounding prose,
    tolerate nothing else: an unparseable answer is dropped, never repaired."""
    if not text:
        return None
    body = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", body, re.S)
    if fence:
        body = fence.group(1).strip()
    try:
        return json.loads(body)
    except Exception:
        pass
    start = body.find("{")
    end = body.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(body[start:end + 1])
        except Exception:
            return None
    return None


def normalize_key(name):
    return re.sub(r"[^a-z0-9一-鿿]+", "", (name or "").lower())


def is_named_organization(name):
    """Cheap sanity, not entity resolution. Rejects the strings that kept
    arriving where an organisation should be: placeholders, page titles and
    news headlines."""
    text = (name or "").strip()
    if len(text) < 2 or len(text) > 120:
        return False
    low = re.sub(r"[^\w\s]+", " ", text.lower()).strip()
    words = low.split()
    if not words:
        return False
    # Whole phrase, first word, or nothing but filler: "Terms, conditions" and
    # "Directory of energy storage vendors" are both titles, not companies.
    if low in _PLACEHOLDERS or words[0] in _PLACEHOLDERS:
        return False
    if all(w in _PLACEHOLDERS or w in ("of", "and", "the", "for", "in") for w in words):
        return False
    if _HEADLINE.search(text):
        return False
    if text.startswith(("http://", "https://", "www.")) or text.count("/") >= 2:
        return False
    # A sentence is not a name.
    return len(text.split()) <= 8


def is_the_account(name, company, aliases=()):
    """The account is never its own competitor or its own distributor.

    Containment in either direction, because the failure mode was "AMADA Micro
    Welding Section" and "Amada Miyachi Europe" sitting in AMADA WELD TECH's own
    competitor table.
    """
    key = normalize_key(name)
    if not key:
        return True
    for other in [company] + list(aliases or []):
        ok = normalize_key(other)
        if not ok or len(ok) < 3:
            continue
        if key == ok or key.startswith(ok) or ok.startswith(key) or ok in key:
            return True
    return False


def _clearly_true(value):
    """Clearly true, not merely truthy. "unclear", "partial", "likely", None and
    a missing field are all the same answer here: omit the company."""
    if value is True:
        return True
    return isinstance(value, str) and value.strip().lower() == "true"


def _clean_rows(rows, kind, company, aliases, valid_ids):
    """Schema, identity, duplicates, placeholders, citations. In that order, and
    that is the whole of the deterministic judgement in this module."""
    out, seen, dropped = [], set(), {"schema": 0, "self": 0, "placeholder": 0,
                                     "duplicate": 0, "uncited": 0, "unsupported": 0}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            dropped["schema"] += 1
            continue
        name = (row.get("name") or "").strip()
        if kind == COMPETITOR:
            kind_field, allowed = "competition_type", COMPETITION_TYPES
        else:
            kind_field, allowed = "role", CHANNEL_ROLES
        value = (row.get(kind_field) or "").strip().upper()
        if not name or value not in allowed:
            dropped["schema"] += 1
            continue
        if not is_named_organization(name):
            dropped["placeholder"] += 1
            continue
        if is_the_account(name, company, aliases):
            dropped["self"] += 1
            continue
        ids = [i for i in (row.get("evidence_ids") or []) if i in valid_ids]
        if not ids:
            # Nothing factual is published from model memory alone.
            dropped["uncited"] += 1
            continue
        # The model was asked to adjudicate its own citation: does this evidence
        # establish the relationship, or only co-mention? Anything short of a
        # clear yes is omitted, including a missing field. Same rule both sides.
        verdict = ("evidence_supports_competition" if kind == COMPETITOR
                   else "evidence_supports_relationship")
        if not _clearly_true(row.get(verdict)):
            dropped["unsupported"] += 1
            continue
        key = normalize_key(name)
        if key in seen:
            dropped["duplicate"] += 1
            continue
        seen.add(key)
        clean = {"organization_name": name, "organization_key": key,
                 "evidence_ids": sorted(set(ids)),
                 "confidence": (row.get("confidence") or "medium").strip().lower(),
                 "discovered_by": "genai+tavily"}
        if kind == COMPETITOR:
            clean["competition_type"] = value
            clean["why_it_competes"] = (row.get("why_it_competes") or "").strip()
            clean["evidence_supports_competition"] = True
        else:
            clean["role"] = value
            clean["territory"] = (row.get("territory") or "").strip() or None
            clean["states"] = (row.get("states") or "").strip()
            clean["evidence_supports_relationship"] = True
            clean["is_representation"] = True
        out.append(clean)
    return out, dropped


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------

def empty_coverage(kind):
    cov = {"used": False, "failed": False, "model_calls": 0, "search_count": 0,
           "queries": [], "candidates_named": 0, "targeted_queries": 0,
           "pages_fetched": 0, "sources_offered": 0, "rows_proposed": 0,
           "rows_published": 0, "distinct_domains": 0, "skip_reason": None,
           "dropped_schema": 0, "dropped_self": 0, "dropped_placeholder": 0,
           "dropped_duplicate": 0, "dropped_uncited": 0, "dropped_unsupported": 0}
    if kind == CHANNEL:
        cov["go_to_market_model"] = "UNKNOWN"
    return cov


# ---------------------------------------------------------------------------
# The flow
# ---------------------------------------------------------------------------

def discover(kind, company, aliases, ask, search, fetch, progress=None,
             max_searches=MAX_SEARCHES, max_fetches=MAX_FETCHES):
    """model plans -> Tavily proves -> model answers from the evidence only.

    `ask(prompt)` returns the model's text. `search(query)` returns result dicts
    with a url. `fetch(url)` returns (text, method). Any of them may raise; the
    stage degrades and the run continues.

    Returns (rows, sources, coverage). `sources` are the fetched pages, for the
    caller to turn into evidence items under its own retention rules.
    """
    progress = progress or (lambda *_a, **_k: None)
    cov = empty_coverage(kind)
    company = (company or "").strip()
    if not company:
        cov["skip_reason"] = "no company name"
        return [], [], cov

    site = ""
    label = "competitors" if kind == COMPETITOR else "channel"

    # ---- pass 1: planning. Nothing here is published. -------------------
    candidates = []
    plan_prompt = (COMPETITOR_PLAN if kind == COMPETITOR else CHANNEL_PLAN).format(
        company=company, site=site)
    try:
        cov["model_calls"] += 1
        plan = parse_json(ask(plan_prompt)) or {}
        candidates = [c for c in (plan.get("candidates") or [])
                      if isinstance(c, str) and c.strip()]
        candidates = [c for c in candidates if not is_the_account(c, company, aliases)]
    except Exception as e:
        cov["failed"] = True
        progress(label, "WARN planning call failed ({}) - continuing with generic "
                        "queries".format(type(e).__name__))
    cov["candidates_named"] = len(candidates)

    queries = plan_queries(kind, company, candidates)
    cov["targeted_queries"] = min(len(candidates), max_searches)
    if not queries:
        cov["skip_reason"] = "no queries could be planned"
        return [], [], cov

    # ---- evidence acquisition -------------------------------------------
    hits, seen_urls = [], set()
    for query in queries[:max_searches]:
        try:
            results = search(query) or []
        except Exception as e:
            cov["failed"] = True
            progress(label, "WARN search failed ({}) - continuing".format(type(e).__name__))
            continue
        cov["search_count"] += 1
        cov["used"] = True
        cov["queries"].append(query)
        for r in results:
            url = (r or {}).get("url")
            if url and url not in seen_urls:
                seen_urls.add(url)
                hits.append(r)

    sources = []
    for hit in hits[:max_fetches]:
        try:
            text, _method = fetch(hit["url"])
        except Exception:
            text = ""
        cov["pages_fetched"] += 1
        if not text:
            continue
        sources.append({"url": hit["url"], "title": hit.get("title") or "",
                        "text": text})
    cov["sources_offered"] = len(sources)
    if not sources:
        cov["skip_reason"] = "research returned no readable sources"
        progress(label, "No readable sources for {} - reporting none found".format(label))
        return [], [], cov

    # ---- pass 2: the answer, from the evidence only ----------------------
    numbered = "\n\n---\n\n".join(
        "[{}] {}\nURL: {}\n{}".format(i, s["title"] or "(untitled)", s["url"],
                                      s["text"])
        for i, s in enumerate(sources, 1))
    verify_prompt = (COMPETITOR_VERIFY if kind == COMPETITOR else CHANNEL_VERIFY).format(
        company=company, evidence=numbered)
    try:
        cov["model_calls"] += 1
        answer = parse_json(ask(verify_prompt)) or {}
    except Exception as e:
        cov["failed"] = True
        cov["skip_reason"] = "verification call failed ({})".format(type(e).__name__)
        progress(label, "WARN verification call failed ({}) - continuing"
                        .format(type(e).__name__))
        return [], sources, cov

    proposed = answer.get("competitors" if kind == COMPETITOR else "channel") or []
    cov["rows_proposed"] = len(proposed) if isinstance(proposed, list) else 0
    rows, dropped = _clean_rows(proposed, kind, company, aliases,
                                set(range(1, len(sources) + 1)))
    for reason, n in dropped.items():
        cov["dropped_" + reason] = n

    by_id = {i: s for i, s in enumerate(sources, 1)}
    for row in rows:
        row["source_urls"] = [by_id[i]["url"] for i in row["evidence_ids"] if i in by_id]
    cov["rows_published"] = len(rows)
    cov["distinct_domains"] = len({_domain(u) for r in rows for u in r["source_urls"]})
    if kind == CHANNEL:
        gtm = (answer.get("go_to_market") or "UNKNOWN").strip().upper()
        cov["go_to_market_model"] = gtm if gtm in GTM_MODELS else "UNKNOWN"
    progress(label, "{}: {} search(es), {} source(s), {} verified".format(
        label.capitalize(), cov["search_count"], len(sources), len(rows)))
    return rows, sources, cov


def _domain(url):
    m = re.match(r"https?://([^/]+)", url or "")
    return (m.group(1).lower().replace("www.", "") if m else "")
