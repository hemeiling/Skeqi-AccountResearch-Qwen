"""
Decision-maker ranking and merge.

Apollo SUPPLEMENTS web research. This module:
  1. scores every candidate against SKEQI's actual sales motion,
  2. drops anyone who matches no relevant role (Apollo returns plenty of them),
  3. parses the people the model found on the web out of its own report,
  4. merges the two on person identity so a CEO listed on the company's
     leadership page and in Apollo is ONE row, not two,
  5. returns a ranked roster carrying its provenance.

Scoring is deterministic - a title regex and a seniority weight. No LLM call is
made to rank people or to write "why relevant": both follow from the matched
role, so this costs nothing per company and gives the same answer every run.

Source precedence, per the brief: for executive/C-suite roles an official
company leadership page outranks Apollo, so a merged record keeps the official
title. Apollo supplies the structured department/seniority that web prose
rarely states, and fills the procurement / sourcing / plant / automation roles
that are hard to find publicly.
"""

import re

SOURCE_APOLLO = "Apollo"
SOURCE_OFFICIAL = "Official"
SOURCE_WEB = "Web"

# (score, department, why-relevant-to-SKEQI, title pattern). First match wins,
# so the most specific roles are listed first.
RELEVANCE_RULES = (
    (100, "Executive Leadership", "Owns capex approval and manufacturing strategy; sponsor for a line-level partnership",
     r"\b(chief executive|c\.?e\.?o\.?|president|managing director|general manager|geschäftsführer|总经理|总裁|董事长)\b"),
    (96, "Operations", "Accountable for plant output, throughput and ramp-up - the outcomes SKEQI lines are bought for",
     r"\b(chief operating|c\.?o\.?o\.?)\b"),
    (95, "Technology", "Sets process and equipment technology direction, including automation and inspection",
     r"\b(chief technolog|chief technical|c\.?t\.?o\.?)\b"),
    (94, "Supply Chain", "Owns supplier strategy and approved-vendor status - the gate for a new equipment supplier",
     r"\b(chief supply chain|c\.?s\.?c\.?o\.?|chief procurement|c\.?p\.?o\.?)\b"),
    (92, "Digital / IT", "Owns MES, traceability and digital-factory systems that SKEQI's QIHANG platform addresses",
     r"\b(chief information|chief digital|c\.?i\.?o\.?)\b"),
    (90, "Manufacturing", "Owns the manufacturing footprint and equipment investment decisions",
     r"\b(chief manufacturing|chief production|chief industrial)\b"),
    (86, "Procurement", "Runs the buying process for production equipment and holds the supplier shortlist",
     r"\b(v\.?p\.?|vice president|head|svp|evp|senior vice president)\b.{0,24}\b(procurement|purchasing|sourcing)\b"),
    (70, "Digital Manufacturing", "Owns MES, traceability and smart-factory rollout - matches SKEQI's QIHANG digital factory",
     r"\b(digital manufactur|smart manufactur|smart factory|industry 4\.0|industrie 4\.0|m\.?e\.?s\.?)\w*"),
    (85, "Manufacturing", "Specifies and approves assembly, welding and inspection lines",
     r"\b(v\.?p\.?|vice president|head|svp|evp)\b.{0,24}\b(manufactur|production|industrialis|industrializ)\w*"),
    (84, "Engineering", "Owns process and equipment engineering standards a new line must satisfy",
     r"\b(v\.?p\.?|vice president|head|svp|evp)\b.{0,24}\b(engineering|technolog)\w*"),
    (83, "Operations", "Owns plant performance, yield and uptime targets",
     r"\b(v\.?p\.?|vice president|head|svp|evp)\b.{0,24}\b(operations|manufacturing operations)\b"),
    (82, "Supply Chain", "Owns supplier qualification and localisation of equipment supply",
     r"\b(v\.?p\.?|vice president|head|svp|evp)\b.{0,24}\b(supply chain|logistics)\b"),
    (80, "Quality", "Owns yield, weld quality and inspection acceptance criteria - directly tied to SKEQI X-ray and laser scope",
     r"\b(v\.?p\.?|vice president|head|svp|evp)\b.{0,24}\b(quality)\b"),
    (78, "Strategic Sourcing", "Runs strategic sourcing for capital equipment; hard to reach through public web sources",
     r"\b(strategic sourcing|category manager|commodity manager)\b"),
    (77, "Procurement", "Day-to-day owner of equipment RFQs and supplier onboarding",
     r"\b(procurement|purchasing|sourcing)\b.{0,20}\b(director|manager|lead|head)\w*"),
    (76, "Supply Chain", "Coordinates supplier capacity and delivery for production equipment",
     r"\b(supply chain)\b.{0,20}\b(director|manager|lead|head)\w*"),
    (75, "Plant Leadership", "Owns the site where a SKEQI line would be installed and commissioned",
     r"\b(plant manager|plant director|site director|site manager|works manager|factory manager|厂长)\b"),
    (74, "Manufacturing", "Defines line layout, cycle time and equipment requirements",
     r"\b(manufactur|production)\w*\b.{0,20}\b(director|manager|lead|head)\w*"),
    (73, "Automation", "Direct technical counterpart for automation, robotics and controls scope",
     r"\b(automation|robotic|controls)\w*\b.{0,24}\b(director|manager|lead|head|engineer)\w*"),
    (72, "Battery Engineering", "Owns cell, module and PACK process design - SKEQI's core assembly scope",
     r"\b(battery|cell|module|pack)\b.{0,24}\b(engineering|director|manager|lead|head)\w*"),
    (71, "Energy Storage", "Owns ESS product and manufacturing scope that SKEQI assembly lines serve",
     r"\b(energy storage|e\.?s\.?s\.?|bess)\b"),
    (69, "Engineering", "Technical evaluator for equipment, tooling and process capability",
     r"\b(process engineering|equipment engineering|industrial engineering|manufacturing engineering)\b"),
    (68, "Engineering", "Reviews and approves equipment specifications",
     r"\b(engineering)\b.{0,20}\b(director|manager|lead|head)\w*"),
    (66, "Quality", "Sets inspection and acceptance standards for incoming lines",
     r"\b(quality)\b.{0,20}\b(director|manager|lead|head)\w*"),
    (60, "Sales / Business Leadership", "Commercial owner of the programmes that drive new capacity",
     r"\b(v\.?p\.?|vice president|head|director)\b.{0,24}\b(sales|business development|commercial)\b"),
)

