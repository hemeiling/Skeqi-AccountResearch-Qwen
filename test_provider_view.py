#!/usr/bin/env python
"""Provider representation: what retrieval verified survives into the report.

The rule: a category is never a company. The model was always TOLD that, and
told it in a prompt that also asks for a Company column, so it filled the column
with "MES provider" and 系统集成商 when it had no name. A phrase in that column
reads as a verified supplier, which is worse than an empty table - so the
rendered report is corrected against the verified rows, not merely instructed.

Nothing is deleted: a rejected cell moves to the field it belongs in.

No network, no model call.  .venv/bin/python test_provider_view.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import provider_view as pv                                       # noqa: E402
import research_service as rs                                    # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (" - " + detail if detail else ""))


PROVIDERS = [
    {"name": "ABB Robotics", "category": "robotics", "relationship": rs.REL_CONFIRMED,
     "provenance": rs.PROV_TARGET, "source_domain": "qualitymag.com"},
    {"name": "FANUC", "category": "robotics", "relationship": rs.REL_STRONG,
     "provenance": rs.PROV_TARGET, "source_domain": "automateamerica.com"},
    {"name": "Jilin Landi Automation Engineering", "category": "integrator",
     "relationship": rs.REL_MARKET, "provenance": rs.PROV_MARKET,
     "source_domain": "cjxjy.com"},
]

REPORT = """## Existing Automation Providers / 现有自动化供应商

| Company | Capability | Evidence | Incumbency |
| --- | --- | --- | --- |
| ABB Robotics | welding cells | [1] | incumbent |
| MES provider | traceability | [2] | incumbent |
| System Integrators | line build | [3] | current supplier |
| 系统集成商 | 焊装线 | [4] | 现有供应商 |
| Internal engineering team | tooling | [5] | in-house |
| Jilin Landi Automation Engineering | conveyors | [6] | incumbent |

## Key Decision Makers / 关键决策人

| Company | Role |
| --- | --- |
| MES provider | should be untouched here |
"""

print("\n[1] The prompt carries verified rows as facts")
blk = pv.provider_prompt_block(PROVIDERS, "Ford Motor Company")
check("every verified organisation appears",
      all(p["name"] in blk for p in PROVIDERS))
check("each carries its relationship", "CONFIRMED" in blk and "STRONG" in blk
      and "MARKET ONLY" in blk)
check("each carries its provenance", "target" in blk and "market" in blk)
check("relationship and provenance are named as separate dimensions",
      "SEPARATE" in blk)
check("the market-only rule is stated", "never" in blk.lower() and "incumbent" in blk.lower())
check("it is bilingual", "已核实供应商" in blk)
empty = pv.provider_prompt_block([], "Ford")
check("with nothing verified the exact sentence is supplied",
      pv.NO_PROVIDER_EN in empty and pv.NO_PROVIDER_ZH in empty)

print("\n[2] Categories are removed from the Company column")
out, notes = pv.enforce(REPORT, PROVIDERS, "Ford Motor Company")
prov_section = out[out.index("## Existing Automation Providers"):out.index("## Key Decision Makers")]
for junk in ("MES provider", "System Integrators", "系统集成商"):
    row = "| %s |" % junk
    check("%s no longer occupies a company row" % junk, row not in prov_section, junk)
check("a real organisation is kept", "| ABB Robotics |" in out)
check("a market-only organisation is kept as a row",
      "Jilin Landi Automation Engineering" in out)

print("\n[3] Nothing is deleted, only re-filed")
check("rejected categories are listed as capabilities",
      pv.CAPABILITY_LABEL in out and "MES provider" in out, out[-400:])
check("the account's own engineering is filed as internal capability",
      pv.INTERNAL_LABEL in out and "Internal engineering team" in out)
check("capability and internal are separate fields",
      out.index(pv.CAPABILITY_LABEL) != out.index(pv.INTERNAL_LABEL))
check("the moves are reported to the caller",
      len(notes["moved_capability"]) >= 2 and len(notes["moved_internal"]) == 1,
      str(notes))

print("\n[4] MARKET_ONLY is never an incumbent")
landi = [l for l in out.split("\n") if "Jilin Landi" in l][0]
check("the incumbency claim is stripped", "incumbent" not in landi.lower(), landi)
check("it is relabelled as market context", "market context" in landi.lower(), landi)
abb = [l for l in out.split("\n") if "ABB Robotics" in l and l.startswith("|")][0]
check("a CONFIRMED provider keeps its incumbency", "incumbent" in abb.lower(), abb)

print("\n[5] Only provider sections are touched")
tail = out[out.index("## Key Decision Makers"):]
check("a company column elsewhere is left completely alone",
      "| MES provider | should be untouched here |" in tail, tail[:200])

print("\n[6] Nothing verified yields the fixed bilingual sentence")
bare = """## Existing Automation Providers / 现有自动化供应商

