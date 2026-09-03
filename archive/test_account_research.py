#!/usr/bin/env python3
"""
Standalone Account Research test utility.

Runs the proven retrieval pattern against Alibaba Bailian / DashScope native:

    company -> short search queries -> web search -> dedupe -> page text
            -> evidence set -> closed-book synthesis -> cited answer

Credentials and model configuration come from ai_credentials.env. Nothing is
hardcoded here and the API key is never printed.

Usage:
    python3 test_account_research.py

Dependencies: none. Python 3.8+ standard library only.
"""

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
CRED_FILE = HERE / "ai_credentials.env"
RESULTS_DIR = HERE / "test_results"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

RETRIEVAL_MAX_TOKENS = 48   # retrieval prose is discarded; do not pay for it
MAX_PAGES_TO_FETCH = 8
MAX_CHARS_OFFICIAL = 5000
MAX_CHARS_OTHER = 1500
MAX_EVIDENCE_ITEMS = 10


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------

def load_credentials(path=CRED_FILE):
    """Parse a KEY=VALUE env file. Values are never logged."""
    if not path.exists():
        sys.exit(f"ERROR: {path.name} not found in {path.parent}.\n"
                 f"Copy ai_credentials.env.example to ai_credentials.env and fill it in.")
    cfg = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        cfg[key.strip()] = value.strip().strip('"').strip("'")
    required = ["DASHSCOPE_API_KEY", "DASHSCOPE_WORKSPACE_ID", "DASHSCOPE_BASE_URL",
                "DASHSCOPE_PATH_TEXT", "DASHSCOPE_PATH_MULTIMODAL"]
    missing = [k for k in required if not cfg.get(k)]
    if missing:
        sys.exit(f"ERROR: {path.name} is missing: {', '.join(missing)}")
    return cfg


def truthy(value, default=True):
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def post_json(url, payload, api_key, timeout):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"},   # never logged
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"message": raw[:400]}
    except Exception as e:                                  # timeout, DNS, TLS
        return 0, {"message": f"{type(e).__name__}: {e}"}


def _decode(raw, content_type):
    """Chinese sites still serve GB2312/GBK; UTF-8 decoding yields mojibake."""
    head = raw[:2048].decode("latin-1", "replace")
    m = (re.search(r"charset=([\w-]+)", content_type or "", re.I)
         or re.search(r'<meta[^>]+charset=["\']?([\w-]+)', head, re.I))
    declared = (m.group(1) if m else "utf-8").lower().replace("-", "")
    enc = "gb18030" if declared in ("gb2312", "gbk", "gb18030") else \
          "big5" if declared.startswith("big5") else "utf-8"
    return raw.decode(enc, "replace")


def html_to_text(html):
    s = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    s = re.sub(r"<!--.*?-->", " ", s, flags=re.S)
    s = re.sub(r"<[^>]+>", "\n", s)
    for a, b in (("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&quot;", '"'), ("&#39;", "'"), ("&mdash;", "—"), ("&copy;", "©")):
        s = s.replace(a, b)
    seen, out = set(), []
    for line in s.split("\n"):
        line = line.strip()
        if not (2 <= len(line) <= 400):
            continue
        if not re.search(r"[一-鿿]|[A-Za-z]{3}", line):
            continue
        if line in seen:
            continue
        seen.add(line)
        out.append(line)
    return "\n".join(out)


def fetch_url(url, timeout=20):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return _decode(resp.read(), resp.headers.get("Content-Type", "")), resp.geturl()


def fetch_page_text(url, timeout=20, min_chars=400):
    """Standard fetch, then JS-bundle extraction for SPA sites that ship an HTML shell."""
    try:
        html, final = fetch_url(url, timeout)
    except Exception as e:
        return "", "failed", str(e)[:80]

    text = html_to_text(html)
    if len(text) >= min_chars:
        return text, "standard", None

    # Site builders (skeqi.com among them) put the page content inside a JS bundle.
    combined = []
    for src in re.findall(r'<script[^>]+src=[\'"]([^\'"]+)[\'"]', html, re.I)[:6]:
        try:
            bundle, _ = fetch_url(urllib.parse.urljoin(final, src), timeout)
        except Exception:
            continue
        for a, b in (("\\u003c", "<"), ("\\u003e", ">"), ("\\u0026", "&"),
                     ('\\"', '"'), ("\\/", "/"), ("\\r\\n", "\n"), ("\\n", "\n")):
            bundle = bundle.replace(a, b)
        combined.append(html_to_text(bundle))
    bundled = "\n".join(combined).strip()
    if len(bundled) >= min_chars:
        return bundled, "script-bundle", None
    return (text or bundled), "insufficient", None