SENIORITY_BONUS = {"c_suite": 12, "founder": 10, "owner": 9, "partner": 7,
                   "vp": 6, "head": 5, "director": 3, "manager": 0,
                   "senior": -4, "entry": -8, "intern": -12}

_NOT_A_PERSON = re.compile(
    r"^(not enough evidence|证据不足|n/?a|none|unknown|tbd|—|-|待定|未知)\b", re.I)

# Checked BEFORE scoring. "Executive Assistant to the CEO" contains "CEO" and
# otherwise scores as a chief executive; support and back-office roles are not
# decision makers for a production-line purchase.
EXCLUDED_TITLES = re.compile(
    r"\b(executive assistant|administrative assistant|personal assistant|assistant to the|"
    r"secretary|receptionist|intern|trainee|apprentice|recruiter|recruiting|"
    r"talent acquisition|payroll|accounts (receivable|payable)|bookkeep|"
    r"human resources|people operations|social media|communications|public relations|"
    r"copywriter|customer service|customer support|office manager|"
    r"travel|benefits|facilities coordinator|safety coordinator|助理|秘书|人事|招聘)\b", re.I)


_ROLE_WORD = (r"director|manager|head|vice president|vp|svp|evp|lead|chief|"
              r"president|officer|specialist|engineer")


def title_variants(title):
    """The title as written, plus an order-swapped form.

    Real titles come both ways round - "Supply Chain Director" and "Director of
    Supply Chain" are the same job. Matching only one order silently dropped
    procurement, supply-chain and automation leaders, which are exactly the
    contacts Apollo is here to find.
    """
    base = " " + re.sub(r"[^a-z0-9&./ ]+", " ", (title or "").lower()) + " "
    base = re.sub(r"\s{2,}", " ", base)
    out = [base]
    swapped = re.sub(r"\b(" + _ROLE_WORD + r")\s+(?:of|for)\s+(?:the\s+)?(.+)",
                     r"\2 \1", base)
    if swapped != base:
        out.append(" " + swapped.strip() + " ")
    return out


def score_person(title, seniority=""):
    """(score, department, why). score 0 means: not relevant, do not retain."""
    variants = title_variants(title)
    t_cjk = title or ""
    if any(EXCLUDED_TITLES.search(v) for v in variants) or EXCLUDED_TITLES.search(t_cjk):
        return 0, "", ""
    for score, dept, why, pattern in RELEVANCE_RULES:
        if any(re.search(pattern, v, re.I) for v in variants) or re.search(pattern, t_cjk, re.I):
            return score + SENIORITY_BONUS.get((seniority or "").lower(), 0), dept, why
    return 0, "", ""


