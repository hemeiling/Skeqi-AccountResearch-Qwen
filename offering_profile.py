"""What the account SELLS, derived from retained evidence only.

The distinction this module exists to hold:

    technologies the account USES  ≠  products the account SELLS

Ford's strongest retained evidence is "ABB Robotics recognized as a 2026 Ford
Supplier of the Year" and "Ford uses co-bots". A naive noun sweep turns those
into "robotics" and "collaborative robots" as FORD'S OFFERINGS, which would then
send competitor discovery looking for robot makers - Ford's suppliers, not its
rivals. ACRO's evidence is the mirror image: its own site titles ARE its
offerings.

So direction is the whole design. A capability counts as an offering only when
the ACCOUNT is the subject of a selling verb, or the account's own site says so.
When a vendor is the subject and the account is the object, the same words are
recorded as USAGE and never reach the offering set.

Deterministic: the same retained evidence yields the same profile. Every
assertion carries the source it came from, so nothing here is ungrounded.

No network, no model call. Import-safe.
"""
import re
from collections import OrderedDict

import channel_discovery as chdisc
import research_service as rs

DIRECT, DISTRIBUTOR_LED, REPRESENTATIVE_LED, MIXED, UNKNOWN = (
    "DIRECT", "DISTRIBUTOR_LED", "REPRESENTATIVE_LED", "MIXED", "UNKNOWN")

# Generic manufacturing vocabulary used only to NORMALISE extracted text into
# comparable concepts. It never invents a capability the evidence did not state,
# and it is deliberately industry-neutral - no battery, automotive or account
# specific terms.
_CAPABILITY_LEXICON = OrderedDict([
    ("assembly", r"assembly|assembling|装配|组装"),
    ("welding", r"weld(?:ing)?|焊接|焊装"),
    ("laser processing", r"laser|激光"),
    ("machining", r"machining|cnc|milling|turning|机加工|数控"),
    ("stamping/forming", r"stamping|forming|press(?:ing)?|冲压"),
    ("dispensing", r"dispens\w*|gluing|adhesive|点胶|涂胶"),
    ("inspection/test", r"inspection|inspect\w*|testing|test stand|metrology|检测|测试"),
    ("machine vision", r"machine vision|vision system|视觉"),
    ("robotics", r"robot\w*|cobot\w*|机器人"),
    ("material handling", r"material handling|conveyor|palleti\w*|输送|物料搬运"),
    ("packaging", r"packaging|包装"),
    ("controls/PLC", r"\bplc\b|motion control|control system|控制系统"),
    ("MES/software", r"\bmes\b|manufacturing execution|traceability|追溯"),
    ("tooling/fixtures", r"tooling|fixtur\w*|工装|夹具"),
    ("turnkey systems", r"turnkey|integrated system|custom automat\w*|成套|集成系统"),
])

_INDUSTRY_LEXICON = OrderedDict([
    ("automotive", r"automotive|vehicle|汽车"),
    ("medical device", r"medical device|medical|医疗"),
    ("aerospace", r"aerospace|aviation|航空"),
    ("electronics", r"electronic\w*|semiconductor|电子|半导体"),
    ("appliance", r"appliance|白色家电|家电"),
    ("energy/battery", r"batter\w*|energy storage|电池|储能"),
    ("consumer goods", r"consumer goods|packaging goods|日用品"),
    ("industrial equipment", r"industrial equipment|machinery|工业设备"),
])

# The account is the SUBJECT: these describe what it sells.
_SELLS = (r"design(?:s|ed|ing)?|build(?:s|ing)?|manufactur\w*|produc(?:e|es|ed|ing)|"
          r"provid(?:e|es|ed|ing)|offer(?:s|ed|ing)?|suppl(?:y|ies|ied|ying)|"
          r"deliver(?:s|ed|ing)?|specialis\w*|specializ\w*|integrat(?:e|es|ing)")
# The account is the OBJECT: these describe what it BUYS or uses.
_USES = (r"uses?|using|used|deploy\w*|install\w*|operat\w*|adopt\w*|"
         r"supplier of the year|supplier to|supplies to|awarded by")

# Go-to-market signals, NAMED and GRADED.
#
# The distinction that matters for gating: a company having a sales team is
# evidence of a direct sales MOTION. It is not evidence that no channel exists.
# Almost every distributor-led manufacturer also has an internal sales team, a
# quote form and account managers. Treating any one of those as proof of
# exclusivity is the same absence-reasoning error the earlier version made from
# the other direction, and it would silently switch channel discovery off for
# exactly the accounts worth searching.
#
# So each cue carries a strength. EXPLICIT means the company states how it goes
# to market. SUPPORTING means the company has a direct sales capability, which
# is compatible with any channel model.
EXPLICIT, SUPPORTING = "explicit", "supporting"