# --------------------------------------------------------------------------
# Stage 1 — short search queries (never the research prompt)
# --------------------------------------------------------------------------

def plan_queries(name, name_cn, domain):
    """Short keyword queries. The long research instruction is NOT a search query:
    measured, it collapses retrieval from ~9 results to 1."""
    both = " ".join(x for x in (name, name_cn) if x)
    queries = [
        f"{both} company products",
        f"{both} 公司简介 产品",
        f"{both} customers partners 客户 合作伙伴",
    ]
    if domain:
        queries.append(f"site:{domain} {name_cn or name} 产品")
    queries.append(f"{both} 战略合作 partnership")
    seen, out = set(), []
    for q in queries:
        q = re.sub(r"\s+", " ", q).strip()
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out[:6]


# --------------------------------------------------------------------------
# Stage 2 — retrieval
# --------------------------------------------------------------------------

def endpoint_for(model, cfg):
    multimodal = [m.strip() for m in cfg.get("DASHSCOPE_MULTIMODAL_MODELS", "").split(",") if m.strip()]
    if model in multimodal:
        return cfg["DASHSCOPE_PATH_MULTIMODAL"], True
    return cfg["DASHSCOPE_PATH_TEXT"], False


def build_search_options(cfg):
    opts = {"search_strategy": cfg.get("AI_SEARCH_STRATEGY", "turbo")}
    if truthy(cfg.get("AI_SOURCE_ENABLED")):
        opts["enable_source"] = True
    if truthy(cfg.get("AI_CITATION_ENABLED")):
        opts["enable_citation"] = True
        opts["citation_format"] = cfg.get("AI_CITATION_FORMAT", "[<number>]")
    # freshness is deliberately NOT set: it collapsed results to 0-1 in testing.
    return opts


def search(query, cfg, timeout):
    """Bailian exposes web search only through a model call, so the fast model is
    used purely as a retrieval vehicle. Its prose is discarded."""
    model = cfg.get("AI_MODEL_FAST", "deepseek-v4-flash-0731")
    path, is_multi = endpoint_for(model, cfg)
    url = cfg["DASHSCOPE_BASE_URL"].rstrip("/") + path
    content = [{"text": query}] if is_multi else query
    params = {"enable_search": True, "search_options": build_search_options(cfg),
              "max_tokens": RETRIEVAL_MAX_TOKENS}
    if not is_multi:
        params["result_format"] = "message"
    status, data = post_json(url, {
        "model": model,
        "input": {"messages": [{"role": "user", "content": content}]},
        "parameters": params,
    }, cfg["DASHSCOPE_API_KEY"], timeout)
    results = (data.get("output", {}) or {}).get("search_info", {}).get("search_results", []) or []
    return status, [r for r in results if r.get("url")], data.get("message")


def dedupe_sources(all_results, name, name_cn, domain):
    """Merge, drop duplicate URLs, and reject obvious wrong-entity matches."""
    domain = (domain or "").replace("www.", "")
    by_url, rejected = {}, []
    for item in all_results:
        url = item.get("url", "")
        key = re.sub(r"[#?].*$", "", url.rstrip("/")).lower()
        if not key:
            continue
        if key in by_url:
            by_url[key]["hits"] += 1
            continue
        hay = f"{item.get('title','')} {item.get('site_name','')} {url}".lower()
        host = urllib.parse.urlparse(url).netloc.lower().replace("www.", "")
        official = bool(domain) and (host == domain or host.endswith("." + domain))
        # Identity: the Chinese name, the domain, or the Latin name are all acceptable
        # signals at this stage; page text is checked again after fetching.
        ident = official or (name_cn and name_cn in hay) or (name and name.lower() in hay)
        if not ident:
            rejected.append({"title": item.get("title", ""), "url": url, "reason": "no identity match"})
            continue
        by_url[key] = {"title": item.get("title", ""), "url": url,
                       "site_name": item.get("site_name", ""),
                       "official": official, "hits": 1}
    ordered = sorted(by_url.values(), key=lambda s: (not s["official"], -s["hits"]))
    return ordered, rejected


# --------------------------------------------------------------------------
# Stage 3 — evidence
# --------------------------------------------------------------------------

