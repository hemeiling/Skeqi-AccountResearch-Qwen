# -*- coding: utf-8 -*-
"""Every optional stage may fail. None of them may end the research run.

The governing rule is the project's oldest one: partial evidence beats no
report. Provider discovery has always honoured it. The offering profile,
competitor discovery and channel discovery are enhancements on top of evidence
that already exists, so a defect in any of them must cost their contribution and
nothing else.

Two levels are checked. Structurally, each stage call sits inside a try whose
handler records a limitation, which is what actually keeps the run alive.
Dynamically, the discovery loops survive a throwing planner, a throwing search
and a throwing verifier, and still return what they had already found.

No network, no model call.  .venv/bin/python test_failure_isolation.py
"""
import ast
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import app                                                        # noqa: E402
import channel_discovery as ch                                    # noqa: E402
import competitor_discovery as cd                                 # noqa: E402
import offering_profile as op                                     # noqa: E402
import research_service as rs                                     # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (" - " + detail if detail else ""))


class Boom(Exception):
    pass


PROFILE = {
    "offerings": ["custom automated assembly systems"],
    "capabilities": ["assembly", "welding", "machine vision"],
    "industries": ["automotive"], "geography": ["Milwaukee"],
    "project_types": ["custom/engineered systems"],
    "business_model": "equipment_supplier_or_integrator",
    "go_to_market_model": "UNKNOWN", "go_to_market_confidence": "none",
    "competitor_discovery_ready": True, "competitor_skip_reason": None,
    "channel_discovery_ready": True, "channel_skip_reason": None,
    "confidence": {"capabilities": "high"},
}


class Client(object):
    def __init__(self, results=(), throw=False):
        self.results, self.throw, self.queries = list(results), throw, []

    def search(self, query):
        self.queries.append(query)
        if self.throw:
            raise Boom("search exploded")
        return list(self.results)


HIT = [{"url": "https://x.com/a", "title": "Rival One", "domain": "x.com"}]


def throwing(*_a, **_k):
    raise Boom("stage exploded")


print("\n[1] The pipeline wraps every optional stage")
SRC = io.open(os.path.join(HERE, "research_service.py"), encoding="utf-8").read()
tree = ast.parse(SRC)
fn = next(n for n in ast.walk(tree)
          if isinstance(n, ast.FunctionDef) and n.name == "build_shared_evidence")


def _calls(node):
    """Every dotted call name reachable from this node."""
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and isinstance(n.func.value, ast.Name):
            out.add(n.func.value.id + "." + n.func.attr)
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            out.add(n.func.id)
    return out


guarded, handlers = set(), []
for node in ast.walk(fn):
    if isinstance(node, ast.Try):
        body = set()
        for stmt in node.body:
            body |= _calls(stmt)
        guarded |= body
        handled = set()
        for h in node.handlers:
            for stmt in h.body:
                handled |= _calls(stmt)
        handlers.append((body, handled))

for call in ("op.build_profile", "cdisc.discover", "chdisc.discover",
             "competitor_verifier", "channel_verifier"):
    check("%s runs inside a try" % call, call in guarded)
for call in ("op.build_profile", "cdisc.discover", "chdisc.discover"):
    owning = [h for b, h in handlers if call in b]
    check("and its handler records a limitation" if owning else
          "and its handler records a limitation (%s)" % call,
          bool(owning) and all("limitation" in h for h in owning))
check("the handler also logs for diagnosis",
      all("_log_stage_failure" in h for b, h in handlers
          if "op.build_profile" in b or "cdisc.discover" in b or "chdisc.discover" in b))
# Provider discovery already worked this way; the new stages match it.
check("provider discovery is still guarded the same way",
      "tv.discover_providers" in guarded)
check("no stage re-raises after recording", (lambda: all(
    not any(isinstance(n, ast.Raise) for stmt in h.body for n in ast.walk(stmt))
    for node in ast.walk(fn) if isinstance(node, ast.Try)
    for h in node.handlers))())

print("\n[2] A profile that cannot be built stands down, it does not raise")
prof = op.unavailable_profile("Acme", "account profile could not be built (Boom)")
check("it is shaped like a profile",
      set(prof) >= {"capabilities", "industries", "business_model",
                    "go_to_market_model", "confidence"})
check("it is ready for neither path",
      not prof["competitor_discovery_ready"] and not prof["channel_discovery_ready"])
check("both reasons name the failure",
      "could not be built" in prof["competitor_skip_reason"]
      and "could not be built" in prof["channel_skip_reason"])