_DIRECT_SIGNALS = OrderedDict([
    ("explicit direct-sales statement",
     (EXPLICIT, r"we sell directly|sells? direct(?:ly)|sold directly|"
                r"direct[- ]sales (?:model|organi[sz]ation|only|force)|"
                r"only through our own sales|no distributors|without (?:a )?distributors?|"
                r"直销|直接销售|直接签约")),
    ("direct project contracting",
     (SUPPORTING, r"contract(?:s|ed|ing)? directly with|we contract directly")),
    ("internal sales team",
     (SUPPORTING, r"our sales team|contact (?:our|the) sales(?: team)?|"
                  r"our (?:in-house|internal) sales|our sales (?:department|organi[sz]ation)")),
    ("named sales owners",
     (SUPPORTING, r"account (?:executive|manager)s?|our sales engineers?|"
                  r"your sales engineer")),
    ("direct quote workflow",
     (SUPPORTING, r"request a (?:quote|proposal)|get a quote|submit an rfq|询价")),
])

_DISTRIBUTOR_SIGNALS = OrderedDict([
    ("distributor network",
     (EXPLICIT, r"authoriz(?:ed|sed) distributor|find a distributor|distributor network|"
                r"dealer locator|our distributors|经销商|分销商|代理商")),
])

_REP_SIGNALS = OrderedDict([
    ("representative network",
     (EXPLICIT, r"manufacturers'? representative|sales representative network|"
                r"rep(?:resentative)? network|our reps\b|代表处")),
])

_GTM_SIGNALS = OrderedDict([
    (DIRECT, _DIRECT_SIGNALS),
    (DISTRIBUTOR_LED, _DISTRIBUTOR_SIGNALS),
    (REPRESENTATIVE_LED, _REP_SIGNALS),
])

# Kept as a flat model -> pattern view for callers that only need "does any cue
# for this model appear". Derived, so the two can never drift apart.
_GTM_CUES = OrderedDict(
    (model, "|".join(pat for _s, pat in sigs.values()))
    for model, sigs in _GTM_SIGNALS.items())


# Street suffixes are not places. They appear because a company's contact page
# puts the address immediately above the city.
_STREET = frozenset(("road", "street", "avenue", "drive", "lane", "boulevard",
                     "court", "parkway", "highway", "way", "suite", "circle",
                     "place", "terrace", "rd", "st", "ave", "dr", "blvd", "hwy"))


def _clean_place(raw):
    words = [w for w in (raw or "").split() if w]
    while words and words[0].lower() in _STREET:
        words.pop(0)
    while words and words[-1].lower() in _STREET:
        words.pop()
    return " ".join(words)


_STOP_TITLE = re.compile(
    r"^(home|about|about us|contact|contact us|products?|services?|news|careers|"
    r"privacy|terms|sitemap|blog|login)$", re.I)


def _clean_title(title):
    """A page title minus the site furniture, so it can be read as an offering."""
    t = re.sub(r"\s*[|\-–—]\s*[^|\-–—]*$", "", (title or "").strip())
    t = re.sub(r"\s*\(.*?\)\s*", " ", t)
    return re.sub(r"\s+", " ", t).strip(" .-|")


def _own_site(item, domain):
    d = (item.get("domain") or "") or rs.registrable_domain(item.get("url") or "")
    return bool(item.get("official")) or (
        bool(domain) and rs.registrable_domain("https://" + domain) == rs.registrable_domain("https://" + d))


def _sentences(text):
    return re.split(r"(?<=[.!?。！？])\s+|\n+", text or "")


def _match_lexicon(lex, text):
    return [k for k, pat in lex.items() if re.search(pat, text or "", re.I)]


