"""
Account Research service.

Shared research logic, extracted from ../test_account_research.py so the CLI and
the web app run exactly the same pipeline:

    company -> short search queries -> web search -> disambiguate
            -> dedupe + tier rank -> page text -> evidence
            -> closed-book synthesis -> cited report

Credentials come from ai_credentials.env and never leave the backend.
Standard library only apart from the Flask app that imports this.
"""

import concurrent.futures
import gzip
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

import apollo_service as apollo
import finance_service as fin
import people_service as people

HERE = Path(__file__).resolve().parent

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

RETRIEVAL_MAX_TOKENS = 48      # retrieval prose is discarded; do not pay for it
MAX_PAGES_TO_FETCH = 20        # measured: 33 raw -> ~19 ranked -> ~6-12 readable
MAX_EVIDENCE_ITEMS = 16
MAX_LOW_TIER_ITEMS = 6         # at most 6 low-tier items ACCEPTED into the evidence
LOW_TIER_ATTEMPTS = 12         # but try more, since many low-tier hosts block or 404
CHAR_BUDGET = {1: 5000, 2: 4000, 3: 2500, 4: 2000, 5: 1600, 6: 900}

# Concurrency. Kept deliberately modest: the searches hit one rate-limited backend,
# and three parallel syntheses is exactly the "Run All Three" case.
SEARCH_CONCURRENCY = 4
FETCH_CONCURRENCY = 10       # I/O bound across distinct hosts

# Hosts that can be evidence but are never the answer to "what is this
# company's official website".
NOT_OFFICIAL_HOSTS = (
    "linkedin.com", "wikipedia.org", "wikimedia.org", "crunchbase.com", "bloomberg.com",
    "facebook.com", "twitter.com", "x.com", "instagram.com", "youtube.com", "tiktok.com",
    "weibo.com", "zhihu.com", "baike.baidu.com", "baidu.com", "qcc.com", "tianyancha.com",
    "aiqicha.com", "11467.com", "jobui.com", "zhipin.com", "51job.com", "liepin.com",
    "lagou.com", "indeed.com", "glassdoor.com", "amazon.", "alibaba.com", "1688.com",
    "made-in-china.com", "globalsources.com", "taobao.com", "jd.com", "sohu.com",
    "163.com", "qq.com", "sina.com.cn", "ifeng.com", "toutiao.com", "reuters.com",
    "yahoo.com", "google.", "bing.com", "medium.com",
)
GENERIC_NAME_WORDS = {
    "inc", "inc.", "corp", "corp.", "corporation", "co", "co.", "ltd", "ltd.", "limited",
    "llc", "lp", "plc", "gmbh", "ag", "sa", "nv", "bv", "srl", "spa", "the", "and",
    "group", "holdings", "company", "technologies", "technology", "solutions",
    "international", "有限公司", "股份", "集团", "科技", "公司",
}
# Industry words: a domain built only from these is a trade portal, not a company.
WEAK_DOMAIN_TOKENS = {"american", "china", "global", "national", "united", "general",
                      "motor", "motors", "auto", "industries", "industrial", "electric",
                      "energy", "new", "north", "south", "east", "west", "europe", "asia",
                      "automation", "systems", "laser", "battery", "tech", "digital",
                      "machine", "machinery", "equipment", "power", "smart", "robotics"}
PAGE_TIMEOUT = 12              # was 20s; one slow page cost 21.7s of a 29.8s stage
SYNTHESIS_TIMEOUT = 480        # per model, independent: one stall must not block the others
EVIDENCE_STAGE_DEADLINE = 40   # hard wall for the whole page-fetch stage

EVIDENCE_CACHE_DIR = HERE / "evidence_cache"


class RetrievalError(RuntimeError):
    """Carries a specific reason code so the batch UI can say what actually failed."""

    def __init__(self, reason, message, website="", website_status="unverified"):
        super().__init__(message)
        self.reason = reason
        self.website = website
        self.website_status = website_status


FAILURE_REASONS = {
    "site_blocked":       "Official website blocked automated access",
    "no_company_match":   "Search returned no company-matching results",
    "js_unextractable":   "JS-rendered website could not be extracted",
    "website_mismatch":   "Supplied website did not match company identity",
    "timeout":            "Retrieval timed out",
    "site_unverified":    "Official website could not be verified",
    "insufficient":       "Broader web research produced insufficient reliable evidence",
    "search_unavailable": "Every search call failed - check credentials or network",
    "model_access_denied": "No configured model is available to this account (403 AccessDenied)",
}


def parallel_map(fn, items, workers, deadline=None, fallback=None):
    """Run fn over items concurrently, preserving input order.

    With a deadline, anything unfinished when it expires yields `fallback` instead
    of holding the whole stage open behind one slow host.
    """
    if not items:
        return []
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(workers, len(items))))
    try:
        futures = [pool.submit(fn, item) for item in items]
        if deadline is None:
            return [f.result() for f in futures]
        concurrent.futures.wait(futures, timeout=deadline)
        out = []
        for f in futures:
            if f.done() and not f.cancelled():
                try:
                    out.append(f.result())
                    continue
                except Exception:
                    pass
            f.cancel()
            out.append(fallback)
        return out
    finally:
        pool.shutdown(wait=False)

MODEL_LABELS = {
    "qwen3.6-flash": "Qwen 3.6 Flash",
    "deepseek-v4-pro": "DeepSeek V4 Pro",
    "deepseek-v4-flash-0731": "DeepSeek V4 Flash",
}
ALL_MODELS = list(MODEL_LABELS)

# Known name collisions. Generic identity matching already rejects most unrelated
# results; this is an explicit backstop for collisions we have actually observed.
# Known same-name companies, keyed by the target's lowercase name. A page whose
# text carries one of these phrases is about the OTHER company, so it is rejected
# outright and never re-opened by Tier B.
#
# Deliberately narrow and auditable: each entry names the other entity, never a
# bare place or industry word. "ACRO Automation Systems" (Wisconsin, assembly
# automation) collides with "ACRO 苏州", a GMP pharma-plant operator; the phrase
# must tie ACRO to that entity, so "苏州" alone is NOT listed — plenty of
# legitimate coverage mentions Suzhou.
COLLISIONS = {
    "skeqi": ["skechers", "斯凯奇", "运动鞋", "sneaker", "footwear", "shoes"],
    "acro automation systems": ["acro苏州", "acro 苏州", "acro suzhou",
                                "苏州爱克罗", "acro生物", "acrobiosystems"],
}

# Financial-source ladder, in the order the account-research brief requires:
#   1. official Investor Relations / annual reports   (tier 2, official domain)
#   2. regulatory filings                             (tier 3)
#   3. Yahoo Finance                                  (tier 4, subrank 1)
#   4. reputable financial / business publications    (tier 4, subrank 2)
IR_PATH_HINTS = ("/investor", "/investors", "/ir/", "/annual-report", "/annualreport",
                 "/financial", "/shareholder", "/results", "touzizhe", "投资者")
REGULATORY_FILINGS = ("sec.gov", "hkexnews.hk", "sse.com.cn", "szse.cn",
                      "cninfo.com.cn", "neeq.com.cn", "sedar.com", "find-and-update.company-information.service.gov.uk")
YAHOO_FINANCE_HOSTS = ("finance.yahoo.com",)
FINANCIAL_PUBS = ("bloomberg.com", "reuters.com", "marketwatch.com", "nasdaq.com",
                  "ft.com", "wsj.com", "investing.com", "cn.investing.com",
                  "seekingalpha.com", "barrons.com", "cnbc.com", "forbes.com",
                  "fool.com", "morningstar.com", "stcn.com", "cs.com.cn")
FINANCE_SOURCES = YAHOO_FINANCE_HOSTS + REGULATORY_FILINGS + FINANCIAL_PUBS

# Ordering INSIDE one tier. Only tier 4 currently holds more than one kind, so
# every other tier keeps exactly the order it had before.
SUBRANK = {"yahoo-finance": 1}


def subrank(kind):
    return SUBRANK.get(kind, 2)
GOV_MEDIA = ("gov.cn", "cnr.cn", "xinhuanet.com", "news.cn", "people.com.cn",
             "chinadaily.com.cn", "jfdaily.com.cn", "thepaper.cn", "cctv.com",
             "ce.cn", "stcn.com", "cs.com.cn", "reuters.com", "bloomberg.com")
INDUSTRY_PUBS = ("gg-lb.com", "gaogong123.com", "ofweek.com", "d1ev.com",
                 "escn.com.cn", "bjx.com.cn", "chinaaet.com", "jrj.com.cn")
PORTALS = ("sohu.com", "163.com", "qq.com", "sina.com.cn", "toutiao.com", "ifeng.com")
REGISTRIES = ("qcc.com", "tianyancha.com", "baike.baidu.com", "aiqicha", "11467.com", "jobui.com")
JOB_BOARDS = ("zhipin.com", "job", "career", "jygl", "campus", "51job", "lagou",
              "liepin", "job5156", "pjcareer")
PRODUCT_HINTS = ("product", "solution", "chanpin", "产品", "方案", "case", "业务")


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

# Every setting the app reads. On a host like Render there is no credentials
# file at all, so the environment is the source; locally the file is.
CONFIG_KEYS = (
    "DASHSCOPE_API_KEY", "DASHSCOPE_WORKSPACE_ID", "DASHSCOPE_BASE_URL", "DASHSCOPE_REGION",
    "TOKEN_PLAN_API_KEY", "TOKEN_PLAN_BASE_URL",
    "AI_MODEL_QUALITY", "AI_MODEL_FAST", "AI_MODEL_CITATION", "AI_MODEL_FALLBACKS",
    "DASHSCOPE_PATH_TEXT", "DASHSCOPE_PATH_MULTIMODAL", "DASHSCOPE_MULTIMODAL_MODELS",
    "AI_SEARCH_STRATEGY", "AI_SEARCH_ENABLED", "AI_SOURCE_ENABLED",
    "AI_CITATION_ENABLED", "AI_CITATION_FORMAT", "AI_REQUEST_TIMEOUT_MS",
    "AI_PRICE_INPUT_PER_1K", "AI_PRICE_OUTPUT_PER_1K", "AI_PRICE_CURRENCY",
    "APOLLO_API_KEY", "APOLLO_ENRICH_LIMIT",
    "APP_ACCESS_USERNAME", "APP_ACCESS_PASSWORD", "APP_ACCESS_SECRET", "APP_SERVICE_KEY",
    "ALLOWED_FRAME_ANCESTORS",
)

REQUIRED_KEYS = ("DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL",
                 "DASHSCOPE_PATH_TEXT", "DASHSCOPE_PATH_MULTIMODAL")


def find_credentials_file():
    """The local credentials file, or None when running from the environment."""
    for candidate in (HERE / "ai_credentials.env", HERE.parent / "ai_credentials.env"):
        if candidate.exists():
            return candidate
    return None


def _read_env_file(path):
    cfg = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        cfg[key.strip()] = value.strip().strip('"').strip("'")
    return cfg


def load_config():
    """Environment first, then ai_credentials.env on top when it exists.

    Local development is unchanged: the file is present and its values win.
    A deployed host has no file, so the same keys come from real env vars —
    which is the only way secrets reach a platform like Render.
    """
    cfg = {k: os.environ[k] for k in CONFIG_KEYS if os.environ.get(k)}
    source = "environment"
    path = find_credentials_file()
    if path:
        cfg.update({k: v for k, v in _read_env_file(path).items() if v})
        source = str(path)
    missing = [k for k in REQUIRED_KEYS if not cfg.get(k)]
    if missing:
        raise ValueError(
            "Missing required configuration: {}. Set them as environment "
            "variables, or copy ai_credentials.env.example to "
            "ai_credentials.env and fill it in.".format(", ".join(missing)))
    cfg["_source_file"] = source
    return cfg


def public_config(cfg):
    """Only non-secret fields — this is what the browser is allowed to see."""
    return {
        "models": [{"id": m, "label": MODEL_LABELS[m]} for m in ALL_MODELS],
        "search_strategy": cfg.get("AI_SEARCH_STRATEGY", "turbo"),
        "search_enabled": truthy(cfg.get("AI_SEARCH_ENABLED")),
        "protocol": "DashScope Native",
    }


def truthy(value, default=True):
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def post_json(url, payload, api_key, timeout):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + api_key})   # never logged or returned
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"message": raw[:400]}
    except Exception as e:
        return 0, {"message": "{}: {}".format(type(e).__name__, e)}


def _decompress(raw, content_encoding=""):
    """Some hosts gzip even when urllib advertises identity (finance.yahoo.com does).
    Undetected, the body decodes to mojibake and the page is silently useless."""
    enc = (content_encoding or "").lower()
    try:
        if "gzip" in enc or raw[:2] == b"\x1f\x8b":
            return gzip.decompress(raw)
        if "deflate" in enc:
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
        if "br" in enc:
            import brotli                       # optional; absent in the stdlib venv
            return brotli.decompress(raw)
    except Exception:
        pass                                    # fall through with the raw bytes
    return raw


