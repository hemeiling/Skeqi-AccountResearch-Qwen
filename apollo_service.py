"""
Apollo people-enrichment client.

OPTIONAL. Apollo supplements web research for the Key Decision Makers section;
it never replaces it and never fails a company. With no APOLLO_API_KEY present
every entry point returns an empty, well-formed result and research continues.

Two stages, deliberately separated so credits are only spent on useful people:

  STAGE 1  mixed_people/search   - broad, cheap. Returns name/title/seniority/
                                   department but NOT a usable email.
  STAGE 2  people/bulk_match     - costs a credit per person revealed. Only the
                                   top-ranked contacts reach it, batched 10 at a
                                   time, business emails only.

A company that returns 100 candidates therefore costs ~12 enrichment credits,
not 100. Cached evidence packages carry the Apollo result, so re-running a
cached company spends nothing.
  * Apollo usage is counted separately from LLM tokens. An Apollo call is an
    HTTP request against a people database, not model inference; mixing the two
    would corrupt the cost picture.

The key is read server-side from ai_credentials.env or the environment and is
never logged, never returned to the browser and never written to a report.
"""

import json
import os
import urllib.error
import urllib.request

API_BASE = "https://api.apollo.io/api/v1"
TIMEOUT = 25
PER_PAGE = 100                 # one page is enough for a decision-maker shortlist
BULK_MATCH_BATCH = 10          # Apollo's documented maximum per bulk_match call
DEFAULT_ENRICH_LIMIT = 12      # "top 8-15 useful contacts"; override APOLLO_ENRICH_LIMIT

# Seniorities worth a sales briefing. Everything below manager is noise here.
SENIORITIES = ["owner", "founder", "c_suite", "partner", "vp", "head", "director", "manager"]

# Apollo OR-matches these as free text. They mirror the priority titles in the
# account-research brief; the real filtering happens locally in people_service.
TITLE_QUERIES = [
    "CEO", "Chief Executive Officer", "President", "COO", "Chief Operating Officer",
    "CTO", "Chief Technology Officer", "CIO", "Chief Information Officer",
    "Chief Supply Chain Officer", "Chief Procurement Officer", "Chief Manufacturing Officer",
    "VP Manufacturing", "VP Engineering", "VP Operations", "VP Procurement",
    "VP Supply Chain", "VP Quality",
    "Head of Procurement", "Head of Manufacturing", "Head of Operations",
    "Strategic Sourcing Director", "Supply Chain Director", "Procurement Director",
    "Manufacturing Director", "Manufacturing Engineering Manager", "Plant Manager",
    "Plant Director", "Operations Director", "Automation Director", "Automation Manager",
    "Engineering Director", "Process Engineering Manager", "Equipment Engineering Manager",
    "Battery Engineering", "Cell Engineering", "Energy Storage",
    "Digital Manufacturing", "Smart Manufacturing", "Quality Director", "Quality Manager",
    "Director of Sales", "VP Business Development",
]

# Response headers Apollo uses for usage/credit accounting. Captured verbatim so
# cost can be reconciled without guessing at a price.
USAGE_HEADERS = ("x-24-hour-usage", "x-24-hour-requests-left", "x-hourly-usage",
                 "x-hourly-requests-left", "x-minute-usage", "x-minute-requests-left",
                 "x-rate-limit-24-hour", "x-rate-limit-hourly", "x-rate-limit-minute",
                 "x-credits-used", "x-credits-remaining")


def api_key(cfg=None):
    """Server-side only: ai_credentials.env first, then the process environment."""
    return ((cfg or {}).get("APOLLO_API_KEY") or os.environ.get("APOLLO_API_KEY") or "").strip()


def configured(cfg=None):
    return bool(api_key(cfg))