def build_evidence(sources, name, name_cn, official_url):
    evidence = []
    seeds = []
    if official_url and not any(s["official"] for s in sources):
        seeds.append({"title": "(official website — seeded)", "url": official_url,
                      "site_name": "", "official": True, "hits": 1})
    for src in seeds + sources:
        if len(evidence) >= MAX_EVIDENCE_ITEMS:
            break
        if len(evidence) >= MAX_PAGES_TO_FETCH and not src["official"]:
            break
        text, method, _err = fetch_page_text(src["url"])
        if len(text) < 120:
            continue
        if not src["official"]:
            hay = text[:4000]
            if not ((name_cn and name_cn in hay) or (name and name.lower() in hay.lower())):
                continue                       # post-fetch disambiguation
        cap = MAX_CHARS_OFFICIAL if src["official"] else MAX_CHARS_OTHER
        evidence.append({"id": len(evidence) + 1, "title": src["title"], "url": src["url"],
                         "official": src["official"], "method": method, "text": text[:cap]})
    return evidence


RESEARCH_INSTRUCTION = """You are an account-research analyst. Answer ONLY from the EVIDENCE below.

Rules:
- Cite an evidence id like [2] for every material claim. Uncited claims are not allowed.
- Label each finding: VERIFIED (directly stated in evidence), LIKELY / PARTIALLY SUPPORTED
  (implied but not stated), or NOT ENOUGH EVIDENCE.
- Never invent company information. If the evidence does not cover something, write
  "Not enough evidence" instead of filling the gap from your own knowledge.
- Prefer the company's official website over other sources, and say so when sources conflict.
- Note whether a customer/partner claim is first-party (the company's own site) or third-party.

Answer these sections for {company}{site}:

1. Company overview
2. Core business
3. Main products / services
4. Industries served
5. Key customers / partners  (only where evidence supports them)
6. Differentiators
7. Brand / company values (if available)
8. Sources — numbered list of the evidence ids you used, with URLs

EVIDENCE:
"""


def render_evidence(evidence):
    return "\n\n---\n\n".join(
        f"[{e['id']}] {e['title'] or '(untitled)'}\n"
        f"URL: {e['url']}\n"
        f"SOURCE: {'official company website' if e['official'] else 'third-party web source'}\n"
        f"CONTENT:\n{e['text']}"
        for e in evidence)


