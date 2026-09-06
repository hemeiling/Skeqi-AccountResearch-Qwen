#!/usr/bin/env python
"""Who competes with the TARGET, and the two rules that keep it honest.

Search concepts are RANKED, not enumerated: ACRO's profile yields fourteen
capabilities, and templating all of them spends the budget on "assembly" and
"automation" - words every automation company matches, which return the same
large vendors for every account.

Competition is scored on three INDEPENDENT dimensions before any label exists,
so "operates in the same industry" can never by itself produce a competitor.
That is the false positive this module is built to refuse.

No network, no model call.  .venv/bin/python test_competitor_discovery.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import competitor_discovery as cd                                # noqa: E402
import research_service as rs                                    # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (" - " + detail if detail else ""))


PROFILE = {
    "offerings": ["automated welding solutions", "custom automated equipment"],
    "capabilities": ["welding", "machine vision", "assembly", "automation",
                     "laser processing", "material handling"],
    "project_types": ["custom/engineered systems"],
    "industries": ["automotive", "medical device"],
    "geography": ["Milwaukee"],
    "business_model": "equipment_supplier_or_integrator",
    "competitor_discovery_ready": True,
    "competitor_skip_reason": None,
    "confidence": {"capabilities": "high", "offerings": "high"},
    "supporting_evidence": {"offerings": {"automated welding solutions": ["u1", "u2"]},
                            "capabilities": {"machine vision": ["u1", "u2", "u3"]}},
}

print("\n[1] Generic words are never a search subject")
for g in ("assembly", "automation", "manufacturing", "engineering", "systems",
          "solutions", "equipment", "custom"):
    check("%r has zero specificity" % g, cd._specificity(g) == 0)
plan = cd.rank_concepts(PROFILE)
picked = [c["concept"] for c in plan["concepts"]]
for g in ("assembly", "automation"):
    check("%r is not searched" % g, g not in picked, str(picked))
check("and it is reported as rejected", "assembly" in plan["rejected_generic"],
      str(plan["rejected_generic"]))
check("a purely generic offering is dropped too",
      "custom automated equipment" not in picked, str(picked))

print("\n[2] Offerings outrank capabilities")
check("the first concept is an offering", plan["concepts"][0]["kind"] == "offerings",
      plan["concepts"][0]["kind"])
check("and it is the real product line",
      plan["concepts"][0]["concept"] == "automated welding solutions",
      plan["concepts"][0]["concept"])
kinds = [c["kind"] for c in plan["concepts"]]
check("no capability precedes an offering",
      kinds.index("offerings") < (kinds.index("capabilities")
                                  if "capabilities" in kinds else 99))
check("only a few concepts are used", len(plan["concepts"]) <= 4, str(len(plan["concepts"])))
check("industries and geography are QUALIFIERS, not subjects",
      "automotive" not in picked and "Milwaukee" not in picked)

print("\n[3] Queries carry a qualifier and never the account name")
qs = [q for _, q in cd.plan_intents(PROFILE, batch=1)]
check("batch one issues three", len(qs) == 3, str(len(qs)))
check("each query is qualified", all("automotive" in q for q in qs), str(qs[:1]))
check("the account name is absent", all("ACRO" not in q for q in qs),
      "a competitor's page never mentions the account")
check("batch two is different from batch one",
      not set(q for _, q in cd.plan_intents(PROFILE, batch=2)) & set(qs))

print("\n[4] The three dimensions are independent")
SAME_INDUSTRY_ONLY = "We are a large supplier to the automotive industry worldwide."
d = cd.score_overlap(SAME_INDUSTRY_ONLY, PROFILE, "ACRO Automation Systems")
check("industry alone gives industry overlap", d["customer_or_industry_overlap"])
check("but no offering overlap", not d["offering_overlap"])
check("and therefore NOT a competitor", cd.classify(d) is cd.NOT_A_COMPETITOR,
      "sharing a market is not competing for the same work")

ALL_THREE = ("Our automated welding solutions serve automotive manufacturers "
             "across Milwaukee and the Midwest.")
d3 = cd.score_overlap(ALL_THREE, PROFILE, "ACRO Automation Systems")
check("all three dimensions hold", d3["offering_overlap"]
      and d3["customer_or_industry_overlap"] and d3["geographic_or_market_overlap"])
check("-> DIRECT", cd.classify(d3) == cd.DIRECT)

TWO = "Our automated welding solutions serve automotive manufacturers nationwide."
check("offering + industry -> PARTIAL",
      cd.classify(cd.score_overlap(TWO, PROFILE, "ACRO")) == cd.PARTIAL)
ONE = "We build automated welding solutions for a range of clients."
check("offering only -> ADJACENT",
      cd.classify(cd.score_overlap(ONE, PROFILE, "ACRO")) == cd.ADJACENT)
check("geography alone is not a competitor",
      cd.classify(cd.score_overlap("A company based in Milwaukee.", PROFILE, "ACRO"))
      is cd.NOT_A_COMPETITOR)
check("an empty page is not a competitor",
      cd.classify(cd.score_overlap("", PROFILE, "ACRO")) is cd.NOT_A_COMPETITOR)

print("\n[5] Competitor classes are their own vocabulary")
check("DIRECT/PARTIAL/ADJACENT are distinct from the provider classes",
      {cd.DIRECT, cd.PARTIAL, cd.ADJACENT}.isdisjoint(
          {rs.REL_CONFIRMED, rs.REL_STRONG, rs.REL_MARKET}),
      "MARKET_ONLY is a provider relationship, not a competitor class")

print("\n[6] Placeholders are not organisations")
for p in ("Unidentified Vision", "Unnamed supplier", "Unspecified integrator",
          "Undisclosed Tier-1s", "Confidential customer", "Anonymous partner"):
    check("rejected: %s" % p, not rs.is_named_organization(p))
for o in ("ABB Robotics", "Cognex", "Keyence", "比亚迪", "Dürr Systems"):
    check("accepted: %s" % o, rs.is_named_organization(o))


class FakeClient(object):
    def __init__(self, fail=False):
        self.queries = []
        self.fail = fail

    def search(self, q, **kw):
        self.queries.append(q)
        if self.fail:
            raise RuntimeError("down")
        return [{"title": "t", "url": "https://c%d.com/%d" % (len(self.queries), i),
                 "content": "c"} for i in range(8)]


def verifier(per_call):
    n = {"i": 0}

    def v(cands, key):
        n["i"] += 1
        out = dict(per_call(n["i"]))
        out.setdefault("evidence", [])
        return out
    return v


print("\n[7] The budget is bounded and skips a weak profile")
c = FakeClient()
kept, cov = cd.discover(c, PROFILE, verifier(lambda i: {"verified": 0, "competitors": 0}))
check("no yield stops after one batch", cov["batches"] == 1, str(cov["batches"]))
check("three searches, not fourteen", cov["search_count"] == 3, str(cov["search_count"]))

c2 = FakeClient()
# Genuinely PARTIAL: one competitor found, none of them DIRECT, so coverage is
# never satisfied and the second batch has to run. The earlier fixture handed
# back a direct hit on the first call and stopped immediately - it was testing
# the early-stop path while claiming to test the ceiling.
kept2, cov2 = cd.discover(c2, PROFILE, verifier(
    lambda i: {"verified": 2, "competitors": 1 if i == 1 else 0, "direct": 0,
               "evidence": [{"domain": "d%d.com" % i}]}))
check("partial yield runs batch two", cov2["batches"] == 2, str(cov2["batches"]))
# The ceiling is a CAP, not a target. This profile supports only five searchable
# concepts once generic words are rejected, so five searches is the correct
# answer - padding to six would mean inventing a concept to spend a budget.
check("it never exceeds the ceiling",
      cov2["search_count"] <= cd.MAX_COMPETITOR_SEARCHES, str(cov2["search_count"]))
check("and uses every concept the profile actually supports",
      cov2["search_count"] == len(cd.rank_concepts(PROFILE, limit=cd.MAX_COMPETITOR_SEARCHES)["concepts"]),
      "%d searches for %d concepts" % (
          cov2["search_count"],
          len(cd.rank_concepts(PROFILE, limit=cd.MAX_COMPETITOR_SEARCHES)["concepts"])))
RICH = dict(PROFILE, capabilities=PROFILE["capabilities"] + [
    "x-ray inspection", "leak testing", "torque fastening", "pick and place"])
c2b = FakeClient()
_, cov2b = cd.discover(c2b, RICH, verifier(
    lambda i: {"verified": 1, "competitors": 1 if i == 1 else 0, "direct": 0,
               "evidence": [{"domain": "d%d.com" % i}]}))
check("a richer profile is still capped at six",
      cov2b["search_count"] == cd.MAX_COMPETITOR_SEARCHES, str(cov2b["search_count"]))

c3 = FakeClient()
kept3, cov3 = cd.discover(c3, PROFILE, verifier(
    lambda i: {"verified": 2, "competitors": 1, "direct": 1,
               "evidence": [{"domain": "d%d.com" % i}]}))
check("useful coverage stops early", cov3["batches"] == 1, str(cov3["batches"]))
check("coverage needs a DIRECT competitor",
      not cd.coverage_is_useful({"contribution_count": 5, "direct": 0, "distinct_domains": 3}))
check("and more than one domain",
      not cd.coverage_is_useful({"contribution_count": 5, "direct": 2, "distinct_domains": 1}))

NOT_READY = dict(PROFILE, competitor_discovery_ready=False,
                 competitor_skip_reason="insufficient profile evidence: capabilities")
c4 = FakeClient()
kept4, cov4 = cd.discover(c4, NOT_READY, verifier(lambda i: {}))
check("a profile that is not ready spends NOTHING", cov4["search_count"] == 0)
check("used stays false", cov4["used"] is False)
check("and the reason is carried", "capabilities" in (cov4["skip_reason"] or ""),
      str(cov4["skip_reason"]))
check("no searches were issued", not c4.queries)

print("\n[8] Failure degrades, never terminates")
c5 = FakeClient(fail=True)
kept5, cov5 = cd.discover(c5, PROFILE, verifier(lambda i: {}))
check("an upstream failure does not raise", isinstance(cov5, dict))
check("and returns a list", isinstance(kept5, list))
check("profile confidence is recorded either way", cov5["profile_confidence"] is not None)

print("\n{} passed, {} failed".format(len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