def new_usage():
    """Apollo accounting, deliberately separate from any token counter.

    people_returned  - candidates the search produced   (no credit cost)
    people_retained  - candidates that passed relevance ranking
    people_enriched  - contacts actually sent to bulk_match (the credit spend)
    """
    return {"configured": False, "status": "not_configured", "calls": 0,
            "search_calls": 0, "enrich_calls": 0,
            "people_returned": 0, "people_retained": 0,
            "people_enrich_requested": 0, "people_enriched": 0,
            "emails_found": 0, "emails_verified": 0,
            "organization": None, "credits": {}, "errors": []}


def _request(path, key, usage, method="GET", body=None, params=None):
    url = API_BASE + path
    if params:
        import urllib.parse
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json", "Accept": "application/json",
        "Cache-Control": "no-cache",
        "x-api-key": key,          # never logged
    })
    usage["calls"] += 1
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8") or "{}")
            _capture_credits(resp.headers, usage)
            return resp.status, payload
    except urllib.error.HTTPError as e:
        _capture_credits(e.headers, usage)
        raw = e.read().decode("utf-8", "replace")[:300]
        try:
            detail = json.loads(raw)
            msg = detail.get("error") or detail.get("message") or raw
        except json.JSONDecodeError:
            msg = raw
        return e.code, {"error": str(msg)[:200]}
    except Exception as e:
        return 0, {"error": "{}: {}".format(type(e).__name__, e)[:200]}


def _capture_credits(headers, usage):
    for h in USAGE_HEADERS:
        value = headers.get(h) if headers else None
        if value is not None:
            usage["credits"][h] = value


# Apollo returns this literal placeholder instead of an address when a contact
# has not been revealed. Treating it as an email would put a fake address in a
# sales report, so it is filtered explicitly.
_LOCKED_EMAIL = "email_not_unlocked"

# Apollo email_status -> what we tell the reader. Anything unrecognised is
# reported verbatim rather than being upgraded to "verified".
EMAIL_STATUS = {
    "verified": "Verified",
    "guessed": "Unverified (guessed)",
    "unverified": "Unverified",
    "likely to engage": "Unverified",
    "unavailable": "Unavailable",
    "bounced": "Unavailable (bounced)",
    "pending_manual_fulfillment": "Pending",
}


def clean_email(value):
    """An address, or "" - never a placeholder and never an invented one."""
    value = (value or "").strip()
    if not value or _LOCKED_EMAIL in value.lower() or "@" not in value:
        return ""
    return value


def email_status_label(raw_status, email):
    if email:
        return EMAIL_STATUS.get((raw_status or "").strip().lower(),
                                (raw_status or "Unknown").replace("_", " ").title())
    if raw_status:
        return EMAIL_STATUS.get((raw_status or "").strip().lower(), "Not available")
    return "Not available"


def _location(raw):
    parts = [raw.get("city"), raw.get("state"), raw.get("country")]
    return ", ".join(p for p in parts if p)


def _norm_person(raw, org_name=""):
    org = raw.get("organization") or raw.get("account") or {}
    first = (raw.get("first_name") or "").strip()
    last = (raw.get("last_name") or "").strip()
    name = (raw.get("name") or "{} {}".format(first, last)).strip()
    email = clean_email(raw.get("email"))
    return {
        "name": name, "first_name": first, "last_name": last,
        "title": (raw.get("title") or "").strip(),
        "seniority": (raw.get("seniority") or "").strip(),
        "departments": raw.get("departments") or [],
        "subdepartments": raw.get("subdepartments") or [],
        "linkedin_url": raw.get("linkedin_url") or "",
        "city": raw.get("city") or "", "country": raw.get("country") or "",
        "location": _location(raw),
        "organization_name": org.get("name") or org_name,
        "organization_domain": org.get("primary_domain") or org.get("website_url") or "",
        "email": email,
        "email_status": email_status_label(raw.get("email_status"), email),
        "email_status_raw": (raw.get("email_status") or ""),
        "enriched": False,
        "apollo_id": raw.get("id") or "",
    }


