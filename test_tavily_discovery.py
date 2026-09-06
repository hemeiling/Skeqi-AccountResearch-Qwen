#!/usr/bin/env python
"""Bounded Tavily provider discovery, and the named-organisation gate.

Two rules these protect:

  Tavily is DISCOVERY. It finds URLs and nothing else. Every candidate re-enters
  the existing pipeline - fetch, identity, collision, provenance, retention - and
  its own ordering is discarded, because ranking is not verification.

  The budget is bounded and measured. Two batches, eight searches, stop on useful
  verified coverage or on zero marginal yield. None of it is a gate: an account
  that yields nothing still produces a report.

No network, no MCP connection, no model call.
  .venv/bin/python test_tavily_discovery.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import research_service as rs                                    # noqa: E402
import tavily_service as tv                                      # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (" - " + detail if detail else ""))


class FakeClient(object):
    """Returns eight results for any query, as the real one does."""
    def __init__(self, fail_after=None):
        self.queries = []
        self.fail_after = fail_after

    def search(self, query, **kw):
        self.queries.append(query)
        if self.fail_after is not None and len(self.queries) > self.fail_after:
            raise RuntimeError("upstream down")
        return [{"title": "t%d" % i, "url": "https://d%d.com/%d" % (len(self.queries), i),
                 "content": "c"} for i in range(8)]


def verifier(per_call):
    calls = {"n": 0}

    def v(cands, key):
        calls["n"] += 1
        out = dict(per_call(calls["n"], key))
        out.setdefault("evidence", [])
        return out
    v.calls = calls
    return v


print("\n[1] The plan is categories, never seeded vendor names")
b1 = tv.plan_intents("Ford Motor Company")
check("batch one is four intents", len(b1) == 4, str(len(b1)))
check("in measured-yield order",
      [k for k, _ in b1] == ["automation", "robotics", "inspection", "mes"],
      str([k for k, _ in b1]))
joined = " ".join(q for _, q in b1 + tv.plan_intents("Ford", batch=2)).lower()
for vendor in ("siemens", "abb", "fanuc", "kuka", "rockwell", "comau", "durr"):
    check("no %s is seeded" % vendor, vendor not in joined)
check("batch two avoids what batch one covered",
      "integrator" not in [k for k, _ in tv.plan_intents("F", covered={"integrator"}, batch=2)])
check("the account name is carried into every query",
      all("Ford" in q for _, q in b1))

print("\n[2] The budget is bounded")
c = FakeClient()
kept, cov = tv.discover_providers(c, "Acme", verifier(
    lambda n, k: {"verified": 0, "organizations": 0}))
check("no verified yield stops after one batch", cov["batches_run"] == 1, str(cov["batches_run"]))
check("four searches, not fourteen", cov["searches_used"] == 4, str(cov["searches_used"]))
check("and it is recorded as marginal yield zero", cov["marginal_yield"] == [0],
      str(cov["marginal_yield"]))

c2 = FakeClient()
kept2, cov2 = tv.discover_providers(c2, "Acme", verifier(
    lambda n, k: {"verified": 1, "organizations": 1 if n == 1 else 0,
                  "confirmed": 1 if n == 1 else 0,
                  "evidence": [{"domain": "one.com"}]}))
check("partial yield runs a second batch", cov2["batches_run"] == 2, str(cov2["batches_run"]))
check("and stops at the ceiling of eight",
      cov2["searches_used"] == tv.MAX_PROVIDER_SEARCHES, str(cov2["searches_used"]))
check("the ceiling is flagged", cov2["ceiling_reached"] is True)

c3 = FakeClient()
kept3, cov3 = tv.discover_providers(c3, "Acme", verifier(
    lambda n, k: {"verified": 2, "organizations": 1, "confirmed": 1,
                  "evidence": [{"domain": "d%d.com" % n}]}))
check("useful coverage stops early", cov3["batches_run"] == 1, str(cov3["batches_run"]))
check("coverage is judged useful", tv.coverage_is_useful(cov3))
check("across distinct categories", len(cov3["categories_covered"]) >= 2,
      str(cov3["categories_covered"]))
check("and distinct domains", cov3["source_domains"] >= 2, str(cov3["source_domains"]))

print("\n[3] Stop conditions are about verified breadth, not raw results")
narrow = {"confirmed": 5, "strong_indication": 0, "categories_covered": ["automation"],
          "source_domains": 1}
check("five organisations on ONE domain in ONE category is not coverage",
      not tv.coverage_is_useful(narrow))
broad = {"confirmed": 1, "strong_indication": 1, "categories_covered": ["a", "b"],
         "source_domains": 2}
check("two across two categories and two domains is", tv.coverage_is_useful(broad))

print("\n[4] Discovery never becomes a gate")
c4 = FakeClient(fail_after=1)
kept4, cov4 = tv.discover_providers(c4, "Acme", verifier(lambda n, k: {"verified": 0}))
check("an upstream failure does not raise", isinstance(cov4, dict))
check("it records what did run", cov4["searches_used"] == 1, str(cov4["searches_used"]))
check("and returns evidence rather than nothing", isinstance(kept4, list))

print("\n[5] The named-organisation gate")
for phrase in ("MES provider", "Systems Integrators", "Warehouse Management System",
               "通用焊接/MES厂商", "生态伙伴", "Tier-1 supplier", "local integrator",
               "unknown supplier", "internal engineering team", "内部工程团队",
               "Manufacturing Execution System", "suppliers", "automation"):
    check("category rejected: %s" % phrase, not rs.is_named_organization(phrase))
for org in ("ABB Robotics", "ABB", "IBM", "FANUC", "Silk EV", "Metro Group",
            "Jilin Landi Automation Engineering", "比亚迪", "宁德时代"):
    check("organisation accepted: %s" % org, rs.is_named_organization(org))
check("an empty candidate is rejected", not rs.is_named_organization(""))
check("a very long string is rejected", not rs.is_named_organization("x" * 80))

print("\n[6] Relationship strength and provenance are SEPARATE dimensions")
acct = "Ford Motor Company"
# Provenance says how the SOURCE relates to the account. The class says what the
# source ASSERTS. Capping the class by provenance discarded the clearest vendor
# evidence in the corpus, so the two are kept independent.
check("an explicit account link is CONFIRMED",
      rs.classify_relationship(
          "ABB Robotics has been named a 2026 Ford Supplier of the Year.",
          acct, [], rs.PROV_TARGET) == rs.REL_CONFIRMED)
check("a VENDOR page can also be CONFIRMED",
      rs.classify_relationship(
          "ABB will provide robots for Changan Ford's body-in-white welding line.",
          acct, [], rs.PROV_ECOSYSTEM) == rs.REL_CONFIRMED,
      "ecosystem provenance must not cap the class")
check("so can a first-party history",
      rs.classify_relationship(
          "ABB has worked with Changan Ford since 2007, producing Ford Mondeos.",
          acct, [], rs.PROV_ECOSYSTEM) == rs.REL_CONFIRMED)
check("the class is identical whatever the provenance, given the same text",
      len({rs.classify_relationship(
          "ABB will provide robots for Ford's welding line.", acct, [], p)
          for p in (rs.PROV_TARGET, rs.PROV_ECOSYSTEM, rs.PROV_MARKET, None)}) == 1,
      "provenance is recorded beside the class, never inside it")

check("a job listing is only STRONG_INDICATION",
      rs.classify_relationship(
          "Apply directly to this Fanuc Ford contract job listing.",
          acct, [], rs.PROV_TARGET) == rs.REL_STRONG)
check("a hedged plan is only STRONG_INDICATION",
      rs.classify_relationship("Ford reportedly plans to select a new MES supplier.",
                               acct, [], rs.PROV_TARGET) == rs.REL_STRONG)

check("no account mention is MARKET_ONLY",
      rs.classify_relationship(
          "Jilin Landi Automation delivered a body shop conveyor for JAC.",
          acct, [], rs.PROV_TARGET) == rs.REL_MARKET)
check("naming the account without asserting anything is MARKET_ONLY",
      rs.classify_relationship("Ford was mentioned in our automation market survey.",
                               acct, [], rs.PROV_TARGET) == rs.REL_MARKET,
      "an industry survey names everybody")
check("an alias counts as the account being named",
      rs.classify_relationship("ABB supplies Hongqi's welding line.", "\u7ea2\u65d7",
                               ["hongqi"], rs.PROV_TARGET) == rs.REL_CONFIRMED)
check("verb stems match real prose, not only exact forms",
      all(rs.classify_relationship("ABB %s equipment to Ford." % v, acct, [],
                                   rs.PROV_TARGET) == rs.REL_CONFIRMED
          for v in ("will provide", "provides", "provided", "supplies", "delivered")),
      "'will provide' was missed by an exact-form list")

print("\n[7] Parsing tolerates what the wrapper actually returns")
check("no relevance score is invented",
      all("score" not in r for r in tv.parse_results(
          "Title: A\nURL: https://a.com\nContent: c")))
check("empty text yields nothing", tv.parse_results("") == [])
check("None yields nothing", tv.parse_results(None) == [])
check("non-http urls are dropped",
      tv.parse_results("Title: A\nURL: javascript:alert(1)\nContent: c") == [])

print("\n{} passed, {} failed".format(len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