def _decode(raw, content_type):
    """Chinese sites still serve GB2312/GBK; decoding as UTF-8 yields mojibake."""
    head = raw[:2048].decode("latin-1", "replace")
    m = (re.search(r"charset=([\w-]+)", content_type or "", re.I)
         or re.search(r'<meta[^>]+charset=["\']?([\w-]+)', head, re.I))
    declared = (m.group(1) if m else "utf-8").lower().replace("-", "")
    enc = ("gb18030" if declared in ("gb2312", "gbk", "gb18030")
           else "big5" if declared.startswith("big5") else "utf-8")
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


def _looks_like_tls_failure(exc):
    text = "{} {}".format(type(exc).__name__, exc).lower()
    return any(k in text for k in ("ssl", "tlsv1", "certificate", "handshake",
                                   "protocol version", "wrong_version"))


def fetch_via_curl(url, timeout=PAGE_TIMEOUT):
    """Last-resort fetch through the system curl.

    This interpreter links LibreSSL 2.8.3 (the macOS command-line-tools Python),
    which cannot complete a handshake with some perfectly healthy modern hosts -
    www.acro.com answers curl with 200 and answers urllib with
    'TLSV1_ALERT_PROTOCOL_VERSION'. Without this the site is simply invisible to
    the app and the company gets a "no information" report.
    """
    try:
        out = subprocess.run(
            ["curl", "-sSL", "--compressed", "--max-time", str(int(timeout)),
             "-A", UA, "-H", "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8",
             "-w", "\n<<<CURLURL:%{url_effective}>>>", url],
            capture_output=True, timeout=timeout + 5)
    except Exception:
        return "", url
    if out.returncode != 0 or not out.stdout:
        return "", url
    raw = out.stdout
    final = url
    marker = raw.rfind(b"<<<CURLURL:")
    if marker >= 0:
        final = raw[marker + 11:].split(b">>>")[0].decode("utf-8", "replace") or url
        raw = raw[:marker]
    return _decode(_decompress(raw), ""), final


def fetch_url(url, timeout=PAGE_TIMEOUT):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = _decompress(resp.read(), resp.headers.get("Content-Encoding", ""))
            return _decode(body, resp.headers.get("Content-Type", "")), resp.geturl()
    except urllib.error.HTTPError:
        raise                                  # a real 403/404 is an answer, not a transport fault
    except Exception as e:
        if not _looks_like_tls_failure(e):
            raise
        text, final = fetch_via_curl(url, timeout)
        if not text:
            raise
        return text, final


def fetch_page_text(url, timeout=PAGE_TIMEOUT, min_chars=400):
    """Standard fetch, then JS-bundle extraction for SPA sites that ship an HTML shell."""
    try:
        html, final = fetch_url(url, timeout)
    except urllib.error.HTTPError as e:
        return "", ("blocked" if e.code in (401, 403, 405, 429) else "http_error")
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", ""))
        return "", ("unreachable" if ("not known" in reason or "nodename" in reason) else "failed")
    except Exception:
        return "", "failed"
    text = html_to_text(html)
    if len(text) >= min_chars:
        return text, "standard"
    # Only 3 bundles, on a short timeout: this fallback is for site-builder shells,
    # and sequential sub-fetches here dominated the evidence stage (97.8s measured).
    parts = []
    for src in re.findall(r'<script[^>]+src=[\'"]([^\'"]+)[\'"]', html, re.I)[:3]:
        try:
            bundle, _ = fetch_url(urllib.parse.urljoin(final, src), min(timeout, 6))
        except Exception:
            continue
        for a, b in (("\\u003c", "<"), ("\\u003e", ">"), ("\\u0026", "&"),
                     ('\\"', '"'), ("\\/", "/"), ("\\r\\n", "\n"), ("\\n", "\n")):
            bundle = bundle.replace(a, b)
        parts.append(html_to_text(bundle))
    bundled = "\n".join(parts).strip()
    if len(bundled) >= min_chars:
        return bundled, "script-bundle"
    return (text or bundled), "insufficient"


# ---------------------------------------------------------------------------
# Official-website discovery
# ---------------------------------------------------------------------------

def _name_tokens(name):
    raw = re.split(r"[^A-Za-z0-9\u4e00-\u9fff]+", (name or "").lower())
    return [t for t in raw if t and t not in GENERIC_NAME_WORDS]


def _is_blocked_host(host):
    h = (host or "").lower()
    return any(b in h for b in NOT_OFFICIAL_HOSTS)


def score_official_candidate(url, title, name, name_cn, page_text=""):
    """How likely is this URL the company's own site? Returns (score, signals)."""
    host = urllib.parse.urlparse(url).netloc.lower().replace("www.", "")
    if not host or _is_blocked_host(host):
        return -1, ["blocked-host"]
    signals, score = [], 0
    core = host.split(":")[0].replace("-", "").replace(".", "")
    for t in _name_tokens(name):
        if len(t) >= 3 and t in core:
            score += 4
            signals.append("domain~name:{}".format(t))
            break
    hay = "{} {}".format(title or "", page_text[:4000]).lower()
    if name and name.lower() in hay:
        score += 2; signals.append("name-in-page")
    if name_cn and name_cn in hay:
        score += 2; signals.append("cn-name-in-page")
    matched = sum(1 for t in _name_tokens(name) if t in hay)
    if matched >= 2:
        score += 1; signals.append("tokens-in-page:{}".format(matched))
    for kw, label in (("about", "about"), ("contact", "contact"), ("product", "products"),
                      ("solution", "solutions"), ("公司简介", "about-cn"),
                      ("联系我们", "contact-cn"), ("产品", "products-cn")):
        if kw in hay:
            score += 1; signals.append(label)
    if len(urllib.parse.urlparse(url).path.strip("/").split("/")) <= 1:
        score += 1; signals.append("shallow-path")
    return score, signals


def validate_website(url, name, name_cn):
    """Fetch a candidate and decide whether it is really this company's site.

    A host that blocks automation but whose domain distinctively encodes the
    company name is still that company's site; evidence then comes from search.
    """
    text, method = fetch_page_text(url)
    if not text:
        if method == "blocked":
            score, sigs = score_official_candidate(url, "", name, name_cn, "")
            strong = any(sg.startswith("domain~name:") and len(sg.split(":")[1]) >= 5
                         and sg.split(":")[1] not in WEAK_DOMAIN_TOKENS for sg in sigs)
            if score >= 4 and strong:
                return {"ok": True, "reason": "site_blocked", "score": score,
                        "signals": sigs + ["blocked-but-domain-matches"],
                        "text": "", "method": method}
            return {"ok": False, "reason": "site_blocked", "score": 0, "signals": [],
                    "text": "", "method": method}
        if method == "unreachable":
            return {"ok": False, "reason": "site_unverified", "score": 0, "signals": [],
                    "text": "", "method": method}
        return {"ok": False, "reason": "site_blocked", "score": 0, "signals": [],
                "text": "", "method": method}
    if method == "insufficient" and len(text) < 120:
        return {"ok": False, "reason": "js_unextractable", "score": 0, "signals": [],
                "text": text, "method": method}
    score, signals = score_official_candidate(url, "", name, name_cn, text)
    # A generic domain token plus ordinary page furniture is not identity:
    # automation.com is not ACRO. The company must actually be named.
    named = any(sg in ("name-in-page", "cn-name-in-page") for sg in signals)
    multi = any(sg.startswith("tokens-in-page:") for sg in signals)
    ok = score >= 5 and (named or multi)
    return {"ok": ok, "reason": None if ok else "website_mismatch",
            "score": score, "signals": signals, "text": text, "method": method}


def guess_domains(name):
    """Likely official domains built from the company name.

    The search backend indexes the Chinese web far better than the English one
    and often cannot surface a Western company's own site at all. Probing a few
    constructed domains costs one HTTP request each and finds them directly.
    """
    tokens = [t for t in _name_tokens(name) if t.isascii() and len(t) > 1]
    if not tokens:
        return []
    stems = ["".join(tokens)]
    if len(tokens) >= 2:
        stems += ["".join(tokens[:2]), "-".join(tokens[:2])]
    stems.append(tokens[0])
    for t in tokens:
        if len(t) >= 4 and t not in WEAK_DOMAIN_TOKENS:
            stems.append(t)
    # TLD-major: every stem's .com before any alternate TLD, so a short probe
    # budget still reaches the parent brand (honda.com).
    out, seen = [], set()
    for tld in (".com", ".cn", ".com.cn", ".net"):
        for stem in stems:
            if not stem or len(stem) < 3:
                continue
            host = stem + tld
            if host not in seen:
                seen.add(host)
                out.append("https://www." + host)
    return out[:12]


def _domain_covers_name(url, name):
    """Does this guessed host encode EVERY distinctive token of the name?

    guess_domains() also emits single-token stems, which is what finds honda.com
    for "American Honda Motor Co.". The same rule turns "TE Connectivity" into
    connectivity.com - a real but unrelated business. Partial stems therefore
    have to be corroborated on the page itself (see check() below).
    """
    core = urllib.parse.urlparse(url).netloc.lower().replace("www.", "")
    core = core.split(":")[0].replace("-", "").replace(".", "")
    tokens = [t for t in _name_tokens(name) if t.isascii() and len(t) > 1]
    return bool(tokens) and all(t in core for t in tokens)


def _tokens_on_page(name, text):
    """Every distinctive name token present as a whole word in the page text."""
    tokens = [t for t in _name_tokens(name) if t.isascii() and len(t) > 1]
    if not tokens or not text:
        return False
    low = text[:8000].lower()
    return all(re.search(r"\b" + re.escape(t) + r"\b", low) for t in tokens)


def probe_guessed_domains(name, name_cn, progress=None):
    progress = progress or (lambda *_a, **_k: None)
    candidates = guess_domains(name)
    if not candidates:
        return None
    progress("discover", "Probing {} likely domains for {}".format(len(candidates), name))

    def check(url):
        try:
            v = validate_website(url, name, name_cn)
            if not v["ok"]:
                return None
            # A domain that drops part of the company name must prove itself:
            # the page has to actually name the company. Without this the
            # guesser happily adopts another company's website as "official".
            if not _domain_covers_name(url, name):
                text = v.get("text") or ""
                named = (name and name.lower() in text[:8000].lower()) or \
                        (name_cn and name_cn in text[:8000]) or \
                        _tokens_on_page(name, text)
                # A host that blocks automation cannot corroborate anything, so
                # validate_website's stricter blocked-host rule stands in for the
                # page check (this is what keeps honda.com for American Honda).
                blocked_strong = "blocked-but-domain-matches" in v.get("signals", [])
                if not named and not blocked_strong:
                    return None
            return (url, v)
        except Exception:
            return None

    for hit in parallel_map(check, candidates, FETCH_CONCURRENCY,
                            deadline=EVIDENCE_STAGE_DEADLINE, fallback=None):
        if hit:
            url, v = hit
            return {"website": url, "status": "auto_discovered", "score": v["score"],
                    "signals": v.get("signals", []) + ["domain-guess"], "text": v["text"]}
    return None


def discover_official_website(name, name_cn, cfg, timeout, progress=None):
    """Find and validate the company's own site from its name alone.

    Never takes the first search result: candidates are scored on domain/name
    similarity and on-page identity, and directories, encyclopedias, social and
    news hosts are excluded from being the answer.
    """
    progress = progress or (lambda *_a, **_k: None)
    progress("discover", "Discovering official website...")
    guessed = probe_guessed_domains(name, name_cn, progress)
    if guessed:
        progress("discover", "Official website identified: {}".format(guessed["website"]))
        return guessed

    both = " ".join(x for x in (name, name_cn) if x)
    queries = ["{} 官网".format(both), "{} official website".format(name),
               "{} 公司 简介".format(both)]
    outcomes = parallel_map(lambda q: run_search(q, cfg, timeout), queries, SEARCH_CONCURRENCY)
    seen, candidates = set(), []
    for _status, results in outcomes:
        for item in results:
            url = item.get("url") or ""
            host = urllib.parse.urlparse(url).netloc.lower().replace("www.", "")
            if not host or host in seen or _is_blocked_host(host):
                continue
            seen.add(host)
            pre, _sig = score_official_candidate(url, item.get("title", ""), name, name_cn)
            if pre < 0:
                continue
            root = "{}://{}".format(urllib.parse.urlparse(url).scheme or "https", host)
            candidates.append((pre, root))
    candidates.sort(key=lambda c: -c[0])
    for _pre, root in candidates[:5]:
        v = validate_website(root, name, name_cn)
        if v["ok"]:
            progress("discover", "Official website identified: {}".format(root))
            return {"website": root, "status": "auto_discovered", "score": v["score"],
                    "signals": v.get("signals", []), "text": v["text"]}
    return {"website": "", "status": "unverified", "score": 0, "signals": [], "text": ""}