| Company | Capability |
| --- | --- |
| MES provider | traceability |
| Automation Providers | line build |
"""
out2, notes2 = pv.enforce(bare, [], "Ford Motor Company")
check("the English sentence appears verbatim", pv.NO_PROVIDER_EN in out2)
check("the Chinese sentence appears verbatim", pv.NO_PROVIDER_ZH in out2)
check("no category was left standing as a company",
      "| MES provider |" not in out2 and "| Automation Providers |" not in out2)
check("and the emptiness is reported", notes2["empty"] is True)
check("the sentence is not repeated", out2.count(pv.NO_PROVIDER_EN) == 1,
      str(out2.count(pv.NO_PROVIDER_EN)))

print("\n[7] The generic strings named in the requirement")
for junk in ("MES", "WMS", "System Integrators", "Robotics", "Automation Providers",
             "机器人供应商", "系统集成商"):
    check("rejected as a company: %s" % junk, not rs.is_named_organization(junk))

print("\n[8] Safety: the corrector never mangles a report")
check("an empty report is returned unchanged", pv.enforce("", PROVIDERS, "F")[0] == "")
check("a report with no tables is unchanged",
      pv.enforce("## Existing Automation Providers\n\nProse only.\n",
                 PROVIDERS, "F")[0].startswith("## Existing Automation Providers"))
same = pv.enforce(REPORT, PROVIDERS, "Ford Motor Company")[0]
check("correction is idempotent",
      pv.enforce(same, PROVIDERS, "Ford Motor Company")[0] == same)
check("bilingual headings survive", "现有自动化供应商" in out)
check("evidence citations survive", "[1]" in out and "[6]" in out)

print("\n[9] The provider column is found by HEADER, not by position")
check("Company table -> column 1",
      pv.provider_column("| Company | Classification | Evidence |") == 1)
check("Process/Provider table -> column 2",
      pv.provider_column(
          "| Process or capability | Provider or internal capability | Evidence |") == 2,
      "this is the shape that shipped unprotected")
check("Chinese Company header", pv.provider_column("| 公司 | 能力 | 证据 |") == 1)
check("Chinese provider header",
      pv.provider_column("| 工艺或能力 | 供应商或内部能力 | 证据 |") == 2)
check("a table with no organisation column is left alone",
      pv.provider_column("| Role | Name | Notes |") is None)
check("a provider word beats a capability word in the same header",
      pv.provider_column("| Capability | Supplier | X |") == 2)

print("\n[10] The production defects, as regression cases")
REAL = """## Existing Automation Providers / 现有自动化供应商