def synthesize(model, company, website, evidence, cfg, timeout):
    """Search is OFF here on purpose: synthesis must be closed-book over the evidence."""
    path, is_multi = endpoint_for(model, cfg)
    url = cfg["DASHSCOPE_BASE_URL"].rstrip("/") + path
    prompt = (RESEARCH_INSTRUCTION.format(
        company=company, site=f" (website: {website})" if website else "")
        + render_evidence(evidence))
    content = [{"text": prompt}] if is_multi else prompt
    params = {} if is_multi else {"result_format": "message"}
    started = time.time()
    status, data = post_json(url, {
        "model": model,
        "input": {"messages": [{"role": "user", "content": content}]},
        "parameters": params,
    }, cfg["DASHSCOPE_API_KEY"], timeout)
    elapsed = time.time() - started

    msg = ((data.get("output", {}) or {}).get("choices") or [{}])[0].get("message", {})
    body = msg.get("content", "")
    answer = body if isinstance(body, str) else "".join(p.get("text", "") for p in body or [])
    usage = data.get("usage", {}) or {}
    return {
        "status": status, "answer": answer, "latency": elapsed,
        "endpoint": "multimodal-generation" if is_multi else "text-generation",
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "error": None if status == 200 else str(data.get("message") or data)[:300],
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

BAR = "=" * 40


def header(title):
    print(f"\n{BAR}\n{title}\n{BAR}\n")


def prompt_inputs(cfg):
    print(f"\n{BAR}\nACCOUNT RESEARCH TEST\n{BAR}")
    print("Press Enter to accept the SKEQI defaults.\n")
    name = input("Company name  [SKEQI / 思客琦]: ").strip() or "SKEQI / 思客琦"
    site = input("Company website [https://www.skeqi.com]: ").strip() or "https://www.skeqi.com"

    models = {
        "1": cfg.get("AI_MODEL_CITATION", "qwen3.6-flash"),
        "2": cfg.get("AI_MODEL_QUALITY", "deepseek-v4-pro"),
        "3": cfg.get("AI_MODEL_FAST", "deepseek-v4-flash-0731"),
    }
    print("\nSelect model:")
    print(f"  1. Qwen 3.6 Flash      ({models['1']})")
    print(f"  2. DeepSeek V4 Pro     ({models['2']})")
    print(f"  3. DeepSeek V4 Flash   ({models['3']})")
    print("  4. Run all three")
    choice = input("\nChoice [1]: ").strip() or "1"
    selected = list(models.values()) if choice == "4" else [models.get(choice, models["1"])]
    return name, site, selected


def save_result(company, model, website, queries, sources, evidence, result):
    RESULTS_DIR.mkdir(exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", company.lower()).strip("-")[:24] or "company"
    path = RESULTS_DIR / f"{slug}_{model}.json"
    path.write_text(json.dumps({
        "timestamp": datetime.now().astimezone().isoformat(),
        "company": company, "website": website, "model": model,
        "protocol": "DashScope Native", "endpoint": result["endpoint"],
        "search_queries": queries,
        "sources": [{"id": e["id"], "title": e["title"], "url": e["url"],
                     "official": e["official"], "retrieval_method": e["method"]} for e in evidence],
        "sources_found_total": len(sources),
        "research_response": result["answer"],
        "latency_seconds": round(result["latency"], 1),
        "token_usage": {"input": result["input_tokens"], "output": result["output_tokens"],
                        "total": result["total_tokens"]},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main():
    cfg = load_credentials()
    timeout = int(cfg.get("AI_REQUEST_TIMEOUT_MS", "90000")) / 1000
    company, website, models = prompt_inputs(cfg)

    name = company.split("/")[0].strip()
    name_cn = next((p.strip() for p in company.split("/")[1:]
                    if re.search(r"[一-鿿]", p)), "")
    domain = urllib.parse.urlparse(website).netloc.replace("www.", "") if website else ""

    # ---- retrieval -------------------------------------------------------
    queries = plan_queries(name, name_cn, domain)
    header("SEARCH QUERIES")
    for i, q in enumerate(queries, 1):
        print(f"{i}. {q}")

    raw, failures = [], []
    print("\nsearching...")
    for q in queries:
        status, results, err = search(q, cfg, timeout)
        print(f"  [{status}] {len(results):>2} results   {q}")
        if status != 200:
            failures.append((q, err))
        raw.extend(results)

    sources, rejected = dedupe_sources(raw, name, name_cn, domain)
    if not sources and failures:
        sys.exit(f"\nERROR: every search call failed. First error: {failures[0][1]}")

    print("\nfetching page content...")
    evidence = build_evidence(sources, name, name_cn, website)

    header("SOURCES FOUND")
    if not evidence:
        print("(none — retrieval problem, not a model problem)")
    for e in evidence:
        tag = " [OFFICIAL]" if e["official"] else ""
        print(f"[{e['id']}] {e['title'] or '(untitled)'}{tag}")
        print(f"    {e['url']}")
        print(f"    via {e['method']}, {len(e['text'])} chars\n")
    print(f"{len(raw)} raw results -> {len(sources)} after dedupe "
          f"({len(rejected)} rejected) -> {len(evidence)} with readable content")

    if not evidence:
        sys.exit("\nNo evidence could be gathered; stopping before the model call.")

    # ---- synthesis -------------------------------------------------------
    runs = []
    for model in models:
        header(f"ACCOUNT RESEARCH — {model}")
        result = synthesize(model, company, website, evidence, cfg, timeout)
        if result["status"] != 200:
            print(f"FAILED (HTTP {result['status']}): {result['error']}")
        else:
            print(result["answer"])

        header("PERFORMANCE")
        print(f"Model:             {model}")
        print(f"Protocol:          DashScope Native")
        print(f"Endpoint:          {result['endpoint']}")
        print(f"Search queries:    {len(queries)}")
        print(f"Sources retrieved: {len(evidence)}")
        print(f"Input tokens:      {result['input_tokens']}")
        print(f"Output tokens:     {result['output_tokens']}")
        print(f"Total tokens:      {result['total_tokens']}")
        print(f"Time:              {result['latency']:.1f} seconds")

        path = save_result(company, model, website, queries, sources, evidence, result)
        print(f"\nSaved: {path.relative_to(HERE)}")
        runs.append((model, result))

    if len(runs) > 1:
        header("MODEL COMPARISON")
        for model, r in runs:
            print(f"{model}")
            print(f"  Time:    {r['latency']:.1f}s")
            print(f"  Tokens:  {r['total_tokens']} (in {r['input_tokens']} / out {r['output_tokens']})")
            print(f"  Sources: {len(evidence)}")
            print(f"  Status:  {'OK' if r['status'] == 200 else 'FAILED ' + str(r['status'])}\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("\nInterrupted.")