def resolve_website(name, name_cn, website, cfg, timeout, progress=None, trust=False):
    """Supplied URL first, treated as a hint; discovery when it does not hold up.

    `trust` is set when a person corrected the URL by hand in the batch table:
    we still fetch it, but we do not second-guess their identity judgement.
    """
    progress = progress or (lambda *_a, **_k: None)
    if website:
        url = website if re.match(r"^https?://", website, re.I) else "https://" + website.lstrip("/")
        v = validate_website(url, name, name_cn)
        if v["ok"]:
            # A hand-corrected URL stays labelled as such even when it validates,
            # so the row shows who chose it.
            return {"website": url, "status": "manual" if trust else "provided",
                    "score": v["score"], "signals": v.get("signals", []), "text": v["text"]}
        if trust and v.get("method") != "unreachable":
            progress("discover", "Using manually corrected website: {}".format(url))
            return {"website": url, "status": "manual", "score": v["score"],
                    "signals": v.get("signals", []) + ["manually-corrected"],
                    "text": v.get("text") or ""}
        if trust:
            # Unreachable, but the person told us this is the company. Keep their
            # URL on the record and let broader web search supply the evidence
            # rather than failing outright.
            progress("discover", "Manually corrected website unreachable - "
                                 "continuing with broader web search")
            return {"website": url, "status": "manual", "score": 0,
                    "signals": ["manually-corrected", "unreachable"], "text": ""}
        progress("discover", "Supplied website did not validate - discovering")
    return discover_official_website(name, name_cn, cfg, timeout, progress)


def _alias_from_text(text, name):
    """The alias half of discover_aliases, for a page already fetched."""
    if not text:
        return ""
    m = re.search(re.escape(name) + r"\s*([\u4e00-\u9fff]{2,8})", text[:1500], re.I) if name else None
    if m:
        return m.group(1)
    stop = {"有限", "公司", "科技", "智能", "装备", "系统", "技术", "解决", "方案", "产品",
            "服务", "行业", "企业", "制造", "设备", "生产", "工业", "中国", "新能源", "首页"}
    counts = {}
    for token in re.findall(r"[\u4e00-\u9fff]{2,4}", text[:6000]):
        if token not in stop:
            counts[token] = counts.get(token, 0) + 1
    if not counts:
        return ""
    alias = max(counts.items(), key=lambda kv: (kv[1], len(kv[0])))[0]
    return alias if counts[alias] >= 3 else ""


# ---------------------------------------------------------------------------
# Stage 1 — short search queries (never the research prompt)
# ---------------------------------------------------------------------------

def plan_queries_labeled(name, name_cn, domain, ticker=""):
    """Short keyword queries across the research areas the report needs.

    The official website is one source category, not the corpus: strategy,
    projects, leadership, finance, sustainability, competitors and vendor
    relationships each get their own search rather than sharing whatever the
    overview query happened to return. Chinese variants are included because
    this backend indexes the Chinese web far better than the English one.
    """
    both = " ".join(x for x in (name, name_cn) if x)
    q = [
        ("official website",   '"{}" official website about'.format(name)),
        ("company overview",   "{} 公司简介 产品".format(both)),
        ("products",           '"{}" company products'.format(name)),
        ("strategy",           "{} 战略 投资 规划".format(both)),
        ("strategy",           '"{}" strategy expansion'.format(name)),
        ("manufacturing",      "{} 工厂 制造 产能".format(both)),
        ("projects",           '"{}" new plant expansion capacity'.format(name)),
        ("automation",         '"{}" automation manufacturing technology'.format(name)),
        ("automation vendors", '"{}" Siemens ABB FANUC KUKA supplier'.format(name)),
        ("leadership",         '"{}" CEO executives leadership'.format(name)),
        ("leadership",         "{} 高管 负责人".format(both)),
        ("financials",         '"{}" revenue annual report'.format(name)),
        ("financials",         '"{}" investor relations annual report'.format(name)),
        ("sustainability",     '"{}" sustainability carbon ESG'.format(name)),
        ("recent news",        '"{}" latest news'.format(name)),
        ("recent news",        "{} 最新消息".format(both)),
        ("competitors",        '"{}" competitors market position'.format(name)),
        ("partners/customers", '"{}" partnership customer case study'.format(name)),
        ("industry trends",    '"{}" industry trends'.format(name)),
    ]
    # Yahoo Finance and filings queries run for PUBLIC companies only. A ticker is
    # only ever set once the listing has been validated, so a private company is
    # never pushed towards financial sources that cannot exist for it.
    if ticker:
        q.append(("financials", '"{}" SEC filing 10-K annual report'.format(name)))
        for yq in fin.yahoo_queries(name, ticker):
            q.append(("financials (Yahoo)", yq))
    if domain:
        q.insert(1, ("official website", "site:{} {} 产品".format(domain, name_cn or name)))
    seen, out = set(), []
    for label, query in q:
        query = re.sub(r"\s+", " ", query).strip()
        if query and query.lower() not in seen:
            seen.add(query.lower())
            out.append((label, query))
    return out


def plan_queries(name, name_cn, domain, ticker=""):
    return [query for _label, query in plan_queries_labeled(name, name_cn, domain, ticker)]


# ---------------------------------------------------------------------------
# Stage 2 — retrieval
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Model access
# ---------------------------------------------------------------------------
# DashScope answers a model the account is not entitled to with
#   HTTP 403  code=AccessDenied.Unpurchased
#   "Access to model denied. Please make sure you are eligible for using the model."
# That is a BILLING condition, not a research one. Conflating it with "not enough
# evidence" hides an outage behind a plausible-looking empty report, so access
# failures are tracked separately and reported as themselves.

MODEL_ACCESS = {}                      # model id -> {"ok", "code", "message"}
_ACCESS_LOCK = threading.Lock()

ACCESS_DENIED_HINT = ("Model not enabled for this DashScope account "
                      "(403 AccessDenied). Enable or purchase it in the Bailian console.")


def is_access_denied(status, data):
    code = str((data or {}).get("code") or "")
    if status == 403 and code.startswith("AccessDenied"):
        return True
    # Some tenants answer with the message but a different code.
    msg = str((data or {}).get("message") or "").lower()
    return status == 403 and "access to model denied" in msg


def record_access(model, status, data):
    """Remember what happened, so a denied model is skipped for the rest of the run."""
    if is_access_denied(status, data):
        with _ACCESS_LOCK:
            MODEL_ACCESS[model] = {
                "ok": False,
                "code": str((data or {}).get("code") or "AccessDenied"),
                "message": str((data or {}).get("message") or "")[:200]}
        return False
    if status == 200:
        with _ACCESS_LOCK:
            MODEL_ACCESS[model] = {"ok": True, "code": "", "message": ""}
    return True


def model_available(model):
    """Unknown models are assumed available until one call proves otherwise."""
    rec = MODEL_ACCESS.get(model)
    return True if rec is None else rec["ok"]


def access_note(model):
    rec = MODEL_ACCESS.get(model)
    return "" if not rec or rec["ok"] else rec["message"] or rec["code"]


def model_candidates(cfg, preferred=None):
    """Ordered models to try: the preferred one, the configured trio, then any
    extra ids in AI_MODEL_FALLBACKS. Duplicates and known-denied models drop out."""
    extra = [m.strip() for m in (cfg.get("AI_MODEL_FALLBACKS") or "").split(",") if m.strip()]
    ordered = ([preferred] if preferred else []) + list(RETRIEVAL_LADDER) + ALL_MODELS + extra
    out = []
    for model in ordered:
        if model and model not in out:
            out.append(model)
    return [m for m in out if model_available(m)]


def access_report():
    """Non-secret snapshot of what is and is not reachable, for the UI."""
    with _ACCESS_LOCK:
        return {m: dict(rec) for m, rec in MODEL_ACCESS.items()}


# Endpoint routing is per-model and getting it wrong returns
# 400 "url error, please check url". DASHSCOPE_MULTIMODAL_MODELS lists the models
# we already know about; anything added via AI_MODEL_FALLBACKS is learned at
# runtime the first time the wrong endpoint is tried.
ENDPOINT_LEARNED = {}


def endpoint_for(model, cfg):
    if model in ENDPOINT_LEARNED:
        is_multi = ENDPOINT_LEARNED[model]
        return (cfg["DASHSCOPE_PATH_MULTIMODAL"] if is_multi
                else cfg["DASHSCOPE_PATH_TEXT"]), is_multi
    multimodal = [m.strip() for m in cfg.get("DASHSCOPE_MULTIMODAL_MODELS", "").split(",") if m.strip()]
    if model in multimodal:
        return cfg["DASHSCOPE_PATH_MULTIMODAL"], True
    return cfg["DASHSCOPE_PATH_TEXT"], False


def wrong_endpoint(status, data):
    return status == 400 and "url error" in str((data or {}).get("message") or "").lower()


def learn_endpoint(model, is_multi):
    ENDPOINT_LEARNED[model] = not is_multi        # the other one is the right one


def search_options(cfg):
    opts = {"search_strategy": cfg.get("AI_SEARCH_STRATEGY", "turbo")}
    if truthy(cfg.get("AI_SOURCE_ENABLED")):
        opts["enable_source"] = True
    if truthy(cfg.get("AI_CITATION_ENABLED")):
        opts["enable_citation"] = True
        opts["citation_format"] = cfg.get("AI_CITATION_FORMAT", "[<number>]")
    # freshness is deliberately NOT set: it collapsed results to 0-1 in testing.
    return opts


# Retrieval fallback ladder. The models share one search backend, but they do not
# return identical result sets for the same query, so a second and third pass buy
# real coverage for companies the first pass misses. Cheapest first, and a later
# wave only runs when the evidence so far is not good enough.
RETRIEVAL_LADDER = ("qwen3.6-flash", "deepseek-v4-flash-0731", "deepseek-v4-pro")


def run_search(query, cfg, timeout, model=None):
    """Bailian exposes web search only through a model call, so the model is
    used purely as a retrieval vehicle. Its prose is discarded."""
    model = model or cfg.get("AI_MODEL_FAST", "deepseek-v4-flash-0731")
    path, is_multi = endpoint_for(model, cfg)
    url = cfg["DASHSCOPE_BASE_URL"].rstrip("/") + path
    params = {"enable_search": True, "search_options": search_options(cfg),
              "max_tokens": RETRIEVAL_MAX_TOKENS}
    if not is_multi:
        params["result_format"] = "message"
    content = [{"text": query}] if is_multi else query
    def _post():
        return post_json(url, {
            "model": model,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": params,
        }, cfg["DASHSCOPE_API_KEY"], timeout)

    status, data = _post()
    if wrong_endpoint(status, data):
        learn_endpoint(model, is_multi)
        path, is_multi = endpoint_for(model, cfg)
        url = cfg["DASHSCOPE_BASE_URL"].rstrip("/") + path
        params = {"enable_search": True, "search_options": search_options(cfg),
                  "max_tokens": RETRIEVAL_MAX_TOKENS}
        if not is_multi:
            params["result_format"] = "message"
        content = [{"text": query}] if is_multi else query
        status, data = _post()
    record_access(model, status, data)
    results = ((data.get("output") or {}).get("search_info") or {}).get("search_results") or []
    return status, [r for r in results if r.get("url")]


def classify(url, domain):
    host = urllib.parse.urlparse(url).netloc.lower().replace("www.", "")
    path = urllib.parse.urlparse(url).path.lower()
    if domain and (host == domain or host.endswith("." + domain)):
        # Investor Relations is the top financial source, above any filing or portal.
        if any(k in path for k in IR_PATH_HINTS) or host.split(".")[0] in ("ir", "investor", "investors"):
            return 2, "official-investor-relations"
        if any(k in path for k in PRODUCT_HINTS):
            return 2, "official-product-page"
        return 1, "official-website"
    # Job boards and registries first: many sit on .edu.cn/.gov.cn hosts and
    # would otherwise be promoted into the government/media tier.
    if any(d in host or d in path for d in JOB_BOARDS):
        return 6, "job-board"
    if any(d in host for d in REGISTRIES):
        return 6, "registry-or-encyclopedia"
    # Finance hosts are checked before GOV_MEDIA: Bloomberg and Reuters appear in
    # both lists, and their financial role is the more specific one.
    if any(host == d or host.endswith("." + d) for d in REGULATORY_FILINGS):
        return 3, "regulatory-filing"
    if any(host == d or host.endswith("." + d) for d in YAHOO_FINANCE_HOSTS):
        return 4, "yahoo-finance"
    if any(host == d or host.endswith("." + d) for d in FINANCIAL_PUBS):
        return 4, "financial-publication"
    if any(host == d or host.endswith("." + d) for d in GOV_MEDIA):
        return 4, "government-or-media"
    if any(d in host for d in INDUSTRY_PUBS):
        return 5, "industry-publication"
    if any(d in host for d in PORTALS):
        return 5, "portal-republication"
    return 6, "other-web-source"


