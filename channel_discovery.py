# -*- coding: utf-8 -*-
"""Who distributes, represents or resells the ACCOUNT's offerings.

The asymmetry with competitor discovery is the whole design. A competitor's page
is about the competitor and rarely names the account, so competitor queries omit
the account name and verification runs on the candidate. A channel page is the
opposite: a distributor exists to say WHOSE products it carries, so the account
name is the query, and the page must name the account or it is not a channel
page at all.

The failure mode this module refuses is calling a partner a distributor. Being
in an account's orbit - integrating its equipment, co-marketing with it, servicing
it - is not representation. A role is assigned only from an explicit statement of
representation linking the candidate to the account. Without one, an integrator
stays an integrator.

Absence is a legitimate finding. Zero verified distributors is an answer, and the
report says so rather than filling a table.

No network, no model call. Import-safe.
"""
import re
from collections import OrderedDict

# Deliberately importing NOTHING from the pipeline. offering_profile imports
# research_service, which imports this module, so depending on it here would make
# the import order load-bearing. The one value needed from that vocabulary is a
# string, and a string is what it stays.
REPRESENTATIVE_LED = "REPRESENTATIVE_LED"

# Roles. Customer, supplier and competitor are deliberately NOT roles here: this
# vocabulary answers one question, and reusing it for others is what collapses
# distinct relationships into a single misleading label.
AUTHORIZED_DISTRIBUTOR = "AUTHORIZED_DISTRIBUTOR"
DISTRIBUTOR = "DISTRIBUTOR"
REPRESENTATIVE = "REPRESENTATIVE"
RESELLER = "RESELLER"
SYSTEM_INTEGRATOR = "SYSTEM_INTEGRATOR"
TECHNOLOGY_PARTNER = "TECHNOLOGY_PARTNER"
SERVICE_PARTNER = "SERVICE_PARTNER"
NOT_A_CHANNEL = "NOT_A_CHANNEL"

# Representation: the candidate sells the account's products on its behalf.
CHANNEL_ROLES = (AUTHORIZED_DISTRIBUTOR, DISTRIBUTOR, REPRESENTATIVE, RESELLER)
# Related, but not representation. Named so the report can be honest about what
# was actually found instead of promoting a partner into a distributor.
PARTNER_ROLES = (SYSTEM_INTEGRATOR, TECHNOLOGY_PARTNER, SERVICE_PARTNER)

BATCH_1, BATCH_2 = 2, 2
MAX_CHANNEL_SEARCHES = BATCH_1 + BATCH_2
ENOUGH_CHANNEL_ENTITIES = 2

# {acct} is replaced by an alternation of the account's names and aliases. The
# account must appear IN the statement: "authorized distributor" on its own says
# the candidate distributes something, not that it distributes this account's
# products.
_ROLE_PATTERNS = (
    (AUTHORIZED_DISTRIBUTOR,
     r"authoris?zed\s+(?:distributor|dealer|reseller|partner)\s+(?:for|of)\s+(?:the\s+)?{acct}"),
    (AUTHORIZED_DISTRIBUTOR, r"{acct}\s*(?:的)?\s*授权\s*(?:经销商|代理商|代理)"),
    (DISTRIBUTOR,
     r"(?:exclusive\s+)?(?:distributor|dealer)\s+(?:for|of)\s+(?:the\s+)?{acct}|"
     r"we\s+distribute\s+{acct}|distributes?\s+{acct}\s+(?:products|equipment|systems)"),
    (DISTRIBUTOR, r"{acct}\s*(?:的)?\s*(?:经销商|代理商|分销商)"),
    (REPRESENTATIVE,
     r"(?:manufacturers?'?s?\s+)?(?:sales\s+)?representative\s+(?:for|of)\s+(?:the\s+)?{acct}|"
     r"represents?\s+{acct}\b|we\s+represent\s+{acct}"),
    (REPRESENTATIVE, r"{acct}\s*(?:的)?\s*代表处"),
    (RESELLER,
     r"resell(?:s|er\s+(?:for|of))\s+(?:the\s+)?{acct}|{acct}\s+reseller"),
)

