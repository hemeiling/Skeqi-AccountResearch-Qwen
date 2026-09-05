#!/usr/bin/env python
"""P0-C regression tests: cross-script aliases and short-brand domain identity.

The two rules these protect:

  C1  An account written in one script and covered in another must still be
      recognisable - but only through a spelling the ACCOUNT ITSELF asserts,
      never a guess and never a transliteration table.
  C2  A short brand whose site cannot be read is identified by CORROBORATION -
      the domain's whole label IS the company's distinctive word - not by
      lowering a length threshold, which would admit industry words.

Neither rule may weaken collision protection, the target/ecosystem provenance
split, P0-A, P0-B, or parent/group vs brand separation.

No network, no model call.  .venv/bin/python test_identity_bridge.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import research_service as rs                                    # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (" - " + detail if detail else ""))


print("\n[1] C1 - the alias comes from the account's own domain")
for name, cn, site, want in [
        ("红旗", "红旗", "https://www.hongqi-auto.com", "hongqi"),
        ("Спутник", "Спутник", "https://sputnik-motors.com", "sputnik"),
        ("いすゞ", "いすゞ", "https://isuzu-global.com", "isuzu")]:
    a = rs.derive_aliases(name, cn, site)
    check("%s -> %s" % (name, want), want in a, str(a))

print("\n[2] C1 - industry words never become an alias")
for name, site in [("红旗", "https://auto-motors.com"),
                   ("某公司", "https://industrial-systems.com"),
                   ("某公司", "https://smart-tech.com")]:
    a = rs.derive_aliases(name, name, site)
    check("no alias from %s" % site, a == [], str(a))
check("a token already in the name is not re-added as an alias",
      rs.derive_aliases("Ford Motor Company", "", "https://ford.com") == [],
      str(rs.derive_aliases("Ford Motor Company", "", "https://ford.com")))
check("no website means no alias", rs.derive_aliases("红旗", "红旗", "") == [])

print("\n[3] C1 - an alias must be corroborated when the site text is available")
OWN = "HONGQI AUTO OFFICIAL WEBSITE\nHome Models Products about hongqi\n" + "x " * 300
check("corroborated by the identity region",
      "hongqi" in rs.derive_aliases("红旗", "红旗", "https://hongqi-auto.com", OWN))
OTHER = "WELCOME TO SOMETHING ELSE ENTIRELY\nHome About\n" + "y " * 300
check("a site that never names its own stem yields no alias",
      rs.derive_aliases("红旗", "红旗", "https://hongqi-auto.com", OTHER) == [],
      str(rs.derive_aliases("红旗", "红旗", "https://hongqi-auto.com", OTHER)))

print("\n[4] C1 - the alias actually rescues real evidence")
PAGE = ("FAW Hongqi Manufacturing Center's Fanrong Plant. The plant runs a fully "
        "automated welding line with hundreds of robots. " * 6)
check("the page genuinely never writes the CJK name", "红旗" not in PAGE)
ok, why = rs.identity_ok(PAGE, "红旗", "红旗", "", "https://gojilin.gov.cn/x")
check("rejected without the alias (the measured defect)", not ok, why)
ok2, why2 = rs.identity_ok(PAGE, "红旗", "红旗", "", "https://gojilin.gov.cn/x", ["hongqi"])
check("accepted with the alias", ok2, why2)
check("and it is recorded as an alias match", "alias match" in why2, why2)
check("alias evidence is TARGET provenance, not market",
      rs.provenance_for_reason(why2) == rs.PROV_TARGET, rs.provenance_for_reason(why2))

print("\n[5] C1 - collision protection is NOT weakened")
saved = rs.COLLISIONS.get("acme", None)
rs.COLLISIONS["acme"] = ["acme brick"]
try:
    ok, why = rs.identity_ok("Acme Brick Company of Texas ships pallets", "Acme", "",
                             "", "https://x.com/a", ["acme"])
    check("a known collision is still rejected even with an alias", not ok, why)
    check("and it is reported as a collision", "unrelated entity" in why, why)
finally:
    if saved is None:
        rs.COLLISIONS.pop("acme", None)
    else:
        rs.COLLISIONS["acme"] = saved

print("\n[6] C1 - an alias does not make unrelated text match")
check("an unrelated page is still rejected",
      not rs.identity_ok("Weather forecast for the weekend. " * 20, "红旗", "红旗",
                         "", "https://n.com/x", ["hongqi"])[0])

print("\n[7] C2 - a short brand is identified by corroboration, not a lower floor")
check("ford.com IS Ford Motor Company",
      rs._domain_identity_strong("https://ford.com", "Ford Motor Company")
      == "domain-is-name:ford",
      rs._domain_identity_strong("https://ford.com", "Ford Motor Company"))
for url, nm, why in [("https://auto.com", "Generic Auto Company", "industry word"),
                     ("https://fordparts-uk.com", "Ford Motor Company", "label is not the token"),
                     ("https://motors.com", "Ford Motor Company", "industry word"),
                     ("https://ford-dealer-nyc.com", "Ford Motor Company", "label is not the token")]:
    check("%s rejected (%s)" % (url, why),
          not rs._domain_identity_strong(url, nm),
          rs._domain_identity_strong(url, nm))
check("a longer distinctive stem still works the old way",
      rs._domain_identity_strong("https://verkorgigafactory.com", "Verkor").startswith("domain~name"),
      rs._domain_identity_strong("https://verkorgigafactory.com", "Verkor"))

print("\n[8] C2 - only a SUPPLIED domain gets the benefit")
class _Stub(object):
    def __init__(self, method): self.method = method
    def __call__(self, url, **kw): return "", self.method

real = rs.fetch_page_text
try:
    rs.fetch_page_text = _Stub("failed")
    v_sup = rs.validate_website("https://ford.com", "Ford Motor Company", "", supplied=True)
    v_dis = rs.validate_website("https://ford.com", "Ford Motor Company", "", supplied=False)
    check("supplied + unreadable + domain-is-name -> validates", v_sup["ok"], str(v_sup["signals"]))
    check("discovered gets NO such benefit", not v_dis["ok"], str(v_dis.get("identity")))
    rs.fetch_page_text = _Stub("unreachable")
    v_un = rs.validate_website("https://auto.com", "Generic Auto Company", "", supplied=True)
    check("an industry-word domain never validates on failure alone", not v_un["ok"])
finally:
    rs.fetch_page_text = real

print("\n[9] Parent/group vs brand separation is preserved")
check("a group domain does not become the brand's identity",
      not rs._domain_identity_strong("https://fawgroup.com", "Hongqi"),
      rs._domain_identity_strong("https://fawgroup.com", "Hongqi"))
check("nor the brand domain the group's",
      not rs._domain_identity_strong("https://hongqi-auto.com", "FAW Group"),
      rs._domain_identity_strong("https://hongqi-auto.com", "FAW Group"))

print("\n[10] Ecosystem provenance still outranked by target verification")
reg = rs.EcosystemRegistry("红旗", "红旗")
ok, why, prov, ent = rs.verify_identity(PAGE, "红旗", "红旗", "", "https://gojilin.gov.cn/x",
                                        reg, ["hongqi"])
check("an alias match is target, never ecosystem", ok and prov == rs.PROV_TARGET,
      "%s/%s" % (ok, prov))
check("and no entity is invented", ent is None, str(ent))

print("\n{} passed, {} failed".format(len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