# Words that are never distinctive on their own: a title containing only one of
# these tells us nothing about which company it concerns.
COMMON_TOKENS = {
    "auto", "motor", "motors", "energy", "battery", "batteries", "power", "systems",
    "system", "automation", "engineering", "electric", "electronics", "industries",
    "industrial", "manufacturing", "machine", "machinery", "equipment", "materials",
    "digital", "data", "smart", "advanced", "precision", "products", "service",
    "services", "global", "national", "international", "european", "american",
    "china", "chinese", "germany", "german", "france", "french", "japan", "korea",
    "new", "next", "first", "one", "pro", "plus", "max", "core", "prime", "united",
    "general", "standard", "central", "modern", "future", "green", "clean", "eco",
    "cell", "cells", "pack", "module", "storage", "mobility", "vehicle", "vehicles",
}


def _mentions(needle, text):
    """Is `needle` present as a standalone word?

    NOT \\b: Python puts word boundaries between \\w and \\W, and CJK characters
    are \\w, so "创企Verkor融资" has no boundary around the Latin name and \\b
    misses it. A third of this corpus is Chinese-language coverage, so that
    silently discarded exactly the third-party reporting we want. ASCII-only
    edges keep "myverkorpage" out while letting CJK context in.
    """
    if not needle:
        return False
    return bool(re.search(r"(?<![A-Za-z0-9])" + re.escape(needle) + r"(?![A-Za-z0-9])",
                          text or "", re.I))


def core_name(name):
    """The company name with legal suffixes removed.

    "Manz AG" -> "Manz". Matching the full legal form verbatim is why a headline
    reading "Manz announces..." was rejected as unrelated: the suffix the press
    never uses was required to be present.
    """
    n = (name or "").strip()
    prev = None
    while n and n != prev:
        prev = n
        n = re.sub(r"[,\s]+(?:{})\.?$".format("|".join([
            "ag", "gmbh", "inc", "incorporated", "llc", "l\.l\.c", "ltd", "limited",
            "corp", "corporation", "co", "company", "s\.a", "sa", "sas", "plc",
            "bv", "b\.v", "nv", "n\.v", "srl", "s\.r\.l", "spa", "s\.p\.a",
            "pte", "kk", "k\.k", "oy", "ab", "as", "a\.s", "aps", "holding",
            "holdings", "group",
        ])), "", n, flags=re.I).strip()
    return n or (name or "").strip()


def distinctive_tokens(name):
    """Tokens that actually identify THIS company, strongest first."""
    toks = _name_tokens(core_name(name))
    return [t for t in toks if t not in COMMON_TOKENS]


def identity_ok(hay, name, name_cn, domain, url):
    """Does this result plausibly concern the target company?

    Two earlier rules lost real coverage. Demanding the FULL name verbatim
    discarded 74 of 96 results for "AMADA WELD TECH". Then a six-character floor
    on a single token discarded EVERY third-party result for "Manz AG" (166 of
    166) and "ACRO Automation Systems": the distinctive token is short, and the
    press never writes the legal suffix.

    Length is not what makes a token distinctive - being the company's own word
    rather than an industry word is. COMMON_TOKENS carries that judgement, and
    COLLISIONS still rejects known name clashes.
    """
    host = urllib.parse.urlparse(url).netloc.lower().replace("www.", "")
    if domain and (host == domain or host.endswith("." + domain)):
        return True, "official domain"
    low = hay.lower()
    reject = COLLISIONS.get((name or "").lower().strip(), [])
    hit_reject = [t for t in reject if t.lower() in low]
    has_cn = bool(name_cn) and name_cn in hay
    if hit_reject and not has_cn:
        return False, "unrelated entity ({})".format(", ".join(hit_reject[:2]))
    if has_cn:
        return True, "chinese-name match"
    if _mentions(name, hay):
        return True, "full-name match"
    core = core_name(name)
    if core and core.lower() != (name or "").lower() and _mentions(core, hay):
        return True, "name match without legal suffix ({})".format(core)
    distinct = distinctive_tokens(name)
    tokens = _name_tokens(name)
    # Whole words only. Raw substring matching let "acro" match "macro" and
    # "acrobat", so a Jiangsu government notice containing neither ACRO nor any
    # automation vendor was accepted as ACRO coverage.
    matched = [t for t in tokens if t and _mentions(t, low)]
    # Two matches only count when at least one is the company's OWN word. Without
    # this, "Automation systems market to grow 8%" matched ACRO Automation
    # Systems, and "Battery systems for storage" matched Elite Battery Systems:
    # a pair of industry words identifies an industry, not a company.
    if len(matched) >= 2 and any(t in distinct for t in matched):
        return True, "name tokens ({})".format(", ".join(matched[:3]))
    hit = [t for t in distinct if _mentions(t, low)]
    if hit:
        t = hit[0]
        if t in host.replace("-", "").replace(".", ""):
            return True, "name token in host ({})".format(t)
        # No length floor: "manz" and "acro" identify their companies as surely
        # as "verkor" does. What disqualifies a token is being an industry word.
        return True, "distinctive name token ({})".format(t)
    return False, "no identity evidence"


# Hosts whose pages are not evidence about a company however plausible the
# title looks: social feeds, marketplaces, job boards, directories.
UNVERIFIABLE_HOSTS = (
    "facebook.", "twitter.", "x.com", "instagram.", "tiktok.", "pinterest.",
    "youtube.", "reddit.", "quora.", "zhihu.com", "weibo.",
    "indeed.", "glassdoor.", "linkedin.com/jobs", "jobs.", "careers.",
    "amazon.", "ebay.", "alibaba.com", "made-in-china.com", "aliexpress.",
    "yellowpages.", "yelp.", "tripadvisor.",
    # Recruitment sites carry a company page for everyone and say nothing about
    # what the company does. zhaopin was retained as Manz "evidence".
    "zhaopin.", "liepin.", "51job.", "lagou.", "boss.zhipin", "jobs.",
)

# ── Tier B budget ────────────────────────────────────────────────────────────
# Measured over 168 deferred candidates across Verkor, Manz AG and ACRO: 14 were
# genuinely about the company. The old ordering surfaced 6 of those 14 in the
# first 10 fetches; ordering by source category surfaces 10. Extending to 20
# adds only 2 more, so the batch stays at 10 and grows only when the evidence is
# still thin. The ceiling is a guardrail, not a target.
TIER_B_FETCH_LIMIT = 10          # first batch
TIER_B_MAX_FETCHES = 30          # hard ceiling across all batches

# Measured hit rate per category, same sample:
#   financial / investor  7/13  (54%)   <- strongest signal by a wide margin
#   news / industry       4/26  (15%)
#   other                 3/124  (2%)
#   government            0/5    (0%)   <- and the source of two false matches
# Government is NOT promoted: the sample gave no support for it.
# Order is CONFIGURABLE: three companies is enough to beat the previous ordering,
# not enough to fix a universal law. Override with TIER_B_CATEGORY_ORDER, e.g.
# "news,financial,other,government".
_DEFAULT_TIER_B_ORDER = ("financial", "news", "other", "government")


def _tier_b_order():
    raw = (os.environ.get("TIER_B_CATEGORY_ORDER") or "").strip()
    cats = [c.strip() for c in raw.split(",") if c.strip()] if raw else list(_DEFAULT_TIER_B_ORDER)
    for c in _DEFAULT_TIER_B_ORDER:               # never drop a category entirely
        if c not in cats:
            cats.append(c)
    return {c: i for i, c in enumerate(cats)}


TIER_B_CATEGORY_RANK = _tier_b_order()

_FIN_HOSTS = ("gelonghui", "futunn", "finance.yahoo", "xueqiu", "stockstar", "cninfo",
              "investing.com", "moomoo", "caifuhao.eastmoney", "eastmoney", "stcn",
              "yicai", "reuters.com/markets", "bloomberg")
_NEWS_HOSTS = ("reuters", "ft.com", "wsj", "handelsblatt", "faz.net", "jiemian", "caixin",
               "sina", "sohu", "163.com", "qq.com", "ofweek", "gongkong", "electrive",
               "just-auto", "autonews", "industryweek", "assemblymag", "lesechos",
               "usinenouvelle", "cnbeta", "nikkei", "asia.nikkei")
_GOV_HOSTS = (".gov", ".gouv", "europa.eu", "gov.cn", "miit.", "ndrc.")


def source_category(url):
    """Coarse category used to order Tier B fetching. Measured, not assumed."""
    host = urllib.parse.urlparse(url).netloc.lower().replace("www.", "")
    low = url.lower()
    if any(k in host or k in low for k in _GOV_HOSTS):
        return "government"
    if any(k in host for k in _FIN_HOSTS):
        return "financial"
    if any(k in host for k in _NEWS_HOSTS):
        return "news"
    return "other"


def _plausible_host(url):
    """Could a page here be real coverage of a company?"""
    host = urllib.parse.urlparse(url).netloc.lower().replace("www.", "")
    if not host or _is_blocked_host(host):
        return False
    return not any(b in host or b in url.lower() for b in UNVERIFIABLE_HOSTS)


def dedupe_and_rank(raw, name, name_cn, domain):
    """Split candidates three ways instead of keeping one and binning the rest.

    Tier A - identity confirmed from title/site/URL.
    Tier B - identity uncertain, host plausible. A headline like "Dunkirk
             gigafactory reaches first cell production" never names Verkor, and
             the search provider returns no snippet, so the title alone CANNOT
             decide. These are opened and judged on their text.
    Rejected - a known name collision, or a host whose pages are not evidence.
    """
    by_url, pending, rejected = {}, {}, []
    for item in raw:
        url = item.get("url") or ""
        key = url_key(url)
        if not key:
            continue
        if key in by_url:
            by_url[key]["hits"] += 1
            t = item.get("topic")
            if t and t not in by_url[key]["topics"]:
                by_url[key]["topics"].append(t)
            continue
        if key in pending:
            pending[key]["hits"] += 1
            t = item.get("topic")
            if t and t not in pending[key]["topics"]:
                pending[key]["topics"].append(t)
            continue
        hay = "{} {} {}".format(item.get("title", ""), item.get("site_name", ""), url)
        ok, reason = identity_ok(hay, name, name_cn, domain, url)
        tier, kind = classify(url, domain)
        rec = {"title": item.get("title", ""), "url": url,
               "site_name": item.get("site_name", ""), "tier": tier,
               "source_type": kind, "official": tier <= 2, "hits": 1,
               "topics": [item["topic"]] if item.get("topic") else []}
        if ok:
            by_url[key] = rec
            continue
        # A collision is a decision, not an uncertainty: never re-open those.
        if reason.startswith("unrelated entity") or not _plausible_host(url):
            rejected.append({"title": rec["title"], "url": url, "reason": reason})
            continue
        rec["needs_verification"] = True
        pending[key] = rec
    ranked = sorted(by_url.values(),
                    key=lambda s: (s["tier"], subrank(s["source_type"]), -s["hits"], s["url"]))
    for rec in pending.values():
        rec["category"] = source_category(rec["url"])
    # Category first: it predicts relevance far better than tier does among
    # candidates that already failed the title test.
    tier_b = sorted(pending.values(),
                    key=lambda s: (TIER_B_CATEGORY_RANK.get(s["category"], 4),
                                   s["tier"], subrank(s["source_type"]), -s["hits"], s["url"]))
    return ranked, rejected, tier_b


def apply_evidence_caps(site_evidence, web_evidence):
    """The production retention rule, as a function.

    Extracted so sufficiency can be judged on the evidence that will actually
    SURVIVE, not on candidates that the caps are about to discard. Manz verified
    7 Tier B sources and kept 3; counting the 7 declared success too early.
    Returns (retained, dropped).
    """
    merged = sorted(list(site_evidence) + list(web_evidence),
                    key=lambda e: (e["tier"], subrank(e["source_type"])))
    # With a well-covered official site, low-tier trade portals and marketplace
    # pages add noise rather than evidence. Quality over source count.
    strong = sum(1 for e in merged if e["tier"] <= 2)
    low_budget = 2 if strong >= 5 else MAX_LOW_TIER_ITEMS
    retained, dropped, seen, low_used = [], [], set(), 0
    for item in merged:
        key = url_key(item["url"])
        if key in seen:
            continue
        if len(retained) >= MAX_EVIDENCE_ITEMS:
            dropped.append(dict(item, drop_reason="evidence cap")); continue
        # Measured 2026-09-04: this budget was discarding VERIFIED third-party
        # sources - for Manz AG, four financial pages that each added a new host,
        # a new category and new topics - because Chinese financial hosts are
        # tier 6. The exemption is deliberately narrow: third-party AND proven by
        # page content. No domain or category is promoted to a higher tier.
        if item["tier"] >= 6 and not (item.get("content_verified")
                                      and not item.get("official")):
            if low_used >= low_budget:
                dropped.append(dict(item, drop_reason="low-tier budget")); continue
            low_used += 1
        seen.add(key)
        item = dict(item)
        item["id"] = len(retained) + 1
        retained.append(item)
    return retained, dropped


