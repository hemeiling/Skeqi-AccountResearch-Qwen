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
SOURCE_CRM = "CRM"          # already in our own database; costs nothing to reuse

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
     r"\b(battery|cell|module|pack)\b.{0,24}\b(engineer|engineering|director|manager|lead|head)\w*"),
    (71, "Energy Storage", "Owns ESS product and manufacturing scope that SKEQI assembly lines serve",
     r"\b(energy storage|e\.?s\.?s\.?|bess)\b"),
    (69, "Engineering", "Technical evaluator for equipment, tooling and process capability",
     r"\b(process|equipment|industrial|manufacturing|automation|controls)\s+engineer\w*\b"),
    (68, "Engineering", "Reviews and approves equipment specifications",
     r"\b(engineering)\b.{0,20}\b(director|manager|lead|head)\w*"),
    (66, "Quality", "Sets inspection and acceptance standards for incoming lines",
     r"\b(quality)\b.{0,20}\b(director|manager|lead|head)\w*"),
    (60, "Sales / Business Leadership", "Commercial owner of the programmes that drive new capacity",
     r"\b(v\.?p\.?|vice president|head|director)\b.{0,24}\b(sales|business development|commercial)\b"),

    # ---- Second band: people who influence, specify, evaluate, fund or approve
    # a SKEQI purchase without owning the production line themselves. They rank
    # BELOW every core role above, but scoring them 0 dropped them entirely and
    # left the roster as a list of manufacturing titles rather than a buying
    # centre. Anything here that is genuinely line-adjacent has already matched
    # a higher rule, because first match wins.
    (58, "Program Leadership", "Runs the launch or capacity programme a new line is bought under; controls timing and scope",
     r"\b(program|programme|project|launch|industrialisation|industrialization)\b.{0,24}\b(director|manager|lead|head|owner)\w*"),
    (56, "Business Unit Leadership", "Owns the P&L the investment is charged to; can sponsor or veto a line purchase",
     r"\b(general manager|business unit|division|segment|country manager|managing director)\b"),
    (55, "Finance Leadership", "Approves and funds capital equipment spend; owns the business case a line must pass",
     r"\b(chief financial|c\.?f\.?o\.?|财务总监|首席财务官)\b"
     r"|\b(v\.?p\.?|vice president|head|svp|evp|director|manager)\b.{0,24}"
     r"\b(financ|controll|capital|investment|treasur)\w*"
     r"|\b(financ|treasur)\w*\b.{0,20}\b(director|manager|head|lead|controller|officer|"
     r"vice president|v\.?p\.?)\w*"
     r"|\b(financial controller|controller)\b"),
    (52, "Innovation / R&D", "Sets the technology roadmap that decides which process and equipment get specified",
     r"\b(r ?& ?d|research and development|innovation|advanced (manufactur|engineering|technolog))\w*"
     r"\b.{0,24}\b(director|manager|lead|head|vice president|v\.?p\.?)\w*"
     r"|\b(chief (innovation|research) officer)\b"),
    (50, "Engineering", "Evaluates equipment capability and signs technical acceptance",
     r"\b(engineer)\w*\b"),
    (46, "Quality", "Sets inspection and acceptance criteria a delivered line must meet",
     r"\b(quality|qa|qc)\b"),
    (44, "Technology", "Owns the systems a new line must integrate with",
     r"\b(technolog|digital|automation|controls|i\.?t\.?)\w*\b.{0,24}"
     r"\b(director|manager|lead|head|architect|specialist)\w*"),
    (42, "Maintenance / Reliability", "Owns uptime and spare-part strategy for installed equipment",
     r"\b(maintenance|reliability|asset management)\b"),
    (40, "Supply Chain", "Touches supplier onboarding and delivery for capital equipment",
     r"\b(supplier|vendor|sourcing|procurement|purchasing|supply)\w*\b"),
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


_PLACEHOLDER = {"", "-", "—", "n/a", "na", "none", "null", "unknown", "tbd", "?"}


