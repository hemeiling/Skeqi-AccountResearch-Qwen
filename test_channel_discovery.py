# -*- coding: utf-8 -*-
"""Who sells on the ACCOUNT's behalf, and the line that keeps it honest.

Representation must be STATED. An integrator that installs the account's
equipment, a company that partners with it, a directory that merely lists it -
none of those represent it, and promoting any of them into "distributor" is the
single error this module exists to prevent.

The mirror of competitor discovery: there the page need not mention the account,
here it must, because a distributor page exists to say whose products it carries.

Runs against stored ACRO evidence where available plus mocked search and fetch.
No network, no model call.  .venv/bin/python test_channel_discovery.py
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import app                                                        # noqa: E402
import channel_discovery as ch                                    # noqa: E402
import offering_profile as op                                     # noqa: E402
import provider_view as pv                                        # noqa: E402
import research_service as rs                                     # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (" - " + detail if detail else ""))


ACCOUNT = "ACRO Automation Systems"
CACHE = os.path.join(
    "/private/tmp/claude-501/-Users-meilinghe-Downloads-Qwen-API-Search-----",
    "1c06afbf-82e9-4c76-8bed-53680bf7d688/scratchpad/evidence_cache_before",
    "acro-automation-systems_7096d244d2875e57.json")


def ev(url, title, text, official=False, domain=""):
    return {"url": url, "title": title, "text": text, "official": official,
            "domain": domain or url.split("/")[2], "tier": 1 if official else 3,
            "source_type": "official-website" if official else "web"}


if os.path.exists(CACHE) and not os.environ.get("CHANNEL_FIXTURE_ONLY"):
    STORED = json.load(io.open(CACHE, encoding="utf-8"))["evidence"]
else:
    STORED = [ev("https://www.acro.com/", ACCOUNT,
                 "ACRO Automation Systems designs and builds custom automated "
                 "assembly systems, robotic welding cells and machine vision "
                 "inspection for automotive manufacturers. Our sales team works "
                 "with every customer. Milwaukee, WI.", official=True,
                 domain="acro.com")]

PROFILE = op.build_profile(STORED, ACCOUNT, "acro.com")

PAGES = {
    "https://midwest-dist.com/lines": (
        "Midwest Industrial Distribution is the authorized distributor for ACRO "
        "Automation Systems in the Upper Midwest. We stock and support their "
        "assembly and welding equipment."),
    "https://ontario-rep.com/about": (
        "Ontario Automation Group represents ACRO Automation Systems throughout "
        "Ontario and Quebec."),
    "https://integrator.com/about": (
        "Lakeside Systems is a system integrator. We integrate ACRO Automation "
        "Systems equipment into our customers' production lines."),
    "https://directory.com/listing": (
        "ACRO Automation Systems, Milwaukee WI. Automation equipment. "
        "Category: assembly systems."),
    "https://buyer.com/news": (
        "Northwind Motors purchased three assembly systems from ACRO Automation "
        "Systems for its Michigan plant."),
    "https://unrelated.com/news": (
        "Vector Robotics is an authorized distributor for a leading robot brand "
        "across North America."),
    "https://acro.com/partners": (
        "ACRO Automation Systems partners with technology leaders."),
    "https://blank.com/x": "",
}
FETCHED = []
_real_fetch = rs.fetch_page_text


def fake_fetch(url, *a, **k):
    FETCHED.append(url)
    if url not in PAGES:
        raise RuntimeError("unreachable")
    return PAGES[url], "mock"


rs.fetch_page_text = fake_fetch


def hit(url, title=""):
    return {"url": url, "title": title, "snippet": "snippet", "domain": url.split("/")[2]}


class FakeClient(object):
    def __init__(self, results, fail_on=()):
        self.results, self.fail_on, self.queries = results, set(fail_on), []

    def search(self, query):
        self.queries.append(query)
        if query in self.fail_on:
            raise RuntimeError("boom")
        return list(self.results)


def run(hits, profile=PROFILE, account=ACCOUNT, name_cn="", fail_on=()):
    sink = []
    client = FakeClient(hits, fail_on)
    verify = rs.channel_verifier(profile, account, name_cn,
                                 rs.derive_aliases(account, name_cn, "acro.com"), sink)
    ev_, cov = ch.discover(client, profile, account, verify, name_cn=name_cn)
    return sink, ev_, cov, client


print("\n[1] Representation must be stated, in so many words")
for text, expect in (
        (PAGES["https://midwest-dist.com/lines"], ch.AUTHORIZED_DISTRIBUTOR),
        (PAGES["https://ontario-rep.com/about"], ch.REPRESENTATIVE),
        (PAGES["https://integrator.com/about"], ch.SYSTEM_INTEGRATOR),
        (PAGES["https://directory.com/listing"], ch.NOT_A_CHANNEL),
        (PAGES["https://unrelated.com/news"], ch.NOT_A_CHANNEL)):
    role = ch.classify_role(text, ACCOUNT)[0]
    check("%s -> %s" % (expect.lower().replace("_", " "), role), role == expect, role)
check("a distributor of SOMEONE ELSE is not this account's channel",
      ch.classify_role(PAGES["https://unrelated.com/news"], ACCOUNT)[0] == ch.NOT_A_CHANNEL,
      "the account must appear inside the statement")
check("an integrator is a partner, never a distributor",
      ch.classify_role(PAGES["https://integrator.com/about"], ACCOUNT)[0]
      not in ch.CHANNEL_ROLES)
check("authorization is recorded when stated",
      ch.classify_role(PAGES["https://midwest-dist.com/lines"], ACCOUNT)[1] is True)
check("and not invented when it is not",
      ch.classify_role(PAGES["https://ontario-rep.com/about"], ACCOUNT)[1] is False)
check("territory comes from the sentence",
      ch.classify_role(PAGES["https://midwest-dist.com/lines"], ACCOUNT)[2] == "Upper Midwest",
      str(ch.classify_role(PAGES["https://midwest-dist.com/lines"], ACCOUNT)[2]))
check("the stated words are quoted back",
      "authorized distributor" in
      ch.classify_role(PAGES["https://midwest-dist.com/lines"], ACCOUNT)[3].lower())
CN = "本公司为 思客琦 授权经销商，覆盖华东地区。"
check("Chinese representation is recognised",
      ch.classify_role(CN, "SKEQI", "思客琦")[0] == ch.AUTHORIZED_DISTRIBUTOR,
      ch.classify_role(CN, "SKEQI", "思客琦")[0])

print("\n[2] The account name is IN the query, unlike competitor discovery")
plan = ch.plan_intents(PROFILE, ACCOUNT, batch=1)
check("every query names the account", all(ACCOUNT in q for _k, q in plan), str(plan))
check("a Chinese intent is added only when a Chinese name exists",
      not any("经销商" in q for _k, q in ch.plan_intents(PROFILE, ACCOUNT, batch=2))
      and any("经销商" in q for _k, q in
              ch.plan_intents(PROFILE, ACCOUNT, "思客琦", batch=2)))
check("a representative-led account is asked about reps first",
      ch.plan_intents({"go_to_market_model": op.REPRESENTATIVE_LED}, ACCOUNT,
                      batch=1)[0][0] == "representative")
check("everyone else is asked about distributors first", plan[0][0] == "authorized distributor")
check("no account name means no query", ch.plan_intents(PROFILE, "", batch=1) == [])

print("\n[3] Discovery is bounded and stops early")
comp, evd, cov, client = run([hit("https://midwest-dist.com/lines", "Midwest"),
                              hit("https://ontario-rep.com/about", "Ontario")])
check("searches ran", cov["search_count"] >= 1)
check("the ceiling holds", cov["search_count"] <= ch.MAX_CHANNEL_SEARCHES,
      str(cov["search_count"]))
check("it stopped once two entities were verified",
      cov["channel_entities"] >= ch.ENOUGH_CHANNEL_ENTITIES
      and cov["search_count"] < ch.MAX_CHANNEL_SEARCHES,
      "%d entities in %d searches" % (cov["channel_entities"], cov["search_count"]))
check("queries are recorded as issued", cov["queries"] == client.queries)
empty_client = FakeClient([])
sink = []
_, cov0 = ch.discover(empty_client, PROFILE, ACCOUNT,
                      rs.channel_verifier(PROFILE, ACCOUNT, "", (), sink))
check("zero marginal yield stops after the first batch",
      cov0["search_count"] <= ch.BATCH_1, str(cov0["search_count"]))
check("and nothing is invented from nothing", not sink)

print("\n[4] Rows carry the role, not a guess")
by_name = {c["organization_name"]: c for c in comp}
check("two organisations were retained", len(comp) == 2, str(list(by_name)))
check("every row states representation",
      all(c["is_representation"] for c in comp))
check("provenance is ecosystem, kept separate from the role",
      all(c["provenance"] == rs.PROV_ECOSYSTEM for c in comp))
check("no competitor vocabulary leaks in",
      not any(c.get("competition_type") for c in comp))
for f in ("organization_name", "organization_key", "role", "authorized", "territory",
          "evidence_quote", "source_urls", "source_domains", "provenance", "confidence"):
    check("row carries %s" % f, all(f in c for c in comp))

print("\n[5] A partner is recorded as a partner")
p_sink, p_ev, p_cov, _ = run([hit("https://integrator.com/about", "Lakeside Systems")])
check("the integrator was kept", len(p_sink) == 1, str(p_sink))
check("but NOT as representation", p_sink[0]["is_representation"] is False)
check("it does not count as a channel entity", p_cov["channel_entities"] == 0)
check("it counts as a partner", p_cov["partners"] == 1)

print("\n[6] Mentioning the account is not representing it")
d_sink, d_ev, d_cov, _ = run([hit("https://buyer.com/news", "Northwind Motors")])
check("a customer that merely bought from the account is rejected",
      not d_sink, str(d_sink))
check("and the rejection is counted", d_cov["rejected_no_representation"] >= 1,
      str(d_cov["rejected_no_representation"]))
check("no evidence is retained from it", not d_ev)
l_sink, l_ev, _, _ = run([hit("https://directory.com/listing", "Directory")])
check("a directory listing OF the account is not a channel entity",
      not l_sink and not l_ev, str(l_sink))
FETCHED[:] = []
b_sink, b_ev, b_cov, _ = run([hit("https://blank.com/x", "Big Distributor")])
check("the page is fetched before anything is believed", "https://blank.com/x" in FETCHED)
check("an empty body yields nothing", not b_sink and not b_ev)
a_sink, _, _, _ = run([hit("https://acro.com/partners", ACCOUNT)])
check("the account is never its own channel", not a_sink, str(a_sink))

print("\n[7] Gating follows go-to-market CONFIDENCE, not the label")
STRONG_DIRECT = op.build_profile(
    [ev("https://s.com/", "How we sell",
        "S Corp designs and builds custom automated assembly systems and robotic "
        "welding cells for automotive manufacturers. We sell directly to customers.",
        official=True, domain="s.com")], "S Corp", "s.com")
s_sink, s_ev, s_cov, s_client = run([hit("https://midwest-dist.com/lines", "M")],
                                    profile=STRONG_DIRECT, account="S Corp")
check("strongly corroborated DIRECT issues no search",
      s_client.queries == [] and s_cov["search_count"] == 0)
check("used stays false", s_cov["used"] is False)
check("and a reason is recorded", "corroborated as DIRECT" in (s_cov["skip_reason"] or ""),
      str(s_cov["skip_reason"]))
check("ACRO's medium-confidence DIRECT still searches",
      PROFILE["go_to_market_model"] == op.DIRECT
      and PROFILE["go_to_market_confidence"] in ("low", "medium")
      and cov["search_count"] > 0,
      "%s/%s" % (PROFILE["go_to_market_model"], PROFILE["go_to_market_confidence"]))
THIN = op.build_profile([ev("https://t.com/a", "T", "T Inc exists.", domain="t.com")],
                        "T Inc", "")
t_sink, t_ev, t_cov, t_client = run([hit("https://midwest-dist.com/lines", "M")],
                                    profile=THIN, account="T Inc")
check("a thin profile skips without searching", t_client.queries == [])
check("and says the account is not understood, not that no channel exists",
      "account understanding" in (t_cov["skip_reason"] or ""), str(t_cov["skip_reason"]))

print("\n[8] Absence is stated, never filled in")
# PROFILE is channel-ready, so an empty result here means searched-and-empty.
block = pv.channel_prompt_block([], PROFILE)
check("the absence sentence is verbatim EN", pv.NO_CHANNEL_EN in block)
check("the absence sentence is verbatim ZH", pv.NO_CHANNEL_ZH in block)
check("and it says discovery was performed",
      "performed" in block and "NOT PERFORMED" not in block)
check("general knowledge is forbidden", "general knowledge" in block.lower())
check("a low or medium model is flagged as unsettled",
      "NOT firmly established" in block)
check("no organisation is named", "Midwest" not in block)
full = pv.channel_prompt_block(comp + p_sink, PROFILE)
check("verified channel entities are listed", "Midwest Industrial Distribution" in full)
check("their role and territory travel with them",
      "AUTHORIZED_DISTRIBUTOR" in full and "Upper Midwest" in full)
check("partners are listed separately as partners",
      "NOT channel" in full and "Lakeside Systems" in full)
check("a strongly corroborated model is not flagged as unsettled",
      "NOT firmly established" not in pv.channel_prompt_block([], STRONG_DIRECT))

print("\n[9] The report has a section that means this")
SRC = io.open(os.path.join(HERE, "research_service.py"), encoding="utf-8").read()
check("the heading exists", "## Distributors & Channel Partners / 分销与渠道伙伴" in SRC)
i = SRC.index("## Distributors & Channel Partners")
sec = SRC[i:SRC.index("## Existing Automation Providers", i)]
check("it is scoped to the account's outbound channel", "outbound channel" in sec)
check("it forbids absence reasoning", "absence of" in sec.lower())
check("it carries the absence wording in both languages",
      pv.NO_CHANNEL_EN in sec and pv.NO_CHANNEL_ZH in sec)
check("and the not-performed wording, kept distinct",
      pv.NOT_SEARCHED_CHANNEL_EN in sec and pv.NOT_SEARCHED_CHANNEL_ZH in sec)
check("it separates partners from channel", "NOT CHANNEL" in sec)
check("the heading count was updated", "these 20 headings" in SRC)
import re                                                          # noqa: E402
_body = SRC[SRC.index("these 20 headings"):SRC.index("## Sources / 信息来源")]
check("and the prompt really carries 20 headings",
      len(re.findall(r"^## (?!<English)", _body, re.M)) + 1 == 20,
      str(len(re.findall(r"^## (?!<English)", _body, re.M)) + 1))
check("synthesis is handed the channels",
      "channels=channels" in SRC and "channel_prompt_block" in SRC)
check("the package carries channels and coverage",
      '"channels": channels' in SRC and '"channel_coverage": chan_cov' in SRC)

print("\n[10] The manifest counts channel work separately")
facts = app._channel_facts(cov, comp, PROFILE)
check("used is true once a search ran", facts["used"] is True)
check("searches match the client", facts["searches"] == len(client.queries))
check("channel_entities counts representation only",
      app._channel_facts(p_cov, p_sink, PROFILE)["channel_entities"] == 0
      and app._channel_facts(p_cov, p_sink, PROFILE)["partners"] == 1)
check("authorized is counted", facts["authorized"] >= 1)
check("the skip case is unused and carries its reason", (lambda f: (
      f["used"] is False and f["skip_reason"]))(
      app._channel_facts(s_cov, [], STRONG_DIRECT)))
check("go-to-market travels with the numbers",
      facts["go_to_market_model"] == PROFILE["go_to_market_model"]
      and facts["go_to_market_confidence"] == PROFILE["go_to_market_confidence"])
whole = app.execution_facts({"channel_coverage": cov, "channels": comp,
                             "profile": PROFILE, "evidence": [], "ai_usage": []}, [], None)
check("the block sits in the execution manifest", "channel_discovery" in whole)
check("no monetary field was added",
      not any("cost" in k or "price" in k for k in whole["channel_discovery"]))
check("competitor and channel blocks stay distinct",
      whole["channel_discovery"] is not whole["competitor_discovery"]
      and "channel_entities" not in whole["competitor_discovery"])

print("\n[11] A failing search degrades, never terminates")
f_sink, _, f_cov, _ = run([hit("https://midwest-dist.com/lines",
                                    "Midwest Industrial Distribution")],
                          fail_on=(ch.plan_intents(PROFILE, ACCOUNT, batch=1)[0][1],))
check("the run continued past the failure", f_cov["search_count"] >= 1)
check("and still produced a channel entity", len(f_sink) >= 1)

print("\n[12] Each path has its own budget and cannot spend another's")
import competitor_discovery as cd                                 # noqa: E402
import tavily_service as tv                                       # noqa: E402
check("channel ceiling is 4", ch.MAX_CHANNEL_SEARCHES == 4, str(ch.MAX_CHANNEL_SEARCHES))
check("competitor ceiling is 6", cd.MAX_COMPETITOR_SEARCHES == 6,
      str(cd.MAX_COMPETITOR_SEARCHES))
check("provider ceiling is 8", tv.MAX_PROVIDER_SEARCHES == 8,
      str(tv.MAX_PROVIDER_SEARCHES))
check("the three discovery paths total 18",
      tv.MAX_PROVIDER_SEARCHES + cd.MAX_COMPETITOR_SEARCHES
      + ch.MAX_CHANNEL_SEARCHES == 18)
# The general fallback is a separate, pre-existing path and is counted apart so
# the number above stays the one the design fixed.
check("the general fallback keeps its own separate ceiling of 4",
      tv.MAX_GENERAL_SEARCHES == 4, str(tv.MAX_GENERAL_SEARCHES))
big = FakeClient([hit("https://integrator.com/about", "Lakeside Systems")])
sink12 = []
_, cov12 = ch.discover(big, PROFILE, ACCOUNT,
                       rs.channel_verifier(PROFILE, ACCOUNT, "", (), sink12))
check("a stream of partners never exceeds the channel ceiling",
      cov12["search_count"] <= ch.MAX_CHANNEL_SEARCHES, str(cov12["search_count"]))
check("and partners alone never satisfy the stop condition",
      cov12["channel_entities"] == 0 and cov12["partners"] >= 1)

rs.fetch_page_text = _real_fetch
print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