# Independent corroboration, measured on the evidence that survives the caps.
# The company's own domain is counted SEPARATELY: ten pages from one site is one
# perspective repeated ten times, and must not satisfy a diversity test.
SUFFICIENT_THIRD_PARTY = 4
SUFFICIENT_THIRD_PARTY_HOSTS = 3
SUFFICIENT_CATEGORIES = 2


def evidence_sufficiency(retained, official_domain):
    """Is the RETAINED evidence broad enough to stop fetching?"""
    dom = (official_domain or "").lower().replace("www.", "")
    third = [e for e in retained
             if not dom or dom not in (e.get("url") or "").lower()]
    hosts = {urllib.parse.urlparse(e["url"]).netloc.lower().replace("www.", "")
             for e in third}
    cats = {source_category(e["url"]) for e in third}
    topics = set()
    for e in retained:
        topics.update(e.get("topics") or [])
    state = {"retained": len(retained), "third_party": len(third),
             "third_party_hosts": len(hosts), "categories": len(cats),
             "topics": len(topics)}
    state["enough"] = (len(third) >= SUFFICIENT_THIRD_PARTY
                       and len(hosts) >= SUFFICIENT_THIRD_PARTY_HOSTS
                       and len(cats) >= SUFFICIENT_CATEGORIES)
    return state


def verify_from_text(text, name, name_cn, url):
    """Does the PAGE say it is about this company? Same rules as the title test,
    applied to what the article actually contains."""
    if not text:
        return False, "no text"
    head = text[:6000]                     # the lede carries the subject
    return identity_ok(head, name, name_cn, "", url)


# ---------------------------------------------------------------------------
# Stage 3 — evidence
# ---------------------------------------------------------------------------

def build_evidence(ranked, name, name_cn, domain, official_url, progress=None,
                   official_text=""):
    seeds = []
    if official_url and not any(s["official"] for s in ranked):
        tier, kind = classify(official_url, domain)
        seeds.append({"title": "(official website)", "url": official_url, "site_name": domain,
                      "tier": tier, "source_type": kind, "official": True, "hits": 1,
                      "topics": ["company overview"]})
    # Choose the candidate slate first, then fetch those pages concurrently.
    # Sequential fetching cost 29.8s in the baseline, 21.7s of it one slow host.
    slate, low_attempts = [], 0
    for src in seeds + ranked:
        if len(slate) >= MAX_PAGES_TO_FETCH:
            break
        if src["tier"] >= 6:
            # Attempt more low-tier pages than we will keep: many block bots or 404,
            # so an attempt-based cap silently starves the evidence set.
            if low_attempts >= LOW_TIER_ATTEMPTS:
                continue
            low_attempts += 1
        slate.append(src)

    if progress:
        progress("Reading {} source pages...".format(len(slate)))

    def _load(src):
        if src.get("page_text"):
            # Tier B already downloaded this to verify identity; do not pay twice.
            return src["page_text"], "verified-tier-b"
        if src["official"] and official_text:
            return official_text, "cached-official"      # already fetched for alias discovery
        return fetch_page_text(src["url"])

    pages = parallel_map(_load, slate, FETCH_CONCURRENCY,
                         deadline=EVIDENCE_STAGE_DEADLINE, fallback=("", "timeout"))
    # Remember what we downloaded. The adaptive loop re-evaluates the retained
    # set after every Tier B batch, and without this the Tier A pages were
    # re-fetched each time — Manz spent 197s where it should spend ~120s.
    for src, (text, _m) in zip(slate, pages):
        if text and not src.get("page_text"):
            src["page_text"] = text

    evidence, low_kept = [], 0
    for src, (text, method) in zip(slate, pages):
        if len(evidence) >= MAX_EVIDENCE_ITEMS:
            break
        if len(text) < 120:
            continue
        # Identity is decided BEFORE the low-tier budget, because whether a page
        # proved it is about this company is exactly what the budget should key on.
        content_verified = False
        if not src["official"]:
            ok, _reason = identity_ok(text[:4000], name, name_cn, domain, src["url"])
            if not ok:
                continue                      # post-fetch disambiguation on body text
            content_verified = True
        # The budget exists to keep UNVERIFIED portal chatter out. A low-tier page
        # whose own body text proves it is about this company is evidence, not
        # noise, and is not discarded for its tier alone.
        if src["tier"] >= 6 and not content_verified:
            if low_kept >= MAX_LOW_TIER_ITEMS:
                continue
            low_kept += 1
        evidence.append({
            "id": len(evidence) + 1, "title": src["title"], "url": src["url"],
            "domain": urllib.parse.urlparse(src["url"]).netloc,
            "tier": src["tier"], "source_type": src["source_type"],
            "official": src["official"], "retrieval_method": method,
            # Set only where the page's own body text was matched to the company.
            # Third-party + content_verified is what exempts an item from the
            # low-tier budget; nothing else grants the exemption.
            "content_verified": content_verified,
            # Which research areas this source was found for. Survives into the
            # saved record so coverage gaps become measurable later.
            "topics": src.get("topics") or [],
            "category": source_category(src["url"]),
            "text": text[:CHAR_BUDGET.get(src["tier"], 900)],
        })
    return evidence


# ---------------------------------------------------------------------------
# Evidence sufficiency
# ---------------------------------------------------------------------------
# Source COUNT alone is a bad gate: 15 unrelated portal reposts are worse than 3
# official pages. The threshold therefore combines volume with three specific
# things a briefing cannot be written without - identity, overview, products.

OVERVIEW_MARKERS = ("about us", "about-us", "who we are", "our story", "our history",
                    "founded", "established", "headquarter", "company profile",
                    "we are a", "公司简介", "企业简介", "成立", "总部", "简介")
PRODUCT_MARKERS = ("product", "solution", "service", "system", "equipment", "capabilit",
                   "machine", "produkt", "产品", "方案", "设备", "服务", "系统", "装备")

EVIDENCE_LEVELS = ((8, "strong"), (5, "acceptable"), (2, "weak"), (0, "insufficient"))


def _level_for(count):
    for threshold, label in EVIDENCE_LEVELS:
        if count >= threshold:
            return label
    return "insufficient"


def _has_marker(evidence, markers, sections=(), min_hits=1):
    for e in evidence:
        if e.get("section") in sections:
            return True
        low = (e.get("text") or "").lower()
        if sum(1 for m in markers if m in low) >= min_hits:
            return True
    return False


def assess_evidence(evidence, name, name_cn, domain, website_status, ranked=None):
    """What do we actually have, and is it enough to write a report?"""
    official = [e for e in evidence if e.get("official")]
    identity_verified = bool(
        official
        or website_status in ("provided", "manual")
        or (website_status == "auto_discovered" and domain))
    overview = _has_marker(evidence, OVERVIEW_MARKERS, ("homepage", "about"))
    products = _has_marker(evidence, PRODUCT_MARKERS, ("products",), min_hits=2)
    count = len(evidence)
    level = _level_for(count)
    reasons = []
    if not identity_verified:
        reasons.append("company identity not verified")
    if not official:
        reasons.append("no official / company-specific source retrieved")
    if not overview:
        reasons.append("no company-overview evidence")
    if not products:
        reasons.append("no products / services evidence")
    if level in ("weak", "insufficient"):
        reasons.append("only {} unique sources ({})".format(count, level))
    return {
        "level": level,
        "sources": count,
        "official_sources": len(official),
        "candidates": len(ranked or []),
        "identity_verified": identity_verified,
        "official_evidence": bool(official),
        "overview_evidence": overview,
        "products_evidence": products,
        # Another retrieval wave is worth paying for.
        "needs_fallback": level in ("weak", "insufficient") or not (overview and products),
        # The hard guard: nothing company-specific was found at all.
        "blocking": (not identity_verified) and not official,
        "sufficient_for_report": bool(official) and identity_verified and overview,
        "reasons": reasons,
    }


# ---------------------------------------------------------------------------
# Stage 3b - direct official-site crawl
# ---------------------------------------------------------------------------
# A readable official website is on its own enough to answer Company Overview,
# Products/Services and Industry. It is retrieved directly rather than hoping the
# search backend surfaces those pages, because for Western companies it usually
# does not.

SITE_SECTIONS = (
    ("about", 1, ("about", "company", "who-we-are", "whoweare", "our-story", "overview",
                  "profile", "corporate", "公司简介", "关于", "企业")),
    ("products", 2, ("product", "solution", "service", "capabilit", "technolog", "system",
                     "equipment", "产品", "方案", "解决", "设备", "业务")),
    ("industries", 2, ("industr", "market", "sector", "application", "expertise",
                       "行业", "应用", "领域")),
    ("leadership", 2, ("leadership", "team", "management", "executive", "our-people",
                       "board", "团队", "管理层", "高管")),
    ("news", 2, ("news", "press", "blog", "media", "insights", "新闻", "资讯", "动态")),
    ("contact", 2, ("contact", "location", "facilit", "office", "联系", "地址")),
)
SITE_SKIP = ("login", "cart", "privacy", "cookie", "terms", "legal", "sitemap", "search",
             "career", "job", "rss", "feed", "wp-content", "wp-json", "wp-admin",
             ".pdf", ".jpg", ".png", ".zip", ".mp4", "mailto:", "tel:", "javascript:")
MAX_SITE_PAGES = 8


def _site_links(html, base, domain):
    """Internal links from the homepage, keyed to the section they look like."""
    picks, seen_urls = {}, set()
    for raw in re.findall(r'href=["\']([^"\'#]+)["\']', html or "", re.I):
        url = urllib.parse.urljoin(base, raw.strip())
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            continue
        host = parsed.netloc.lower().replace("www.", "")
        if not host or (domain and host != domain and not host.endswith("." + domain)):
            continue
        low = url.lower()
        if any(bad in low for bad in SITE_SKIP):
            continue
        path = parsed.path.lower().strip("/")
        if not path:
            continue
        url = url.split("?")[0].rstrip("/")          # /contact-us and /contact-us/ are one page
        if url.lower() in seen_urls:
            continue
        seen_urls.add(url.lower())
        for section, tier, keywords in SITE_SECTIONS:
            if any(k in path for k in keywords):
                depth = len(path.split("/"))
                slot = picks.setdefault(section, [])
                if not any(u == url for _d, u, _t in slot):
                    slot.append((depth, url, tier))
                break
    return {sec: [u for _d, u, _t in sorted(v)[:2]] for sec, v in picks.items()}


def crawl_official_site(website, domain, name, name_cn, homepage_text="", progress=None):
    """Read the company's own site directly: homepage plus the key sections.

    Returns evidence-shaped items. This runs before and independently of web
    search, so a search failure can never reduce a company with a readable site
    to "no information".
    """
    progress = progress or (lambda *_a, **_k: None)
    if not website:
        return []
    try:
        html, final = fetch_url(website)
    except Exception:
        html, final = "", website
    home_text = homepage_text or html_to_text(html)
    items = []
    if len(home_text) >= 200:
        items.append({"title": "{} - official website".format(name), "url": final,
                      "domain": domain, "tier": 1, "source_type": "official-website",
                      "official": True, "retrieval_method": "direct-site",
                      "section": "homepage", "text": home_text[:CHAR_BUDGET[1]]})
    links = _site_links(html, final, domain)
    targets = []
    for section, tier, _kw in SITE_SECTIONS:
        for url in links.get(section, []):
            if len(targets) < MAX_SITE_PAGES:
                targets.append((section, tier, url))
    if not targets:
        progress("Official site: homepage only, no section links found")
        return items
    progress("Official site: reading {} section pages ({})".format(
        len(targets), ", ".join(sorted({t[0] for t in targets}))))
    pages = parallel_map(lambda t: fetch_page_text(t[2]), targets, FETCH_CONCURRENCY,
                         deadline=EVIDENCE_STAGE_DEADLINE, fallback=("", "timeout"))
    for (section, tier, url), (text, method) in zip(targets, pages):
        if len(text) < 200:
            continue
        items.append({
            "title": "{} - {}".format(name, section), "url": url, "domain": domain,
            "tier": tier,
            "source_type": "official-{}".format(
                "investor-relations" if section == "about" and "investor" in url.lower()
                else "product-page" if section == "products" else section),
            "official": True, "retrieval_method": "direct-site-{}".format(method),
            "section": section, "text": text[:CHAR_BUDGET.get(tier, 2500)]})
    return items


# SKEQI's own capability areas, taken from skeqi.com and the verified ground truth
# in archive/benchmark_reference/. Recommendations may only use these areas — the
# model must not invent SKEQI products.
SKEQI_CAPABILITIES = """- Battery cell manufacturing equipment / 电芯装配设备
- Module assembly lines / 模组成型产线
- PACK assembly lines / PACK封装产线
- Energy-storage (ESS) assembly / 储能系统装配
- Laser welding, laser energy control, laser shaping / 激光焊接与激光应用技术
- Industrial non-destructive testing: 2D/3D X-ray inspection / 工业无损检测（2D/3D X射线）
- Intelligent logistics (KEPAILE / 科派乐): conveying, handling, warehousing / 智能物流
- Digital factory (QIHANG / 琦航 5G digital factory: MES, WMS, supply-chain, dashboards) / 数字工厂系统
- Automotive & components manufacturing equipment: body, chassis, body-in-white welding lines / 汽车与零部件装备
- Precision manufacturing: precision machining, moulds, plastic and hardware parts / 精密制造
- Battery recycling and disassembly / 电池回收拆解"""