# Related-but-not-representation. Only consulted when no representation statement
# was found, and only when the account is actually named.
_PARTNER_PATTERNS = (
    (SERVICE_PARTNER,
     r"(?:authoris?zed\s+)?service\s+(?:partner|center|centre|provider)\s+(?:for|of)\s+{acct}|"
     r"services?\s+and\s+supports?\s+{acct}\s+(?:equipment|systems|machines)"),
    (SYSTEM_INTEGRATOR,
     r"system\s+integrator[^.]{{0,60}}{acct}|integrates?\s+{acct}\s+(?:equipment|systems)|"
     r"{acct}[^.]{{0,60}}system\s+integrator"),
    (TECHNOLOGY_PARTNER,
     r"technology\s+partner[^.]{{0,60}}{acct}|partnership\s+with\s+{acct}|"
     r"{acct}[^.]{{0,60}}technology\s+partner|partners?\s+with\s+{acct}"),
)

# No "." inside the token class: a full stop ends the territory. Allowing it let
# "in the Upper Midwest. We stock ..." run past the sentence boundary.
_TERRITORY = re.compile(
    r"(?:in|for|covering|serving|throughout|across)\s+"
    r"((?:the\s+)?[A-Z][\w'&-]+(?:\s+[A-Z][\w'&-]+){0,3})")
_TERRITORY_STOP = frozenset((
    "we", "our", "us", "the", "this", "these", "more", "please", "contact",
    "learn", "read", "click", "view", "all", "products", "systems"))


def _acct_alternation(account, name_cn="", aliases=()):
    """Every name the account is known by, longest first so the fullest match wins."""
    names = [n for n in [account, name_cn] + list(aliases or []) if n and n.strip()]
    uniq = []
    for n in sorted(set(names), key=lambda x: -len(x)):
        if len(n.strip()) >= 2:
            uniq.append(re.escape(n.strip()))
    return "|".join(uniq) if uniq else None


def _find_role(text, alt, patterns):
    for role, template in patterns:
        pat = template.replace("{acct}", "(?:%s)" % alt)
        m = re.search(pat, text, re.I)
        if m:
            return role, m
    return None, None


def _territory(text, at):
    window = text[at:at + 240]
    for m in _TERRITORY.finditer(window):
        cand = " ".join(m.group(1).split())
        if cand.lower().startswith("the "):
            cand = cand[4:]                    # "the Upper Midwest" -> "Upper Midwest"
        cand = cand.rstrip(".,;:)")
        words = cand.split()
        if not words or words[0].lower() in _TERRITORY_STOP or len(cand) < 3:
            continue
        return cand
    return None


def classify_role(text, account, name_cn="", aliases=()):
    """The role, or NOT_A_CHANNEL. Representation must be STATED.

    Returns (role, authorized, territory, quote). A page that merely mentions
    the account - a customer list, a news item, a directory - yields
    NOT_A_CHANNEL, because mentioning a company is not representing it.
    """
    alt = _acct_alternation(account, name_cn, aliases)
    if not alt or not text:
        return NOT_A_CHANNEL, False, None, ""
    body = " ".join(text.split())
    role, m = _find_role(body, alt, _ROLE_PATTERNS)
    if role is None:
        role, m = _find_role(body, alt, _PARTNER_PATTERNS)
    if role is None:
        return NOT_A_CHANNEL, False, None, ""
    quote = body[max(0, m.start() - 60):m.end() + 80].strip()
    authorized = role == AUTHORIZED_DISTRIBUTOR or bool(
        re.search(r"authoris?zed|exclusive|授权", quote, re.I))
    return role, authorized, _territory(body, m.end()), quote


def plan_intents(profile, account, name_cn="", batch=1, covered=()):
    """Queries carry the account NAME, because that is what a channel page states.

    Ordered by what the profile already suggests: a distributor-led account is
    asked about distributors first. An account with no channel signal is still
    asked, because "we found no evidence of a channel" is only honest after
    looking.
    """
    gtm = (profile or {}).get("go_to_market_model")
    acct = (account or "").strip()
    if not acct:
        return []
    distributor = [
        ("authorized distributor", '"{}" authorized distributor'.format(acct)),
        ("distributor", '"{}" distributor OR dealer'.format(acct)),
    ]
    representative = [
        ("representative", '"{}" manufacturers representative'.format(acct)),
        ("reseller", '"{}" reseller OR "sales agent"'.format(acct)),
    ]
    if gtm == REPRESENTATIVE_LED:
        pool = representative + distributor
    else:
        pool = distributor + representative
    if name_cn:
        # Placed inside the second batch's window, not appended past it: a
        # Chinese-named account's channel is named in Chinese, so an intent that
        # can never be reached is the same as not having one.
        pool.insert(BATCH_1, ("chinese channel",
                              '"{}" 经销商 OR 代理商'.format(name_cn)))
    start = 0 if batch == 1 else BATCH_1
    window = pool[start:start + (BATCH_1 if batch == 1 else BATCH_2)]
    return [(k, q) for k, q in window if k not in covered]


