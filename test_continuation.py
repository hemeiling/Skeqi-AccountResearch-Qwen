#!/usr/bin/env python
"""Regression suite for the BEST-EFFORT CONTINUATION invariant (see CLAUDE.md).

    A backend-stage failure degrades the report. It does NOT terminate the
    research session. partial evidence > no report.

Every test here corresponds to a branch that USED to end the run. If one of these
fails, fail-fast behaviour has been reintroduced.

No network, no model call, no cost. Run:  .venv/bin/python test_continuation.py
"""
import io
import os
import re
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import research_service as rs                                    # noqa: E402
import people_service as people                                  # noqa: E402
import apollo_service as apollo                                  # noqa: E402
import finance_service as fin                                    # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  {} {}{}".format("PASS" if cond else "FAIL", name,
                             "" if cond or not detail else "  <- " + detail))


class patch:
    """Minimal monkeypatch context manager; the venv has no pytest."""

    def __init__(self, **kw):
        self.kw, self.old = kw, {}

    def __enter__(self):
        for dotted, val in self.kw.items():
            mod, attr = dotted.rsplit("__", 1)
            target = {"rs": rs, "people": people, "apollo": apollo, "fin": fin}[mod]
            self.old[dotted] = getattr(target, attr)
            setattr(target, attr, val)
        return self

    def __exit__(self, *a):
        for dotted, val in self.old.items():
            mod, attr = dotted.rsplit("__", 1)
            setattr({"rs": rs, "people": people, "apollo": apollo, "fin": fin}[mod], attr, val)


CFG = {"DASHSCOPE_API_KEY": "test", "DASHSCOPE_BASE_URL": "https://example.invalid",
       "DASHSCOPE_PATH_TEXT": "/t", "DASHSCOPE_PATH_MULTIMODAL": "/m",
       "AI_REQUEST_TIMEOUT_MS": "1000", "AI_MODEL_CITATION": "qwen3.6-flash"}

PRIVATE = {"public_company": False, "listing_status": "private", "ticker": "",
           "listed_name": "", "resolution_method": "none", "notes": ""}


def run_pipeline(**over):
    """build_shared_evidence with every network seam stubbed."""
    site = over.pop("resolve", {"website": "", "status": "unverified", "score": 0,
                                "signals": [], "text": "", "blocked": False})
    stubs = dict(
        rs__resolve_website=lambda *a, **k: site,
        rs__crawl_official_site=over.pop("crawl", lambda *a, **k: []),
        rs__search_all=over.pop("search_all", None),
        rs__fetch_page_text=over.pop("fetch", lambda u, **k: ("", "blocked")),
        rs__build_evidence=over.pop("build_evidence", lambda *a, **k: []),
        rs__yahoo_finance_evidence=over.pop("yahoo", lambda l: None),
        fin__resolve_listing=over.pop("listing", lambda *a, **k: dict(PRIVATE)),
        apollo__configured=over.pop("apollo_configured", lambda cfg: False),
    )
    stubs = {k: v for k, v in stubs.items() if v is not None}
    stubs.update(over)
    with patch(**stubs):
        return rs.build_shared_evidence("Testco", "", CFG, lambda *a, **k: None)


print("\n[A] Converted terminal branches - retrieval must never raise\n")

# 3-6: every empty-evidence branch. Each used to raise RetrievalError.
for label, resolve, blocked in [
    ("no_company_match  (unverified site, no candidates)",
     {"website": "", "status": "unverified", "score": 0, "signals": [], "text": ""}, False),
    ("site_unverified   (unverified site)",
     {"website": "", "status": "unverified", "score": 0, "signals": [], "text": ""}, False),
    ("site_blocked      (validated on domain, no text)",
     {"website": "https://t.example", "status": "provided", "score": 5,
      "signals": [], "text": "", "blocked": True}, True),
    ("insufficient      (site read, nothing else)",
     {"website": "https://t.example", "status": "provided", "score": 5,
      "signals": [], "text": "x" * 500, "blocked": False}, False),
]:
    try:
        pkg = run_pipeline(resolve=resolve)
        check("continues: " + label, True)
        if blocked:
            check("  site_blocked recorded as a state", pkg.get("site_blocked") is True)
    except rs.RetrievalError as e:
        check("continues: " + label, False, "raised RetrievalError({})".format(e.reason))
    except Exception as e:
        check("continues: " + label, False, "{}: {}".format(type(e).__name__, e))

