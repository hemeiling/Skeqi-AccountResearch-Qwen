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
import genai_discovery as gd                                      # noqa: E402
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

for call in ("gdisc.discover",):
    check("%s runs inside a try" % call, call in guarded)
for call in ("gdisc.discover",):
    owning = [h for b, h in handlers if call in b]
    check("and its handler records a limitation" if owning else
          "and its handler records a limitation (%s)" % call,
          bool(owning) and all("limitation" in h for h in owning))
check("the handler also logs for diagnosis",
      all("_log_stage_failure" in h for b, h in handlers if "gdisc.discover" in b))
# Provider discovery already worked this way; the new stages match it.
check("provider discovery is still guarded the same way",
      "tv.discover_providers" in guarded)
check("no stage re-raises after recording", (lambda: all(
    not any(isinstance(n, ast.Raise) for stmt in h.body for n in ast.walk(stmt))
    for node in ast.walk(fn) if isinstance(node, ast.Try)
    for h in node.handlers))())

print("\n[2] The planning call may fail without ending the stage")
calls = {"ask": 0, "search": 0}


def ask_boom(prompt):
    calls["ask"] += 1
    raise Boom("planning exploded")


def search_ok(query):
    calls["search"] += 1
    return [{"url": "https://rival.example.com/a", "title": "Rival One"}]


def fetch_ok(url):
    return ("Rival One builds automated welding systems and competes with Acme "
            "for automotive customers.", "mock")


rows, sources, cov = gd.discover(gd.COMPETITOR, "Acme", (), ask_boom, search_ok,
                                 fetch_ok)
check("a throwing planning call does not propagate", isinstance(cov, dict))
check("and is recorded as a failure", cov["failed"] is True)
check("generic queries still ran", cov["search_count"] > 0, str(cov["search_count"]))
check("nothing is published without a verification answer", rows == [], str(rows))

print("\n[3] The search may fail without ending the stage")


def ask_plan_only(prompt):
    calls["ask"] += 1
    return '{"need_research": false, "candidates": ["Rival One"]}'


def search_boom(query):
    raise Boom("search exploded")


rows, sources, cov = gd.discover(gd.COMPETITOR, "Acme", (), ask_plan_only,
                                 search_boom, fetch_ok)
check("a throwing search does not propagate", isinstance(cov, dict))
check("it is recorded", cov["failed"] is True)
check("and no source was invented", sources == [] and rows == [])
check("the reason is carried", bool(cov["skip_reason"]), str(cov["skip_reason"]))

print("\n[4] The fetch may fail without ending the stage")


def fetch_boom(url):
    raise Boom("fetch exploded")


rows, sources, cov = gd.discover(gd.COMPETITOR, "Acme", (), ask_plan_only,
                                 search_ok, fetch_boom)
check("a throwing fetch does not propagate", isinstance(cov, dict))
check("pages are still counted as attempted", cov["pages_fetched"] > 0)
check("no unreadable page becomes a source", sources == [])
check("and nothing is published", rows == [])

print("\n[5] The verification call may fail without ending the stage")


def ask_then_boom(prompt):
    calls["ask"] += 1
    if "RESEARCH PLANNING" in prompt:
        return '{"need_research": false, "candidates": ["Rival One"]}'
    raise Boom("verification exploded")


rows, sources, cov = gd.discover(gd.COMPETITOR, "Acme", (), ask_then_boom,
                                 search_ok, fetch_ok)
check("a throwing verification does not propagate", isinstance(cov, dict))
check("it is recorded as a failure", cov["failed"] is True)
check("the fetched sources are still reported", len(sources) >= 1)
check("but nothing is published from them", rows == [])

print("\n[6] Channel behaves identically")
rows, sources, cov = gd.discover(gd.CHANNEL, "Acme", (), ask_boom, search_ok,
                                 fetch_ok)
check("a throwing planning call degrades the channel stage too",
      isinstance(cov, dict) and cov["failed"] is True)
check("and it still searched", cov["search_count"] > 0)
check("an unparseable answer is dropped, not repaired",
      gd.parse_json("this is not json") is None)
check("a truncated answer is dropped too",
      gd.parse_json('{"competitors": [{"name": "Rival') is None)

print("\n[7] The manifest says a stage broke, and does not call it used")
broken = dict(gd.empty_coverage(gd.COMPETITOR), failed=True, search_count=0,
              skip_reason="competitor discovery failed (Boom)")
facts = app._competitor_facts(broken, [])
check("failure is recorded", facts["failed"] is True)
check("a broken stage that never searched is not used", facts["used"] is False)
check("the reason survives", "failed" in (facts["skip_reason"] or ""))
hbroken = dict(gd.empty_coverage(gd.CHANNEL), failed=True, used=True, search_count=2,
               skip_reason="channel discovery failed (Boom)")
hfacts = app._channel_facts(hbroken, [])
check("a stage that searched then broke is still used", hfacts["used"] is True)
check("and still reports the failure", hfacts["failed"] is True)
check("a healthy stage is not marked failed",
      app._competitor_facts(gd.empty_coverage(gd.COMPETITOR), [])["failed"] is False)

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
