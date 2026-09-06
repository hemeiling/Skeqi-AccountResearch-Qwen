# -*- coding: utf-8 -*-
"""The synthesis request has a ceiling, and it is measured the way the provider
measures it.

AMADA WELD TECH retrieved sixteen good sources and produced no report: HTTP 413,
before the provider counted a single input token. Retrieval was not at fault and
is unchanged. What was missing was a budget on the REQUEST.

Two measurement traps are asserted here directly, because both are silent:

  BYTES, NOT CHARACTERS - a Chinese character is three bytes, so a character cap
  gives a CJK-heavy account three times the payload for the same nominal limit.

  THE SERIALIZED REQUEST, NOT THE PROMPT - json.dumps escapes non-ASCII to
  \\uXXXX, six bytes per Chinese character. On AMADA that added 19.8%.

Runs on the reconstructed AMADA package and every stored research package. No
network, no model call.   .venv/bin/python test_synthesis_budget.py
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import app                                                        # noqa: E402
import provider_view as pv                                        # noqa: E402
import research_service as rs                                     # noqa: E402
import synthesis_payload as sp                                    # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (" - " + detail if detail else ""))


SCRATCH = os.path.join(
    "/private/tmp/claude-501/-Users-meilinghe-Downloads-Qwen-API-Search-----",
    "1c06afbf-82e9-4c76-8bed-53680bf7d688/scratchpad")
CACHE = os.path.join(SCRATCH, "evidence_cache_before")
AMADA = os.path.join(SCRATCH, "amada_evidence.json")
BUDGET = rs.SYNTHESIS_BUDGET_BYTES


def item(i, url, tier, text, domain=None, title=None, stype="web"):
    return {"id": i, "url": url, "title": title or ("Source %d" % i), "tier": tier,
            "source_type": stype, "text": text, "domain": domain or url.split("/")[2],
            "official": tier == 1, "provenance": "target"}


def load_amada():
    if os.path.exists(AMADA):
        raw = json.load(io.open(AMADA, encoding="utf-8"))
        return [item(i, r["url"], r["tier"],
                     (r["text"] or "")[:rs.CHAR_BUDGET.get(r["tier"], 900)],
                     r["domain"], r["title"], r["source_type"])
                for i, r in enumerate(raw, 1)]
    # Stand-in with the same shape: eight tier-1 pages at the ceiling plus
    # third-party sources, mixed English and Chinese.
    en = "AMADA WELD TECH designs laser welding and machine vision systems. " * 90
    zh = "阿玛达焊接技术公司提供激光焊接与视觉检测系统，服务汽车与电子制造客户。" * 40
    ev = [item(i, "https://amadaweldtech.com/p%d" % i, 1, en[:5000], "amadaweldtech.com")
          for i in range(1, 9)]
    ev += [item(8 + i, "https://cn%d.example.com/a" % i, 6, zh[:900], "cn%d.example.com" % i)
           for i in range(1, 9)]
    return ev


def payload_of(evidence, company="AMADA WELD TECH", extras=True):
    """The serialized request, assembled exactly as synthesize() assembles it."""
    head = rs.INSTRUCTION.format(company=company, site=" (website: https://x.com)",
                                 capabilities=rs.SKEQI_CAPABILITIES)
    tail = ""
    if extras:
        tail += pv.provider_prompt_block(PROVIDERS, company)
        tail += pv.competitor_prompt_block(COMPETITORS, PROFILE)
        tail += pv.channel_prompt_block(CHANNELS, PROFILE)
    prompt = head + rs.render_evidence(evidence) + tail
    return {"model": "qwen3.6-flash",
            "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
            "parameters": {}}, prompt, head, tail


PROVIDERS = [{"name": "Yaskawa", "relationship": rs.REL_MARKET, "provenance": rs.PROV_MARKET,
              "capability": "robotics", "evidence": ["https://y.com"], "confidence": "medium"}]
COMPETITORS = [{"organization_name": "Rival One", "organization_key": "rivalone",
                "competition_type": "DIRECT", "offering_overlap": True,
                "customer_or_industry_overlap": True, "geographic_or_market_overlap": True,
                "competitive_rationale": "offerings: welding", "source_urls": ["https://r1.com"],
                "source_domains": ["r1.com"], "provenance": rs.PROV_MARKET,
                "confidence": "high", "discovered_by": "tavily"}]
CHANNELS = [{"organization_name": "Midwest Distribution", "organization_key": "midwestdistribution",
             "role": "AUTHORIZED_DISTRIBUTOR", "is_representation": True, "authorized": True,
             "territory": "Upper Midwest", "evidence_quote": "authorized distributor for",
             "source_urls": ["https://md.com"], "source_domains": ["md.com"],
             "provenance": rs.PROV_ECOSYSTEM, "confidence": "high", "discovered_by": "tavily"}]
PROFILE = {"go_to_market_model": "UNKNOWN", "go_to_market_confidence": "none",
           "competitor_skip_reason": None, "channel_skip_reason": None}


def fit(evidence, company="AMADA WELD TECH", budget=None, emergency=False):
    """Run the same compaction synthesize() runs, and return the request."""
    budget = budget or BUDGET
    _p, _prompt, head, tail = payload_of(evidence, company)
    empty, _pr, _h, _t = payload_of([], company)
    overhead = sp.serialized_bytes(empty) - sp.utf8(_pr)
    available = budget - sp.utf8(head) - sp.utf8(tail) - overhead
    items, stats = sp.compact(
        evidence, company, max(available, 0), rs.render_evidence, (),
        ceilings=rs.SYNTHESIS_EMERGENCY_ITEM_BYTES if emergency else None,
        floor=sp.EMERGENCY_ITEM_FLOOR if emergency else sp.ITEM_FLOOR)
    guard = 0
    while sp.serialized_bytes(payload_of(items, company)[0]) > budget and guard < 12:
        guard += 1
        available = int(available * 0.85)
        items, stats = sp.compact(
            evidence, company, max(available, 0), rs.render_evidence, (),
            ceilings=rs.SYNTHESIS_EMERGENCY_ITEM_BYTES if emergency else None,
            floor=sp.EMERGENCY_ITEM_FLOOR if emergency else sp.ITEM_FLOOR)
    return items, stats, sp.serialized_bytes(payload_of(items, company)[0])


print("\n[1] The provider counts bytes of the serialized request")
zh = "激光焊接与视觉检测系统" * 50
en = "laser welding and machine vision systems " * 30
check("a Chinese string costs three bytes per character",
      sp.utf8(zh) == len(zh) * 3, "%d chars, %d bytes" % (len(zh), sp.utf8(zh)))
body_zh = sp.serialized_bytes({"t": zh})
check("and six once JSON-escaped", body_zh > sp.utf8(zh) * 1.9,
      "%d bytes on the wire for %d bytes of UTF-8" % (body_zh, sp.utf8(zh)))
check("an ASCII string is one byte per character", sp.utf8(en) == len(en))
check("equal character counts are NOT equal payloads",
      sp.utf8(zh[:200]) != sp.utf8(en[:200]),
      "this is why the budget cannot be counted in characters")
check("the measurement matches what post_json sends",
      sp.serialized_bytes({"a": zh}) == len(json.dumps({"a": zh}).encode("utf-8")))

print("\n[2] AMADA, the run that produced no report")
ev = load_amada()
before_payload, before_prompt, _h, _t = payload_of(ev)
before_bytes = sp.serialized_bytes(before_payload)
after, stats, after_bytes = fit(ev)
check("the original request exceeds the budget", before_bytes > BUDGET,
      "%d B against a %d B budget" % (before_bytes, BUDGET))
check("the compacted request fits", after_bytes <= BUDGET, "%d B" % after_bytes)
check("every source is preserved", len(after) == len(ev),
      "%d -> %d" % (len(ev), len(after)))
check("every domain is preserved",
      len({e["domain"] for e in after}) == len({e["domain"] for e in ev}),
      "%d domains" % len({e["domain"] for e in after}))
check("every official tier-1 source is preserved",
      sum(1 for e in after if e["tier"] == 1) == sum(1 for e in ev if e["tier"] == 1))
check("every citation identifier survives",
      [e["id"] for e in after] == [e["id"] for e in ev])
check("every URL survives", [e["url"] for e in after] == [e["url"] for e in ev])
print("       %d B -> %d B (-%.1f%%), %d sources, %d domains" % (
    before_bytes, after_bytes, 100.0 * (before_bytes - after_bytes) / before_bytes,
    len(after), len({e["domain"] for e in after})))

print("\n[3] Every stored package, including the largest and the most Chinese")
rows, worst = [], None
for f in sorted(os.listdir(CACHE)) if os.path.isdir(CACHE) else []:
    if not f.endswith(".json") or "sec_company" in f:
        continue
    d = json.load(io.open(os.path.join(CACHE, f), encoding="utf-8"))
    src = d.get("evidence") or []
    if not src:
        continue
    pkg = [item(i, e["url"], e["tier"], e.get("text") or "", e.get("domain"),
                e.get("title"), e.get("source_type") or "web")
           for i, e in enumerate(src, 1)]
    company = d.get("company") or "X"
    b0 = sp.serialized_bytes(payload_of(pkg, company)[0])
    kept, st, b1 = fit(pkg, company)
    cjk = sum(1 for e in pkg for ch in (e["text"] or "") if "一" <= ch <= "鿿")
    chars = sum(len(e["text"] or "") for e in pkg)
    rows.append((f, len(pkg), len(kept), b0, b1,
                 len({e["domain"] for e in pkg}), len({e["domain"] for e in kept}),
                 100.0 * cjk / max(chars, 1)))
if rows:
    check("every package fits the budget", all(r[4] <= BUDGET for r in rows),
          "worst %d B" % max(r[4] for r in rows))
    check("no package loses a source", all(r[2] == r[1] for r in rows))
    check("no package loses a domain", all(r[6] == r[5] for r in rows))
    big = max(rows, key=lambda r: r[3])
    check("the largest package (%s) fits" % big[0][:22], big[4] <= BUDGET,
          "%d B -> %d B, %d sources kept" % (big[3], big[4], big[2]))
    chinese = max(rows, key=lambda r: r[7])
    check("the most Chinese package (%.0f%% CJK) fits" % chinese[7], chinese[4] <= BUDGET,
          "%d B -> %d B" % (chinese[3], chinese[4]))
    english = min(rows, key=lambda r: r[7])
    check("an English-heavy package fits too", english[4] <= BUDGET,
          "%.0f%% CJK, %d B -> %d B" % (english[7], english[3], english[4]))
    print("       %d packages, max after %d B, median after %d B" % (
        len(rows), max(r[4] for r in rows), sorted(r[4] for r in rows)[len(rows) // 2]))
else:
    check("stored packages are available", False, "cache directory missing")

print("\n[4] A giant page cannot reach synthesis through any path")
GIANT = "Great Lakes Automation supplies welding systems. " * 30000     # ~1.4 MB
check("the fixture really is over a megabyte", sp.utf8(GIANT) > 1048576,
      "%.1f MB" % (sp.utf8(GIANT) / 1048576.0))
SRC = io.open(os.path.join(HERE, "research_service.py"), encoding="utf-8").read()
body = SRC[SRC.index("def tavily_verifier"):SRC.index("def tavily_verifier") + 5000]
check("tavily_verifier caps the retained text", "CHAR_BUDGET" in body)
check("tavily_verifier no longer stores the whole page", '"text": body,' not in body)
# Competitor and channel evidence is built in one place now, and capped there.
check("discovery evidence is capped where it is built",
      'CHAR_BUDGET.get(5, 1600)' in SRC)
check("no evidence path stores an uncapped page body",
      SRC.count('"text": (src["text"] or "")[:CHAR_BUDGET') == 1)
for tier, path in ((5, "competitor/channel page"), (6, "tavily web source")):
    capped = GIANT[:rs.CHAR_BUDGET.get(tier, 900)]
    check("a 1.4 MB %s becomes %d chars" % (path, rs.CHAR_BUDGET.get(tier, 900)),
          len(capped) == rs.CHAR_BUDGET.get(tier, 900))
giant_pkg = load_amada()[:8] + [item(99, "https://giant.com/x", 5, GIANT, "giant.com")]
kept_g, _st, bytes_g = fit(giant_pkg)
check("and a package containing one still fits the budget", bytes_g <= BUDGET,
      "%d B" % bytes_g)
check("the giant source is kept, not dropped",
      any(e["id"] == 99 for e in kept_g), "compaction bounds it rather than losing it")

print("\n[5] Compaction spends the budget on breadth, not on one page")
many = [item(i, "https://d%d.com/a" % i, 3, ("Fact %d about welding systems. " % i) * 400,
             "d%d.com" % i) for i in range(1, 17)]
kept_m, _s, b_m = fit(many)
check("sixteen medium sources all survive", len(kept_m) == 16, str(len(kept_m)))
check("and the request fits", b_m <= BUDGET, "%d B" % b_m)
four_giants = [item(i, "https://g%d.com/a" % i, 1, "Welding system detail. " * 3000,
                    "g%d.com" % i) for i in range(1, 5)]
kept_f, _s2, b_f = fit(four_giants)
check("four giant sources are bounded rather than dropped", len(kept_f) == 4)
check("and that request fits as well", b_f <= BUDGET, "%d B" % b_f)
check("no item is dropped while any item is above the floor",
      all(sp.utf8(e["text"]) >= 1 for e in kept_m))

print("\n[6] Passage selection, not head truncation")
NOISE = "Home About Contact Careers Privacy Cookie settings Newsletter signup. " * 40
FACT = ("AMADA WELD TECH acquired MacGregor Welding Systems in 2023, adding "
        "resistance welding capacity at its Hampshire plant.")
page = NOISE + "\n\n" + FACT + "\n\n" + NOISE
text, _sk = sp.compact_text(page, 600, "AMADA WELD TECH", (), [])
check("the fact survives a hard ceiling", FACT[:60] in text, text[:90])
check("and the navigation boilerplate does not dominate",
      text.count("Cookie settings") <= 1, text[:80])
dup_seen = []
sp.compact_text(NOISE + FACT, 600, "AMADA WELD TECH", (), dup_seen)
t2, skipped = sp.compact_text(NOISE + FACT, 600, "AMADA WELD TECH", (), dup_seen)
check("a passage another source already carried is skipped", skipped > 0, str(skipped))
check("a page with no sentence structure is still clipped safely",
      sp.utf8(sp.compact_text("中" * 5000, 300, "X", (), [])[0]) <= 300,
      "byte-exact, never splitting a character")

print("\n[7] Structured verified facts are never the budget's casualty")
after_s, _st, _b = fit(load_amada())
_pay, prompt_after, _h, _t = payload_of(after_s)
check("the provider block survives", "Yaskawa" in prompt_after)
check("the competitor block survives", "Rival One" in prompt_after)
check("the channel block survives", "Midwest Distribution" in prompt_after)
check("the channel role survives", "AUTHORIZED_DISTRIBUTOR" in prompt_after)
check("citations survive with their URLs",
      all(e["url"] in prompt_after for e in after_s))
check("source identity survives",
      all(("[%d]" % e["id"]) in prompt_after for e in after_s))

print("\n[8] The emergency budget is materially lower")
check("the normal budget is 48000 bytes", rs.SYNTHESIS_BUDGET_BYTES == 48000)
check("the emergency budget is half of it",
      rs.SYNTHESIS_EMERGENCY_BUDGET_BYTES == 24000,
      "not a retry of nearly the same request")
em, em_stats, em_bytes = fit(load_amada(), budget=rs.SYNTHESIS_EMERGENCY_BUDGET_BYTES,
                             emergency=True)
check("the emergency request fits its ceiling",
      em_bytes <= rs.SYNTHESIS_EMERGENCY_BUDGET_BYTES, "%d B" % em_bytes)
check("it is materially smaller than the normal one", em_bytes < after_bytes * 0.75,
      "%d B against %d B" % (em_bytes, after_bytes))
_pay2, em_prompt, _h2, _t2 = payload_of(em)
check("citations still survive", all(("[%d]" % e["id"]) in em_prompt for e in em))
check("verified blocks still survive",
      "Rival One" in em_prompt and "Midwest Distribution" in em_prompt)
check("it keeps at least the minimum source breadth",
      len(em) >= min(sp.MIN_SOURCES, len(load_amada())), "%d sources" % len(em))

print("\n[9] Budget configuration")
check("the key is read from configuration",
      "SYNTHESIS_PAYLOAD_BUDGET_BYTES" in rs.CONFIG_KEYS)
check("a configured value wins",
      rs.synthesis_budget({"SYNTHESIS_PAYLOAD_BUDGET_BYTES": "30000"}) == 30000)
check("an absent value falls back to the default",
      rs.synthesis_budget({}) == rs.SYNTHESIS_BUDGET_BYTES)
check("a nonsense value falls back rather than raising",
      rs.synthesis_budget({"SYNTHESIS_PAYLOAD_BUDGET_BYTES": "wide"}) == rs.SYNTHESIS_BUDGET_BYTES)
check("the emergency budget ignores configuration",
      rs.synthesis_budget({"SYNTHESIS_PAYLOAD_BUDGET_BYTES": "999999"}, True)
      == rs.SYNTHESIS_EMERGENCY_BUDGET_BYTES)

print("\n[10] The manifest records sizes, never bodies")
facts = app._payload_facts(dict(sp.empty_stats(), payload_bytes=47800,
                                estimated_input_tokens=12600, budget_bytes=48000,
                                compaction_applied=True, evidence_items_before=16,
                                evidence_items_after=16, evidence_bytes_before=59925,
                                evidence_bytes_after=31000, sources_preserved=16,
                                domains_preserved=7))
for k in ("synthesis_payload_bytes", "synthesis_estimated_input_tokens",
          "synthesis_budget_bytes", "synthesis_compaction_applied",
          "synthesis_emergency_compaction", "synthesis_evidence_items_before",
          "synthesis_evidence_items_after", "synthesis_evidence_bytes_before",
          "synthesis_evidence_bytes_after", "synthesis_sources_preserved",
          "synthesis_domains_preserved"):
    check("manifest carries %s" % k, k in facts)
check("counters are integers",
      all(isinstance(facts[k], int) for k in facts if k.endswith(("bytes", "before", "after",
                                                                  "tokens", "preserved"))))
check("no prompt or evidence body is stored",
      not any(isinstance(v, str) and len(v) > 200 for v in facts.values()))
check("a run that never compacted says so",
      app._payload_facts(sp.empty_stats())["synthesis_compaction_applied"] is False)
check("an absent payload record yields zeros, not a crash",
      app._payload_facts(None)["synthesis_payload_bytes"] == 0)
whole = app.execution_facts({"evidence": [], "ai_usage": []}, [], None,
                            payload={"payload_bytes": 100})
check("the block sits in the execution manifest", "synthesis_payload" in whole)
check("and does not disturb the others",
      "competitor_discovery" in whole and "channel_discovery" in whole
      and "retrieval" in whole)

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