# Zero evidence must switch modes, not stop.
pkg = run_pipeline()
check("zero evidence -> zero_grounding flag", pkg.get("zero_grounding") is True)
check("zero evidence -> a limitation is recorded", len(pkg.get("limitations") or []) > 0)
check("zero evidence -> package still returned", "evidence" in pkg)
check("zero evidence -> quality.degraded", bool((pkg.get("quality") or {}).get("degraded")))

# 7/8: contact stages are enrichment, never dependencies.
def boom(*a, **k):
    raise RuntimeError("stage exploded")

try:
    pkg = run_pipeline(people__from_crm=boom)
    check("continues: CRM contact lookup raises", True)
    check("  CRM failure recorded as a limitation",
          any(l["stage"] == "contacts" for l in pkg.get("limitations") or []))
except Exception as e:
    check("continues: CRM contact lookup raises", False, repr(e))

try:
    pkg = run_pipeline(apollo_configured=lambda cfg: True, apollo__search_people=boom)
    check("continues: Apollo raises", True)
    check("  Apollo failure recorded as a limitation",
          any(l["stage"] == "apollo" for l in pkg.get("limitations") or []))
except Exception as e:
    check("continues: Apollo raises", False, repr(e))

print("\n[B] Static guard - the branches must stay converted\n")

SRC = io.open("research_service.py", encoding="utf-8").read()
body = SRC[SRC.index("def build_shared_evidence"):]
check("no `raise RetrievalError` remains in the pipeline",
      "raise RetrievalError" not in body,
      "found {}".format(body.count("raise RetrievalError")))
check("no generation-blocking gate in app.py",
      'quality.get("blocking") and not force' not in
      io.open("app.py", encoding="utf-8").read())

print("\n[C] Zero-grounding mode - continuation is unconditional, invention is not\n")

sent = {}


def fake_post(url, payload, key, timeout):
    sent["prompt"] = payload["input"]["messages"][0]["content"]
    return 200, {"output": {"choices": [{"message": {"content": "ok"}}]}, "usage": {}}


with patch(rs__post_json=fake_post):
    rs.synthesize("qwen3.6-flash", "Testco", "", [], CFG)
    empty = sent["prompt"]
    empty = empty if isinstance(empty, str) else empty[0]["text"]
    rs.synthesize("qwen3.6-flash", "Testco", "", [
        {"id": 1, "title": "t", "url": "https://x.example", "domain": "x.example",
         "tier": 4, "source_type": "news-article", "official": False,
         "retrieval_method": "fetch", "topics": [], "category": "news", "text": "y" * 200}],
        CFG)
    grounded = sent["prompt"]
    grounded = grounded if isinstance(grounded, str) else grounded[0]["text"]

check("zero evidence -> synthesis is told it has no grounding",
      "NO VERIFIED EVIDENCE" in empty)
check("zero evidence -> the required refusal wording is given",
      "Insufficient verified public evidence" in empty and "缺乏足够的已验证公开信息" in empty)
check("evidence present -> notice absent", "NO VERIFIED EVIDENCE" not in grounded)

print("\n[D] Retention policy - verified third-party survives its tier\n")


def ev(url, tier, verified, official=False):
    return {"url": url, "tier": tier, "source_type": "other-web-source",
            "official": official, "content_verified": verified, "topics": [], "title": url}


strong = [ev("https://own.example/p%d" % i, 1, False, True) for i in range(5)]
kept, _ = rs.apply_evidence_caps(
    strong,
    [ev("https://fin%d.example/a" % i, 6, True) for i in range(4)]
    + [ev("https://spam%d.example/a" % i, 6, False) for i in range(4)])
check("verified low-tier third-party is kept",
      sum(1 for e in kept if e["tier"] >= 6 and e.get("content_verified")) == 4)