def build_profile(evidence, name, domain="", aliases=()):
    """Deterministic profile from retained evidence. Nothing is inferred from
    absence, and every field records the sources that produced it."""
    ev = list(evidence or [])
    names = [n for n in [name, rs.core_name(name)] + list(aliases or []) if n]
    tokens = [t for t in rs.distinctive_tokens(name) if len(t) >= 3]

    def mentions_account(s):
        return any((rs._mentions(n, s) if n.isascii() else n in s) for n in names) \
            or any(rs._mentions(t, s) for t in tokens)

    offerings, capabilities, industries, geography = OrderedDict(), OrderedDict(), OrderedDict(), OrderedDict()
    usage = OrderedDict()                     # what the account USES - kept apart
    gtm_hits = {k: OrderedDict() for k in _GTM_SIGNALS}
    # Representation the pipeline has ALREADY retrieved. The go-to-market cues
    # above read the account's own pages only, so without this a third-party
    # source saying "X is the authorized distributor for the account" would sit
    # in the evidence while the profile called the account exclusively direct.
    retained_channel = []
    own_pages = 0

    for item in ev:
        url = item.get("url") or ""
        text = item.get("text") or ""
        title = _clean_title(item.get("title"))
        own = _own_site(item, domain)
        if own:
            own_pages += 1
            # The account's own page title names what it sells - the single most
            # reliable offering signal available, and the reason ACRO profiles
            # cleanly while Ford (whose site is blocked) does not.
            if title and not _STOP_TITLE.match(title) and not mentions_account(title):
                offerings.setdefault(title.lower(), []).append(url)
            elif title and not _STOP_TITLE.match(title):
                stripped = title
                for n in sorted(names, key=len, reverse=True):
                    stripped = re.sub(re.escape(n), " ", stripped, flags=re.I)
                stripped = re.sub(r"\s+", " ", stripped).strip(" ,.-|")
                if stripped and not _STOP_TITLE.match(stripped):
                    offerings.setdefault(stripped.lower(), []).append(url)
            for k in _match_lexicon(_CAPABILITY_LEXICON, title + " " + text[:4000]):
                capabilities.setdefault(k, []).append(url)
            for k in _match_lexicon(_INDUSTRY_LEXICON, text[:8000]):
                industries.setdefault(k, []).append(url)

        for s in _sentences(text[:20000]):
            s = " ".join(s.split())
            if len(s) < 25 or len(s) > 320 or not mentions_account(s):
                continue
            sells = re.search(r"(?:%s)\s+(?:%s)" % ("|".join(re.escape(n) for n in names[:2]),
                                                    _SELLS), s, re.I) \
                or re.search(r"(?:%s)\b[^.]{0,40}\b(?:%s)" % ("|".join(tokens), _SELLS), s, re.I)
            uses = re.search(_USES, s, re.I)
            caps = _match_lexicon(_CAPABILITY_LEXICON, s)
            if sells and not uses:
                for k in caps:
                    capabilities.setdefault(k, []).append(url)
                for k in _match_lexicon(_INDUSTRY_LEXICON, s):
                    industries.setdefault(k, []).append(url)
            elif uses:
                # Same words, opposite direction. Recorded, never promoted.
                for k in caps:
                    usage.setdefault(k, []).append(url)
        # A single space, never \s+: crawled page text folds a street address onto
        # the city line, and "\s+" turned "... Road\nMilwaukee, WI" into the place
        # name "Road Milwaukee", which then leaked into search queries.
        for m in re.finditer(r"\b([A-Z][a-z]+(?: [A-Z][a-z]+)?),[ ]*(?:[A-Z]{2}\b|USA|United States)", text[:6000]):
            place = _clean_place(m.group(1))
            if own and place:
                geography.setdefault(place, []).append(url)
        # Any page, not just the account's own: a distributor states this on its
        # own site. Partner language is deliberately NOT collected here - only
        # stated representation or resale counts.
        role, authorized, territory, quote = chdisc.classify_role(
            text[:20000], name, "", aliases)
        if role in chdisc.CHANNEL_ROLES:
            retained_channel.append({"url": url, "role": role,
                                     "authorized": bool(authorized),
                                     "own_site": bool(own), "quote": quote})
        if own:
            hay = title + " " + text[:20000]
            for model, sigs in _GTM_SIGNALS.items():
                for signal, (strength, pat) in sigs.items():
                    if re.search(pat, hay, re.I):
                        gtm_hits[model].setdefault(signal, {"strength": strength,
                                                            "urls": []})
                        gtm_hits[model][signal]["urls"].append(url)

    # Capabilities the account only USES are never offerings.
    for k in list(capabilities):
        if k in usage and len(usage[k]) > len(capabilities[k]):
            capabilities.pop(k)

    business_model, bm_ev = _business_model(offerings, capabilities, usage, own_pages)
    gtm, gtm_conf, gtm_ev = _go_to_market(gtm_hits, retained_channel)

    prof = {
        "account": name,
        "offerings": list(offerings),
        "capabilities": list(capabilities),
        "industries": list(industries),
        "project_types": _project_types(offerings, capabilities),
        "geography": list(geography),
        "business_model": business_model,
        "go_to_market_model": gtm,
        "go_to_market_confidence": gtm_conf,
        "go_to_market_evidence": gtm_ev,
        "uses_not_sells": list(usage),
        "supporting_evidence": {
            "offerings": {k: sorted(set(v)) for k, v in offerings.items()},
            "capabilities": {k: sorted(set(v)) for k, v in capabilities.items()},
            "industries": {k: sorted(set(v)) for k, v in industries.items()},
            "geography": {k: sorted(set(v)) for k, v in geography.items()},
            "business_model": bm_ev,
            "go_to_market_model": {m: sorted({u for sig in sigs.values()
                                                for u in sig["urls"]})
                                   for m, sigs in gtm_hits.items() if sigs},
            "usage": {k: sorted(set(v)) for k, v in usage.items()},
        },
        "own_site_pages": own_pages,
        "evidence_count": len(ev),
    }
    prof["confidence"] = _confidence(prof)
    prof.update(_readiness(prof))
    return prof


