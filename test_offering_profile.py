#!/usr/bin/env python
"""The offering profile: what the account SELLS, never what it USES.

Ford's strongest retained evidence is "ABB Robotics recognized as a 2026 Ford
Supplier of the Year" and "Ford uses co-bots". A noun sweep turns those into
robotics as FORD'S offering, and competitor discovery then hunts robot makers -
Ford's suppliers, not its rivals. So direction is the property under test.

No network, no model call.  .venv/bin/python test_offering_profile.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import offering_profile as op                                    # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (" - " + detail if detail else ""))


def ev(url, title="", text="", official=False, domain=""):
    return {"url": url, "title": title, "text": text, "official": official,
            "domain": domain or url.split("/")[2]}


# An integrator: its own site names what it sells.
INTEGRATOR = [
    ev("https://acme-auto.com/", "Custom Automated Equipment - Acme Auto", official=True,
       domain="acme-auto.com",
       text="Acme Auto designs and builds custom automated assembly systems and "
            "automated welding solutions for automotive and medical device manufacturers. "
            "Our engineers work directly with customers on each project."),
    ev("https://acme-auto.com/welding", "Automated Welding Solutions", domain="acme-auto.com",
       text="Acme Auto provides robotic welding cells, laser processing and vision "
            "inspection for production lines."),
]
# A manufacturer: no own site, and the evidence is about what it BUYS.
MANUFACTURER = [
    ev("https://qualitymag.com/a", "ABB Robotics named Supplier of the Year",
       domain="qualitymag.com",
       text="ABB Robotics has been named a 2026 Northwind Motors Supplier of the Year. "
            "Northwind Motors uses collaborative robots on its assembly lines."),
    ev("https://news.example.com/b", "Northwind expands plant", domain="news.example.com",
       text="Northwind Motors will expand vehicle production at its automotive plant."),
]

print("\n[1] An integrator's own site defines its offerings")
p = op.build_profile(INTEGRATOR, "Acme Auto", "acme-auto.com")
check("business model is supplier/integrator",
      p["business_model"] == "equipment_supplier_or_integrator", p["business_model"])
check("offerings come from its own pages", len(p["offerings"]) >= 1, str(p["offerings"]))
check("welding is a capability it SELLS", "welding" in p["capabilities"], str(p["capabilities"]))
check("industries are picked up", "automotive" in p["industries"], str(p["industries"]))
check("competitor discovery is ready", p["competitor_discovery_ready"],
      str(p["competitor_skip_reason"]))

print("\n[2] A manufacturer's SUPPLIERS never become its offerings")
m = op.build_profile(MANUFACTURER, "Northwind Motors", "northwind-motors.com")
check("business model is manufacturer/OEM",
      m["business_model"] == "manufacturer_or_oem", m["business_model"])
check("robotics is NOT an offering", "robotics" not in m["capabilities"], str(m["capabilities"]))
check("robotics IS recorded as used", "robotics" in m["uses_not_sells"], str(m["uses_not_sells"]))
check("no offerings are invented from third-party text",
      not m["offerings"], str(m["offerings"]))
check("competitor discovery is NOT ready", not m["competitor_discovery_ready"])
check("and says why", "capabilities" in (m["competitor_skip_reason"] or ""),
      str(m["competitor_skip_reason"]))

print("\n[3] Supplier and customer names never become offerings")
NAMED = [ev("https://n.example.com/x", "Deal", domain="n.example.com",
            text="Northwind Motors uses ABB Robotics and FANUC equipment. "
                 "Northwind Motors supplies vehicles to Hertz and Avis.")]
n = op.build_profile(NAMED, "Northwind Motors", "")
blob = " ".join(n["offerings"] + n["capabilities"]).lower()
for who in ("abb", "fanuc", "hertz", "avis"):
    check("%s is not an offering" % who, who not in blob, blob[:60])

print("\n[4] Go-to-market is a POSITIVE finding, never the residue of absence")
check("no channel language at all yields UNKNOWN",
      op.build_profile([ev("https://x.com/", "Home", official=True, domain="x.com",
                           text="We make things. Contact us.")],
                       "X", "x.com")["go_to_market_model"] == op.UNKNOWN)
check("an empty corpus yields UNKNOWN",
      op.build_profile([], "X", "x.com")["go_to_market_model"] == op.UNKNOWN)
# Product-line language proves what is SOLD, not HOW. Engineered-to-order goods
# are routinely sold through representatives, so the two questions are separate.
check("engineered-to-order product lines do NOT give DIRECT",
      p["go_to_market_model"] == op.UNKNOWN, p["go_to_market_model"])
SELLS_DIRECT = [ev("https://s.com/", "Sales", official=True, domain="s.com",
                   text="Contact our sales team. Our account executives contract "
                        "directly with each customer.")]
check("explicit direct-selling language DOES give DIRECT",
      op.build_profile(SELLS_DIRECT, "S", "s.com")["go_to_market_model"] == op.DIRECT)
DIST = [ev("https://d.com/", "Find a Distributor", official=True, domain="d.com",
           text="D designs and builds custom automated assembly systems and "
                "robotic welding cells for automotive manufacturers. Our authorized distributor network covers North America.")]
check("distributor language gives DISTRIBUTOR_LED",
      op.build_profile(DIST, "D", "d.com")["go_to_market_model"] == op.DISTRIBUTOR_LED)
REP = [ev("https://r.com/", "Reps", official=True, domain="r.com",
           text="R designs and builds custom automated assembly systems and "
                "robotic welding cells for automotive manufacturers. Our manufacturers' representative network serves the Midwest.")]
check("representative language gives REPRESENTATIVE_LED",
      op.build_profile(REP, "R", "r.com")["go_to_market_model"] == op.REPRESENTATIVE_LED)
BOTH = [ev("https://b.com/", "Sales", official=True, domain="b.com",
           text="B designs and builds custom automated assembly systems and "
                "robotic welding cells for automotive manufacturers. Request a quote from our sales team, or find a distributor near you.")]
check("both signals give MIXED",
      op.build_profile(BOTH, "B", "b.com")["go_to_market_model"] == op.MIXED)
check("a blocked site cannot become DIRECT",
      op.build_profile([ev("https://t.com/a", "News", domain="t.com",
                           text="T Corp had a good quarter.")],
                       "T Corp", "t.com")["go_to_market_model"] == op.UNKNOWN,
      "absence of a distributor is not evidence of direct sales")

print("\n[5] Readiness is per path, not one opaque score")
check("confidence is field-level",
      set(p["confidence"]) >= {"offerings", "capabilities", "industries", "geography",
                               "business_model", "go_to_market"}, str(p["confidence"]))
check("competitor readiness ignores go-to-market",
      op.build_profile(INTEGRATOR, "Acme Auto", "acme-auto.com")["competitor_discovery_ready"])
weak = op.build_profile([ev("https://w.com/a", "W", domain="w.com", text="W Inc exists.")], "W Inc", "")
check("a weak profile is ready for neither path",
      not weak["competitor_discovery_ready"] and not weak["channel_discovery_ready"])
check("and both give a reason",
      weak["competitor_skip_reason"] and weak["channel_skip_reason"])
check("channel readiness turns on account understanding, not on knowing the model",
      "account understanding" in
      (op.build_profile([ev("https://w2.com/a", "W", domain="w2.com",
                            text="W Inc exists.")], "W Inc", "")["channel_skip_reason"] or ""))

print("\n[5b] Channel readiness does not require a KNOWN go-to-market")
# Discovering the model is part of what channel discovery is for. Requiring it
# first would make the path unreachable exactly when it is most useful.
check("UNKNOWN plus a solid profile is READY to search",
      p["go_to_market_model"] == op.UNKNOWN and p["channel_discovery_ready"],
      "%s / %s" % (p["go_to_market_model"], p["channel_skip_reason"]))
check("verified DIRECT skips the search instead",
      not op.build_profile(SELLS_DIRECT, "S", "s.com")["channel_discovery_ready"])
check("and says the model was verified, not that evidence was missing",
      "verified as DIRECT" in
      (op.build_profile(SELLS_DIRECT, "S", "s.com")["channel_skip_reason"] or ""))
check("DISTRIBUTOR_LED is ready", op.build_profile(DIST, "D", "d.com")["channel_discovery_ready"])
check("REPRESENTATIVE_LED is ready", op.build_profile(REP, "R", "r.com")["channel_discovery_ready"])
check("MIXED is ready", op.build_profile(BOTH, "B", "b.com")["channel_discovery_ready"])
# The two kinds of "unknown" must stay apart.
thin = op.build_profile(MANUFACTURER, "Northwind Motors", "northwind-motors.com")
check("a thin profile is NOT ready even though its model is also unknown",
      not thin["channel_discovery_ready"])
check("and the reason names understanding, not the missing model",
      "account understanding" in (thin["channel_skip_reason"] or "")
      and "go_to_market" not in (thin["channel_skip_reason"] or ""),
      str(thin["channel_skip_reason"]))

print("\n[6] Every assertion is traceable, and the result is deterministic")
check("offerings carry their sources",
      all(v for v in p["supporting_evidence"]["offerings"].values()))
check("capabilities carry their sources",
      all(v for v in p["supporting_evidence"]["capabilities"].values()))
check("go-to-market carries its source when one was found",
      bool(op.build_profile(SELLS_DIRECT, "S", "s.com")
           ["supporting_evidence"]["go_to_market_model"]))
check("and carries none when the model is unknown",
      not p["supporting_evidence"]["go_to_market_model"],
      "an unknown model must not fabricate a citation")
check("usage is kept separately",
      "usage" in m["supporting_evidence"] and m["supporting_evidence"]["usage"])
a1 = op.build_profile(INTEGRATOR, "Acme Auto", "acme-auto.com")
a2 = op.build_profile(INTEGRATOR, "Acme Auto", "acme-auto.com")
import json
check("the same evidence yields the same profile",
      json.dumps(a1, sort_keys=True) == json.dumps(a2, sort_keys=True))

print("\n[7] Nothing account-specific is hard-coded")
# Strip comments and docstrings first. Naming Ford and ACRO in the prose that
# explains WHY direction matters is the opposite of a problem; what must not
# happen is an account name reaching a lexicon, a pattern or a branch.
import ast
import io as _io
_src = _io.open("offering_profile.py", encoding="utf-8").read()
_tree = ast.parse(_src)
_docstrings = set()
for _n in ast.walk(_tree):
    if isinstance(_n, (ast.Module, ast.FunctionDef, ast.ClassDef)):
        _d = ast.get_docstring(_n)
        if _d:
            _docstrings.add(_d)
_code = _src
for _d in _docstrings:
    _code = _code.replace(_d, " ")
_code = "\n".join(l.split("#")[0] for l in _code.split("\n"))
for word in ("acro", "ford", "hongqi", "verkor", "tesla", "battery cell", "automotive tier"):
    check("no %r in executable logic" % word, word not in _code.lower())
check("the lexicons are industry-neutral",
      "battery" in str(op._INDUSTRY_LEXICON).lower()
      and "automotive" in str(op._INDUSTRY_LEXICON).lower(),
      "generic industry NAMES are normalisation, not account specialisation")

print("\n{} passed, {} failed".format(len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