check("it is marked unavailable", prof.get("unavailable") is True)
check("discovery run on it issues no search", (lambda: (
    lambda c: (cd.discover(c, prof, throwing), c.queries == [])[1])(Client(HIT)))())

print("\n[3] Competitor discovery survives every throw point")
c = Client(HIT, throw=True)
ev, cov = cd.discover(c, PROFILE, throwing)
check("a throwing search does not propagate", cov["search_count"] == 0 and ev == [])
check("and the run keeps its coverage record", cov["used"] is False)

c = Client(HIT)
ev, cov = cd.discover(c, PROFILE, throwing)
check("a throwing verifier does not propagate", isinstance(cov, dict))
check("the searches that ran are still counted", cov["search_count"] >= 1)
check("and the failure is recorded", cov["failed"] is True)
check("nothing unverified leaks into evidence", ev == [])

_real_plan = cd.plan_intents
cd.plan_intents = throwing
try:
    c = Client(HIT)
    ev, cov = cd.discover(c, PROFILE, lambda *_a, **_k: {})
    check("a throwing planner does not propagate", isinstance(cov, dict))
    check("and is recorded as a failure", cov["failed"] is True)
    check("no query was invented", c.queries == [])
finally:
    cd.plan_intents = _real_plan

print("\n[4] Channel discovery survives every throw point")
c = Client(HIT, throw=True)
ev, cov = ch.discover(c, PROFILE, "Acme", throwing)
check("a throwing search does not propagate", cov["search_count"] == 0 and ev == [])
c = Client(HIT)
ev, cov = ch.discover(c, PROFILE, "Acme", throwing)
check("a throwing verifier does not propagate", isinstance(cov, dict))
check("the failure is recorded", cov["failed"] is True)
check("the searches that ran are still counted", cov["search_count"] >= 1)
_real_plan = ch.plan_intents
ch.plan_intents = throwing
try:
    c = Client(HIT)
    ev, cov = ch.discover(c, PROFILE, "Acme", lambda *_a, **_k: {})
    check("a throwing planner does not propagate", isinstance(cov, dict))
    check("and is recorded as a failure", cov["failed"] is True)
finally:
    ch.plan_intents = _real_plan

print("\n[5] Partial results already found are kept")
sink = []


def half(candidates, key):
    """Verifies the first batch, then breaks."""
    if sink:
        raise Boom("second batch exploded")
    sink.append({"organization_name": "Rival One", "organization_key": "rivalone",
                 "competition_type": cd.DIRECT})
    return {"verified": 1, "competitors": 1, "direct": 1, "evidence": []}


c = Client(HIT)
ev, cov = cd.discover(c, PROFILE, half)
check("the first batch's competitor survives the second batch's crash",
      len(sink) == 1, str(sink))
check("and the coverage still reports what worked", cov["contribution_count"] >= 1)

print("\n[6] A page fetch that explodes costs one candidate, not the batch")
_real_fetch = rs.fetch_page_text


def exploding_fetch(url, *a, **k):
    raise Boom("fetch exploded")


rs.fetch_page_text = exploding_fetch
try:
    csink = []
    v = rs.competitor_verifier(PROFILE, "Acme", "", (), csink)
    out = v(HIT, "assembly")
    check("competitor verification returns rather than raises", isinstance(out, dict))
    check("and yields nothing from an unreadable page",
          out["competitors"] == 0 and not csink)
    hsink = []
    hv = rs.channel_verifier(PROFILE, "Acme", "", (), hsink)
    hout = hv(HIT, "distributor")
    check("channel verification returns rather than raises", isinstance(hout, dict))
    check("and yields nothing from an unreadable page",
          hout["channel_entities"] == 0 and not hsink)
finally:
    rs.fetch_page_text = _real_fetch

print("\n[7] The manifest says a stage broke, and does not call it used")
broken = dict(cd.empty_coverage(), failed=True, search_count=0,
              skip_reason="competitor discovery failed (Boom)")
facts = app._competitor_facts(broken, [], PROFILE)
check("failure is recorded", facts["failed"] is True)
check("a broken stage that never searched is not used", facts["used"] is False)
check("the reason survives", "failed" in (facts["skip_reason"] or ""))
hbroken = dict(ch.empty_coverage(), failed=True, used=True, search_count=2,
               skip_reason="channel discovery failed (Boom)")
hfacts = app._channel_facts(hbroken, [], PROFILE)
check("a stage that searched then broke is still used", hfacts["used"] is True)
check("and still reports the failure", hfacts["failed"] is True)
check("a healthy stage is not marked failed",
      app._competitor_facts(cd.empty_coverage(), [], PROFILE)["failed"] is False)

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