INSTRUCTION = """You are an account-research analyst preparing a sales briefing for SKEQI (思客琦),
a Chinese supplier of intelligent battery-manufacturing equipment. Answer ONLY from the EVIDENCE below.

The report must answer: what is happening at this company, where could SKEQI realistically help,
who should we engage, and what should we talk to them about?

RULES
- Cite an evidence id like [2] for every material claim. Uncited claims are not allowed.
- Tag material claims with exactly one of: **Verified** / **Likely** / **Not enough evidence**.
- Never invent companies, people, projects, figures or products. If the evidence does not
  cover something, write "Not enough evidence / 证据不足" and move on.
- Source priority when sources conflict: official company website > official product pages >
  official customer/partner references > government or reputable media > industry publications >
  other web sources. Say so when they conflict.
- For customers and partners, say whether the claim is first-party (their own site) or third-party.

OUTPUT LANGUAGE
Produce ONE answer containing BOTH English and Chinese from the SAME evidence. Do not research
twice and do not translate loosely. Under every heading use exactly:

## <English Heading> / <中文标题>
**English:**
<content with [n] citations>

**中文：**
<the same content in Chinese, same [n] citations>

Keep company, product, brand and award names in their official original language in BOTH blocks
(e.g. 宁德时代, CATL, 琦航数字工厂系统). Be concise: bullets, not essays.

Use exactly these 19 headings in this order for {company}{site}:

## Executive Summary / 执行摘要
Write this LAST but place it FIRST. What the company does; why it matters to SKEQI; the top 3-5
opportunities; major upcoming projects; who to engage; the most relevant manufacturing challenges;
and the recommended SKEQI engagement approach. Useful to a salesperson before a meeting.

## Company Overview / 公司概况
Legal/company name, public/private status, headquarters, employee count where available, industry,
divisions, products/services, manufacturing footprint, customer and end markets. For diversified
groups, say which divisions are most relevant to SKEQI.

## Strategic Initiatives / 战略举措
Publicly announced priorities: capacity expansion, localization, battery technology, energy storage,
automation, digital manufacturing, quality, yield, cost reduction, supply chain, sustainability.
Note where production equipment may still be specified or purchased.

## Industry Trends / 行业趋势
Trends most relevant to this company: EV, ESS, battery chemistry, prismatic/cylindrical/pouch, CTP,
solid state, automation, digital factory, traceability, yield, recycling, localization.
Clearly separate an industry-wide trend from a company-specific action.

## SWOT Analysis / SWOT分析
A 2x2: Strengths / 优势 and Weaknesses / 劣势 on top, Opportunities / 机会 and Threats / 威胁 below.
Concise, evidence-based points. Do NOT invent weaknesses to fill the table — fewer entries is correct.

## Competitor Analysis / 竞争对手分析
Only the genuinely relevant competitors. Compare product range, technology, industry focus,
geography, manufacturing capacity and innovation. Use a table.

## Existing Automation Providers / 现有自动化供应商
Public evidence of equipment/automation suppliers and partners: Siemens, ABB, Schneider Electric,
Mitsubishi Electric, Rockwell Automation, Beckhoff, Bosch Rexroth, FANUC, KUKA, Keyence, Cognex,
Trumpf, IPG, Han's Laser 大族激光, Lead Intelligent 先导智能, Hymson 海目星 and other battery-equipment
vendors. NEVER assume a relationship exists. Label each: **Confirmed** / **Likely / partially
supported** / **Not enough evidence**, with the source URL. Note where SKEQI could compete or complement.

## Key Decision Makers / 关键决策人
Leaders relevant to an equipment engagement: CEO/President, COO, CTO, CIO, manufacturing,
engineering, automation, equipment engineering, process engineering, digital manufacturing,
procurement, strategic sourcing, supply chain, battery/energy-storage engineering, quality,
plant leadership. Do NOT force a fixed number.
Only people supported by the EVIDENCE or by the APOLLO PEOPLE DIRECTORY below, if one is present.
Prefer the official company leadership page for executive/C-suite titles when both are available.
If the same person appears in both, give them ONE row, not two.
Mark the Source column exactly as: Apollo, Official Website, LinkedIn/Public Web, or Other.
Table: Name | Title | Department | Seniority | Why Relevant to SKEQI | Source.

## Upcoming Projects / 未来项目
Greenfield plants, expansions, new production lines, module/PACK plants, ESS manufacturing,
modernization, localization, pilot lines. Give location, timing, capacity and investment where
available, and why each could matter to SKEQI.

## Latest News / 最新动态
Prioritise manufacturing, battery, ESS, new plants, expansion, automation, partnerships, technology
investment and executive changes. Include the publication date and source URL; if no date can be
established from the evidence, mark it Not enough evidence rather than guessing.

## Financial Information / 财务信息
Public companies: revenue, growth, profitability where relevant, CapEx, R&D, manufacturing
investment, key growth drivers, fiscal-year timing. If the company is private and reliable figures
are unavailable, state exactly "Not publicly available / 未公开". Do NOT invent estimates.

## Manufacturing Challenges / 制造挑战
Two clearly separated lists, never merged:
**Evidence-based challenges / 有证据支持** — stated in the evidence about THIS company.
**Industry-inferred challenges / 基于行业推断** — plausible for the industry, NOT stated about them.
Cover throughput, cycle time, yield, welding quality, inspection, traceability, automation, labour
reduction, flexible manufacturing, logistics, digital factory, commissioning, ramp-up, energy,
reliability. Never present an inferred pain point as a confirmed fact.

## Sustainability Objectives / 可持续发展目标
Carbon neutrality, factory energy efficiency, renewable energy, waste reduction, recycling, water,
material efficiency. Then where SKEQI could reasonably support them. State a reasonable link or none.

## Relevant SKEQI Solutions / 思客琦相关解决方案
At most 8. You may ONLY use these approved SKEQI capability areas — naming anything else is an error:
{capabilities}
Table: Customer Need | SKEQI Solution | Application | Business Value | Confidence.

## Sales Strategies / 销售策略
At most 8, split into **0-12 months (tactical)** and **3-5 years (strategic)**. Position SKEQI as a
strategic manufacturing partner, not an equipment vendor.
Table: Strategy | Time Horizon | Target Stakeholder | Opportunity | Recommended Action.

## Strategic Objectives / 战略目标
At most 8 account objectives, e.g. establish an engineering relationship, gain approved-supplier
status, win an entry project, expand from one station to a full line, enter additional factories,
build executive sponsorship, enter future plant expansions.
Do NOT invent revenue targets. Table: Objective | Why It Matters | Target Stakeholder | Suggested Measure.

## Potential SKEQI Use Cases / 潜在思客琦应用场景
At most 8, based on the company's actual manufacturing needs, formats and projects. Use only the
approved capability areas listed above.

## Acronyms & Business Terms / 术语与缩写
Company-specific terms and acronyms found in the evidence only. Do NOT manufacture acronyms; an
empty list is correct if none appear. Table: Term | Meaning | Business Context.

## Sources / 信息来源
Numbered list of the evidence ids used, with URLs. No duplicated prose here.

EVIDENCE:
"""


def render_evidence(evidence):
    return "\n\n---\n\n".join(
        "[{id}] {title}\nURL: {url}\nSOURCE TYPE: {st} (priority tier {tier})\nCONTENT:\n{text}".format(
            id=e["id"], title=e["title"] or "(untitled)", url=e["url"],
            st=e["source_type"], tier=e["tier"], text=e["text"])
        for e in evidence)


def synthesize(model, company, website, evidence, cfg, timeout=SYNTHESIS_TIMEOUT,
               apollo_people=None):
    """Search is OFF here on purpose: synthesis is closed-book over the evidence set.

    apollo_people, when supplied, is appended as a clearly separated directory
    block rather than as numbered web evidence - see people.to_prompt_block.
    """
    path, is_multi = endpoint_for(model, cfg)
    url = cfg["DASHSCOPE_BASE_URL"].rstrip("/") + path
    prompt = INSTRUCTION.format(
        company=company, site=" (website: {})".format(website) if website else "",
        capabilities=SKEQI_CAPABILITIES,
    ) + render_evidence(evidence)
    block = people.to_prompt_block(apollo_people or [], company)
    if block:
        prompt += "\n\n---\n\n" + block
    content = [{"text": prompt}] if is_multi else prompt
    params = {} if is_multi else {"result_format": "message"}
    started = time.time()

    def _post():
        return post_json(url, {
            "model": model,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": params,
        }, cfg["DASHSCOPE_API_KEY"], timeout)

    status, data = _post()
    if wrong_endpoint(status, data):
        learn_endpoint(model, is_multi)
        path, is_multi = endpoint_for(model, cfg)
        url = cfg["DASHSCOPE_BASE_URL"].rstrip("/") + path
        content = [{"text": prompt}] if is_multi else prompt
        params = {} if is_multi else {"result_format": "message"}
        status, data = _post()
    elapsed = time.time() - started
    record_access(model, status, data)
    msg = ((data.get("output") or {}).get("choices") or [{}])[0].get("message", {})
    body = msg.get("content", "")
    answer = body if isinstance(body, str) else "".join(p.get("text", "") for p in body or [])
    usage = data.get("usage") or {}
    # Merge what the model found on the web with what Apollo supplied, on person
    # identity, so one person is one row with all of their provenance.
    domain = urllib.parse.urlparse(website).netloc.replace("www.", "") if website else ""
    sources = [{k: v for k, v in e.items() if k != "text"} for e in evidence]
    web_people = people.parse_report_people(answer, sources, domain) if answer else []
    roster = people.merge(web_people, apollo_people or [])
    return {
        "model": model, "model_label": MODEL_LABELS.get(model, model),
        "status": status, "report": answer,
        "decision_makers": roster,
        "people_summary": people.summary(roster, None),
        "endpoint": "multimodal-generation" if is_multi else "text-generation",
        "protocol": "DashScope Native",
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "latency_seconds": round(elapsed, 1),
        "access_denied": is_access_denied(status, data),
        "error": None if status == 200 else str(data.get("message") or data)[:300],
    }


def synthesize_with_fallback(model, company, website, evidence, cfg,
                             timeout=SYNTHESIS_TIMEOUT, apollo_people=None, progress=None):
    """Synthesise with the requested model, falling back on access denial.

    An unusable model must not become an empty report: the user asked for
    research, not for a demonstration that one model id is unpurchased. The run
    records which model actually produced the answer.
    """
    progress = progress or (lambda *_a, **_k: None)
    # Models already proven unusable earlier in this run are skipped rather than
    # re-called, but they are still reported so the switch is explainable.
    tried = [MODEL_LABELS.get(m, m) for m, rec in access_report().items() if not rec["ok"]]
    last = None
    for candidate in model_candidates(cfg, preferred=model):
        run = synthesize(candidate, company, website, evidence, cfg, timeout,
                         apollo_people=apollo_people)
        run["requested_model"] = model
        run["model_used"] = candidate
        run["fallback_used"] = candidate != model
        run["models_unavailable"] = list(tried)
        run["models_tried"] = list(tried)
        if not run.get("access_denied"):
            if run["fallback_used"]:
                progress("Research completed using: {}".format(
                    MODEL_LABELS.get(candidate, candidate)))
            return run
        tried.append(MODEL_LABELS.get(candidate, candidate))
        last = run
        nxt = next((MODEL_LABELS.get(m, m) for m in model_candidates(cfg, preferred=model)
                    if m != candidate), None)
        progress("{} - Access unavailable{}".format(
            MODEL_LABELS.get(candidate, candidate),
            ". Falling back to {}...".format(nxt) if nxt else ". No further models configured."))
    if last is None:
        last = {"model": model, "model_label": MODEL_LABELS.get(model, model),
                "status": 403, "report": "", "endpoint": "?", "protocol": "DashScope Native",
                "input_tokens": None, "output_tokens": None, "total_tokens": None,
                "latency_seconds": 0, "decision_makers": [], "people_summary": {},
                "access_denied": True, "error": ACCESS_DENIED_HINT}
    last["requested_model"] = model
    last["model_used"] = None
    last["fallback_used"] = False
    last["models_tried"] = tried
    last["error"] = "{} Tried: {}.".format(ACCESS_DENIED_HINT, ", ".join(tried) or "none")
    return last


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def discover_aliases(website, name):
    """Learn the company's local-language name from its own website.

    Entering just "SKEQI" is not enough to validate Chinese-language results —
    pages titled 宁德思客琦智能装备有限公司 contain no "SKEQI" token, so every
    result would be rejected. The official site is the authority on its own name.
    Returns (alias, official_page_text) so the page fetch is not repeated.
    """
    if not website:
        return "", ""
    text, _method = fetch_page_text(website)
    if not text:
        return "", ""
    head = text[:1500]
    # Prefer a Chinese run sitting directly next to the Latin name in the title.
    m = re.search(re.escape(name) + r"\s*([\u4e00-\u9fff]{2,8})", head, re.I) if name else None
    if m:
        return m.group(1), text
    # Otherwise the most frequent 2-4 char Chinese token in the page header area,
    # ignoring common industry words that are not company names.
    stop = {"有限", "公司", "科技", "智能", "装备", "系统", "技术", "解决", "方案", "产品",
            "服务", "行业", "企业", "制造", "设备", "生产", "工业", "中国", "新能源", "首页"}
    counts = {}
    for token in re.findall(r"[\u4e00-\u9fff]{2,4}", text[:6000]):
        if token in stop:
            continue
        counts[token] = counts.get(token, 0) + 1
    if not counts:
        return "", text
    alias = max(counts.items(), key=lambda kv: (kv[1], len(kv[0])))[0]
    return (alias if counts[alias] >= 3 else ""), text


