"""Tavily MCP: candidate DISCOVERY only.

Tavily finds URLs. It does not decide what is true about an account. Everything
it returns re-enters the existing pipeline unchanged - fetch, P0-A/P0-C identity,
collision protection, target/ecosystem provenance, parent-brand separation and
P0-B retention - and its own ranking is discarded, because ranking is not
verification. The MCP wrapper does not expose relevance scores anyway.

Measured 2026-09-05 against Ford, running all fourteen candidate intents: four
of them carried every confirmed provider (ABB, IBM, FANUC) and the other ten
added sources but no new named organisation. So the plan is four intents, then
at most four more aimed at what the first batch missed, and never more than
eight. Fourteen bought nothing.

The protocol is JSON-RPC 2024-11-05 over SSE: GET the endpoint, receive a session
URL, POST calls to it and read the replies off the stream.
"""
import json
import queue
import re
import threading
import urllib.parse
import urllib.request

MCP_TIMEOUT = 45
SEARCH_TIMEOUT = 60

# Batch one, in measured-yield order. Categories, not vendor names: seeding a
# vendor would measure recall of our own assumption instead of discovery.
PRIMARY_INTENTS = (
    ("automation", "{name} plant automation equipment supplier contract"),
    ("robotics", "{name} assembly plant industrial robots installed manufacturing"),
    ("inspection", "{name} manufacturing quality inspection vision system plant"),
    ("mes", "{name} MES manufacturing execution system digital factory plant"),
)

# Batch two, chosen by which categories batch one left empty.
SECONDARY_INTENTS = (
    ("integrator", "{name} system integrator turnkey production line project"),
    ("machine_builder", "{name} plant machine builder tooling supplier equipment order"),
    ("process", "{name} body shop welding stamping paint shop line retooling"),
    ("logistics", "{name} plant intelligent logistics AGV material handling automation"),
    ("contractor", "{name} plant engineering contractor manufacturing facility"),
    ("channel", "{name} equipment engineering services partner supplier agreement"),
)

GENERAL_INTENTS = (
    ("overview", "{name} company manufacturing operations overview"),
    ("plants", "{name} manufacturing plant factory production base"),
    ("news", "{name} manufacturing investment expansion announcement"),
    ("thirdparty", "{name} suppliers manufacturing ecosystem analysis report"),
)

MAX_PROVIDER_SEARCHES = 8          # hard ceiling per account, both batches
MAX_GENERAL_SEARCHES = 4           # the general fallback runs once, at most


class TavilyUnavailable(Exception):
    """Discovery could not run. Never fatal: the account keeps its own retrieval."""