def _key(name):
    """Identity key for dedupe: accent-folded first + last token, lowercase."""
    cleaned = re.sub(r"\b(dr|mr|ms|mrs|prof|ing|dipl|phd|mba|jr|sr|iii|ii)\b\.?", " ",
                     (name or "").lower())
    cleaned = re.sub(r"[^a-z0-9一-鿿 ]+", " ", cleaned)
    parts = [p for p in cleaned.split() if p]
    if not parts:
        return ""
    return parts[0] if len(parts) == 1 else "{} {}".format(parts[0], parts[-1])


def from_apollo(people):
    """Score, filter and shape Apollo people. Anyone scoring 0 is dropped."""
    out = []
    for p in people:
        score, dept, why = score_person(p.get("title"), p.get("seniority"))
        if score <= 0:
            continue
        out.append({
            "name": p["name"], "title": p["title"],
            "department": dept or ", ".join(p.get("departments") or []) or "—",
            "seniority": (p.get("seniority") or "").replace("_", " ") or "—",
            "why": why, "score": score,
            "sources": [SOURCE_APOLLO],
            "linkedin_url": p.get("linkedin_url") or "",
            "evidence_url": "", "apollo_id": p.get("apollo_id", ""),
            "company": p.get("organization_name") or "",
            "location": p.get("location") or ", ".join(
                x for x in (p.get("city"), p.get("country")) if x),
            "email": p.get("email") or "",
            "email_status": p.get("email_status") or "Not available",
            "enriched": bool(p.get("enriched")),
        })
    out.sort(key=lambda r: -r["score"])       # ranked before anything is enriched
    return out


# ---------------------------------------------------------------------------
# The people the model already found on the web, read back out of its report
# ---------------------------------------------------------------------------

_SECTION = re.compile(r"^##\s*Key Decision Makers.*?$(.*?)(?=^##\s|\Z)", re.M | re.S)


def parse_report_people(report, sources=None, official_domain=""):
    """Read the Key Decision Makers table out of the model's own report.

    Only the English block is parsed - the Chinese block restates the same
    people and would double every row.
    """
    m = _SECTION.search(report or "")
    if not m:
        return []
    body = m.group(1)
    cn = body.find("**中文")
    if cn > 0:
        body = body[:cn]
    by_id = {str(s.get("id")): s.get("url", "") for s in (sources or [])}
    rows, header = [], None
    for line in body.split("\n"):
        line = line.strip()
        if not (line.startswith("|") and line.endswith("|")):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if re.fullmatch(r"[\s:|-]*", "".join(cells)):
            continue
        low = [c.lower() for c in cells]
        if header is None and any("name" in c for c in low):
            header = low
            continue
        if header is None:
            continue
        rows.append(dict(zip(header, cells)))

    def pick(row, *keys):
        for k in row:
            if any(key in k for key in keys):
                return row[k]
        return ""

    out = []
    for row in rows:
        name = re.sub(r"\*\*|\[\d+\]", "", pick(row, "name")).strip()
        if not name or _NOT_A_PERSON.match(name) or len(name) > 60:
            continue
        title = re.sub(r"\*\*|\[\d+\]", "", pick(row, "title", "role", "position")).strip()
        # The same exclusions apply however a person was found. HR, comms and
        # assistants are not decision makers for a production-line purchase.
        if EXCLUDED_TITLES.search(title or ""):
            continue
        src_cell = pick(row, "source", "evidence")
        why = re.sub(r"\*\*", "", pick(row, "relevance", "why")).strip()
        urls = re.findall(r"https?://[^\s|)\]]+", src_cell)
        for cid in re.findall(r"\[(\d+)\]", src_cell):
            if by_id.get(cid):
                urls.append(by_id[cid])
        # The model is told to label the Source column, and it restates Apollo
        # people there. Crediting those to the web would invent corroboration
        # that does not exist, so the declared source is honoured.
        cell_low = src_cell.lower()
        official = bool(official_domain) and any(official_domain in u for u in urls)
        if not official:
            official = bool(re.search(r"official (website|site)|company website|leadership page", cell_low))
        if re.search(r"\bapollo\b", cell_low) and not urls and not official:
            source = SOURCE_APOLLO
        elif official:
            source = SOURCE_OFFICIAL
        else:
            source = SOURCE_WEB
        score, dept, rule_why = score_person(title)
        out.append({
            "name": name, "title": title or "—",
            "department": dept or "—", "seniority": "—",
            "why": why or rule_why,
            "score": score if score > 0 else (0 if source == SOURCE_APOLLO else 50),
            "sources": [source],
            "linkedin_url": next((u for u in urls if "linkedin.com" in u), ""),
            "evidence_url": urls[0] if urls else "", "apollo_id": "",
            "company": "", "location": "",
            # Web research never yields an email. Saying "Not available" is the
            # honest answer; guessing one from a domain pattern is not.
            "email": "", "email_status": "Not available", "enriched": False,
        })
    return out