| Process or capability | Provider or internal capability | Evidence | Confidence | Incumbency |
| --- | --- | --- | --- | --- |
| Structural battery laser welding | Undisclosed Tier-1s / Ford internal engineering | [14] | Verified | High |
| Cobots & flex assembly stations | ABB Robotics, Kuka/Rethink/Fanuc (market) | [15] | Verified | Very High |
| Digital factory | MES provider | [3] | Likely | Moderate |
"""
PR = [{"name": "ABB Robotics", "relationship": rs.REL_CONFIRMED,
       "provenance": rs.PROV_TARGET}]
fixed, n2 = pv.enforce(REAL, PR, "Ford Motor Company")
rows = [l for l in fixed.split("\n") if l.startswith("| ") and "---" not in l]
cols = lambda r: [c.strip() for c in r.split("|")]
check("Undisclosed Tier-1s does not survive as a provider",
      "Undisclosed Tier-1s" not in cols(rows[1])[2], cols(rows[1])[2])
check("it is re-filed as a capability",
      "Undisclosed Tier-1s" in n2["moved_capability"], str(n2["moved_capability"]))
check("Ford internal engineering moves to Internal Capability",
      any("Ford internal engineering" in x for x in n2["moved_internal"]),
      str(n2["moved_internal"]))
check("a row losing every provider also loses its incumbency grade",
      cols(rows[1])[5] == "—", cols(rows[1])[5])
check("the confirmed provider survives intact",
      "ABB Robotics" in cols(rows[2])[2], cols(rows[2])[2])
check("market-only vendors keep their row but not Very High",
      "Very High" not in rows[2] and "market context" in rows[2].lower(), rows[2])
check("a generic capability in the provider column is removed",
      "MES provider" not in cols(rows[3])[2], cols(rows[3])[2])
check("table structure is preserved exactly",
      len({len(r.split("|")) for r in rows}) == 1,
      str(sorted({len(r.split("|")) for r in rows})))
check("citations survive", "[14]" in fixed and "[15]" in fixed)
check("bilingual heading survives", "现有自动化供应商" in fixed)
check("correction is idempotent",
      pv.enforce(fixed, PR, "Ford Motor Company")[0] == fixed)

print("\n[11] Capitalisation alone is not an organisation")
for phrase in ("Structural battery laser welding", "Cobots & flex assembly stations",
               "Precision machining (Aluminum Unicasting)", "Undisclosed Tier-1s",
               "Automation Providers", "System Integrators", "Aluminum Unicasting",
               "机器人供应商", "系统集成商"):
    check("rejected: %s" % phrase, not rs.is_named_organization(phrase))
for org in ("ABB Robotics", "ABB", "FANUC", "Kuka", "PMi2", "Silk EV",
            "Rockwell Automation", "Dürr Systems", "比亚迪", "宁德时代"):
    check("accepted: %s" % org, rs.is_named_organization(org))

print("\n[12] Account-owned capability, including the short name")
# From the ACRO production run: "ACRO (In-house)" survived as a competitor row.
# Two causes - the corrector stripped the parenthetical before asking about
# ownership, and the short name was never matched against the full legal name.
ACCT = "ACRO Automation Systems"
for entry, acct in [("ACRO (In-house)", ACCT), ("Ford (In-house)", "Ford Motor Company"),
                    ("\u7ea2\u65d7 (\u5185\u90e8)", "\u7ea2\u65d7"), ("ACRO", ACCT),
                    ("ACRO internal engineering", ACCT),
                    ("Internal engineering team", ACCT),
                    ("In-house robotic manipulators", ACCT)]:
    check("owned: %s" % entry, pv._account_owned(entry, acct), acct)
# Exact-token equality is what keeps the short-name rule narrow. Substring
# matching would swallow every one of these.
for entry, acct in [("Acromag", ACCT), ("ACROBAT Automation", ACCT),
                    ("Macro Automation", ACCT), ("Acro-Tech Welding", ACCT),
                    ("ACRO Systems Inc", ACCT),
                    ("Fordham Automation", "Ford Motor Company"),
                    ("Teslong Instruments", "Tesla, Inc."),
                    ("FANUC", ACCT), ("Motoman (Yaskawa)", ACCT)]:
    check("NOT owned: %s" % entry, not pv._account_owned(entry, acct), acct)
check("a very short distinctive token is not used for ownership",
      not pv._account_owned("TE", "TE Connectivity"),
      "two characters would match far too much")

print("\n[13] The ACRO row is re-filed end to end")
# The heading is the PROVIDER section: this table is provider intelligence, and
# since the corrector stopped governing Competitor Analysis that is where it
# lives. The rows are the real ones from the ACRO run.
ACRO_TABLE = """## Existing Automation Providers / \u73b0\u6709\u81ea\u52a8\u5316\u4f9b\u5e94\u5546