def unavailable_profile(name, reason):
    """The profile a caller gets when construction FAILED, rather than an
    exception. Shaped exactly like a real profile so every downstream reader
    works unchanged, and not ready for either discovery path, so nothing is
    searched on a profile we do not have."""
    return {
        "account": name, "offerings": [], "capabilities": [], "industries": [],
        "project_types": [], "geography": [], "business_model": "unknown",
        "go_to_market_model": UNKNOWN, "go_to_market_confidence": "none",
        "go_to_market_evidence": {"signals": [], "direct_signal_count": 0,
                                  "explicit_direct_statement": False,
                                  "channel_signal_count": 0},
        "uses_not_sells": [], "supporting_evidence": {}, "own_site_pages": 0,
        "evidence_count": 0, "unavailable": True,
        "confidence": {"offerings": "none", "capabilities": "none",
                       "industries": "none", "geography": "none",
                       "business_model": "none", "go_to_market": "none"},
        "competitor_discovery_ready": False,
        "competitor_skip_reason": reason,
        "channel_discovery_ready": False,
        "channel_skip_reason": reason,
    }


def _project_types(offerings, capabilities):
    out = []
    joined = " ".join(list(offerings) + list(capabilities)).lower()
    for label, pat in (("custom/engineered systems", r"custom|engineered|turnkey|integrated"),
                       ("standard equipment", r"standard|catalog|off[- ]the[- ]shelf"),
                       ("services", r"service|maintenance|support|retrofit")):
        if re.search(pat, joined):
            out.append(label)
    return out


def _business_model(offerings, capabilities, usage, own_pages):
    """Integrator/equipment supplier versus manufacturer/OEM, from direction.

    An account whose OWN pages advertise process capabilities sells those
    capabilities. An account that mostly appears as the RECIPIENT of process
    capability buys it - it is the manufacturer, not the supplier.
    """
    ev = []
    if own_pages and capabilities and len(capabilities) >= 2:
        ev.append("own-site pages advertise %d process capabilities" % len(capabilities))
        return "equipment_supplier_or_integrator", ev
    if usage and len(usage) > len(capabilities):
        ev.append("appears as the recipient of %d capabilities it does not advertise" % len(usage))
        return "manufacturer_or_oem", ev
    if offerings:
        ev.append("offerings named without process-capability evidence")
        return "unclassified_seller", ev
    ev.append("insufficient evidence")
    return "unknown", ev