class TavilyClient:
    """One MCP session. Deliberately tiny: search is the only call we make."""

    def __init__(self, url, api_key, timeout=MCP_TIMEOUT):
        self.url = url
        self.api_key = api_key
        self.timeout = timeout
        self.endpoint = None
        self._replies = {}
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._id = 0
        self._stream = None

    # -- transport ---------------------------------------------------------
    def _open(self):
        req = urllib.request.Request(self.url, headers={
            "Authorization": "Bearer " + self.api_key, "Accept": "text/event-stream"})
        # A failure to OPEN is a real error and keeps its previous behaviour:
        # connect() sees no endpoint and raises TavilyUnavailable.
        self._stream = urllib.request.urlopen(req, timeout=self.timeout)
        try:
            self._read(self._stream)
        except Exception:
            # close() tears the socket out from under this loop, so the read that
            # was already in flight fails. urllib's response sets fp to None on
            # close, which surfaces as AttributeError from inside a DAEMON thread
            # - harmless, because the call it was serving has already returned,
            # but it printed a traceback to stderr on every run that used Tavily
            # and read like a failure.
            #
            # Only the shutdown race is swallowed. A stream that dies while we
            # still wanted it is a real fault and is re-raised exactly as before.
            if not self._stop.is_set():
                raise

    def _read(self, stream):
        buf = ""
        for raw in stream:
            if self._stop.is_set():
                return
            buf += raw.decode("utf-8", "replace")
            while "\n\n" in buf:
                frame, buf = buf.split("\n\n", 1)
                ev = re.search(r"^event:\s*(.*)$", frame, re.M)
                data = re.search(r"^data:\s*([\s\S]*)$", frame, re.M)
                if not data:
                    continue
                payload = data.group(1).strip()
                if ev and ev.group(1).strip() == "endpoint":
                    origin = "{0.scheme}://{0.netloc}".format(urllib.parse.urlparse(self.url))
                    self.endpoint = origin + payload
                    self._ready.set()
                    continue
                try:
                    msg = json.loads(payload)
                except Exception:
                    continue
                mid = msg.get("id")
                if mid is None:
                    continue
                with self._lock:
                    q = self._replies.get(mid)
                if q:
                    q.put(msg)

    def connect(self):
        t = threading.Thread(target=self._open, daemon=True)
        t.start()
        if not self._ready.wait(self.timeout):
            raise TavilyUnavailable("MCP endpoint handshake timed out")
        self._call("initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "skeqi-account-research", "version": "1.0"}})
        self._notify("notifications/initialized", {})
        return self

    def close(self):
        """Safe on a client that was never connected, and safe to call twice."""
        self._stop.set()
        # Drop the reference BEFORE closing, so a second close cannot touch a
        # half-closed object and a partially-initialised client has nothing to
        # close at all.
        stream, self._stream = self._stream, None
        try:
            if stream is not None:
                stream.close()
        except Exception:
            pass

    def _post(self, body):
        req = urllib.request.Request(
            self.endpoint, data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": "Bearer " + self.api_key,
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            r.read()

    def _call(self, method, params, timeout=None):
        self._id += 1
        mid = self._id
        q = queue.Queue(maxsize=1)
        with self._lock:
            self._replies[mid] = q
        try:
            self._post({"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
            msg = q.get(timeout=timeout or self.timeout)
        except queue.Empty:
            raise TavilyUnavailable("no reply to {}".format(method))
        finally:
            with self._lock:
                self._replies.pop(mid, None)
        if msg.get("error"):
            raise TavilyUnavailable(str(msg["error"])[:200])
        return msg.get("result") or {}

    def _notify(self, method, params):
        self._post({"jsonrpc": "2.0", "method": method, "params": params})

    # -- the one call we make ---------------------------------------------
    def search(self, query, max_results=8, depth="advanced"):
        res = self._call("tools/call", {
            "name": "tavily-search",
            "arguments": {"query": query, "max_results": max_results,
                          "search_depth": depth}},
            timeout=SEARCH_TIMEOUT)
        text = "\n".join(b.get("text") or "" for b in (res.get("content") or []))
        return parse_results(text)


_RESULT = re.compile(
    r"Title:\s*(.*?)\nURL:\s*(\S+)\nContent:\s*([\s\S]*?)(?=\nTitle:|\n*$)")


def parse_results(text):
    """The MCP wrapper returns formatted text, not JSON, and drops relevance
    scores and raw content entirely. Title, URL and snippet is all there is."""
    out = []
    for m in _RESULT.finditer(text or ""):
        url = m.group(2).strip()
        if not url.startswith("http"):
            continue
        out.append({"title": m.group(1).strip(), "url": url,
                    "content": m.group(3).strip()})
    return out


def plan_intents(name, covered=(), batch=1):
    """Intents for one batch. `covered` names the categories already answered, so
    batch two aims at gaps rather than repeating the highest-yield queries."""
    pool = PRIMARY_INTENTS if batch == 1 else SECONDARY_INTENTS
    out = []
    for key, tmpl in pool:
        if key in covered:
            continue
        out.append((key, tmpl.format(name=name)))
        if len(out) >= 4:
            break
    return out


def plan_general(name):
    return [(k, t.format(name=name)) for k, t in GENERAL_INTENTS][:MAX_GENERAL_SEARCHES]


# ---------------------------------------------------------------------------
# The bounded discovery loop
# ---------------------------------------------------------------------------
# Stop conditions are measured on VERIFIED evidence, never on raw result count.
# Tavily returns eight results for any query ever asked; counting them would stop
# on noise. A thin account is allowed to be thin - none of this is a gate, and a
# run continues to synthesis whatever discovery found.
COVERAGE_MIN_ORGS = 2              # named organisations, CONFIRMED or STRONG
COVERAGE_MIN_CATEGORIES = 2        # distinct provider categories
COVERAGE_MIN_DOMAINS = 2           # distinct registrable domains, per P0-B


def empty_coverage():
    return {"organizations_verified": 0, "confirmed": 0, "strong_indication": 0,
            "market_only": 0, "categories_covered": [], "source_domains": 0,
            "account_specific": 0, "batches_run": 0, "marginal_yield": [],
            "searches_used": 0, "ceiling_reached": False, "used": False,
            "candidates": 0, "verified": 0, "retained": 0, "extracts": 0}


def coverage_is_useful(cov):
    """Enough verified provider evidence that another batch is not worth paying
    for. Deliberately about breadth, not volume: two organisations on one domain
    in one category is a single claim repeated."""
    return (cov["confirmed"] + cov["strong_indication"] >= COVERAGE_MIN_ORGS
            and len(cov["categories_covered"]) >= COVERAGE_MIN_CATEGORIES
            and cov["source_domains"] >= COVERAGE_MIN_DOMAINS)


def discover_providers(client, name, verify, progress=None, ceiling=MAX_PROVIDER_SEARCHES):
    """Two batches at most, then stop whatever the outcome.

    `verify(candidates, intent_key)` is the caller's gate - the existing identity,
    provenance and named-organisation rules. It returns a dict describing what
    actually survived. Nothing in here decides truth; it only decides whether to
    spend another four searches.
    """
    progress = progress or (lambda *_a, **_k: None)
    cov = empty_coverage()
    covered = set()
    seen_urls = set()
    kept = []

    for batch in (1, 2):
        if cov["searches_used"] >= ceiling:
            cov["ceiling_reached"] = True
            break
        intents = plan_intents(name, covered=covered, batch=batch)
        intents = intents[:max(0, ceiling - cov["searches_used"])]
        if not intents:
            break
        before = cov["organizations_verified"]
        cov["batches_run"] = batch
        for key, query in intents:
            try:
                hits = client.search(query)
            except Exception as e:                 # never fatal: this is a bonus path
                progress("providers", "WARN Tavily search failed ({}) - continuing"
                                      .format(type(e).__name__))
                continue
            cov["searches_used"] += 1
            cov["used"] = True
            fresh = [h for h in hits if h["url"] not in seen_urls]
            seen_urls.update(h["url"] for h in hits)
            cov["candidates"] += len(fresh)
            out = verify(fresh, key) or {}
            kept.extend(out.get("evidence") or [])
            cov["verified"] += out.get("verified", 0)
            cov["organizations_verified"] += out.get("organizations", 0)
            cov["confirmed"] += out.get("confirmed", 0)
            cov["strong_indication"] += out.get("strong_indication", 0)
            cov["market_only"] += out.get("market_only", 0)
            cov["account_specific"] += out.get("account_relationships", 0)
            if out.get("organizations"):
                covered.add(key)
                if key not in cov["categories_covered"]:
                    cov["categories_covered"].append(key)
        cov["source_domains"] = len({e.get("domain") for e in kept if e.get("domain")})
        gained = cov["organizations_verified"] - before
        cov["marginal_yield"].append(gained)
        progress("providers", "Provider discovery batch {}: {} search(es), {} verified "
                              "source(s), {} named organisation(s)"
                              .format(batch, cov["searches_used"], cov["verified"],
                                      cov["organizations_verified"]))
        if coverage_is_useful(cov):
            progress("providers", "Provider coverage sufficient - stopping")
            break
        if batch == 2 or gained == 0:
            # A second batch of the same shape will not do better than a first
            # that produced nothing new.
            if gained == 0 and batch == 1:
                progress("providers", "First batch added no verified organisation - stopping")
            break
    if cov["searches_used"] >= ceiling:
        cov["ceiling_reached"] = True
    cov["retained"] = len(kept)
    return kept, cov