| Company | Classification | Capability | Evidence |
| --- | --- | --- | --- |
| FANUC, Motoman (Yaskawa) | Ecosystem | Robots | [13] |
| ACRO (In-house) | Internal | Welding cells | [2] |
| Acromag | Market | I/O modules | [7] |
"""
out13, n13 = pv.enforce(ACRO_TABLE, [{"name": "FANUC", "relationship": rs.REL_MARKET,
                                      "provenance": rs.PROV_MARKET}], ACCT)
rows13 = [l for l in out13.split("\n") if l.startswith("| ") and "---" not in l]
cols13 = lambda r: [c.strip() for c in r.split("|")]
check("ACRO (In-house) leaves the company column",
      "ACRO" not in cols13(rows13[2])[1], cols13(rows13[2])[1])
check("and is filed as Internal Capability",
      any("ACRO" in x for x in n13["moved_internal"]), str(n13["moved_internal"]))
check("Acromag is NOT swept up with it",
      "Acromag" in out13 and not any("Acromag" in x for x in n13["moved_internal"]),
      str(n13["moved_internal"]))
check("FANUC stays a named organisation", "FANUC" in cols13(rows13[1])[1])

print("\n[14] The corrector no longer governs Competitor Analysis")
# Target competitors are not providers. Judging them against the provider
# verified set rewrote a verified competitor's confidence to "market context"
# whenever synthesis chose a column header the provider matcher recognised.
COMPETITOR_HEADERS = ("| Competitor | Competition Type | Evidence | Confidence |",
                      "| Company | Competition Type | Evidence | Confidence |",
                      "| Organization | Competition Type | Evidence | Confidence |",
                      "| \u516c\u53f8 | \u7ade\u4e89\u7c7b\u578b | \u8bc1\u636e | \u7f6e\u4fe1\u5ea6 |",
                      "| \u7ade\u4e89\u5bf9\u624b | \u7ade\u4e89\u7c7b\u578b | \u8bc1\u636e | \u7f6e\u4fe1\u5ea6 |")
for header in COMPETITOR_HEADERS:
    report = ("## Competitor Analysis / \u7ade\u4e89\u5bf9\u624b\u5206\u6790\n\n"
              + header + "\n|---|---|---|---|\n"
              "| Rival One | DIRECT | [4] | high |\n"
              "| Rival Two Corp | PARTIAL | [7] | medium |\n\n"
              "## Existing Automation Providers / \u73b0\u6709\u81ea\u52a8\u5316\u4f9b\u5e94\u5546\n\n"
              "| Process | Provider | Evidence |\n|---|---|---|\n"
              "| Robotics | FANUC | [2] |\n\n"
              "## Key Decision Makers\n")
    label = header.split("|")[1].strip()
    for providers in ([{"name": "FANUC", "relationship": rs.REL_CONFIRMED,
                        "provenance": rs.PROV_TARGET}], []):
        out14, _ = pv.enforce(report, providers, ACCT)
        sec = out14[out14.index("## Competitor Analysis"):out14.index("## Existing Automation")]
        state = "with providers" if providers else "with none verified"
        check("%s column survives untouched (%s)" % (label, state),
              "| Rival One | DIRECT | [4] | high |" in sec
              and "| Rival Two Corp | PARTIAL | [7] | medium |" in sec, sec)
        check("%s column keeps provider vocabulary out (%s)" % (label, state),
              "market context" not in sec and pv.NO_PROVIDER_EN not in sec)
# And the provider section is still corrected exactly as before.
prov_only = ("## Existing Automation Providers / \u73b0\u6709\u81ea\u52a8\u5316\u4f9b\u5e94\u5546\n\n"
             "| Process | Provider | Incumbency | Evidence |\n|---|---|---|---|\n"
             "| Robotics | Ghost Robotics Co | Incumbent supplier | [2] |\n"
             "| Welding | Assembly line integration | Incumbent | [3] |\n\n"
             "## Key Decision Makers\n")
out14b, n14b = pv.enforce(prov_only, [], ACCT)
check("an unverified provider's incumbency claim is still softened",
      "market context" in out14b, out14b)
check("a capability in the provider column is still re-filed",
      any("Assembly line integration" in x for x in n14b["moved_capability"]),
      str(n14b["moved_capability"]))
check("and the provider absence sentence still lands there",
      pv.NO_PROVIDER_EN in out14b)

print("\n[15] Skipped is not the same as searched and empty")
skipped = pv.competitor_prompt_block(
    [], {"skip_reason": "research returned no readable sources"})
searched = pv.competitor_prompt_block([], {"search_count": 4})
check("a skipped path says NOT PERFORMED", "NOT PERFORMED" in skipped)
check("and uses the limitation wording, not the absence wording",
      pv.NOT_SEARCHED_COMPETITOR_EN in skipped and pv.NO_COMPETITOR_EN not in skipped)
check("the Chinese limitation wording travels with it",
      pv.NOT_SEARCHED_COMPETITOR_ZH in skipped)
check("a searched path says it was performed",
      "performed" in searched and "NOT PERFORMED" not in searched)
check("and uses the absence wording verbatim", pv.NO_COMPETITOR_EN in searched
      and pv.NO_COMPETITOR_ZH in searched)
check("the skipped block forbids claiming the account has no competitors",
      "do NOT report that the account has no competitors" in skipped)
h_skipped = pv.channel_prompt_block(
    [], {"go_to_market_model": "UNKNOWN",
         "skip_reason": "research returned no readable sources"})
h_searched = pv.channel_prompt_block([], {"go_to_market_model": "UNKNOWN",
                                          "search_count": 4})
check("a skipped channel path says NOT PERFORMED", "NOT PERFORMED" in h_skipped)
check("and uses the channel limitation wording",
      pv.NOT_SEARCHED_CHANNEL_EN in h_skipped and pv.NO_CHANNEL_EN not in h_skipped)
check("the Chinese channel limitation wording travels with it",
      pv.NOT_SEARCHED_CHANNEL_ZH in h_skipped)
check("a searched channel path uses the absence wording",
      pv.NO_CHANNEL_EN in h_searched and "NOT PERFORMED" not in h_searched)
check("both still forbid naming distributors from general knowledge",
      "general knowledge" in h_skipped and "general knowledge" in h_searched)

print("\n{} passed, {} failed".format(len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