def _clean(value):
    """A field the CRM filled with a placeholder is an empty field."""
    v = (value or "").strip()
    return "" if v.lower() in _PLACEHOLDER else v


def _norm_company(value):
    """Company identity for dedupe: lowercase, no legal suffix, no punctuation."""
    v = (value or "").lower()
    v = re.sub(r"\b(co\.?,? ?ltd\.?|ltd\.?|llc|inc\.?|corp\.?|corporation|company|"
               r"gmbh|plc|s\.?a\.?|s\.?p\.?a\.?|ag|bv|nv|pte\.? ?ltd\.?)\b", " ", v)
    v = re.sub(r"[^a-z0-9一-鿿 ]+", " ", v)
    return re.sub(r"\s+", " ", v).strip()


def identity_keys(person):
    """Every key this person can be recognised by, strongest evidence first.

    Email is exact; a LinkedIn profile is exact; a name is only decisive
    alongside a company — "Jim Farley" at two different companies is two people.
    """
    keys = []
    email = (person.get("email") or "").strip().lower()
    if email and "@" in email:
        keys.append("email:" + email)
    li = _norm_linkedin(person.get("linkedin_url"))
    if li:
        keys.append("li:" + li)
    name = _key(person.get("name"))
    if name:
        keys.append("name:{}|{}".format(name, _norm_company(person.get("company"))))
    return keys


def _norm_linkedin(value):
    li = (value or "").strip().lower()
    if not li:
        return ""
    li = re.sub(r"^https?://(www\.)?", "", li).rstrip("/")
    return re.sub(r"\?.*$", "", li)


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


