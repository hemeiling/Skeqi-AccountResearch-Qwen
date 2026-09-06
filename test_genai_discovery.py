# -*- coding: utf-8 -*-
"""The model reasons, Tavily proves, and nothing is published on memory alone.

The suite that this one replaces tested an overlap scorer, a capability lexicon
and a role-regex table. On AMADA WELD TECH those three produced eighteen
"competitors", six of which were AMADA itself, several of which were news
headlines, and one of which matched on the word "Terms" lifted from a
terms-and-conditions page. What remains here are guardrails, so this suite tests
guardrails: schema, identity, duplicates, placeholders, citations and budgets.

The reasoning is the model's and is not asserted. What IS asserted is that an
answer without a valid citation never reaches the report.

Runs on stored AMADA and Torus evidence with a scripted model and a scripted
search. No network, no model call.

  .venv/bin/python test_genai_discovery.py
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import app                                                        # noqa: E402
import genai_discovery as gd                                      # noqa: E402
import provider_view as pv                                        # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (" - " + detail if detail else ""))


SCRATCH = os.path.join(
    "/private/tmp/claude-501/-Users-meilinghe-Downloads-Qwen-API-Search-----",
    "1c06afbf-82e9-4c76-8bed-53680bf7d688/scratchpad")
ACCOUNT = "AMADA WELD TECH"
ALIASES = ("AMADA", "Amada Miyachi", "AMADA WELD TECH EUROPE")

PAGES = {
    "https://rival-one.com/about": (
        "Rival One Systems builds resistance welding and laser welding equipment "
        "for automotive and electronics manufacturers, competing directly with "
        "AMADA WELD TECH in micro-joining."),
    "https://directory.example.com/list": (
        "Directory of welding equipment suppliers in North America."),
    "https://dist.example.com/lines": (
        "Great Lakes Supply is the authorized distributor for AMADA WELD TECH in "
        "the Upper Midwest."),
    "https://integrator.example.com/about": (
        "Lakeside Systems integrates AMADA WELD TECH equipment into production "
        "lines for its customers."),
}


def fetch(url):
    if url not in PAGES:
        raise RuntimeError("unreachable")
    return PAGES[url], "mock"


class Script(object):
    """A scripted model: one answer for planning, one for verification."""

    def __init__(self, plan, verify):
        self.plan, self.verify = plan, verify
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.plan if "RESEARCH PLANNING" in prompt else self.verify


def searcher(urls):
    seen = {"n": 0, "queries": []}

    def search(query):
        seen["n"] += 1
        seen["queries"].append(query)
        return [{"url": u, "title": u} for u in urls]

    search.seen = seen
    return search


print("\n[1] Two passes: the first plans, the second answers")
ask = Script('{"need_research": false, "candidates": ["Rival One Systems"],'
             ' "what_they_sell": "welding equipment"}',
             '{"competitors": [{"name": "Rival One Systems",'
             ' "competition_type": "DIRECT", "why_it_competes": "same micro-joining",'
             ' "evidence_ids": [1], "evidence_supports_competition": true,'
             ' "confidence": "high"}]}')
search = searcher(["https://rival-one.com/about"])
rows, sources, cov = gd.discover(gd.COMPETITOR, ACCOUNT, ALIASES, ask, search, fetch)
check("exactly two model calls", cov["model_calls"] == 2, str(cov["model_calls"]))
check("the first prompt is the planning prompt", "RESEARCH PLANNING" in ask.prompts[0])
check("the second prompt carries the evidence",
      "[1]" in ask.prompts[1] and "https://rival-one.com/about" in ask.prompts[1])
check("the second prompt does not ask from memory",
      "ONLY the evidence" in ask.prompts[1] or "ONLY these numbered" in ask.prompts[1]
      or "Based ONLY" in ask.prompts[1])
check("one competitor was published", len(rows) == 1, str(rows))
check("it carries its source url",
      rows[0]["source_urls"] == ["https://rival-one.com/about"])
check("the model's own reason travels with it",
      rows[0]["why_it_competes"] == "same micro-joining")

print("\n[2] A named candidate makes the search targeted")
check("the candidate appears in a query",
      any("Rival One Systems" in q for q in search.seen["queries"]),
      str(search.seen["queries"][:2]))
check("the account appears in every query",
      all(ACCOUNT in q for q in search.seen["queries"]))
check("targeted queries are counted", cov["targeted_queries"] >= 1)
generic = gd.plan_queries(gd.COMPETITOR, ACCOUNT, [])
check("no candidate falls back to generic queries",
      generic and all("competitor" in q or "alternative" in q or "vs" in q
                      for q in generic), str(generic))
check("channel generics ask the channel questions",
      any("where to buy" in q for q in gd.plan_queries(gd.CHANNEL, ACCOUNT, [])))
check("the budget caps the query plan",
      len(gd.plan_queries(gd.COMPETITOR, ACCOUNT,
                          ["A", "B", "C", "D", "E", "F"])) <= gd.MAX_SEARCHES)

print("\n[3] Nothing factual is published from memory alone")
ask2 = Script('{"need_research": false, "candidates": ["Rival One Systems"]}',
              '{"competitors": [{"name": "Confident From Memory Inc",'
              ' "competition_type": "DIRECT", "why_it_competes": "I know this",'
              ' "evidence_ids": [], "evidence_supports_competition": true,'
              ' "confidence": "high"}]}')
rows2, _s2, cov2 = gd.discover(gd.COMPETITOR, ACCOUNT, ALIASES, ask2,
                               searcher(["https://rival-one.com/about"]), fetch)
check("an uncited row is dropped", rows2 == [], str(rows2))
check("and the drop is counted", cov2["dropped_uncited"] == 1)
ask3 = Script('{"need_research": false, "candidates": []}',
              '{"competitors": [{"name": "Ghost Corp", "competition_type": "DIRECT",'
              ' "why_it_competes": "x", "evidence_ids": [9],'
              ' "evidence_supports_competition": true, "confidence": "high"}]}')
rows3, _s3, cov3 = gd.discover(gd.COMPETITOR, ACCOUNT, ALIASES, ask3,
                               searcher(["https://rival-one.com/about"]), fetch)
check("a citation outside the evidence set is dropped", rows3 == [])
check("and counted as uncited", cov3["dropped_uncited"] == 1)

print("\n[3b] Co-mention is not competition: the model adjudicates its own citation")
# The failure this closes: a page that names two companies proves they were
# named together, nothing more. The verification pass must say, per row, that
# the evidence shows competition - and anything short of a clear yes is omitted.
CASES = (("true as a boolean", True, True),
         ('"true" as a string', "true", True),
         ("false", False, False),
         ("the field missing", None, False),
         ('"unclear"', "unclear", False),
         ('"partial"', "partial", False),
         ('"likely"', "likely", False))
for label, value, published in CASES:
    row = {"name": "Northwind Components", "competition_type": "PARTIAL",
           "why_it_competes": "appears on the same page", "evidence_ids": [1],
           "confidence": "medium"}
    if value is not None:
        row["evidence_supports_competition"] = value
    a = Script('{"need_research": false, "candidates": []}',
               json.dumps({"competitors": [row]}))
    r, _s, c = gd.discover(gd.COMPETITOR, ACCOUNT, ALIASES, a,
                           searcher(["https://rival-one.com/about"]), fetch)
    check("%s -> %s" % (label, "published" if published else "omitted"),
          bool(r) == published, str([x["organization_name"] for x in r]))
    if not published:
        check("  and counted as unsupported", c["dropped_unsupported"] == 1,
              str(c["dropped_unsupported"]))
check("a published row carries the adjudication",
      rows[0].get("evidence_supports_competition") is True)
check("the prompt names the roles that are not competitors",
      all(w in gd.COMPETITOR_VERIFY for w in
          ("supplier", "distributor", "integrator", "service provider",
           "technology partner", "customer", "manufacturer")))
check("the prompt says co-mention is insufficient",
      "merely mentioning both companies is NOT sufficient" in gd.COMPETITOR_VERIFY)
check("and forbids inferring from co-mention",
      "Do not infer competition from co-mention alone." in gd.COMPETITOR_VERIFY)
check("unsupported is counted apart from uncited",
      "dropped_unsupported" in gd.empty_coverage(gd.COMPETITOR)
      and "dropped_uncited" in gd.empty_coverage(gd.COMPETITOR),
      "one had no evidence; the other had evidence that did not support it")
check("channel asks its own version of the question",
      "evidence_supports_relationship" in gd.CHANNEL_VERIFY
      and "evidence_supports_competition" not in gd.CHANNEL_VERIFY)

print("\n[4] The account is never its own competitor or distributor")
for name in ("AMADA WELD TECH", "AMADA Micro Welding Section", "Amada Miyachi Europe",
             "AMADA WELD TECH EUROPE"):
    check("rejected as self: %s" % name, gd.is_the_account(name, ACCOUNT, ALIASES))
check("a genuine rival is not rejected",
      not gd.is_the_account("Rival One Systems", ACCOUNT, ALIASES))
ask4 = Script('{"need_research": false, "candidates": []}',
              '{"competitors": [{"name": "Amada Miyachi America",'
              ' "competition_type": "DIRECT", "why_it_competes": "x",'
              ' "evidence_ids": [1], "evidence_supports_competition": true,'
              ' "confidence": "high"}]}')
rows4, _s4, cov4 = gd.discover(gd.COMPETITOR, ACCOUNT, ALIASES, ask4,
                               searcher(["https://rival-one.com/about"]), fetch)
check("a group entity is dropped from the published set", rows4 == [])
check("and counted as self", cov4["dropped_self"] == 1)

print("\n[5] Placeholders and headlines are not organisations")
for bad in ("Undisclosed", "Various", "Company Profile", "Terms",
            "Amada Miyachi Europe Acquires MacGregor",
            "https://example.com/page", "Distributor"):
    check("rejected as a name: %s" % bad[:34], not gd.is_named_organization(bad))
for good in ("Rival One Systems", "KEYENCE America", "宁德时代"):
    check("accepted as a name: %s" % good, gd.is_named_organization(good))

print("\n[6] Duplicates and schema")
ask6 = Script('{"need_research": false, "candidates": []}',
              json.dumps({"competitors": [
                  {"name": "Rival One Systems", "competition_type": "DIRECT",
                   "why_it_competes": "a", "evidence_ids": [1],
                   "evidence_supports_competition": True, "confidence": "high"},
                  {"name": "rival one systems", "competition_type": "PARTIAL",
                   "why_it_competes": "b", "evidence_ids": [1],
                   "evidence_supports_competition": True, "confidence": "low"},
                  {"name": "No Type Corp", "evidence_ids": [1],
                   "evidence_supports_competition": True},
                  {"name": "Bad Type Corp", "competition_type": "RIVAL",
                   "evidence_ids": [1], "evidence_supports_competition": True},
                  "not even an object"]}))
rows6, _s6, cov6 = gd.discover(gd.COMPETITOR, ACCOUNT, ALIASES, ask6,
                               searcher(["https://rival-one.com/about"]), fetch)
check("the duplicate is suppressed", len(rows6) == 1, str([r["organization_name"] for r in rows6]))
check("and counted", cov6["dropped_duplicate"] == 1)
check("a missing classification is a schema drop", cov6["dropped_schema"] >= 2,
      str(cov6["dropped_schema"]))
check("an invalid classification is refused", not any(
    r["competition_type"] == "RIVAL" for r in rows6))

print("\n[7] Channel: representation only, partners excluded by the model")
ask7 = Script('{"need_research": false, "candidates": ["Great Lakes Supply"]}',
              '{"go_to_market": "DISTRIBUTOR_LED", "channel": [{"name": "Great Lakes Supply",'
              ' "role": "AUTHORIZED_DISTRIBUTOR", "territory": "Upper Midwest",'
              ' "states": "is the authorized distributor for AMADA WELD TECH",'
              ' "evidence_ids": [1], "evidence_supports_relationship": true,'
              ' "confidence": "high"}]}')
search7 = searcher(["https://dist.example.com/lines", "https://integrator.example.com/about"])
rows7, sources7, cov7 = gd.discover(gd.CHANNEL, ACCOUNT, ALIASES, ask7, search7, fetch)
check("the distributor is published", len(rows7) == 1, str(rows7))
check("its role survives", rows7[0]["role"] == "AUTHORIZED_DISTRIBUTOR")
check("the stated phrase travels with it", "authorized distributor" in rows7[0]["states"])
check("the go-to-market model comes from the evidence pass",
      cov7["go_to_market_model"] == "DISTRIBUTOR_LED")
check("every published row is representation",
      all(r["is_representation"] for r in rows7))
check("the integrator page was offered but not published",
      cov7["sources_offered"] == 2 and len(rows7) == 1,
      "the model excluded it; no regex decided that")
ask7b = Script('{"need_research": true, "candidates": []}',
               '{"go_to_market": "UNKNOWN", "channel": []}')
rows7b, _s, cov7b = gd.discover(gd.CHANNEL, ACCOUNT, ALIASES, ask7b,
                                searcher(["https://integrator.example.com/about"]), fetch)
check("an empty answer publishes nothing", rows7b == [])
check("and leaves the model unknown", cov7b["go_to_market_model"] == "UNKNOWN")

print("\n[7b] Co-mention is not representation either")
CHAN_CASES = (("true as a boolean", True, True), ('"true" as a string', "true", True),
              ("false", False, False), ("the field missing", None, False),
              ('"unclear"', "unclear", False), ('"partial"', "partial", False),
              ('"likely"', "likely", False))
for label, value, published in CHAN_CASES:
    row = {"name": "Lakeside Systems", "role": "DISTRIBUTOR",
           "territory": "Midwest", "states": "appears on the same page",
           "evidence_ids": [1], "confidence": "medium"}
    if value is not None:
        row["evidence_supports_relationship"] = value
    a = Script('{"need_research": false, "candidates": []}',
               json.dumps({"go_to_market": "UNKNOWN", "channel": [row]}))
    r, _s, c = gd.discover(gd.CHANNEL, ACCOUNT, ALIASES, a,
                           searcher(["https://integrator.example.com/about"]), fetch)
    check("channel %s -> %s" % (label, "published" if published else "omitted"),
          bool(r) == published, str([x["organization_name"] for x in r]))
    if not published:
        check("  and counted as unsupported", c["dropped_unsupported"] == 1)
check("a published channel row carries its adjudication",
      rows7[0].get("evidence_supports_relationship") is True)
check("the channel prompt names the roles that are not channel",
      all(w in gd.CHANNEL_VERIFY for w in
          ("technology partner", "system integrator", "service partner",
           "supplier", "customer", "similarly named")))
check("it says co-mention is not enough",
      "Mere co-mention is not enough." in gd.CHANNEL_VERIFY)
check("it demands the exact company",
      "THIS EXACT COMPANY" in gd.CHANNEL_VERIFY)

print("\n[7c] A similarly named company is not the target")
# torus.co is the account; torus-technology.com is a different company whose
# name resembles it. The evidence ties the distributor to the wrong one.
TORUS_PAGES = {
    "https://reseller.example.com/lines": (
        "Vantage Power Systems is the authorized distributor for Torus "
        "Technology GmbH, a German drive manufacturer, across the DACH region."),
}


def torus_fetch(url):
    if url not in TORUS_PAGES:
        raise RuntimeError("unreachable")
    return TORUS_PAGES[url], "mock"


wrong = Script('{"need_research": false, "candidates": ["Vantage Power Systems"]}',
               json.dumps({"go_to_market": "UNKNOWN", "channel": [
                   {"name": "Vantage Power Systems", "role": "AUTHORIZED_DISTRIBUTOR",
                    "territory": "DACH",
                    "states": "authorized distributor for Torus Technology GmbH",
                    "evidence_ids": [1],
                    "evidence_supports_relationship": False,
                    "confidence": "low"}]}))
rows7c, _s7c, cov7c = gd.discover(gd.CHANNEL, "Torus", ("Torus Inc",), wrong,
                                  searcher(["https://reseller.example.com/lines"]),
                                  torus_fetch)
check("a distributor of the similarly named company is omitted", rows7c == [],
      str(rows7c))
check("and counted as unsupported", cov7c["dropped_unsupported"] == 1)
check("the page was still read", cov7c["sources_offered"] == 1,
      "the evidence existed; it just did not establish THIS relationship")
right = Script('{"need_research": false, "candidates": ["Vantage Power Systems"]}',
               json.dumps({"go_to_market": "DISTRIBUTOR_LED", "channel": [
                   {"name": "Vantage Power Systems", "role": "AUTHORIZED_DISTRIBUTOR",
                    "territory": "DACH", "states": "authorized distributor for Torus",
                    "evidence_ids": [1],
                    "evidence_supports_relationship": True,
                    "confidence": "high"}]}))
rows7d, _s7d, _c7d = gd.discover(gd.CHANNEL, "Torus", ("Torus Inc",), right,
                                 searcher(["https://reseller.example.com/lines"]),
                                 torus_fetch)
check("the same page DOES publish when the model ties it to the target",
      len(rows7d) == 1, "the model decides; the guardrail only enforces the verdict")

print("\n[8] When the model still does not know after research")
ask8 = Script('{"need_research": true, "candidates": []}',
              '{"competitors": []}')
rows8, _s8, cov8 = gd.discover(gd.COMPETITOR, ACCOUNT, ALIASES, ask8,
                               searcher(["https://directory.example.com/list"]), fetch)
check("research still ran", cov8["search_count"] > 0)
check("nothing was published", rows8 == [])
check("no skip reason is invented when the search worked",
      cov8["skip_reason"] is None, str(cov8["skip_reason"]))
block = pv.competitor_prompt_block([], cov8)
check("synthesis is told discovery was performed",
      "performed" in block and "NOT PERFORMED" not in block)
check("with the verified-absence wording", pv.NO_COMPETITOR_EN in block)
empty_search = searcher([])
rows8b, _s, cov8b = gd.discover(gd.COMPETITOR, ACCOUNT, ALIASES, ask8, empty_search, fetch)
check("no readable source is a stated reason",
      "no readable sources" in (cov8b["skip_reason"] or ""), str(cov8b["skip_reason"]))
check("and synthesis reads it as not performed",
      "NOT PERFORMED" in pv.competitor_prompt_block([], cov8b))

print("\n[9] Budgets")
many = searcher(["https://rival-one.com/about"])
gd.discover(gd.COMPETITOR, ACCOUNT, ALIASES,
            Script('{"need_research": false, "candidates": ["A","B","C","D","E","F","G"]}',
                   '{"competitors": []}'), many, fetch)
check("searches never exceed the budget", many.seen["n"] <= gd.MAX_SEARCHES,
      str(many.seen["n"]))
check("the competitor budget is 4 searches", gd.MAX_SEARCHES == 4)
check("at most 8 pages are fetched", gd.MAX_FETCHES == 8)
check("at most 2 model calls per kind", gd.MAX_MODEL_CALLS == 2)
check("both kinds together cost 8 searches and 4 model calls",
      gd.MAX_SEARCHES * 2 == 8 and gd.MAX_MODEL_CALLS * 2 == 4,
      "down from 10 searches under the deleted engines")

print("\n[10] The manifest reports the guardrails")
facts = app._competitor_facts(cov6, rows6)
check("published rows are counted", facts["retained_competitors"] == len(rows6))
check("proposed rows are counted too", facts["rows_proposed"] == 5)
check("each guardrail reports separately",
      all(k in facts for k in ("dropped_self", "dropped_placeholder",
                               "dropped_uncited", "dropped_unsupported",
                               "dropped_duplicate", "dropped_schema")))
check("model calls are recorded", facts["model_calls"] == 2)
check("used is derived from a real search", facts["used"] is True)
check("a stage that never searched is unused",
      app._competitor_facts(gd.empty_coverage(gd.COMPETITOR), [])["used"] is False)
hfacts = app._channel_facts(cov7, rows7)
check("channel entities are counted", hfacts["channel_entities"] == 1)
check("channel reports both drop counters separately",
      "dropped_uncited" in hfacts and "dropped_unsupported" in hfacts)
check("authorized is counted", hfacts["authorized"] == 1)
check("the go-to-market model reaches the manifest",
      hfacts["go_to_market_model"] == "DISTRIBUTOR_LED")
whole = app.execution_facts({"competitor_coverage": cov6, "competitors": rows6,
                             "channel_coverage": cov7, "channels": rows7,
                             "evidence": [], "ai_usage": []}, [], None)
check("both blocks sit in the execution manifest",
      "competitor_discovery" in whole and "channel_discovery" in whole)
check("no monetary field was added",
      not any("cost" in k for k in whole["competitor_discovery"]))

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