_GENERIC = {"inc", "corp", "co", "ltd", "limited", "llc", "plc", "gmbh", "ag", "sa",
            "nv", "bv", "group", "holdings", "company", "the", "international",
            "technologies", "technology", "solutions", "systems", "automation"}


def _org_matches(org, name):
    """Guard against enriching the WRONG company.

    The upstream website resolver can hand back a guessed domain when the real
    site blocks bots - te.com blocks, and discovery guessed connectivity.com,
    an unrelated business. Without this check Apollo would confidently return
    that other company's staff. Distinctive name tokens must overlap.
    """
    def tokens(text):
        import re as _re
        raw = _re.split(r"[^a-z0-9]+", (text or "").lower())
        return {t for t in raw if t and t not in _GENERIC and len(t) > 1}
    ours = tokens(name)
    theirs = tokens((org or {}).get("name", ""))
    if not ours or not theirs:
        return False
    # A single shared word is not identity: "Connectivity Inc" shares one token
    # with "TE Connectivity" and is a different company. Most of OUR distinctive
    # tokens have to be present.
    return len(ours & theirs) / len(ours) >= 0.6


def resolve_organization(domain, name, key, usage):
    """Domain enrichment is exact; the name search is the fallback.

    Either way the match is verified against the company name before use.
    """
    if domain:
        status, data = _request("/organizations/enrich", key, usage, params={"domain": domain})
        org = (data or {}).get("organization")
        if status == 200 and org:
            if _org_matches(org, name):
                return org, "domain"
            usage["errors"].append(
                "Apollo domain {} resolved to '{}', which does not match '{}' - ignored".format(
                    domain, org.get("name", "?"), name)[:180])
        if status in (401, 403):
            usage["errors"].append("Apollo auth failed ({}) - check APOLLO_API_KEY".format(status))
            return None, "auth_failed"
    status, data = _request("/mixed_companies/search", key, usage, method="POST",
                            body={"q_organization_name": name, "page": 1, "per_page": 5})
    orgs = (data or {}).get("organizations") or (data or {}).get("accounts") or []
    if status == 200 and orgs:
        for org in orgs:
            if _org_matches(org, name):
                return org, "name"
        usage["errors"].append("Apollo name search returned only non-matching companies")
        return None, "name_mismatch"
    if status not in (200,):
        usage["errors"].append("Apollo company search returned {}: {}".format(
            status, (data or {}).get("error", ""))[:160])
    return None, "not_found"


def search_people(company, domain, cfg, usage=None):
    """Return (people, usage). Never raises; an unusable Apollo is an empty list."""
    usage = usage if usage is not None else new_usage()
    key = api_key(cfg)
    if not key:
        usage["status"] = "not_configured"
        return [], usage
    usage["configured"] = True
    try:
        org, how = resolve_organization(domain, company, key, usage)
        if not org:
            usage["status"] = how if how in ("auth_failed", "name_mismatch") else "no_org_match"
            return [], usage
        usage["organization"] = {"id": org.get("id"), "name": org.get("name"),
                                 "domain": org.get("primary_domain") or org.get("website_url"),
                                 "matched_by": how}
        body = {"person_seniorities": SENIORITIES, "person_titles": TITLE_QUERIES,
                "page": 1, "per_page": PER_PAGE}
        if org.get("id"):
            body["organization_ids"] = [org["id"]]
        elif domain:
            body["q_organization_domains_list"] = [domain]
        status, data = _request("/mixed_people/search", key, usage, method="POST", body=body)
        usage["search_calls"] += 1
        if status == 404:                       # older/newer tenants expose /people/search
            status, data = _request("/people/search", key, usage, method="POST", body=body)
            usage["search_calls"] += 1
        if status != 200:
            usage["status"] = "search_failed"
            usage["errors"].append("Apollo people search returned {}: {}".format(
                status, (data or {}).get("error", ""))[:160])
            return [], usage
        raw = (data or {}).get("people") or (data or {}).get("contacts") or []
        people = [_norm_person(p, org.get("name") or company) for p in raw]
        people = [p for p in people if p["name"] and p["title"]]
        usage["people_returned"] = len(people)
        usage["status"] = "ok" if people else "no_people"
        return people, usage
    except Exception as e:                      # a broken Apollo must not fail research
        usage["status"] = "error"
        usage["errors"].append("{}: {}".format(type(e).__name__, e)[:160])
        return [], usage