def split_company(company):
    name = company.split("/")[0].strip()
    name_cn = next((p.strip() for p in company.split("/")[1:] if re.search(r"[一-鿿]", p)), "")
    if not name_cn and re.search(r"[一-鿿]", name):
        name_cn = name
    return name, name_cn


def cache_key(company, website):
    raw = "{}|{}".format((company or "").strip().lower(), (website or "").strip().lower())
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def cache_path(company, website):
    EVIDENCE_CACHE_DIR.mkdir(exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", (company or "").lower()).strip("-")[:24] or "company"
    return EVIDENCE_CACHE_DIR / "{}_{}.json".format(slug, cache_key(company, website))


def load_cached_evidence(company, website):
    path = cache_path(company, website)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    data["cached"] = True
    data["age_seconds"] = int(time.time() - path.stat().st_mtime)
    return data


def save_cached_evidence(package):
    path = cache_path(package["company"], package["website"])
    path.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def url_key(url):
    """Canonical identity for a page: scheme, www., trailing slash and query all
    ignored. http://www.acro.com and https://www.acro.com/ are one source."""
    parsed = urllib.parse.urlparse((url or "").strip())
    host = parsed.netloc.lower().replace("www.", "")
    path = re.sub(r"/+$", "", parsed.path)
    return "{}{}".format(host, path.lower())


def insert_evidence(evidence, item):
    """Place a pre-built evidence item at its ranked position and renumber ids.

    Used for the Yahoo Finance block, which is fetched directly rather than via
    the search backend. If the evidence set is already full, the weakest
    (highest-tier) item makes way rather than the financial source being dropped.
    """
    if len(evidence) >= MAX_EVIDENCE_ITEMS:
        weakest = max(range(len(evidence)),
                      key=lambda i: (evidence[i]["tier"], subrank(evidence[i]["source_type"])))
        if (evidence[weakest]["tier"], subrank(evidence[weakest]["source_type"])) <= \
           (item["tier"], subrank(item["source_type"])):
            return evidence, False
        evidence.pop(weakest)
    pos = len(evidence)
    for i, e in enumerate(evidence):
        if (e["tier"], subrank(e["source_type"])) > (item["tier"], subrank(item["source_type"])):
            pos = i
            break
    evidence.insert(pos, item)
    for i, e in enumerate(evidence, 1):
        e["id"] = i
    return evidence, True


def yahoo_finance_evidence(listing):
    """Fetch the validated ticker's Yahoo Finance quote page as an evidence item."""
    block = fin.fetch_yahoo_finance(listing["ticker"], fetch_url, fetch_page_text)
    if not block:
        return None
    tier, kind = 4, "yahoo-finance"
    return {"id": 0, "title": block["title"], "url": block["url"],
            "domain": "finance.yahoo.com", "tier": tier, "source_type": kind,
            "official": False, "retrieval_method": block["retrieval_method"],
            "text": block["text"][:CHAR_BUDGET.get(tier, 2000)]}


def contact_outcome(crm_people, final_people, usage, company):
    """One human sentence describing what contact enrichment actually produced.

    The caller decides styling from the wording: a line starting with "OK" is a
    success, "WARN" is non-blocking degradation. Counts are always included so
    "no contacts" can be told apart from "no lookup".
    """
    supplied = usage.get("crm_contacts_supplied", 0)
    crm_n = len(crm_people)
    total = len(final_people)
    apollo_n = usage.get("people_retained", 0)
    status = usage.get("status") or ""

    if status == "skipped_crm_sufficient":
        return ("OK Contact enrichment - {} CRM contact(s) found, {} relevant, "
                "Apollo not needed".format(supplied, crm_n))
    if crm_n and apollo_n:
        return ("OK Contact enrichment - {} CRM contact(s) + {} Apollo contact(s), "
                "{} after merge".format(crm_n, apollo_n, total))
    if crm_n and not apollo_n:
        if status in ("ok", "no_people"):
            return ("OK Contact enrichment - {} CRM contact(s); Apollo added none"
                    .format(crm_n))
        return ("WARN Contact enrichment - {} CRM contact(s) kept; Apollo unavailable "
                "({}); continuing".format(crm_n, status or "no result"))
    if apollo_n:
        return ("OK Contact enrichment - no CRM contacts for \"{}\"; {} from Apollo"
                .format(company, apollo_n))
    # Nothing from either side: say WHICH side produced nothing, and why.
    if status == "not_configured":
        return ("WARN Contact enrichment - no CRM contacts for \"{}\" and Apollo is not "
                "configured; continuing on web evidence".format(company))
    if supplied == 0:
        return ("WARN Contact enrichment - no CRM contacts matched \"{}\" (try the full "
                "registered company name) and Apollo returned none ({}); continuing on "
                "web evidence".format(company, status or "no result"))
    return ("WARN Contact enrichment - {} CRM contact(s) supplied but none relevant, "
            "Apollo returned none ({}); continuing on web evidence"
            .format(supplied, status or "no result"))


def build_shared_evidence(company, website, cfg, progress=None, trust_website=False,
                          known_contacts=None):
    """Stage 1-6, run ONCE per company. The result is shared by every model and cached.

    Searches and page fetches run concurrently; the baseline did both sequentially
    (14.9s and 29.8s respectively).
    """
    progress = progress or (lambda *_a, **_k: None)
    timeout = int(cfg.get("AI_REQUEST_TIMEOUT_MS", "90000")) / 1000
    timings = {}
    t_all = time.time()

    name, name_cn = split_company(company)

    # Public/private status and ticker resolve while the official website is being
    # fetched, so this costs no extra wall-clock. It never raises: a failure means
    # "treat as private", and private companies are never forced through Yahoo.
    listing_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    listing_future = listing_pool.submit(
        fin.resolve_listing, company, name, name_cn, fetch_page_text)

    # Only the company NAME is required. A supplied website is a hint: it is
    # validated, and replaced by a discovered official site when it does not
    # hold up (wrong company, blocked, unreachable, JS-only shell).
    t = time.time()
    resolved = resolve_website(name, name_cn, website, cfg, timeout, progress,
                               trust=trust_website)
    website = resolved["website"] or ""
    website_status = resolved["status"]
    official_text = resolved.get("text") or ""
    domain = urllib.parse.urlparse(website).netloc.replace("www.", "") if website else ""
    if not name_cn and official_text:
        name_cn = _alias_from_text(official_text, name) or name_cn
    timings["official_site"] = round(time.time() - t, 1)
    progress("official", "Website {}: {}".format(
        {"provided": "confirmed", "auto_discovered": "auto-discovered",
         "manual": "manually corrected",
         "unverified": "could not be verified"}.get(website_status, website_status),
        website or "none"))

    try:
        listing = listing_future.result(timeout=25)
    except Exception as e:
        listing = {"public_company": False, "listing_status": "unknown", "ticker": "",
                   "listed_name": "", "resolution_method": "none", "quote_url": "",
                   "notes": "Listing lookup did not finish: {}".format(type(e).__name__)}
    finally:
        listing_pool.shutdown(wait=False)
    progress("listing", "Listing check: {}{}".format(
        "public - {} ({})".format(listing["ticker"], listing["listed_name"])
        if listing["public_company"] else listing["listing_status"].replace("_", "/"),
        "" if listing["public_company"] else " - Yahoo Finance not forced"))

    t = time.time()
    plan = plan_queries_labeled(name, name_cn, domain,
                                ticker=listing["ticker"] if listing["public_company"] else "")
    queries = [q for _l, q in plan]
    timings["query_generation"] = round(time.time() - t, 1)
    areas = []
    for label, _q in plan:
        if label not in areas:
            areas.append(label)
    progress("queries", "Generated {} search queries across {} research areas".format(
        len(queries), len(areas)), queries=queries)

    # Read the company's own website directly, before any search. A readable
    # official site alone can answer overview, products and industry, so search
    # failure can no longer produce a "no information" report.
    t = time.time()
    site_evidence = crawl_official_site(website, domain, name, name_cn,
                                        homepage_text=official_text,
                                        progress=lambda m: progress("site", m))
    timings["official_site_crawl"] = round(time.time() - t, 1)
    progress("site", "Official website: {} page{} retrieved".format(
        len(site_evidence), "" if len(site_evidence) == 1 else "s")
        if site_evidence else "Official website: nothing readable retrieved")

    # Sequential retrieval ladder. A later model runs ONLY when what we have is
    # still not good enough, so the common case still costs one wave.
    t = time.time()
    raw, waves, all_failed = [], [], True
    ranked, rejected = [], []
    denied_models = []
    ladder = model_candidates(cfg)          # configured trio + AI_MODEL_FALLBACKS
    wave_no = 0
    for model in ladder:
        if not model_available(model):
            continue
        label = MODEL_LABELS.get(model, model)

        def _search(item, _model=model):
            qlabel, query = item
            status, results = run_search(query, cfg, timeout, model=_model)
            # Which research area surfaced this result. Carried through candidate
            # selection, verification and evidence so retrieval can eventually
            # reason about GAPS rather than counts. No prompt change: the label
            # already exists in the query plan.
            for r in results:
                r["topic"] = qlabel
            return status, results

        outcomes = parallel_map(_search, plan, SEARCH_CONCURRENCY)

        # A model the account cannot call is an access problem, not a thin-results
        # problem. Skip to the next model instead of banking an empty wave.
        if not model_available(model):
            denied_models.append(label)
            waves.append({"wave": None, "model": model, "model_label": label,
                          "raw_results": 0, "queries_failed": len(plan),
                          "unique_candidates": len(ranked), "access_denied": True})
            nxt = next((MODEL_LABELS.get(m, m) for m in ladder
                        if m != model and model_available(m)), None)
            progress("search", "{} - Access unavailable{}".format(
                label, ". Falling back to {}...".format(nxt) if nxt
                else ". No further models configured."))
            continue

        wave_no += 1
        before = len(raw)
        failed = 0
        for status, results in outcomes:
            if status != 200:
                failed += 1
            raw.extend(results)
        if failed < len(plan):
            all_failed = False
        ranked, rejected, tier_b = dedupe_and_rank(raw, name, name_cn, domain)
        interim = assess_evidence(site_evidence, name, name_cn, domain, website_status,
                                  ranked=ranked)
        gained = len(raw) - before
        usable = len(ranked)
        waves.append({"wave": wave_no, "model": model, "model_label": label,
                      "raw_results": gained, "queries_failed": failed,
                      "unique_candidates": usable})
        progress("search", "{} web search... {} raw results, {} usable candidates".format(
            label, gained, usable))

        # Enough when the site already carries the essentials, or when the
        # candidate pool is comfortably above the "acceptable" threshold.
        remaining = [m for m in ladder[ladder.index(model) + 1:] if model_available(m)]
        enough = (not interim["needs_fallback"]) or usable >= 8
        if enough:
            for skipped in remaining:
                waves.append({"wave": None, "model": skipped,
                              "model_label": MODEL_LABELS.get(skipped, skipped),
                              "raw_results": 0, "queries_failed": 0,
                              "unique_candidates": usable, "skipped": True})
            if remaining:
                progress("search", "Evidence acceptable - {} not needed".format(
                    ", ".join(MODEL_LABELS.get(m, m) for m in remaining)))
            break
        if remaining:
            progress("search", "Evidence {} ({}) - falling back to {}".format(
                interim["level"], "; ".join(interim["reasons"][:2]) or "thin coverage",
                MODEL_LABELS.get(remaining[0], "next model")))

    timings["web_retrieval"] = round(time.time() - t, 1)
    retrieval_blocked = bool(denied_models) and wave_no == 0
    if retrieval_blocked and not site_evidence:
        raise RetrievalError("model_access_denied", "{} ({})".format(
            FAILURE_REASONS["model_access_denied"], ", ".join(denied_models)))
    if retrieval_blocked:
        progress("search", "Web search unavailable - no model this account can call. "
                           "Continuing on official-website evidence only.")
    if all_failed and not raw and not site_evidence and not retrieval_blocked:
        raise RetrievalError("search_unavailable", FAILURE_REASONS["search_unavailable"])

    # Widen with short, unrestricted company-name queries before giving up.
    if len(ranked) < 8:
        short = [name, "{} company".format(name), "{} 公司".format(name)]
        if name_cn:
            short.append(name_cn)
        short = [q for q in short if q and q not in queries]
        if short:
            progress("search", "Widening with company-name-only queries")
            for _st, more in parallel_map(
                    lambda q: run_search(q, cfg, timeout, model=(model_candidates(cfg) or [None])[0]),
                    short, SEARCH_CONCURRENCY):
                for r in more:
                    r.setdefault("topic", "company overview")
                raw.extend(more)
            queries = queries + short
            ranked, rejected, tier_b = dedupe_and_rank(raw, name, name_cn, domain)

    t = time.time()

    # ---- Tier B: open the uncertain ones and let the page decide -----------
    # The provider returns no snippet, so a title that omits the company name
    # proves nothing either way. Rather than discard that coverage, a bounded
    # number of plausible-host candidates are fetched and judged on their text.
    already = {url_key(e["url"]) for e in site_evidence}
    verified_b, b_fetched, b_failed = [], 0, 0
    queue = [c for c in tier_b if url_key(c["url"]) not in already]
    stop_reason = "no uncertain candidates"
    # Adaptive: a small high-value batch, then more ONLY while the evidence is
    # still narrow. Ordering does the heavy lifting, so most companies stop after
    # the first batch.
    while queue and b_fetched < TIER_B_MAX_FETCHES:
        batch = queue[:TIER_B_FETCH_LIMIT]
        queue = queue[TIER_B_FETCH_LIMIT:]
        progress("verify", "Verifying {} uncertain source(s) from page content".format(len(batch)))
        pages_b = parallel_map(lambda c: fetch_page_text(c["url"]), batch,
                               FETCH_CONCURRENCY, deadline=EVIDENCE_STAGE_DEADLINE,
                               fallback=("", "timeout"))
        for cand, (text, _how) in zip(batch, pages_b):
            b_fetched += 1
            ok, why = verify_from_text(text, name, name_cn, cand["url"])
            if ok:
                cand["verified_by"] = "page text: {}".format(why)
                cand["page_text"] = text
                verified_b.append(cand)
            else:
                b_failed += 1
                rejected.append({"title": cand["title"], "url": cand["url"],
                                 "reason": "page text: {}".format(why)})
        # Sufficiency is judged on what would SURVIVE the caps, including Tier A
        # third-party evidence. Counting verified candidates alone was wrong in
        # both directions: it ignored Tier A, and it credited Tier B sources the
        # caps were about to discard.
        provisional = build_evidence(
            [r for r in ranked + verified_b if url_key(r["url"]) not in already],
            name, name_cn, domain, website, None, official_text=official_text)
        kept, _dropped = apply_evidence_caps(site_evidence, provisional)
        suff = evidence_sufficiency(kept, domain)
        progress("verify", "Content verification - {} of {} uncertain source(s) confirmed; "
                           "retained evidence: {} ({} third-party across {} host(s), "
                           "{} categor(ies))".format(
                               len(verified_b), b_fetched, suff["retained"],
                               suff["third_party"], suff["third_party_hosts"],
                               suff["categories"]))
        if suff["enough"]:
            stop_reason = "evidence sufficient"
            break
        if not queue:
            stop_reason = "candidate pool exhausted"
            break
        if b_fetched >= TIER_B_MAX_FETCHES:
            stop_reason = "safety ceiling reached"
            break
        progress("verify", "Evidence still narrow - fetching the next batch")
    if b_fetched:
        progress("verify", "Tier B finished after {} fetch(es) - {}".format(b_fetched, stop_reason))
    ranked = sorted(ranked + verified_b,
                    key=lambda s: (s["tier"], subrank(s["source_type"]), -s["hits"], s["url"]))
    timings["source_processing"] = round(time.time() - t, 1)
    progress("dedupe", "Deduplicating evidence - {} candidate sources found, {} retained "
                       "({} confirmed by content)".format(len(raw), len(ranked), len(verified_b)))

    t = time.time()
    # Pages already read straight off the official site are not fetched again.
    web_ranked = [r for r in ranked if url_key(r["url"]) not in already]
    web_evidence = build_evidence(web_ranked, name, name_cn, domain, website,
                                  lambda m: progress("evidence", m),
                                  official_text=official_text)
    evidence, dropped = apply_evidence_caps(site_evidence, web_evidence)
    timings["evidence_build"] = round(time.time() - t, 1)
    if not evidence:
        ctx = {"website": website, "website_status": website_status}
        if website_status == "unverified" and not ranked:
            raise RetrievalError("no_company_match", FAILURE_REASONS["no_company_match"], **ctx)
        if website_status == "unverified":
            raise RetrievalError("site_unverified", FAILURE_REASONS["site_unverified"], **ctx)
        raise RetrievalError("insufficient", FAILURE_REASONS["insufficient"], **ctx)
    # Yahoo Finance, for public companies only. The search backend does not
    # surface finance.yahoo.com (measured - see finance_service), so the quote
    # page for the validated ticker is fetched directly.
    yahoo_seeded = False
    if listing["public_company"]:
        try:
            block = yahoo_finance_evidence(listing)
        except Exception as e:
            block, listing["notes"] = None, "Yahoo fetch failed: {}".format(type(e).__name__)
        if block:
            evidence, yahoo_seeded = insert_evidence(evidence, block)
            progress("evidence", "Yahoo Finance quote page retrieved for {}".format(
                listing["ticker"]) if yahoo_seeded else
                "Yahoo Finance retrieved but evidence set was full")
        else:
            progress("evidence", "Yahoo Finance page unavailable for {}".format(listing["ticker"]))

    yahoo_urls = [e["url"] for e in evidence if "finance.yahoo.com" in e["url"].lower()]
    financial_sources = {
        "public_company": listing["public_company"],
        "listing_status": listing["listing_status"],
        "ticker": listing["ticker"],
        "listed_name": listing["listed_name"],
        "resolution_method": listing["resolution_method"],
        "notes": listing["notes"],
        # The headline the brief asks for: was Yahoo Finance ACTUALLY used?
        "yahoo_finance_used": bool(yahoo_urls),
        "yahoo_finance_urls": yahoo_urls,
        "yahoo_finance_direct_fetch": yahoo_seeded,
        "yahoo_finance_queries": [q for l, q in plan if l == "financials (Yahoo)"],
        # What the ladder actually produced, rung by rung.
        "ladder": {
            "1_investor_relations": [e["url"] for e in evidence
                                     if e["source_type"] == "official-investor-relations"],
            "2_regulatory_filings": [e["url"] for e in evidence
                                     if e["source_type"] == "regulatory-filing"],
            "3_yahoo_finance": yahoo_urls,
            "4_financial_publications": [e["url"] for e in evidence
                                         if e["source_type"] == "financial-publication"],
        },
    }
    progress("financial", "Yahoo Finance used: {}{}".format(
        "Yes" if yahoo_urls else "No",
        " ({})".format(yahoo_urls[0]) if yahoo_urls else
        ("" if listing["public_company"] else " - private/unlisted company")))

    # Apollo people enrichment. Optional, runs once per company alongside the web
    # evidence, and cannot fail the company: no key, no match or an API error all
    # leave an empty roster and the research continues on web evidence alone.
    t = time.time()
    apollo_usage = apollo.new_usage()
    apollo_people = []

    # ---- Contacts we already hold come first -----------------------------
    # The CRM's own records cost nothing and carry emails we already verified.
    # Apollo is a gap-filler, not the first call. `known_contacts` is attached
    # by the CRM proxy; running the engine standalone simply gets none.
    crm_people = people.from_crm(known_contacts or [])
    apollo_usage["crm_contacts_supplied"] = len(known_contacts or [])
    apollo_usage["crm_contacts_relevant"] = len(crm_people)
    if crm_people:
        progress("contacts", "CRM: {} known contact(s), {} relevant after ranking".format(
            len(known_contacts or []), len(crm_people)))

    enough_from_crm = len(crm_people) >= apollo.enrich_limit(cfg)
    if enough_from_crm:
        progress("contacts",
                 "CRM already covers this company - Apollo not called (saved a lookup)")
        apollo_usage["status"] = "skipped_crm_sufficient"
    elif apollo.configured(cfg):
        progress("apollo", "Apollo: searching people at {}...".format(domain or name))
        # Stage 1: search (no credit cost) -> rank -> stage 2: enrich only the top
        # contacts. Enriching everything Apollo returns would cost ~100 credits a
        # company for people nobody would ever contact.
        raw_people, apollo_usage = apollo.search_people(name, domain, cfg, apollo_usage)
        apollo_people = people.from_apollo(raw_people)      # scored and ranked
        apollo_usage["people_retained"] = len(apollo_people)
        progress("apollo", "Apollo: {} candidates returned, {} relevant after ranking{}".format(
            apollo_usage["people_returned"], len(apollo_people),
            "" if apollo_usage["status"] == "ok"
            else " ({})".format(apollo_usage["status"])))
        if apollo_people:
            limit = apollo.enrich_limit(cfg)
            progress("apollo", "Apollo: enriching top {} contacts for business email".format(
                min(limit, len(apollo_people))))
            apollo_people = apollo.enrich_people(apollo_people, cfg, apollo_usage, limit)
            progress("apollo", "Apollo: {} enriched, {} email(s) found ({} verified)".format(
                apollo_usage["people_enriched"], apollo_usage["emails_found"],
                apollo_usage["emails_verified"]))
    else:
        progress("apollo", "Apollo: not configured - web research only")
        apollo_usage["status"] = "not_configured"
    # Source priority is the order here: CRM first, then Apollo as the filler.
    apollo_people = people.merge(crm_people, apollo_people, limit=40) if crm_people \
        else apollo_people

    # ---- One closing line that states the OUTCOME -------------------------
    # "unavailable" told the reader nothing: 0 CRM contacts because the company
    # name did not match, an Apollo bug, and Apollo simply not being needed all
    # read identically. This says which of those actually happened.
    apollo_usage["contacts_final"] = len(apollo_people)
    progress("contacts", contact_outcome(crm_people, apollo_people, apollo_usage, name))
    timings["apollo"] = round(time.time() - t, 1)

    quality = assess_evidence(evidence, name, name_cn, domain, website_status, ranked=ranked)
    quality["retrieval_waves"] = waves
    quality["models_access_denied"] = denied_models
    quality["web_search_available"] = wave_no > 0          # a search wave executed
    quality["web_search_sources"] = len(raw)               # and what it actually returned
    quality["direct_site_sources"] = len(site_evidence)
    quality["web_sources"] = len(evidence) - len(site_evidence)
    progress("quality", "{} unique sources retained - evidence {}{}".format(
        len(evidence), quality["level"],
        "" if not quality["reasons"] else " ({})".format("; ".join(quality["reasons"]))))
    if quality["blocking"]:
        progress("quality", "Retrieval incomplete - no company-specific source found")

    progress("evidence", "{} sources in evidence package".format(len(evidence)))
    if len(evidence) < 5:
        progress("evidence", "Limited evidence available / 可用证据有限 ({} sources)".format(len(evidence)))

    timings["retrieval_total"] = round(time.time() - t_all, 1)
    package = {
        "company": company, "website": website, "resolved_alias": name_cn,
        "website_status": website_status, "limited_evidence": len(evidence) < 5,
        "financial_sources": financial_sources,
        "quality": quality,
        "apollo": {"usage": apollo_usage, "people": apollo_people},
        "search_queries": queries,
        "raw_result_count": len(raw), "rejected_count": len(rejected),
        "evidence": evidence,
        "sources": [{k: v for k, v in e.items() if k != "text"} for e in evidence],
        "timings": timings,
        "built_at": datetime_now(),
        "cached": False, "age_seconds": 0,
    }
    save_cached_evidence(package)
    return package


def datetime_now():
    from datetime import datetime
    return datetime.now().astimezone().isoformat()
def run_research(company, website, models, cfg, progress=None):
    """Sequential convenience wrapper (CLI). The web app uses build_shared_evidence()
    plus parallel synthesize() calls instead."""
    cb = progress or (lambda _m: None)
    pkg = build_shared_evidence(company, website, cfg,
                                progress=lambda _stage, msg, **_k: cb(msg))
    runs = []
    for model in models:
        cb("Generating research with {}...".format(MODEL_LABELS.get(model, model)))
        runs.append(synthesize_with_fallback(
            model, company, website, pkg["evidence"], cfg,
            apollo_people=(pkg.get("apollo") or {}).get("people"), progress=cb))
    return {
        "company": company, "website": website,
        "resolved_alias": pkg["resolved_alias"],
        "search_queries": pkg["search_queries"],
        "raw_result_count": pkg["raw_result_count"],
        "rejected": [],
        "sources": pkg["sources"],
        "timings": pkg["timings"],
        "runs": runs,
    }