def merge(web_people, apollo_people, limit=20):
    """One row per person, ranked. Web/official records win on title."""
    merged = {}
    order = []
    for person in list(web_people) + list(apollo_people):
        key = _key(person["name"])
        if not key:
            continue
        if key not in merged:
            merged[key] = dict(person)
            order.append(key)
            continue
        into = merged[key]
        for src in person["sources"]:
            if src not in into["sources"]:
                into["sources"].append(src)
        # Official/web title is authoritative for executives; Apollo fills the
        # structured fields that prose does not carry.
        incoming_web = SOURCE_APOLLO not in person["sources"]
        if incoming_web and person.get("title") not in ("", "—"):
            into["title"] = person["title"]
        if into.get("title") in ("", "—"):
            into["title"] = person.get("title") or "—"
        for field in ("department", "seniority", "linkedin_url", "evidence_url",
                      "location", "apollo_id", "company", "email", "email_status"):
            if into.get(field) in ("", "—", None) and person.get(field) not in ("", "—", None):
                into[field] = person[field]
        if not into.get("why"):
            into["why"] = person.get("why", "")
        into["score"] = max(into.get("score", 0), person.get("score", 0))

    people = [merged[k] for k in order]
    for p in people:
        # Ordering keeps Apollo-only rows below corroborated ones at equal score.
        p["source_badges"] = p["sources"]
        p["corroborated"] = len(p["sources"]) > 1
    people.sort(key=lambda p: (-p["score"], not p["corroborated"], p["name"]))
    return people[:limit]


def to_prompt_block(people, org_name=""):
    """Apollo people as a plain block for the synthesis prompt.

    Deliberately NOT added to the numbered web-source evidence: Apollo is a B2B
    database, not a citable web page, and putting it in the [n] list would let
    the model present it as a public source.
    """
    if not people:
        return ""
    lines = ["APOLLO PEOPLE DIRECTORY (source: Apollo B2B database{}; NOT a public web page)".format(
        " - " + org_name if org_name else ""),
        "Cite these as [Apollo], never as a numbered web source.",
        "Name | Title | Department | Seniority"]
    for p in people:
        lines.append("{} | {} | {} | {}".format(p["name"], p["title"], p["department"], p["seniority"]))
    return "\n".join(lines)


def to_markdown_table(people):
    """The merged roster, in the column layout the brief specifies."""
    if not people:
        return ""
    head = ("| Name | Title | Department | Seniority | Why Relevant to SKEQI | Source |\n"
            "|---|---|---|---|---|---|")
    rows = ["| {} | {} | {} | {} | {} | {} |".format(
        p["name"], p["title"], p["department"], p["seniority"],
        (p["why"] or "—").replace("|", "/"), " + ".join(p["sources"])) for p in people]
    return head + "\n" + "\n".join(rows)


def summary(people, apollo_usage=None):
    """Roster composition. Apollo call/credit counts live in apollo_usage and are
    only echoed here when that usage record is actually supplied - reporting 0
    calls when the caller simply did not have the record would be a false zero."""
    out = {
        "total": len(people),
        "apollo_only": sum(1 for p in people if p["sources"] == [SOURCE_APOLLO]),
        "web_only": sum(1 for p in people if SOURCE_APOLLO not in p["sources"]),
        "merged": sum(1 for p in people if p["corroborated"]),
        "departments": sorted({p["department"] for p in people if p["department"] not in ("", "—")}),
    }
    out["with_email"] = sum(1 for p in people if p.get("email"))
    out["verified_email"] = sum(
        1 for p in people if (p.get("email_status") or "").startswith("Verified"))
    if apollo_usage is not None:
        out.update(apollo_people_returned=apollo_usage.get("people_returned", 0),
                   apollo_people_retained=apollo_usage.get("people_retained", 0),
                   apollo_people_enriched=apollo_usage.get("people_enriched", 0),
                   apollo_calls=apollo_usage.get("calls", 0))
    return out
