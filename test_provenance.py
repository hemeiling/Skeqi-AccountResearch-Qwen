#!/usr/bin/env python
"""Regression tests for evidence provenance: target / ecosystem / market.

The rule these protect: a company found by search is a CANDIDATE, never a
trusted ecosystem entity. It is promoted only when a source that itself verified
against the target says the two are connected. Without that ordering, a
competitor's press release becomes the account's supply chain.

No network, no model call.  .venv/bin/python test_provenance.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import research_service as rs                                    # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("  {} {}{}".format("PASS" if cond else "FAIL", name,
                             "" if cond or not detail else "  <- " + detail))


NAME = "Northwind Devices"
TARGET_PAGE = ("Northwind Devices said its final assembly supplier Kestrel Precision "
               "will expand output at two plants this year.")
PARTNER_PAGE = ("Kestrel Precision has installed new laser welding cells and a vision "
                "inspection line at its Suzhou plant.")
STRANGER_PAGE = ("Halcyon Robotics announced a new palletising cell for general industry.")

print("\n[A] A search-discovered name is NOT an ecosystem entity\n")

reg = rs.EcosystemRegistry(NAME)
reg.note_candidates(PARTNER_PAGE, "https://kestrel.example/news")
check("seeing a name only makes it a candidate", reg.verified() == [], str(reg.verified()))
check("an unverified candidate never matches", reg.match(PARTNER_PAGE) is None)
ok, reason, prov, ent = rs.verify_identity(
    PARTNER_PAGE, NAME, "", "", "https://kestrel.example/news", reg)
check("a partner page is rejected before verification", not ok, reason)

print("\n[B] The required sequence promotes it\n")

new = reg.verify_from_target_source(TARGET_PAGE, "https://northwind.example/press")
check("a target-verified source promotes the entity", "Kestrel Precision" in new, str(new))
check("it is now verified", "Kestrel Precision" in reg.verified(), str(reg.verified()))
check("the proof is recorded",
      reg.entities["Kestrel Precision"]["proof"] == "https://northwind.example/press")
ok, reason, prov, ent = rs.verify_identity(
    PARTNER_PAGE, NAME, "", "", "https://kestrel.example/news", reg)
check("the same partner page is now admitted", ok, reason)
check("and it is labelled ecosystem, not target", prov == rs.PROV_ECOSYSTEM, str(prov))
check("naming which entity", ent == "Kestrel Precision", str(ent))

print("\n[C] Promotion needs the target, the entity AND a relationship, together\n")

r2 = rs.EcosystemRegistry(NAME)
check("a source that never mentions the target promotes nothing",
      r2.verify_from_target_source(PARTNER_PAGE, "u") == [])
r3 = rs.EcosystemRegistry(NAME)
check("a target source with no relationship cue promotes nothing",
      r3.verify_from_target_source(
          "Northwind Devices reported quarterly revenue growth of 8 percent.", "u") == [])
r4 = rs.EcosystemRegistry(NAME)
r4.verify_from_target_source(TARGET_PAGE, "u")
check("an unrelated company is still not an entity",
      r4.match(STRANGER_PAGE) is None, str(r4.verified()))
r5 = rs.EcosystemRegistry(NAME)
check("the account can never be its own partner",
      "Northwind" not in " ".join(r5.extract(
          "Northwind Devices is the manufacturing partner Northwind Devices")))

print("\n[D] The ecosystem allowance is ADDITIVE, not a bypass\n")

reg2 = rs.EcosystemRegistry("ACRO Automation Systems")
reg2.entities["Kestrel Precision"] = {"proof": "u", "cue": "supplier"}
collide = "苏州爱克罗 ACRO 生物 Kestrel Precision"
ok, reason, prov, ent = rs.verify_identity(
    collide, "ACRO Automation Systems", "", "", "https://x.example/a", reg2)
check("a known collision is still rejected even with a verified entity present",
      not ok and reason.startswith("unrelated entity"), "%s / %s" % (ok, reason))
ok2, reason2, prov2, _ = rs.verify_identity(
    "Verkor gigafactory Dunkirk", "Verkor", "", "", "https://x.example/b", reg2)
check("target verification still works unchanged", ok2 and prov2 == rs.PROV_TARGET, reason2)
ok3, _, prov3, _ = rs.verify_identity(
    "Anything at all", "Verkor", "", "verkor.com", "https://verkor.com/x", reg2)
check("the official domain is still target", ok3 and prov3 == rs.PROV_TARGET)

print("\n[E] Market evidence is labelled, not smuggled in as the account\n")

# `market` is reserved for the WEAKEST identity signal: one distinctive token of a
# multi-word name, which is what an industry survey mentioning a vendor in passing
# looks like. A page naming the company in full is a strong match and stays target.
ok, reason, prov, _ = rs.verify_identity(
    "The 2026 robotics market grew; ACRO was among the vendors surveyed.",
    "ACRO Automation Systems", "", "", "https://analyst.example/report", None)
check("a one-token passing mention verifies but is labelled market",
      ok and prov == rs.PROV_MARKET, "%s / %s" % (reason, prov))
ok_f, reason_f, prov_f, _ = rs.verify_identity(
    "ACRO Automation Systems opened a new plant.",
    "ACRO Automation Systems", "", "", "https://news.example/a", None)
check("naming the company in full stays target",
      ok_f and prov_f == rs.PROV_TARGET, "%s / %s" % (reason_f, prov_f))
check("provenance_for_reason maps the weak rule to market",
      rs.provenance_for_reason("distinctive name token (manz)") == rs.PROV_MARKET)
check("and a strong rule to target",
      rs.provenance_for_reason("full-name match") == rs.PROV_TARGET)

print("\n[F] Provenance survives candidate -> evidence -> synthesis\n")

raw = [{"url": "https://northwind.example/a", "title": "Northwind Devices plant", "topic": "x"},
       {"url": "https://kestrel.example/b", "title": "Kestrel Precision line", "topic": "y"}]
ranked, rejected, tier_b = rs.dedupe_and_rank(raw, NAME, "", "northwind.example", reg)
by_host = {r["url"]: r for r in ranked}
check("the candidate carries provenance",
      all("provenance" in r for r in ranked), str(len(ranked)))
check("the target page is target",
      by_host.get("https://northwind.example/a", {}).get("provenance") == rs.PROV_TARGET)
check("the partner page is ecosystem",
      by_host.get("https://kestrel.example/b", {}).get("provenance") == rs.PROV_ECOSYSTEM,
      str(by_host.get("https://kestrel.example/b", {}).get("provenance")))

ev = [{"id": 1, "title": "T", "url": "u", "source_type": "news-article", "tier": 4,
       "text": "x", "provenance": rs.PROV_ECOSYSTEM, "entity": "Kestrel Precision"},
      {"id": 2, "title": "T", "url": "u2", "source_type": "news-article", "tier": 4,
       "text": "x", "provenance": rs.PROV_MARKET},
      {"id": 3, "title": "T", "url": "u3", "source_type": "official-website", "tier": 1,
       "text": "x", "provenance": rs.PROV_TARGET}]
rendered = rs.render_evidence(ev)
check("synthesis is told an ecosystem source is not about the account",
      "NOT direct evidence about the account" in rendered and "Kestrel Precision" in rendered)
check("synthesis is told a market source shows no relationship",
      "No account relationship" in rendered)
check("synthesis is told a target source is about the account",
      "EVIDENCE ABOUT: the account itself." in rendered)

print("\n[G] Reporting and safety\n")

rep = reg.report()
check("the report names verified entities with their proof",
      rep["verified"] and rep["verified"][0]["entity"] == "Kestrel Precision")
check("unverified candidates are listed separately", "candidates_unverified" in rep)
check("extraction survives empty input", rs.EcosystemRegistry("X").extract("") == [])
check("verification survives empty input",
      rs.EcosystemRegistry("X").verify_from_target_source("", "u") == [])
big = rs.EcosystemRegistry(NAME)
for i in range(60):
    big.verify_from_target_source(
        "Northwind Devices named supplier Partner%02d Industries today." % i, "u")
check("the entity list is bounded", len(big.verified()) <= 24, str(len(big.verified())))

print("\n{} passed, {} failed".format(len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