def _go_to_market(hits, retained_channel=()):
    """Constrained taxonomy plus an explicit confidence, because the model alone
    cannot carry the difference between "states it sells direct" and "has a sales
    team like everybody else".

    DIRECT stays a POSITIVE finding - never the residue of finding no
    distributor, which would turn every blocked website into a direct-sales
    business. Confidence is what decides whether it is allowed to close down
    channel discovery, and only an explicit statement or several independent
    signals reach that bar.
    """
    present = [m for m, sigs in hits.items() if sigs]
    signals = []
    for model, sigs in hits.items():
        for name, rec in sigs.items():
            signals.append({"model": model, "signal": name,
                            "strength": rec["strength"],
                            "urls": sorted(set(rec["urls"]))})
    direct = hits.get(DIRECT) or {}
    explicit_direct = any(r["strength"] == EXPLICIT for r in direct.values())
    retained = list(retained_channel or [])
    ev = {"signals": signals,
          "direct_signal_count": len(direct),
          "explicit_direct_statement": explicit_direct,
          "channel_signal_count": len(hits.get(DISTRIBUTOR_LED) or {})
                                  + len(hits.get(REPRESENTATIVE_LED) or {}),
          "retained_channel": [{"url": r["url"], "role": r["role"],
                                "authorized": r["authorized"]} for r in retained]}

    if retained:
        # A named distributor or representative in the retained evidence is a
        # fact about the outside world. Own-site direct-sales language cannot
        # erase it: at most the two coexist, which is what MIXED means.
        roles = {r["role"] for r in retained}
        sources = {r["url"] for r in retained}
        conf = "high" if len(sources) >= 2 else "medium"
        if present or explicit_direct:
            return MIXED, conf, ev
        if roles & {chdisc.REPRESENTATIVE}:
            return REPRESENTATIVE_LED, conf, ev
        return DISTRIBUTOR_LED, conf, ev

    if len(present) > 1:
        # Both motions are evidenced. That is a finding, not a doubt, but it
        # never suppresses channel discovery - there IS a channel.
        return MIXED, "medium", ev
    if not present:
        return UNKNOWN, "none", ev

    model = present[0]
    if model != DIRECT:
        # A named channel is a positive, checkable fact about the outside world.
        urls = {u for r in hits[model].values() for u in r["urls"]}
        return model, ("high" if len(urls) >= 2 else "medium"), ev

    # DIRECT. One statement of how they sell is worth more than any number of
    # signals that they can sell.
    if explicit_direct:
        return DIRECT, "high", ev
    n = len(direct)
    return DIRECT, ("high" if n >= 3 else "medium" if n == 2 else "low"), ev


def _score(n, strong=3):
    if n >= strong:
        return "high"
    if n >= 1:
        return "medium" if n >= 2 else "low"
    return "none"


def _confidence(p):
    """Field-level, because one opaque number cannot say WHICH part is weak, and
    the two discovery paths need different parts."""
    return {
        "offerings": _score(len(p["offerings"])),
        "capabilities": _score(len(p["capabilities"])),
        "industries": _score(len(p["industries"])),
        "geography": _score(len(p["geography"]), strong=2),
        "business_model": ("high" if p["business_model"] not in ("unknown", "unclassified_seller")
                           else "low" if p["business_model"] == "unclassified_seller" else "none"),
        # One source of truth: the graded value computed with the model.
        "go_to_market": p["go_to_market_confidence"],
    }


_OK = ("medium", "high")


def _readiness(p):
    """Different paths need different fields, so they are judged separately.

    Competitor discovery needs to know what the account SELLS and to whom;
    without that it would search on industry alone and return the same large
    vendors for every account. Channel discovery needs the go-to-market model,
    because that decides whether a channel can exist at all.
    """
    c = p["confidence"]
    comp_missing = []
    if c["capabilities"] not in _OK:
        comp_missing.append("capabilities")
    if c["industries"] not in _OK and c["offerings"] not in _OK:
        comp_missing.append("industries_or_offerings")
    if c["business_model"] == "none":
        comp_missing.append("business_model")

    # Channel readiness does NOT require a known go-to-market model. Discovering
    # that model is part of what channel discovery is FOR, so requiring it first
    # would make the path unreachable exactly when it is most useful.
    #
    # Two different kinds of "unknown" have to stay apart:
    #   we have not found the answer yet        -> search for it
    #   we do not understand the account enough -> searching would be guessing
    gtm = p["go_to_market_model"]
    chan_missing = []
    if p["business_model"] in ("unknown",):
        chan_missing.append("business_model")
    if c["offerings"] not in _OK and c["capabilities"] not in _OK:
        chan_missing.append("offerings_or_capabilities")

    # Only a STRONGLY corroborated DIRECT model may close this path. A single
    # supporting cue - a sales team, a quote form - says the account can sell
    # direct, not that it sells ONLY direct, and the cost of being wrong is
    # silently never looking for a channel that exists.
    if gtm == DIRECT and p["go_to_market_confidence"] == "high":
        chan_ready, chan_reason = False, (
            "go-to-market corroborated as DIRECT (%s); no channel to discover"
            % ("explicit statement" if p["go_to_market_evidence"]["explicit_direct_statement"]
               else "%d independent direct-sales signals"
                    % p["go_to_market_evidence"]["direct_signal_count"]))
    elif chan_missing:
        chan_ready, chan_reason = False, (
            "insufficient account understanding to search intelligently: "
            + ", ".join(chan_missing))
    else:
        chan_ready, chan_reason = True, None

    return {
        "competitor_discovery_ready": not comp_missing,
        "competitor_skip_reason": (None if not comp_missing
                                   else "insufficient profile evidence: " + ", ".join(comp_missing)),
        "channel_discovery_ready": chan_ready,
        "channel_skip_reason": chan_reason,
    }