def from_crm(rows):
    """Score and shape contacts the CRM already holds.

    Same shape as from_apollo so merge() can treat them as one population. An
    email that is already in the CRM is kept as-is; nothing is invented, and a
    row with no email keeps an empty one rather than a guess.
    """
    out = []
    for r in rows or []:
        name = (r.get("name") or "").strip()
        title = (r.get("title") or "").strip()
        if not name or not title:
            continue
        score, dept, why = score_person(title, r.get("seniority"))
        if score <= 0:
            continue
        email = (r.get("email") or "").strip()
        out.append({
            "name": name, "title": title,
            # A CRM placeholder is not a department: fall back to the ruleset's,
            # which is what makes the roster readable by buying-centre function.
            "department": _clean(r.get("department")) or dept or "—",
            "seniority": _clean(r.get("seniority")).replace("_", " ") or "—",
            "why": why, "score": score,
            "sources": [SOURCE_CRM],
            "linkedin_url": (r.get("linkedin_url") or "").strip(),
            "evidence_url": "", "apollo_id": "",
            "company": (r.get("company") or "").strip(),
            "location": (r.get("location") or "").strip(),
            "email": email,
            "email_status": (r.get("email_status") or "").strip()
                            or ("From CRM" if email else "Not available"),
            "enriched": bool(email),
            "crm_contact_id": r.get("crm_contact_id"),
        })
    out.sort(key=lambda r: -r["score"])
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
        # The model writing "Apollo" in its own table is not evidence that Apollo
        # ran. Verkor's roster carried an Apollo badge on a run where Apollo was
        # never called. Provenance is decided by the pipeline: rows parsed out of
        # the report are web or official, and only records that actually came
        # back from Apollo carry SOURCE_APOLLO (see from_apollo()).
        if official:
            source = SOURCE_OFFICIAL
        else:
            source = SOURCE_WEB
        score, dept, rule_why = score_person(title)
        out.append({
            "name": name, "title": title or "—",
            "department": dept or "—", "seniority": "—",
            "why": why or rule_why,
            "score": score if score > 0 else 50,
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
    merged = {}          # canonical key -> record
    alias = {}           # every identity key seen -> canonical key
    by_name = {}         # normalised name -> [canonical keys]
    order = []

    def _find(person):
        """The record this person already is, or None.

        Email and LinkedIn are exact. A name needs a company to be decisive —
        but only when BOTH sides state one. A row parsed out of the model's
        report carries no company, so requiring one there split every CRM
        contact the model also mentioned into two rows: Margot Cussigh appeared
        twice for Verkor, once with her CRM email and LinkedIn and once empty.
        Within a single company's research run everyone belongs to that company,
        so a missing company is not a distinction.
        """
        for k in identity_keys(person):
            if k in alias:
                return alias[k]
        name = _key(person.get("name"))
        if not name:
            return None
        mine = _norm_company(person.get("company"))
        for canonical in by_name.get(name, []):
            theirs = _norm_company((merged.get(canonical) or {}).get("company"))
            # Same name, and nobody is claiming a DIFFERENT company.
            if not mine or not theirs or mine == theirs:
                return canonical
        return None

    for person in list(web_people) + list(apollo_people):
        # Source priority is the ORDER of this list; see merge()'s callers.
        keys = identity_keys(person)
        if not keys:
            continue
        hit = _find(person)
        if hit is None:
            canonical = keys[0]
            # dict() is shallow, so the copy would SHARE the caller's sources
            # list and merge would append provenance onto the input. Re-merging
            # the same records then accumulated badges that never applied.
            rec = dict(person)
            rec["sources"] = list(person.get("sources") or [])
            merged[canonical] = rec
            order.append(canonical)
            for k in keys:
                alias.setdefault(k, canonical)
            nm = _key(person.get("name"))
            if nm:
                by_name.setdefault(nm, []).append(canonical)
            continue
        # Same person reached by email, LinkedIn, or name with no conflicting company.
        for k in keys:
            alias.setdefault(k, hit)
        into = merged[hit]
        for src in person["sources"]:
            if src not in into["sources"]:
                into["sources"].append(src)
        # Official/web title is authoritative for executives; Apollo fills the
        # structured fields that prose does not carry.
        incoming_web = SOURCE_APOLLO not in person["sources"]
        if incoming_web and person.get("title") not in ("", "—"):
            into["title"] = person["title"]
        # CRM data is authoritative for the structured fields: we verified it, it
        # costs nothing, and nothing else may overwrite it with a blank.
        if SOURCE_CRM in person["sources"]:
            for field in ("email", "email_status", "linkedin_url", "company",
                          "location", "crm_contact_id", "seniority"):
                if person.get(field) not in ("", "—", None):
                    into[field] = person[field]
        if person.get("crm_contact_id") and not into.get("crm_contact_id"):
            into["crm_contact_id"] = person["crm_contact_id"]
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
    return _diversify(people, limit)


# At most this many people from any one department in the returned roster,
# unless there is nothing else to fill the slots with.
_PER_DEPARTMENT_CAP = 4


def _diversify(people, limit):
    """Top N as a BUYING CENTRE, not N copies of the same job title.

    Ford's CRM record holds eighteen battery manufacturing engineers. Ranked on
    score alone they fill the entire roster and push out the plant, procurement,
    finance and programme people who actually influence, fund or approve the
    purchase. So each department gets a cap on the first pass; whatever is left
    over backfills by score, which means a company that genuinely only has one
    kind of contact still returns a full list.
    """
    if limit is None or len(people) <= limit:
        return people[:limit] if limit else people
    picked, seen, spill = [], {}, []
    for person in people:                       # already score-sorted
        dept = person.get("department") or "—"
        if seen.get(dept, 0) < _PER_DEPARTMENT_CAP:
            seen[dept] = seen.get(dept, 0) + 1
            picked.append(person)
            if len(picked) == limit:
                return picked
        else:
            spill.append(person)
    for person in spill:                        # backfill, still by score
        picked.append(person)
        if len(picked) == limit:
            break
    # The cap decides WHO is in the roster; the reader still wants it ranked.
    picked.sort(key=lambda p: (-p.get("score", 0), not p.get("corroborated"), p.get("name", "")))
    return picked[:limit]


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