def enrich_limit(cfg):
    try:
        value = int((cfg or {}).get("APOLLO_ENRICH_LIMIT") or DEFAULT_ENRICH_LIMIT)
    except (TypeError, ValueError):
        value = DEFAULT_ENRICH_LIMIT
    return max(1, min(value, 25))


def enrich_people(people, cfg, usage, limit=None):
    """STAGE 2. Reveal business emails for an already-ranked shortlist.

    `people` must arrive ranked - only the first `limit` are sent, because this
    is the call that costs credits. Personal emails and phone numbers are never
    requested: this is a B2B sales briefing, and asking for them would both cost
    more and pull personal contact data into report files.

    Returns the same list with contact fields filled in where Apollo had them.
    Never raises, and never fabricates an address.
    """
    key = api_key(cfg)
    limit = limit or enrich_limit(cfg)
    shortlist = [p for p in (people or []) if p.get("apollo_id") or p.get("name")][:limit]
    if not key or not shortlist:
        return people or []

    by_id = {}
    details = []
    for person in shortlist:
        detail = {}
        if person.get("apollo_id"):
            detail["id"] = person["apollo_id"]
        if person.get("first_name"):
            detail["first_name"] = person["first_name"]
        if person.get("last_name"):
            detail["last_name"] = person["last_name"]
        if not detail.get("first_name") and person.get("name"):
            detail["name"] = person["name"]
        if person.get("organization_name"):
            detail["organization_name"] = person["organization_name"]
        if person.get("organization_domain"):
            detail["domain"] = person["organization_domain"]
        if person.get("linkedin_url"):
            detail["linkedin_url"] = person["linkedin_url"]
        details.append(detail)
        by_id[person.get("apollo_id") or person["name"].lower()] = person

    usage["people_enrich_requested"] = len(details)
    for start in range(0, len(details), BULK_MATCH_BATCH):
        batch = details[start:start + BULK_MATCH_BATCH]
        status, data = _request(
            "/people/bulk_match", key, usage, method="POST",
            body={"details": batch,
                  "reveal_personal_emails": False,
                  "reveal_phone_number": False})
        usage["enrich_calls"] += 1
        if status != 200:
            usage["errors"].append("Apollo bulk_match returned {}: {}".format(
                status, (data or {}).get("error", ""))[:160])
            continue
        matches = (data or {}).get("matches") or (data or {}).get("people") or []
        for match in matches:
            if not isinstance(match, dict):
                continue
            person = by_id.get(match.get("id")) or by_id.get(
                (match.get("name") or "").lower())
            if person is None:
                continue
            email = clean_email(match.get("email"))
            person["email"] = email
            person["email_status"] = email_status_label(match.get("email_status"), email)
            person["email_status_raw"] = match.get("email_status") or ""
            person["enriched"] = True
            for field, key_name in (("linkedin_url", "linkedin_url"),
                                    ("title", "title"),
                                    ("seniority", "seniority")):
                if not person.get(field) and match.get(key_name):
                    person[field] = match[key_name]
            if not person.get("location"):
                person["location"] = _location(match)
            org = match.get("organization") or {}
            if not person.get("organization_name") and org.get("name"):
                person["organization_name"] = org["name"]

    enriched = [p for p in shortlist if p.get("enriched")]
    usage["people_enriched"] = len(enriched)
    usage["emails_found"] = sum(1 for p in enriched if p.get("email"))
    usage["emails_verified"] = sum(
        1 for p in enriched if (p.get("email_status") or "").startswith("Verified"))
    return people
