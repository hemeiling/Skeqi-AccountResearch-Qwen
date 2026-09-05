#!/usr/bin/env python
"""P0-B regression tests: one publisher cannot consume the whole evidence set.

The rule these protect: retention admits qualified evidence across registrable
domains FIRST, then backfills remaining capacity from domains already present.
Diversity is a preference inside retention, never a quota that can fail a run -
an account with only one useful domain still produces a full report.

No network, no model call.  .venv/bin/python test_evidence_diversity.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import research_service as rs                                    # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (" - " + detail if detail else ""))


def item(url, tier, source_type, official=False, verified=False, topics=()):
    return {"url": url, "tier": tier, "source_type": source_type, "official": official,
            "content_verified": verified, "topics": list(topics), "title": url}


def domains_of(items):
    out = {}
    for e in items:
        d = rs.registrable_domain(e["url"])
        out[d] = out.get(d, 0) + 1
    return out


print("\n[1] Registrable domain is the boundary, not hostname")
SUBS = ["https://pcauto.com.cn/a", "https://m.pcauto.com.cn/b", "https://price.pcauto.com.cn/c",
        "https://www.pcauto.com.cn/d"]
check("all four subdomains collapse to one publisher",
      len({rs.registrable_domain(u) for u in SUBS}) == 1,
      str({rs.registrable_domain(u) for u in SUBS}))
check("and that publisher is named correctly",
      rs.registrable_domain(SUBS[1]) == "pcauto.com.cn", rs.registrable_domain(SUBS[1]))
check("multi-part public suffixes are handled",
      rs.registrable_domain("https://news.bbc.co.uk/x") == "bbc.co.uk",
      rs.registrable_domain("https://news.bbc.co.uk/x"))
check("ordinary two-part domains are unchanged",
      rs.registrable_domain("https://ir.siemens.com/x") == "siemens.com")
check("a malformed url does not explode", rs.registrable_domain("not a url") == "")

print("\n[2] 16 strong sources from one domain + qualified third-party")
site = [item("https://acme.com/p%d" % i, 1, "official-website", official=True)
        for i in range(16)]
web = [item("https://reuters.com/a", 3, "news"),
       item("https://ft.com/b", 3, "news"),
       item("https://gov.uk/c", 2, "government"),
       item("https://bloomberg.com/d", 3, "financial")]
ret, drop = rs.apply_evidence_caps(site, web)
d = domains_of(ret)
check("the set is still full", len(ret) == rs.MAX_EVIDENCE_ITEMS, str(len(ret)))
check("third-party evidence survives", any(not e["official"] for e in ret),
      str(sorted(d.items())))
check("all four third-party sources survive",
      sum(1 for e in ret if not e["official"]) == 4, str(sorted(d.items())))
check("the dominant domain no longer consumes the whole set",
      d.get("acme.com") < rs.MAX_EVIDENCE_ITEMS, str(d))
check("every other publisher present is a distinct one",
      len(d) == 5, str(d))
check("nothing third-party was dropped",
      not any(rs.registrable_domain(e["url"]) != "acme.com" for e in drop),
      str([e["url"] for e in drop][:3]))
# The pre-fix behaviour is the regression this guards against.
check("before the fix the official domain would have taken every slot",
      len(site) >= rs.MAX_EVIDENCE_ITEMS, "fixture would not demonstrate anything")

print("\n[3] Only one qualified domain exists - backfill, never a quota failure")
solo = [item("https://solo.com/p%d" % i, 1, "official-website", official=True)
        for i in range(20)]
ret, drop = rs.apply_evidence_caps(solo, [])
check("a thin account still fills the evidence set",
      len(ret) == rs.MAX_EVIDENCE_ITEMS, str(len(ret)))
check("all of it from the one domain that had anything to say",
      domains_of(ret) == {"solo.com": rs.MAX_EVIDENCE_ITEMS}, str(domains_of(ret)))
check("and it is a report, not a failure", len(ret) > 0)
# Backfill must not be reachable only by luck of ordering.
mixed = [item("https://solo.com/x%d" % i, 1, "official-website", official=True)
         for i in range(12)] + [item("https://one-other.com/y", 3, "news")]
ret2, _ = rs.apply_evidence_caps(mixed, [])
check("the single other domain is admitted before backfill",
      "one-other.com" in domains_of(ret2), str(domains_of(ret2)))
check("and the remaining capacity is still used",
      len(ret2) == 13, str(len(ret2)))

print("\n[3b] With plenty of diverse evidence the pass-1 cap is exactly observable")
# Capacity is filled by diverse evidence, so backfill never runs and the pass-1
# ceiling is what survives. This is the only arrangement in which the constant
# itself is directly visible.
many = [item("https://acme.com/p%d" % i, 1, "official-website", official=True)
        for i in range(20)]
wide = [item("https://pub%02d.com/x" % i, 2, "government") for i in range(20)]
ret, _ = rs.apply_evidence_caps(many, wide)
d = domains_of(ret)
check("the dominant domain stops at the pass-1 cap",
      d.get("acme.com") == rs.DIVERSITY_PER_DOMAIN, str(sorted(d.items())[:4]))
check("the rest of the capacity went to other publishers",
      len(d) == 1 + (rs.MAX_EVIDENCE_ITEMS - rs.DIVERSITY_PER_DOMAIN), str(len(d)))
check("and the set is full", len(ret) == rs.MAX_EVIDENCE_ITEMS, str(len(ret)))

print("\n[4] Subdomains of one publisher share the cap")
portal = [item("https://%s.portal.com.cn/p%d" % (sub, i), 1, "official-website",
               official=True)
          for i, sub in enumerate(["www", "m", "price", "news", "auto", "shop",
                                   "used", "buy", "cars", "deals", "a", "b"])]
extra = [item("https://elsewhere.com/z", 3, "news")]
# Enough other publishers to consume the remaining capacity, so the portal's
# pass-1 ceiling is observable rather than hidden by backfill.
others = [item("https://pub%02d.com/x" % i, 3, "news") for i in range(10)]
ret, _ = rs.apply_evidence_caps(portal, others)
d = domains_of(ret)
check("the portal cannot exceed the cap by spreading across subdomains",
      d.get("portal.com.cn") == rs.DIVERSITY_PER_DOMAIN, str(sorted(d.items())[:3]))
check("its 12 subdomains bought it nothing over a single host",
      d.get("portal.com.cn") < len(portal), str(d.get("portal.com.cn")))
ret2, _ = rs.apply_evidence_caps(portal, extra)
check("a lone other publisher still gets in",
      "elsewhere.com" in domains_of(ret2), str(domains_of(ret2)))

print("\n[5] The official domain is not globally downgraded")
site = [item("https://acme.com/p%d" % i, 1, "official-website", official=True)
        for i in range(4)]
web = [item("https://n%d.com/x" % i, 3, "news") for i in range(4)]
ret, drop = rs.apply_evidence_caps(site, web)
check("a modest official presence is untouched",
      domains_of(ret).get("acme.com") == 4, str(domains_of(ret)))
check("nothing was dropped at all", not drop, str(len(drop)))
check("official sources still sort first",
      all(e["official"] for e in ret[:4]), str([e["official"] for e in ret]))
check("ids are contiguous from 1",
      [e["id"] for e in ret] == list(range(1, len(ret) + 1)), str([e["id"] for e in ret]))

print("\n[6] The verified third-party low-tier exemption still works")
strong = [item("https://acme.com/p%d" % i, 1, "official-website", official=True)
          for i in range(5)]                       # >=5 strong => low_budget is 2
lowt = [item("https://fin%d.cn/x" % i, 6, "financial-portal", verified=True)
        for i in range(4)]
ret, drop = rs.apply_evidence_caps(strong, lowt)
kept_low = [e for e in ret if e["tier"] >= 6]
check("verified third-party low-tier sources are exempt from the budget",
      len(kept_low) == 4, str(len(kept_low)))
check("and none was dropped for the budget",
      not any(e.get("drop_reason") == "low-tier budget" for e in drop),
      str([e.get("drop_reason") for e in drop]))
# Unverified low-tier is still budgeted, exactly as before.
lowu = [item("https://junk%d.cn/x" % i, 6, "job-board") for i in range(6)]
ret2, drop2 = rs.apply_evidence_caps(strong, lowu)
check("unverified low-tier is still budgeted",
      sum(1 for e in ret2 if e["tier"] >= 6) == 2,
      str(sum(1 for e in ret2 if e["tier"] >= 6)))
check("and the surplus is dropped for the budget",
      any(e.get("drop_reason") == "low-tier budget" for e in drop2),
      str([e.get("drop_reason") for e in drop2]))

print("\n[7] Diversity is not a generation gate")
ret, _ = rs.apply_evidence_caps([item("https://only.com/a", 1, "official-website",
                                      official=True)], [])
check("a single source still produces retained evidence", len(ret) == 1)
ret, drop = rs.apply_evidence_caps([], [])
check("no evidence at all is handled without raising", ret == [] and drop == [])

print("\n[8] Sufficiency counts publishers, not hostnames")
port = [item("https://pcauto.com.cn/a", 3, "news"),
        item("https://m.pcauto.com.cn/b", 3, "news"),
        item("https://price.pcauto.com.cn/c", 3, "news"),
        item("https://auto.pcauto.com.cn/d", 3, "news")]
st = rs.evidence_sufficiency(port, "acme.com")
check("four subdomains of one portal count as one host",
      st["third_party_hosts"] == 1, str(st))
check("and that is not enough to stop retrieving", not st["enough"], str(st))
real = [item("https://reuters.com/a", 3, "news"), item("https://ft.com/b", 3, "news"),
        item("https://gov.uk/c", 2, "government"), item("https://bloomberg.com/d", 3,
                                                        "financial")]
st2 = rs.evidence_sufficiency(real, "acme.com")
check("four genuinely different publishers do count",
      st2["third_party_hosts"] == 4, str(st2))

print("\n{} passed, {} failed".format(len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
