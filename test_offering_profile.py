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
check("STRONGLY corroborated DIRECT skips the search instead",
      not op.build_profile(SELLS_DIRECT, "S", "s.com")["channel_discovery_ready"])
check("and says the model was corroborated, not that evidence was missing",
      "corroborated as DIRECT" in
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

print("\n[5c] A sales team is not proof that no channel exists")
# The gating question is NOT "does this company sell direct" - almost everyone
# does, alongside whatever channel they run. It is "is direct selling corroborated
# strongly enough that looking for a channel would be wasted". Only an explicit
# statement, or several independent signals, clears that bar.
SALES_TEAM_ONLY = [ev("https://one.com/", "Company", official=True, domain="one.com",
                      text="One Corp designs and builds custom automated assembly "
                           "systems and robotic welding cells for automotive "
                           "manufacturers. Our sales team works closely with every "
                           "customer.")]
one = op.build_profile(SALES_TEAM_ONLY, "One Corp", "one.com")
check("'our sales team' alone supports DIRECT", one["go_to_market_model"] == op.DIRECT)
check("but only at LOW confidence", one["go_to_market_confidence"] == "low",
      one["go_to_market_confidence"])
check("so channel discovery still runs", one["channel_discovery_ready"],
      str(one["channel_skip_reason"]))
check("and the signal is recorded as supporting, not explicit",
      one["go_to_market_evidence"]["explicit_direct_statement"] is False
      and [g["strength"] for g in one["go_to_market_evidence"]["signals"]] == ["supporting"])

QUOTE_ONLY = [ev("https://two.com/", "Company", official=True, domain="two.com",
                 text="Two Corp designs and builds custom automated assembly systems "
                      "and robotic welding cells for automotive manufacturers. "
                      "Request a quote for your next project.")]
two = op.build_profile(QUOTE_ONLY, "Two Corp", "two.com")
check("a quote form alone is LOW confidence", two["go_to_market_confidence"] == "low",
      two["go_to_market_confidence"])
check("and does not suppress channel discovery", two["channel_discovery_ready"])

EXPLICIT = [ev("https://three.com/", "How we sell", official=True, domain="three.com",
               text="Three Corp designs and builds custom automated assembly systems "
                    "and robotic welding cells for automotive manufacturers. "
                    "We sell directly to customers.")]
three = op.build_profile(EXPLICIT, "Three Corp", "three.com")
check("an explicit statement gives HIGH confidence",
      three["go_to_market_model"] == op.DIRECT
      and three["go_to_market_confidence"] == "high", three["go_to_market_confidence"])
check("it is marked explicit", three["go_to_market_evidence"]["explicit_direct_statement"])
check("and it MAY suppress channel discovery", not three["channel_discovery_ready"])
check("the reason names the statement, not an absence",
      "explicit statement" in (three["channel_skip_reason"] or ""),
      str(three["channel_skip_reason"]))

MANY = [ev("https://four.com/", "Sales", official=True, domain="four.com",
           text="Four Corp designs and builds custom automated assembly systems and "
                "robotic welding cells for automotive manufacturers. Contact our "
                "sales team, request a quote, and our account managers will "
                "contract directly with you.")]
four = op.build_profile(MANY, "Four Corp", "four.com")
check("several independent supporting signals also reach HIGH",
      four["go_to_market_confidence"] == "high"
      and four["go_to_market_evidence"]["direct_signal_count"] >= 3,
      "%s / %d" % (four["go_to_market_confidence"],
                   four["go_to_market_evidence"]["direct_signal_count"]))
check("two signals stay at MEDIUM and keep channel discovery open", (lambda f: (
      f["go_to_market_confidence"] == "medium" and f["channel_discovery_ready"]))(
      op.build_profile([ev("https://five.com/", "Sales", official=True, domain="five.com",
                           text="Five Corp designs and builds custom automated assembly "
                                "systems and robotic welding cells for automotive "
                                "manufacturers. Contact our sales team or request a "
                                "quote.")], "Five Corp", "five.com")))

INTERNAL_PLUS_DIST = [ev("https://six.com/", "Sales", official=True, domain="six.com",
                         text="Six Corp designs and builds custom automated assembly "
                              "systems and robotic welding cells for automotive "
                              "manufacturers. Contact our sales team, or find a "
                              "distributor in your region.")]
six = op.build_profile(INTERNAL_PLUS_DIST, "Six Corp", "six.com")
check("internal sales plus a verified distributor is MIXED",
      six["go_to_market_model"] == op.MIXED, six["go_to_market_model"])
check("MIXED never suppresses channel discovery", six["channel_discovery_ready"])

SILENT = [ev("https://seven.com/", "Company", official=True, domain="seven.com",
             text="Seven Corp designs and builds custom automated assembly systems "
                  "and robotic welding cells for automotive manufacturers.")]
seven = op.build_profile(SILENT, "Seven Corp", "seven.com")
check("silence about channels gives UNKNOWN, not DIRECT",
      seven["go_to_market_model"] == op.UNKNOWN, seven["go_to_market_model"])
check("silence does not imply there are no distributors",
      seven["channel_discovery_ready"], str(seven["channel_skip_reason"]))
check("and no direct signal was invented",
      seven["go_to_market_evidence"]["direct_signal_count"] == 0)
check("a named channel is confident on its own",
      op.build_profile(DIST, "D", "d.com")["go_to_market_confidence"] in ("medium", "high"))
check("but a channel model never suppresses channel discovery",
      op.build_profile(DIST, "D", "d.com")["channel_discovery_ready"]
      and op.build_profile(REP, "R", "r.com")["channel_discovery_ready"])
check("field-level confidence agrees with the graded value",
      all(x["confidence"]["go_to_market"] == x["go_to_market_confidence"]
          for x in (one, three, six, seven)))

print("\n[5d] Retained channel evidence outranks own-site DIRECT language")
# The go-to-market cues read the account's own pages only. Without this, a
# third-party source already in the evidence saying "X is the authorized
# distributor for the account" would sit there unread while the profile declared
# the account exclusively direct and switched channel discovery off.
OWN_DIRECT = ev("https://acme.com/", "How we sell", official=True, domain="acme.com",
                text="Acme Systems designs and builds custom automated assembly systems "
                     "and robotic welding cells for automotive manufacturers. We sell "
                     "directly to customers.")
THIRD_DIST = ev("https://midwest-dist.com/lines", "Midwest Industrial Distribution",
                domain="midwest-dist.com",
                text="Midwest Industrial Distribution is the authorized distributor for "
                     "Acme Systems in the Upper Midwest.")
THIRD_REP = ev("https://ontario-rep.com/about", "Ontario Automation Group",
               domain="ontario-rep.com",
               text="Ontario Automation Group represents Acme Systems throughout Ontario.")
INTEGRATOR_PAGE = ev("https://lakeside.com/about", "Lakeside Systems", domain="lakeside.com",
                text="Lakeside Systems is a system integrator. We integrate Acme Systems "
                     "equipment into our customers' production lines.")
PARTNER_PAGE = ev("https://tp.com/about", "Northwind Tech", domain="tp.com",
             text="Northwind Tech announced a partnership with Acme Systems and services "
                  "Acme Systems equipment for its customers.")

both = op.build_profile([OWN_DIRECT, THIRD_DIST], "Acme Systems", "acme.com")
check("explicit direct plus a verified distributor is MIXED",
      both["go_to_market_model"] == op.MIXED, both["go_to_market_model"])
check("and channel discovery stays ready", both["channel_discovery_ready"],
      str(both["channel_skip_reason"]))
check("the retained representation is recorded with its source",
      [r["role"] for r in both["go_to_market_evidence"]["retained_channel"]]
      == ["AUTHORIZED_DISTRIBUTOR"],
      str(both["go_to_market_evidence"]["retained_channel"]))
check("the explicit direct statement is not erased either",
      both["go_to_market_evidence"]["explicit_direct_statement"] is True,
      "both facts survive; neither classifier overwrites the other")
rep = op.build_profile([OWN_DIRECT, THIRD_REP], "Acme Systems", "acme.com")
check("a verified representative does the same", rep["go_to_market_model"] == op.MIXED)
check("two independent channel sources raise confidence",
      op.build_profile([OWN_DIRECT, THIRD_DIST, THIRD_REP], "Acme Systems",
                       "acme.com")["go_to_market_confidence"] == "high")

alone = op.build_profile([THIRD_DIST], "Acme Systems", "acme.com")
check("a distributor with no direct language gives DISTRIBUTOR_LED",
      alone["go_to_market_model"] == op.DISTRIBUTOR_LED, alone["go_to_market_model"])
check("a representative with no direct language gives REPRESENTATIVE_LED",
      op.build_profile([THIRD_REP], "Acme Systems", "acme.com")["go_to_market_model"]
      == op.REPRESENTATIVE_LED)

# Precision: related is not representation.
integ = op.build_profile([OWN_DIRECT, INTEGRATOR_PAGE], "Acme Systems", "acme.com")
check("an integrator does NOT force MIXED", integ["go_to_market_model"] == op.DIRECT,
      integ["go_to_market_model"])
check("and does not reopen channel discovery", not integ["channel_discovery_ready"])
check("nor is it recorded as retained representation",
      not integ["go_to_market_evidence"]["retained_channel"])
partner = op.build_profile([OWN_DIRECT, PARTNER_PAGE], "Acme Systems", "acme.com")
check("a technology or service partner does not force MIXED either",
      partner["go_to_market_model"] == op.DIRECT, partner["go_to_market_model"])
check("a distributor of SOMEONE ELSE is ignored",
      op.build_profile([OWN_DIRECT,
                        ev("https://v.com/a", "Vector", domain="v.com",
                           text="Vector Robotics is an authorized distributor for a "
                                "leading robot brand across North America.")],
                       "Acme Systems", "acme.com")["go_to_market_model"] == op.DIRECT)
check("silence still gives UNKNOWN, never a channel",
      not op.build_profile([ev("https://q.com/", "Q", official=True, domain="q.com",
                               text="Q Corp designs and builds custom automated "
                                    "assembly systems for automotive manufacturers.")],
                           "Q Corp", "q.com")["go_to_market_evidence"]["retained_channel"])

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