def empty_coverage():
    return {"used": False, "search_count": 0, "batches": 0, "candidates": 0,
            "verified": 0, "retained": 0, "distinct_domains": 0,
            "channel_entities": 0, "authorized": 0, "partners": 0,
            "rejected_no_representation": 0, "skip_reason": None,
            "go_to_market_model": None, "go_to_market_confidence": None,
            "queries": [], "failed": False}


def coverage_is_useful(cov):
    return cov["channel_entities"] >= ENOUGH_CHANNEL_ENTITIES


def discover(client, profile, account, verify, name_cn="", progress=None,
             ceiling=MAX_CHANNEL_SEARCHES):
    """Two bounded batches. Skips only when the profile cannot support a search."""
    progress = progress or (lambda *_a, **_k: None)
    cov = empty_coverage()
    prof = profile or {}
    cov["go_to_market_model"] = prof.get("go_to_market_model")
    cov["go_to_market_confidence"] = prof.get("go_to_market_confidence")
    if not prof.get("channel_discovery_ready"):
        cov["skip_reason"] = prof.get("channel_skip_reason") \
            or "profile not ready for channel discovery"
        progress("channel", "Channel discovery skipped - {}".format(cov["skip_reason"]))
        return [], cov

    kept, covered, seen = [], set(), set()
    for batch in (1, 2):
        if cov["search_count"] >= ceiling:
            break
        try:
            intents = plan_intents(prof, account, name_cn, batch=batch, covered=covered)
        except Exception as e:
            cov["failed"] = True
            progress("channel", "WARN query planning failed ({}) - continuing"
                           .format(type(e).__name__))
            break
        intents = intents[:max(0, ceiling - cov["search_count"])]
        if not intents:
            break
        before = cov["channel_entities"]
        cov["batches"] = batch
        for key, query in intents:
            try:
                hits = client.search(query)
            except Exception as e:
                progress("channel", "WARN channel search failed ({}) - continuing"
                                    .format(type(e).__name__))
                continue
            cov["search_count"] += 1
            cov["used"] = True
            cov["queries"].append(query)
            fresh = [h for h in hits if h.get("url") not in seen]
            seen.update(h.get("url") for h in hits)
            cov["candidates"] += len(fresh)
            # Verification reaches the network and the page parsers. A failure
            # there costs this batch's candidates, never the run.
            try:
                out = verify(fresh, key) or {}
            except Exception as e:
                cov["failed"] = True
                progress("channel", "WARN verification failed ({}) - continuing"
                               .format(type(e).__name__))
                continue
            kept.extend(out.get("evidence") or [])
            cov["verified"] += out.get("verified", 0)
            cov["rejected_no_representation"] += out.get("rejected_no_representation", 0)
            # Distinct organisations, not pages: the same distributor found on
            # two domains is one channel entity, and two sources make it better
            # evidenced, not twice as broad.
            tally = getattr(verify, "tally", None)
            if tally is not None:
                cov.update(tally())
            else:
                cov["channel_entities"] += out.get("channel_entities", 0)
                cov["authorized"] += out.get("authorized", 0)
                cov["partners"] += out.get("partners", 0)
            if out.get("channel_entities"):
                covered.add(key)
        cov["distinct_domains"] = len({e.get("domain") for e in kept if e.get("domain")})
        cov["retained"] = len(kept)
        gained = cov["channel_entities"] - before
        progress("channel", "Channel discovery batch {}: {} search(es), {} verified "
                            "channel entity(ies)".format(batch, cov["search_count"],
                                                         cov["channel_entities"]))
        if coverage_is_useful(cov):
            progress("channel", "Channel coverage sufficient - stopping")
            break
        if gained == 0:
            progress("channel", "Channel discovery: no marginal yield - stopping")
            break
    return kept, cov
