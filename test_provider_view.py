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

print("\n{} passed, {} failed".format(len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
