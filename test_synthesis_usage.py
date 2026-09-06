#!/usr/bin/env python
"""P0-D engine-side contract: synthesis attempts survive the save/callback boundary.

save_run() builds the REPORT document; it does not and should not carry
accounting. The attempts therefore have to travel on the JOB. This pins that
contract, because the defect was exactly a key that existed on one object and
was read from the other.

No network, no model call.  .venv/bin/python test_synthesis_usage.py
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import research_service as rs                                    # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (" - " + detail if detail else ""))


print("\n[1] synthesize_with_fallback records every attempt that reached a model")

calls = {"n": 0}


def fake_synthesize(model, company, website, evidence, cfg, *a, **kw):
    calls["n"] += 1
    denied = model == "denied-model"
    return {"model": model, "model_label": model, "status": 403 if denied else 200,
            "report": "" if denied else "## Executive Summary\n\ntext",
            "endpoint": "e", "protocol": "p", "access_denied": denied,
            "input_tokens": 0 if denied else 12114,
            "output_tokens": 0 if denied else 14948,
            "total_tokens": 0 if denied else 27062,
            "latency_seconds": 1.0, "decision_makers": [], "people_summary": {},
            "error": "AccessDenied" if denied else None}


real = rs.synthesize
try:
    rs.synthesize = fake_synthesize
    run = rs.synthesize_with_fallback("ok-model", "Acme", "https://acme.com", [], {})
    att = run.get("ai_attempts")
    check("the run carries ai_attempts", isinstance(att, list) and len(att) >= 1, str(att))
    check("each attempt is marked as synthesis",
          all(a.get("kind") == "synthesis" for a in att), str([a.get("kind") for a in att]))
    check("the attempt names its own model",
          all(a.get("model") for a in att), str(att))
    check("provider token counts are carried, not invented",
          any(a.get("input_tokens") for a in att), str(att))
finally:
    rs.synthesize = real

print("\n[1b] A 413 is a real attempt, and it earns exactly one retry")
# The preflight ceiling is our own guess at a limit the provider does not
# publish. When it guesses high the run must compact harder and try once more -
# never a loop, and never the same request again.
seen = []


def flaky(model, company, website, evidence, cfg, *a, **kw):
    """413 on the normal budget, success on the emergency one."""
    emergency = bool(kw.get("emergency"))
    seen.append({"model": model, "emergency": emergency})
    if not emergency:
        return {"model": model, "status": 413, "report": "", "access_denied": False,
                "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
                "latency_seconds": 0.4, "decision_makers": [], "people_summary": {},
                "endpoint": "multimodal-generation", "protocol": "p",
                "error": "Request body size exceeds maximum allowed sized",
                "payload": {"payload_bytes": 97998, "budget_bytes": 48000,
                            "compaction_applied": True, "emergency_compaction": False}}
    return {"model": model, "status": 200, "report": "## Executive Summary\n\ntext",
            "access_denied": False, "input_tokens": 9100, "output_tokens": 7300,
            "total_tokens": 16400, "latency_seconds": 2.0, "decision_makers": [],
            "people_summary": {}, "endpoint": "multimodal-generation", "protocol": "p",
            "error": None,
            "payload": {"payload_bytes": 23588, "budget_bytes": 24000,
                        "compaction_applied": True, "emergency_compaction": True}}


real2 = rs.synthesize
try:
    rs.synthesize = flaky
    run = rs.synthesize_with_fallback("ok-model", "Acme", "https://acme.com", [], {})
    att = run.get("ai_attempts") or []
    check("the run recovered", run.get("status") == 200, str(run.get("status")))
    check("exactly two attempts were made", len(seen) == 2, str(seen))
    check("the second used the emergency budget",
          seen[1]["emergency"] is True and seen[0]["emergency"] is False)
    check("the same model was retried, not a different one",
          seen[0]["model"] == seen[1]["model"])
    check("both attempts are accounted", len(att) == 2, str(len(att)))
    check("the 413 attempt is recorded with zero provider tokens",
          att[0]["status"] == 413 and att[0]["input_tokens"] == 0
          and att[0]["output_tokens"] == 0,
          "the provider counted nothing, but the attempt still happened")
    check("the successful attempt carries real token counts",
          att[1]["status"] == 200 and att[1]["input_tokens"] == 9100)
    check("both are marked as synthesis",
          all(a.get("kind") == "synthesis" for a in att))
    check("the surviving payload record is the emergency one",
          (run.get("payload") or {}).get("emergency_compaction") is True
          and (run.get("payload") or {}).get("payload_bytes") == 23588)
finally:
    rs.synthesize = real2

print("\n[1c] A second 413 ends the run rather than looping")
tries = {"n": 0}


def always_too_large(model, company, website, evidence, cfg, *a, **kw):
    tries["n"] += 1
    return {"model": model, "status": 413, "report": "", "access_denied": False,
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
            "latency_seconds": 0.3, "decision_makers": [], "people_summary": {},
            "endpoint": "multimodal-generation", "protocol": "p",
            "error": "Request body size exceeds maximum allowed sized",
            "payload": {"payload_bytes": 30000, "budget_bytes": 24000,
                        "emergency_compaction": bool(kw.get("emergency"))}}


real3 = rs.synthesize
try:
    rs.synthesize = always_too_large
    run = rs.synthesize_with_fallback("ok-model", "Acme", "https://acme.com", [], {})
    check("it stopped after two attempts on this model", tries["n"] <= 2 * 1 + 1,
          "%d calls - no unbounded retry" % tries["n"])
    check("both attempts are still accounted",
          len([a for a in (run.get("ai_attempts") or []) if a["status"] == 413]) >= 2,
          str(run.get("ai_attempts")))
    check("the run reports the failure rather than a report",
          run.get("status") == 413 and not run.get("report"))
finally:
    rs.synthesize = real3

print("\n[2] The saved REPORT document does not carry accounting")
src = io.open("app.py", encoding="utf-8").read()
save_run = src[src.index("def save_run("):]
save_run = save_run[:save_run.index("\ndef ")]
check("save_run does not emit ai_attempts", "ai_attempts" not in save_run,
      "accounting does not belong in the report document")
check("save_run still carries the winning model's token_usage",
      "token_usage" in save_run, "this is what makes historical usage recoverable")

print("\n[3] The callback reads attempts from the JOB, not from the saved record")
check("the job entry is populated with ai_attempts",
      re.search(r"ai_attempts=list\(run\.get\(\"ai_attempts\"\)", src) is not None)
check("the completed payload reads m.get('ai_attempts')",
      'list(m.get("ai_attempts") or [])' in src)
check("it no longer reads them from the saved record",
      '(m.get("result") or {}).get("ai_attempts")' not in src,
      "this was the defect")

print("\n[4] A run where every model failed still reports what it spent")
check("the synthesis_failed callback sends ai_usage",
      re.search(r'"event": "synthesis_failed"[\s\S]{0,400}?"ai_usage"', src) is not None)
check("it includes the retrieval calls", 'package.get("ai_usage")' in src)
check("and the failed attempts", "failed_attempts" in src)

print("\n{} passed, {} failed".format(len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
