#!/usr/bin/env python
"""P0-A regression tests: a validated supplied domain is never silently replaced.

The invariant: a domain a person supplied for an account, once it holds up as
that account's own site, stays the target domain. Auto-discovery may propose a
correction, but a portal, marketplace or news site must NEVER be promoted to
"official website" merely because it names the company.

The 红旗 case is the reason these exist, but nothing here special-cases it: the
same asserts run over Latin, Cyrillic and Japanese fixtures built the same way.

No network, no model call.  .venv/bin/python test_target_domain.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import research_service as rs                                    # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (" - " + detail if detail else ""))


# --------------------------------------------------------------------------
# Fixtures. Each pair is (company's own site served in a DIFFERENT script from
# its name, portal that covers the company in the company's OWN script).
# --------------------------------------------------------------------------

OWN_SITE = (
    "{brand} AUTO OFFICIAL WEBSITE\n"
    "Home Models Products Store Events about {lower} Contact us\n"
    "Explore the range. Book a test drive. Find a dealer.\n"
    + ("Our vehicles combine design and engineering. " * 40)
)

PORTAL = (
    "{portal_brand} - car prices, reviews and comparisons\n"
    "Search  Download the app  New cars  Used cars  Dealers  News\n"
    + ("Compare trims and prices across every major brand. " * 30)
    + "\n{target} model listing: guide price, configuration, reviews.\n"
    + ("Related brands and models you may also like. " * 30)
)

CASES = [
    # name,   name_cn/local,  own domain,               portal domain,   brand stem, portal brand
    ("红旗",   "红旗",          "https://www.hongqi-auto.com", "https://pcauto.com.cn",
     "HONGQI", "PCauto 太平洋汽车"),
    ("Спутник", "Спутник",     "https://www.sputnik-motors.com", "https://autoportal.ru",
     "SPUTNIK", "AutoPortal"),
    ("いすゞ",  "いすゞ",        "https://www.isuzu-global.com", "https://carsensor.net",
     "ISUZU",  "CarSensor"),
]

print("\n[1] A company's own site validates even when its page never writes the name")
for name, cn, own, _portal, stem, _pb in CASES:
    text = OWN_SITE.format(brand=stem, lower=stem.lower())
    check("%s: the page really does not contain the name" % name,
          name not in text, "fixture would be meaningless otherwise")
    ok, sigs = rs.identity_signals(own, "", name, cn, text, supplied=True)
    check("%s: supplied domain establishes identity" % name, ok, str(sigs))
    check("%s: and it does so by self-corroboration" % name,
          any(s.startswith("domain-self-corroborated") for s in sigs), str(sigs))

print("\n[2] The same page does NOT confer official status on a discovered domain")
for name, cn, own, _portal, stem, _pb in CASES:
    text = OWN_SITE.format(brand=stem, lower=stem.lower())
    ok, sigs = rs.identity_signals(own, "", name, cn, text, supplied=False)
    check("%s: discovery gets no self-corroboration benefit" % name, not ok, str(sigs))

print("\n[3] A portal that merely covers the company is never its official site")
for name, cn, _own, portal, _stem, pb in CASES:
    text = PORTAL.format(portal_brand=pb, target=name)
    check("%s: the portal really does name the company" % name,
          name in text, "fixture would be meaningless otherwise")
    ok, sigs = rs.identity_signals(portal, "", name, cn, text, supplied=False)
    check("%s: portal rejected as discovered official site" % name, not ok, str(sigs))
    # And the old, wide rule would have accepted it - this is the regression.
    wide = "{} {}".format("", text[:4000]).lower()
    check("%s: the pre-fix rule WOULD have accepted it" % name,
          (name.lower() in wide) or (cn and cn in wide), "otherwise nothing was fixed")

print("\n[4] The identity region is masthead, not contents")
text = PORTAL.format(portal_brand="PCauto", target="红旗")
reg = rs.identity_region("", text)
check("target appears in the page", "红旗" in text)
check("target does NOT appear in the identity region", "红旗" not in reg)
check("the portal's own brand DOES", "pcauto" in reg)
check("identity region is bounded", len(reg) <= rs.IDENTITY_HEAD + 400, str(len(reg)))

print("\n[5] Ordinary Latin accounts are unaffected")
CORP = ("Verkor - Low carbon batteries for Europe\nAbout Contact Products Solutions\n"
        + "Verkor designs and manufactures low carbon batteries. " * 40)
for supplied in (True, False):
    ok, sigs = rs.identity_signals("https://verkor.com", "", "Verkor", "", CORP,
                                   supplied=supplied)
    check("Verkor validates (supplied=%s)" % supplied, ok, str(sigs))
# Domain spells out every token: identity without the page saying so.
THIN = "Home\nAbout\nContact\nProducts\n" + ("Industrial systems for demanding sites. " * 40)
ok, sigs = rs.identity_signals("https://energytechsolution.com", "",
                               "Energy Tech Solution Co., Ltd.", "", THIN, supplied=False)
check("a domain spelling out the whole name is identity on its own", ok, str(sigs))
check("and it is recorded as such", "domain-covers-name" in sigs, str(sigs))

print("\n[6] A generic domain plus page furniture is still not identity")
GENERIC = "Home About Contact Products Solutions\n" + ("Automation for industry. " * 60)
ok, sigs = rs.identity_signals("https://automation.com", "", "ACRO Automation Systems",
                               "", GENERIC, supplied=False)
check("automation.com is not ACRO", not ok, str(sigs))

print("\n[7] resolve_website never swaps a domain silently")


def fake_progress(*_a, **_k):
    pass


class Recorder(object):
    def __init__(self):
        self.lines = []

    def __call__(self, _stage, msg, **_k):
        self.lines.append(msg)


def with_stubs(fetch, discover, fn):
    real_fetch, real_disc = rs.fetch_page_text, rs.discover_official_website
    rs.fetch_page_text, rs.discover_official_website = fetch, discover
    try:
        return fn()
    finally:
        rs.fetch_page_text, rs.discover_official_website = real_fetch, real_disc


own_text = OWN_SITE.format(brand="HONGQI", lower="hongqi")
portal_text = PORTAL.format(portal_brand="PCauto", target="红旗")

# 7a. The supplied domain holds up -> discovery must not even be consulted.
called = {"n": 0}


def never(*_a, **_k):
    called["n"] += 1
    return {"website": "https://pcauto.com.cn", "status": "auto_discovered",
            "score": 9, "signals": [], "text": portal_text}


out = with_stubs(lambda u, **k: (own_text, "standard"), never,
                 lambda: rs.resolve_website("红旗", "红旗", "https://www.hongqi-auto.com",
                                            {}, 10, fake_progress))
check("supplied domain is kept", "hongqi-auto.com" in out["website"], out["website"])
check("status says provided", out["status"] == "provided", out["status"])
check("discovery was never called", called["n"] == 0, str(called["n"]))

# 7b. Supplied domain does not hold up, discovery finds something -> RECORDED.
rec = Recorder()
out = with_stubs(lambda u, **k: ("Totally unrelated shop. Buy shoes online. " * 40, "standard"),
                 lambda *a, **k: {"website": "https://bmw.com.cn", "status": "auto_discovered",
                                  "score": 9, "signals": [], "text": "BMW China"},
                 lambda: rs.resolve_website("BMW", "", "https://bwm.com", {}, 10, rec))
check("a genuinely wrong domain is still corrected", "bmw.com.cn" in out["website"],
      out["website"])
check("the supplied domain is preserved on the result",
      out.get("supplied_website") == "https://bwm.com", str(out.get("supplied_website")))
check("the substitution is flagged", out.get("replaced_supplied") is True)
check("and warned about in progress",
      any("WARN" in l and "bwm.com" in l for l in rec.lines), str(rec.lines[-2:]))

# 7c. Supplied fails, discovery finds nothing -> keep the user's domain.
rec = Recorder()
out = with_stubs(lambda u, **k: ("Unrelated. " * 200, "standard"),
                 lambda *a, **k: {"website": "", "status": "unverified", "score": 0,
                                  "signals": [], "text": ""},
                 lambda: rs.resolve_website("Northwind Devices", "",
                                            "https://nwd-holdings-intl.com", {}, 10, rec))
check("the user's domain survives when nothing better is found",
      out["website"] == "https://nwd-holdings-intl.com", out["website"])
check("labelled unconfirmed", out["status"] == "supplied_unconfirmed", out["status"])
check("not marked as a replacement", out.get("replaced_supplied") is False)
check("continuation, not failure", bool(out["website"]))

print("\n[8] The four domains stay distinguishable")
check("supplied_website is carried separately from website",
      "supplied_website" in out and out["supplied_website"] != "" and
      out["supplied_website"] == out["website"],
      "supplied=%s website=%s status=%s" % (out.get("supplied_website"),
                                            out.get("website"), out.get("status")))
own = with_stubs(lambda u, **k: (own_text, "standard"), never,
                 lambda: rs.resolve_website("红旗", "红旗", "https://www.hongqi-auto.com",
                                            {}, 10, fake_progress))
check("a validated supplied domain records no replacement",
      not own.get("replaced_supplied"), str(own.get("replaced_supplied")))

print("\n{} passed, {} failed".format(len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