check("UNverified low-tier is still capped",
      sum(1 for e in kept if e["tier"] >= 6 and not e.get("content_verified")) == 2)
kept2, _ = rs.apply_evidence_caps(strong, [ev("https://o%d.example/a" % i, 6, True, True)
                                           for i in range(4)])
check("official low-tier is NOT exempt", sum(1 for e in kept2 if e["tier"] >= 6) == 2)
kept3, drop3 = rs.apply_evidence_caps([], [ev("https://h%d.example/a" % i, 6, True)
                                           for i in range(30)])
check("total evidence cap still absolute", len(kept3) == rs.MAX_EVIDENCE_ITEMS)

print("\n[E] Terminal states - only synthesis failure is fatal\n")

import app                                                        # noqa: E402


def run_worker(models_result, quality):
    app.JOBS.clear()
    jid = "t"
    app.JOBS[jid] = {"status": "running", "phase": "retrieval", "message": "",
                     "company": "Testco", "website": "", "stages": [],
                     "search_queries": [], "sources": [], "evidence_cached": False,
                     "retrieval_timings": {}, "financial_sources": {}, "apollo_usage": {},
                     "quality": {}, "callback_url": "",
                     "models": {"m": {"model": "m", "label": "M", "status": "pending",
                                      "elapsed": None, "result": None, "error": None,
                                      "token_usage": None, "started_at": None}},
                     "model_order": ["m"]}
    pkg = {"sources": [], "search_queries": [], "evidence": [], "quality": quality,
           "timings": {}, "apollo": {"usage": {}, "people": []}, "financial_sources": {}}
    old = (rs.load_config, rs.build_shared_evidence, rs.synthesize_with_fallback,
           app.notify_crm, app.save_run, rs.MODEL_LABELS)
    rs.load_config = lambda: CFG
    rs.build_shared_evidence = lambda *a, **k: pkg
    rs.synthesize_with_fallback = lambda *a, **k: models_result
    app.notify_crm = lambda *a, **k: (True, "")
    app.save_run = lambda p, r: {"id": 1}
    rs.MODEL_LABELS = dict(rs.MODEL_LABELS, m="M")
    try:
        app.worker(jid, "Testco", "", ["m"], False)
        return dict(app.JOBS[jid])
    finally:
        (rs.load_config, rs.build_shared_evidence, rs.synthesize_with_fallback,
         app.notify_crm, app.save_run, rs.MODEL_LABELS) = old


OK_RUN = {"model": "m", "model_label": "M", "status": 200, "report": "text",
          "endpoint": "e", "protocol": "p", "input_tokens": 1, "output_tokens": 1,
          "total_tokens": 2, "latency_seconds": 1.0, "decision_makers": [],
          "people_summary": {}, "error": None}
DEAD_RUN = dict(OK_RUN, status=0, report="", error="boom")

j = run_worker(OK_RUN, {"level": "strong", "blocking": False, "degraded": False,
                        "zero_grounding": False, "limitations": []})
check("clean run -> done / completed", j["status"] == "done"
      and j.get("outcome") == "completed", str(j.get("outcome")))

j = run_worker(OK_RUN, {"level": "insufficient", "blocking": True, "degraded": True,
                        "zero_grounding": True,
                        "limitations": [{"stage": "evidence", "status": "unavailable",
                                         "message": "none"}]})
check("degraded run -> completed_with_limitations",
      j["status"] == "done" and j.get("outcome") == "completed_with_limitations",
      str(j.get("outcome")))
check("  a blocking quality verdict no longer stops generation",
      j["models"]["m"]["status"] == "complete")
check("  needs_review is not used for degradation", j["status"] != "needs_review")

j = run_worker(DEAD_RUN, {"level": "strong", "blocking": False, "degraded": False,
                          "zero_grounding": False, "limitations": []})
check("all models fail -> synthesis_failed", j["status"] == "synthesis_failed",
      j["status"])
check("  retrieval is preserved for retry", j.get("retrieval_preserved") is True)

print("\n{} passed, {} failed".format(len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
