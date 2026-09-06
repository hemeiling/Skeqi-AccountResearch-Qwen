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
