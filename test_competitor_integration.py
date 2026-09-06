#!/usr/bin/env python
"""Target-competitor discovery wired into the production research pipeline.

The integration has one job beyond the discovery module itself: keep the two
questions apart. "Who supplies this account" and "who competes with this
account" use different verification, different vocabularies and different parts
of the report, and the failure mode this suite exists to catch is one silently
becoming the other.

Everything runs against stored evidence from a real earlier run plus mocked
search and fetch. No network, no model call, no cost.

  .venv/bin/python test_competitor_integration.py
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import app                                                        # noqa: E402
import competitor_discovery as cd                                 # noqa: E402
import offering_profile as op                                     # noqa: E402
import provider_view as pv                                        # noqa: E402
import research_service as rs                                     # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (" - " + detail if detail else ""))


# --------------------------------------------------------------------------
# Stored evidence from a real ACRO run. Used unmodified: the point is that the
# profile driving discovery is the one production actually produces.
# --------------------------------------------------------------------------
CACHE = os.path.join(
    "/private/tmp/claude-501/-Users-meilinghe-Downloads-Qwen-API-Search-----",
    "1c06afbf-82e9-4c76-8bed-53680bf7d688/scratchpad/evidence_cache_before",
    "acro-automation-systems_7096d244d2875e57.json")
ACCOUNT = "ACRO Automation Systems"

# The cache is a convenience, not a dependency: set the env var (or run on
# another machine) and the suite runs on the inline fixture instead.
if os.path.exists(CACHE) and not os.environ.get("COMPETITOR_FIXTURE_ONLY"):
    STORED = json.load(io.open(CACHE, encoding="utf-8"))["evidence"]
else:                                       # fixture stands in for the cache
    STORED = [{"url": "https://www.acro.com/", "title": "ACRO Automation Systems",
               "domain": "acro.com", "official": True, "tier": 1,
               "source_type": "official-website",
               "text": "ACRO Automation Systems designs and builds custom automated "
                       "assembly systems, robotic welding cells, machine vision "
                       "inspection and dispensing equipment for automotive and "
                       "consumer goods manufacturers. Milwaukee, WI."}]

PROFILE = op.build_profile(STORED, ACCOUNT, "acro.com")

# A manufacturer: buys automation, does not sell it. Ford is this shape.
MANUFACTURER = op.build_profile(
    [{"url": "https://news.example.com/a", "title": "Plant expansion",
      "domain": "news.example.com", "official": False, "tier": 3,
      "text": "Northwind Motors will expand vehicle production and uses ABB "
              "robots on its assembly lines."}],
    "Northwind Motors", "northwind-motors.com")


# --------------------------------------------------------------------------
# Mocked network. `search` returns snippets; `fetch_page_text` returns bodies.
# The two are deliberately different so a snippet leaking into evidence shows up.
# --------------------------------------------------------------------------
PAGES = {
    "https://rival-one.com/about": (
        "Rival One builds custom automated assembly systems, robotic welding "
        "cells and machine vision inspection stations for automotive "
        "manufacturers. Headquartered in Milwaukee, WI."),
    "https://rival-two.com/company": (
        "Rival Two Corporation supplies dispensing and inspection/test "
        "equipment to consumer goods producers."),
    "https://same-industry.com/about": (
        "Great Lakes Motors is an automotive manufacturer in Milwaukee "
        "producing vehicles and vehicle components."),
    "https://acro.com/about": (
        "ACRO Automation Systems designs and builds custom automated assembly "
        "systems and robotic welding cells for automotive manufacturers."),
    "https://blank.example.com/x": "",
    "https://directory.example.com/listing": (
        "Undisclosed vendor. Various suppliers of automated assembly and "
        "welding systems serve automotive customers in Milwaukee."),
}
SNIPPETS = {
    # The snippet is far richer than the page, on purpose.
    "https://blank.example.com/x": "MegaWeld Systems - robotic welding, machine "
                                   "vision and assembly for automotive, Milwaukee WI",
}


class FakeClient(object):
    def __init__(self, results, fail_on=()):
        self.results, self.fail_on, self.queries = results, set(fail_on), []

    def search(self, query):
        self.queries.append(query)
        if query in self.fail_on:
            raise RuntimeError("boom")
        return list(self.results)


def hit(url, title=""):
    return {"url": url, "title": title,
            "snippet": SNIPPETS.get(url, "search result snippet"),
            "domain": url.split("/")[2]}


_real_fetch = rs.fetch_page_text
FETCHED = []


def fake_fetch(url, *a, **k):
    FETCHED.append(url)
    body = PAGES.get(url)
    if body is None:
        raise RuntimeError("unreachable")
    return body, "mock"


rs.fetch_page_text = fake_fetch


def run(hits, profile=PROFILE, account=ACCOUNT, fail_on=()):
    sink = []
    client = FakeClient(hits, fail_on)
    verify = rs.competitor_verifier(profile, account, "", rs.derive_aliases(account, "", "acro.com"), sink)
    ev, cov = cd.discover(client, profile, verify)
    return sink, ev, cov, client


print("\n[1] Stored ACRO evidence drives real discovery")
check("the stored profile is competitor-ready", PROFILE["competitor_discovery_ready"],
      str(PROFILE["competitor_skip_reason"]))
comp, ev, cov, client = run([hit("https://rival-one.com/about", "Rival One"),
                             hit("https://rival-two.com/company", "Rival Two Corporation")])
check("searches actually ran", cov["search_count"] > 0, str(cov["search_count"]))
check("the ceiling is respected", cov["search_count"] <= cd.MAX_COMPETITOR_SEARCHES,
      str(cov["search_count"]))
check("competitors were retained", len(comp) >= 1, str([c["organization_name"] for c in comp]))
check("coverage records the queries that ran, not the ones planned",
      cov["queries"] == client.queries, str(cov["queries"][:2]))
check("no query carries the account name",
      not any("acro" in q.lower() for q in client.queries), str(client.queries[:2]))
check("no query contains raw whitespace or a slash",
      all(q == " ".join(q.split()) and "/" not in q for q in client.queries),
      str(client.queries[:3]))
row = comp[0]
for f in ("organization_name", "organization_key", "competition_type", "offering_overlap",
          "customer_or_industry_overlap", "geographic_or_market_overlap",
          "competitive_rationale", "source_urls", "source_domains", "provenance",
          "confidence", "discovered_by"):
    check("row carries %s" % f, f in row)

print("\n[2] A snippet is discovery, never evidence")
FETCHED[:] = []
comp2, ev2, cov2, _ = run([hit("https://blank.example.com/x", "MegaWeld Systems")])
check("the page was fetched, not trusted from the snippet",
      "https://blank.example.com/x" in FETCHED)
check("an empty body yields no competitor", not comp2, str(comp2))
check("and yields no evidence", not ev2, str(len(ev2)))
check("the rich snippet never became evidence text",
      not any("MegaWeld" in (e.get("text") or "") for e in ev2))

print("\n[3] Same industry is not competition")
comp3, ev3, cov3, _ = run([hit("https://same-industry.com/about", "Great Lakes Motors")])
check("an automotive manufacturer is rejected", not comp3, str(comp3))
check("and the rejection is counted", cov3["rejected_same_industry"] >= 1,
      str(cov3["rejected_same_industry"]))

print("\n[4] The account itself is never its own competitor")
comp4, _, _, _ = run([hit("https://acro.com/about", "ACRO Automation Systems")])
check("the account's own page is rejected", not comp4, str(comp4))

print("\n[5] A placeholder is not an organisation")
comp5, _, _, _ = run([hit("https://directory.example.com/listing", "Undisclosed")])
check("Undisclosed / Various suppliers is rejected",
      not any(c["organization_name"].lower().startswith(("undisclosed", "various"))
              for c in comp5), str([c["organization_name"] for c in comp5]))

print("\n[6] Competitor and provider vocabularies never mix")
allowed = {cd.DIRECT, cd.PARTIAL, cd.ADJACENT}
check("competition_type stays in its own vocabulary",
      all(c["competition_type"] in allowed for c in comp),
      str([c["competition_type"] for c in comp]))
check("no provider relationship class leaks in",
      not any(c["competition_type"] in (rs.REL_CONFIRMED, rs.REL_STRONG, rs.REL_MARKET)
              for c in comp))
check("provenance is preserved as a separate dimension",
      all(c["provenance"] == rs.PROV_MARKET for c in comp))
check("competitor rows carry no provider fields",
      not any("relationship" in c or "capability" in c for c in comp))

print("\n[7] One row per organisation")
dup = [hit("https://rival-one.com/about", "Rival One"),
       hit("https://rival-one-mirror.com/about", "Rival One")]
PAGES["https://rival-one-mirror.com/about"] = PAGES["https://rival-one.com/about"]
comp7, _, _, _ = run(dup)
names = [c["organization_key"] for c in comp7]
check("duplicate organisations are merged", len(names) == len(set(names)), str(names))
merged = [c for c in comp7 if len(c["source_urls"]) > 1]
check("sources are merged onto the surviving row", bool(merged),
      str([len(c["source_urls"]) for c in comp7]))

print("\n[8] A manufacturer profile skips discovery instead of inventing")
sink8 = []
c8 = FakeClient([hit("https://rival-one.com/about", "Rival One")])
ev8, cov8 = cd.discover(c8, MANUFACTURER,
                        rs.competitor_verifier(MANUFACTURER, "Northwind Motors", "", (), sink8))
check("no search was issued", c8.queries == [] and cov8["search_count"] == 0)
check("no competitor was invented", not sink8 and not ev8)
check("used stays false", cov8["used"] is False)
check("a reason is recorded", bool(cov8["skip_reason"]), str(cov8["skip_reason"]))

print("\n[9] Synthesis sees only verified competitors")
block = pv.competitor_prompt_block(comp, PROFILE)
check("the block is labelled for the target",
      "VERIFIED TARGET COMPETITORS" in block)
check("verified names appear", comp[0]["organization_name"] in block)
check("the three dimensions are shown",
      "Offering overlap" in block and "Geography" in block)
# MANUFACTURER was skipped, so it must read as a limitation, not as a finding.
empty_block = pv.competitor_prompt_block([], MANUFACTURER)
check("a skipped path says NOT PERFORMED", "NOT PERFORMED" in empty_block)
check("the limitation sentence is verbatim EN",
      pv.NOT_SEARCHED_COMPETITOR_EN in empty_block)
check("the limitation sentence is verbatim ZH",
      pv.NOT_SEARCHED_COMPETITOR_ZH in empty_block)
searched_block = pv.competitor_prompt_block([], {})
check("a searched path uses the absence sentence verbatim EN",
      pv.NO_COMPETITOR_EN in searched_block)
check("a searched path uses the absence sentence verbatim ZH",
      pv.NO_COMPETITOR_ZH in searched_block)
check("the empty block forbids general knowledge",
      "general knowledge" in empty_block.lower())
check("the empty block names no organisation",
      "Rival" not in empty_block)
check("the skip reason is passed through",
      (MANUFACTURER["competitor_skip_reason"] or "") in empty_block)

print("\n[10] The prompt now means the TARGET's competitors")
SRC = io.open(os.path.join(HERE, "research_service.py"), encoding="utf-8").read()
i = SRC.index("## Competitor Analysis")
sec = SRC[i:SRC.index("## Existing Automation Providers", i)]
prov = SRC[SRC.index("## Existing Automation Providers"):SRC.index("## Key Decision Makers")]
check("the old SKEQI-centric instruction is gone",
      "competes with SKEQI for THIS account" not in sec and "competes with SKEQI" not in sec)
check("the 'Why it competes with SKEQI' column is gone",
      "Why it competes with SKEQI" not in sec)
check("the section is scoped to the account", "THIS ACCOUNT" in sec)
check("the table uses the competitor vocabulary",
      "DIRECT/PARTIAL/ADJACENT" in sec)
check("the verified block is the only source",
      "VERIFIED TARGET COMPETITORS" in sec)
check("the absence wording is instructed in both languages",
      pv.NO_COMPETITOR_EN in sec and pv.NO_COMPETITOR_ZH in sec)
check("and so is the not-performed wording",
      pv.NOT_SEARCHED_COMPETITOR_EN in sec and pv.NOT_SEARCHED_COMPETITOR_ZH in sec)
check("SKEQI incumbency intelligence still lives in Existing Automation Providers",
      "Incumbency" in prov and "COMPETE/REPLACE/COMPLEMENT/INTEGRATE" in prov)
check("synthesis is handed the competitors",
      "competitors=competitors" in SRC and "competitor_prompt_block" in SRC)
check("the package carries competitors and profile",
      '"competitors": competitors' in SRC and '"profile": profile' in SRC)

print("\n[11] The manifest counts work, not configuration")
ready_but_unrun = app._competitor_facts(
    dict(cd.empty_coverage(), profile_confidence={"capabilities": "high"}), [], PROFILE)
check("a ready profile that never searched is unused",
      ready_but_unrun["used"] is False and ready_but_unrun["searches"] == 0)
facts = app._competitor_facts(cov, comp, PROFILE)
check("used is true once a search ran", facts["used"] is True)
check("searches match what the client saw", facts["searches"] == len(client.queries),
      "%s vs %s" % (facts["searches"], len(client.queries)))
check("retained matches the rows", facts["retained_competitors"] == len(comp))
check("class counts sum to the rows",
      facts["direct"] + facts["partial"] + facts["adjacent"] == len(comp))
check("the skip case carries its reason",
      app._competitor_facts(cov8, [], MANUFACTURER)["skip_reason"] == cov8["skip_reason"])
check("the skip case is unused", app._competitor_facts(cov8, [], MANUFACTURER)["used"] is False)
full = app.execution_facts({"competitor_coverage": cov, "competitors": comp,
                            "profile": PROFILE, "evidence": [], "ai_usage": []}, [], None)
check("the block sits in the execution manifest", "competitor_discovery" in full)
check("counters are absolute integers",
      all(isinstance(full["competitor_discovery"][k], int)
          for k in ("searches", "candidates", "verified_organizations",
                    "retained_competitors", "direct", "partial", "adjacent")))
check("no monetary field was added",
      not any("cost" in k or "price" in k for k in full["competitor_discovery"]))

print("\n[12] A failing search degrades, never terminates")
comp12, ev12, cov12, c12 = run([hit("https://rival-one.com/about", "Rival One")],
                               fail_on=(cd.plan_intents(PROFILE, batch=1)[0][1],))
check("the run continued past the failure", cov12["search_count"] >= 1)
check("and still produced competitors", len(comp12) >= 1)

rs.fetch_page_text = _real_fetch
print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
    for f in FAIL:
        print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
