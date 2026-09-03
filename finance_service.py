"""
Financial-source resolution: public/private status, ticker, Yahoo Finance.

Deterministic. No LLM call, no paid API, no credentials.

WHY THIS MODULE EXISTS — measured constraints, 2026-09-02
--------------------------------------------------------
The pipeline previously *asked* for Yahoo Finance by putting the words in a
search query. Verified against every cached evidence package: that produced
ZERO finance.yahoo.com sources. Direct probes explain why:

  * The DashScope/Bailian search backend does not surface finance.yahoo.com.
    '"TE Connectivity" Yahoo Finance' returned Zhihu and a personal blog;
    'TEL Yahoo Finance' returned pages ABOUT Yahoo Finance (its LinkedIn page,
    a ModelScope dataset) plus unrelated tickers. Search alone cannot deliver
    Yahoo Finance evidence, and the ticker form actively injects noise.
  * https://finance.yahoo.com/quote/<TICKER>/ IS directly fetchable
    (~5-7KB of extracted text). An invalid ticker returns an HTTP error, which
    makes the quote page a reliable ticker VALIDATOR.
  * query1/query2.finance.yahoo.com JSON APIs return 429 without a
    cookie+crumb handshake. Not used.
  * /quote/<T>/financials/ and /quote/<T>/profile/ are blocked. Only the
    quote root works.

So Yahoo Finance is reached by DIRECT FETCH of a validated ticker, and the
requested Yahoo search queries run alongside it for public companies only.
Ticker resolution uses SEC EDGAR's free company_tickers.json (no key, no auth).

Private companies are never forced through any of this.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Reuses the already git-ignored cache directory rather than adding another one.
SEC_TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_CACHE = HERE / "evidence_cache" / "_sec_company_tickers.json"
SEC_CACHE_TTL = 7 * 24 * 3600          # the mapping changes slowly
SEC_TIMEOUT = 20
YAHOO_TIMEOUT = 10

# SEC requires a UA carrying an EMAIL-form contact. Verified: a UA without an
# "@" gets 403; "SKEQI-AccountResearch/1.0 (contact@skeqi.com)" gets 200.
# Override with SEC_CONTACT_UA in ai_credentials.env if you want your own contact.
SEC_UA = "SKEQI-AccountResearch/1.0 (contact@skeqi.com)"

YAHOO_QUOTE = "https://finance.yahoo.com/quote/{}/"

# Legal-form tokens dropped before comparing company names.
LEGAL_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited",
    "llc", "lp", "llp", "plc", "gmbh", "ag", "sa", "sas", "nv", "bv", "srl", "spa",
    "ab", "as", "oy", "kk", "pte", "pty", "holdings", "holding", "group", "the",
    "adr", "ads", "cl", "class", "new", "ordinary", "shares", "common", "stock",
}
# All-caps words that look like a ticker inside parentheses but are not.
NOT_A_TICKER = {"USA", "US", "UK", "EU", "CN", "JP", "DE", "FR", "HQ", "R&D", "OEM",
                "EV", "ESS", "AI", "IT", "HR", "NYSE", "NASDAQ", "SEHK", "SSE", "SZSE"}


def _norm_name(name):
    """Lowercase, strip punctuation and legal-form tokens. Returns a token list."""
    s = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower())
    return [t for t in s.split() if t and t not in LEGAL_SUFFIXES]


def explicit_ticker(company):
    """A ticker the user typed: 'TE Connectivity (TEL)' or 'BYD (NYSE: BYDDY)'."""
    m = re.search(r"[\(\[]\s*(?:NYSE|NASDAQ|SEHK|SSE|SZSE|TSE|HKEX|TICKER)?\s*:?\s*"
                  r"([A-Z]{1,5}(?:\.[A-Z]{1,3})?)\s*[\)\]]", company or "")
    if not m:
        return ""
    ticker = m.group(1)
    return "" if ticker.split(".")[0] in NOT_A_TICKER else ticker


# ---------------------------------------------------------------------------
# SEC EDGAR ticker map — free, authoritative, no key
# ---------------------------------------------------------------------------

def _fetch_sec_map(ua=None):
    req = urllib.request.Request(SEC_TICKER_URL, headers={
        "User-Agent": ua or SEC_UA, "Accept": "application/json",
        "Accept-Encoding": "identity"})
    with urllib.request.urlopen(req, timeout=SEC_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def sec_ticker_map(refresh=False, ua=None):
    """{EDGAR title -> ticker}, cached on disk. Returns {} if SEC is unreachable."""
    if not refresh and SEC_CACHE.exists() and (time.time() - SEC_CACHE.stat().st_mtime) < SEC_CACHE_TTL:
        try:
            return json.loads(SEC_CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass
    try:
        raw = _fetch_sec_map(ua)
    except Exception:
        if SEC_CACHE.exists():                       # stale beats nothing
            try:
                return json.loads(SEC_CACHE.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}
    mapping = {}
    for row in (raw or {}).values():
        title, ticker = (row.get("title") or "").strip(), (row.get("ticker") or "").strip()
        if title and ticker:
            mapping.setdefault(title, ticker)
    SEC_CACHE.parent.mkdir(exist_ok=True)
    SEC_CACHE.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    return mapping


def _sec_lookup(name):
    """Match a company name to an EDGAR ticker.

    Exact normalised match wins. Otherwise the query tokens must be a subset of
    the EDGAR name AND cover >=60% of it, which is what stops the single-token
    case 'Siemens' from matching 'Siemens Energy AG/ADR' (1 of 2 tokens = 50%).
    """
    tokens = _norm_name(name)
    if not tokens:
        return "", ""
    mapping = sec_ticker_map()
    if not mapping:
        return "", ""
    key = " ".join(tokens)
    best = None
    for title, ticker in mapping.items():
        cand = _norm_name(title)
        if not cand:
            continue
        joined = " ".join(cand)
        if joined == key:
            return ticker, title
        if set(tokens) <= set(cand) and len(tokens) / len(cand) >= 0.6:
            if best is None or len(cand) < best[2]:
                best = (ticker, title, len(cand))
    return (best[0], best[1]) if best else ("", "")


# ---------------------------------------------------------------------------
# Yahoo Finance
# ---------------------------------------------------------------------------

def quote_url(ticker):
    return YAHOO_QUOTE.format(urllib.parse.quote(ticker))


def validate_ticker(ticker, name, fetch_text):
    """Confirm the ticker resolves to THIS company on its Yahoo quote page.

    fetch_text(url) -> (text, method); injected so research_service owns the
    HTTP stack and this module stays testable offline.
    """
    if not ticker:
        return {"ok": False, "reason": "no ticker"}
    text, method = fetch_text(quote_url(ticker))
    if not text or method in ("http_error", "blocked", "unreachable", "failed", "timeout"):
        return {"ok": False, "reason": "quote page unavailable ({})".format(method)}
    headline = text.split("\n", 1)[0]
    # "TE Connectivity plc (TEL) Stock Price, News, Quote & History - Yahoo Finance"
    listed = re.split(r"\s*\(", headline)[0]
    ours, theirs = set(_norm_name(name)), set(_norm_name(listed))
    distinctive = {t for t in ours if len(t) > 2}
    overlap = (distinctive & theirs) or (ours & theirs)
    if not overlap:
        return {"ok": False, "reason": "quote page is '{}', not '{}'".format(listed[:60], name)}
    return {"ok": True, "listed_name": listed.strip(), "headline": headline[:160],
            "text": text, "matched_tokens": sorted(overlap)}


def yahoo_queries(name, ticker):
    """The four Yahoo Finance search queries, run only for public companies.

    '[Ticker] Yahoo Finance' is included because it was explicitly requested.
    Measured: through this search backend it returns pages about Yahoo Finance
    itself, so it is name-qualified to give the identity filter something to
    match on, and its noise is discarded downstream like any other result.
    """
    q = ["{} Yahoo Finance".format(name),
         "{} revenue Yahoo Finance".format(name),
         "{} earnings Yahoo Finance".format(name)]
    if ticker:
        q.insert(1, "{} {} Yahoo Finance".format(ticker, name))
    return q


def resolve_listing(company, name, name_cn, fetch_text):
    """Is this company publicly listed, and under what ticker?

    Never raises. A failure downgrades the company to 'unknown', which is
    treated exactly like private: no Yahoo Finance is forced.
    """
    result = {"public_company": False, "listing_status": "private_or_unlisted",
              "ticker": "", "listed_name": "", "resolution_method": "none",
              "quote_url": "", "notes": ""}
    try:
        supplied = explicit_ticker(company) or explicit_ticker(name)
        ticker, edgar_title, method = supplied, "", ("supplied" if supplied else "")
        if not ticker:
            ticker, edgar_title = _sec_lookup(name)
            method = "sec_edgar" if ticker else ""
        if not ticker and name_cn and name_cn != name:
            ticker, edgar_title = _sec_lookup(name_cn)
            method = "sec_edgar" if ticker else ""
        if not ticker:
            result["notes"] = "No ticker in SEC EDGAR and none supplied - treated as private/unlisted."
            return result
        check = validate_ticker(ticker, name, fetch_text)
        if not check["ok"]:
            result["listing_status"] = "unverified"
            result["notes"] = "Ticker candidate {} rejected: {}".format(ticker, check["reason"])
            return result
        result.update(public_company=True, listing_status="public", ticker=ticker,
                      listed_name=check["listed_name"], resolution_method=method,
                      quote_url=quote_url(ticker),
                      notes="Validated on the Yahoo Finance quote page"
                            + (" (EDGAR: {})".format(edgar_title) if edgar_title else ""))
    except Exception as e:
        result["notes"] = "Listing lookup failed: {}: {}".format(type(e).__name__, e)[:160]
    return result


# ---------------------------------------------------------------------------
# Quote-page extraction
# ---------------------------------------------------------------------------
# The generic html_to_text() drops bare numeric lines (it requires letters or
# CJK), so a Yahoo quote page reduced to plain text keeps the LABELS and loses
# every FIGURE. Yahoo renders the summary as
#     <span class="label" title="Market Cap"> ... <span class="value" title="61.6B">
# which is stable and gives label/value pairs directly.

_QUOTE_PAIR = re.compile(
    r'<li[^>]*>.*?<span class="label[^"]*"[^>]*title="([^"]*)".*?'
    r'<span class="value[^"]*"[^>]*title="([^"]*)"', re.S)

# Only the fields that belong in a sales briefing. Price ticks and bid/ask are
# noise for account research and are deliberately excluded.
QUOTE_FIELDS = ("Market Cap", "PE Ratio", "EPS", "Earnings Date", "Forward Dividend",
                "1y Target Est", "52 Week Range", "Avg. Volume", "Beta")


def extract_quote_summary(html):
    """Ordered label -> value pairs from a Yahoo Finance quote page."""
    out = {}
    for label, value in _QUOTE_PAIR.findall(html or ""):
        label, value = label.strip(), value.strip()
        label, value = _unescape(label), _unescape(value)
        if not label or not value or label in out:
            continue
        if any(label.startswith(f) for f in QUOTE_FIELDS):
            out[label] = value
    return out


# Profile block: <div class="infoSection"><p title="VALUE">..</p><h3>LABEL</h3></div>
# Note the VALUE comes BEFORE the label. Pairing these off the flattened text
# silently shifted every field by one (Tesla reported "Sector: Auto
# Manufacturers", "Industry: More about Tesla"), so it is parsed from HTML.
_PROFILE_PAIR = re.compile(
    r'<div class="infoSection[^"]*"[^>]*>\s*<p[^>]*title="([^"]*)".*?<h3[^>]*>(.*?)</h3>', re.S)

PROFILE_FIELDS = ("Sector", "Industry", "Full Time Employees", "Fiscal Year Ends")


def _unescape(text):
    for a, b in (("&amp;", "&"), ("&quot;", '"'), ("&#39;", "'"),
                 ("&lt;", "<"), ("&gt;", ">"), ("&nbsp;", " ")):
        text = text.replace(a, b)
    return text.strip()


def extract_profile(html):
    """Sector / Industry / Employees / Fiscal year end from the Overview block."""
    out = {}
    for value, label in _PROFILE_PAIR.findall(html or ""):
        label = _unescape(re.sub(r"<[^>]+>", "", label))
        value = _unescape(value)
        if label in PROFILE_FIELDS and value and label not in out:
            out[label] = value
    return out


def fetch_yahoo_finance(ticker, fetch_html, fetch_text):
    """Build one Yahoo Finance evidence block for a validated ticker.

    Returns None when the page is unavailable — the caller then reports
    'Yahoo Finance used: No' rather than pretending.
    """
    url = quote_url(ticker)
    try:
        html, _final = fetch_html(url)
    except Exception:
        return None
    summary = extract_quote_summary(html)
    text, method = fetch_text(url)
    profile = extract_profile(html)
    if not summary and not profile:
        return None
    headline = (text or "").split("\n", 1)[0][:160]
    lines = ["Yahoo Finance quote summary for {} ({})".format(
        headline.split("(")[0].strip() or ticker, ticker)]
    for k, v in profile.items():
        lines.append("{}: {}".format(k, v))
    for k, v in summary.items():
        lines.append("{}: {}".format(k, v))
    body = (text or "")
    # Keep some surrounding narrative, but the extracted figures lead.
    block = "\n".join(lines) + ("\n\n" + body[:1800] if body else "")
    return {"url": url, "ticker": ticker, "title": headline or "Yahoo Finance - {}".format(ticker),
            "summary": summary, "profile": profile, "text": block,
            "retrieval_method": "yahoo-quote-direct" if summary else method}
