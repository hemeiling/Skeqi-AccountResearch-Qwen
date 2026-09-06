#!/usr/bin/env python
"""The Tavily client's shutdown race.

close() tears the socket out from under the SSE reader, so the read already in
flight fails. urllib sets the response's fp to None on close, which surfaces as
an AttributeError from inside a DAEMON thread: harmless, because the call it was
serving has already returned, but it printed a traceback to stderr on every run
that used Tavily and read like a failure.

Only the shutdown race may be swallowed. A stream that dies while we still
wanted it is a real fault and must still be raised.

No network, no MCP server, no model call.
  .venv/bin/python test_tavily_shutdown.py
"""
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tavily_service as tv                                      # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (" - " + detail if detail else ""))


class Recorder(object):
    """Captures anything escaping a daemon thread, which is where this lived."""
    def __init__(self):
        self.seen = []
        self._prev = threading.excepthook

    def __enter__(self):
        threading.excepthook = lambda a: self.seen.append(a.exc_type.__name__)
        return self

    def __exit__(self, *a):
        threading.excepthook = self._prev


class FakeStream(object):
    """Behaves like urllib's response: iterating after close() raises the same
    AttributeError, because fp is gone."""
    def __init__(self, frames, block=True):
        self.frames = list(frames)
        self.fp = object()
        self.block = block
        self.closed = False

    def __iter__(self):
        return self

    def __next__(self):
        if self.fp is None:
            raise AttributeError("'NoneType' object has no attribute 'readline'")
        if self.frames:
            return self.frames.pop(0)
        if not self.block:
            raise StopIteration
        time.sleep(0.02)                       # idle, as a live SSE stream is
        if self.fp is None:
            raise AttributeError("'NoneType' object has no attribute 'readline'")
        return b""

    def close(self):
        self.closed = True
        self.fp = None                         # exactly what urllib does


ENDPOINT = b"event:endpoint\ndata:/api/v1/mcps/tavily-ai/message?sessionId=abc\n\n"


def client_with(stream, monkey=True):
    c = tv.TavilyClient("https://host.example/api/v1/mcps/tavily-ai/sse", "key")
    if monkey:
        tv.urllib.request.urlopen = lambda *a, **k: stream
    return c


real_urlopen = tv.urllib.request.urlopen
real_post = tv.TavilyClient._post

print("\n[1] The close race no longer escapes")
with Recorder() as rec:
    st = FakeStream([ENDPOINT])
    c = client_with(st)
    t = threading.Thread(target=c._open, daemon=True)
    t.start()
    time.sleep(0.3)
    check("the endpoint was parsed before close", c.endpoint is not None, str(c.endpoint))
    c.close()
    time.sleep(0.4)
    check("the stream was actually closed", st.closed)
    check("no AttributeError escaped the reader thread",
          "AttributeError" not in rec.seen, str(rec.seen))
    check("nothing at all escaped", rec.seen == [], str(rec.seen))

print("\n[2] A genuine mid-stream failure is STILL raised")
with Recorder() as rec2:
    class Dying(FakeStream):
        def __next__(self):
            raise IOError("connection reset")
    c2 = client_with(Dying([]))
    t2 = threading.Thread(target=c2._open, daemon=True)
    t2.start()
    time.sleep(0.4)
    check("a fault we did not ask for is not swallowed",
          "OSError" in rec2.seen or "IOError" in rec2.seen, str(rec2.seen))
    check("and _stop was never set", not c2._stop.is_set())

print("\n[3] Closing a partially-initialised or already-closed client is safe")
with Recorder() as rec3:
    never = tv.TavilyClient("https://host.example/x", "key")
    never.close()
    check("closing a client that never connected does not raise", True)
    never.close()
    check("closing it twice is safe", True)
    st4 = FakeStream([ENDPOINT])
    c4 = client_with(st4)
    threading.Thread(target=c4._open, daemon=True).start()
    time.sleep(0.25)
    c4.close(); c4.close(); c4.close()
    time.sleep(0.3)
    check("triple close is safe and silent", rec3.seen == [], str(rec3.seen))
    check("the stream reference is dropped", c4._stream is None)

print("\n[4] Normal calls are unchanged")
with Recorder() as rec5:
    reply = json.dumps({"jsonrpc": "2.0", "id": 1,
                        "result": {"tools": [{"name": "tavily-search"}]}}).encode()
    posted = threading.Event()

    class Replying(FakeStream):
        """A real server answers AFTER the POST. Emitting the reply up front
        would race _call's queue registration and drop it - a fixture artifact,
        not a client defect."""
        def __next__(self):
            if self.fp is None:
                raise AttributeError("'NoneType' object has no attribute 'readline'")
            if self.frames:
                return self.frames.pop(0)
            if posted.is_set():
                posted.clear()
                return b"data:" + reply + b"\n\n"
            time.sleep(0.02)
            return b""

    st5 = Replying([ENDPOINT])
    c5 = client_with(st5)
    threading.Thread(target=c5._open, daemon=True).start()
    time.sleep(0.3)
    tv.TavilyClient._post = lambda self, body: posted.set()   # reply follows the POST
    try:
        res = c5._call("tools/list", {}, timeout=3)
        check("a successful call still returns its result",
              (res.get("tools") or [{}])[0].get("name") == "tavily-search", str(res))
    except Exception as e:
        check("a successful call still returns its result", False, repr(e))
    finally:
        tv.TavilyClient._post = real_post
    c5.close()
    time.sleep(0.3)
    check("and closing after a successful call is silent", rec5.seen == [], str(rec5.seen))

print("\n[5] Real request failures still reach the caller")
tv.urllib.request.urlopen = real_urlopen
c6 = tv.TavilyClient("https://host.example/x", "key")
c6.endpoint = "https://host.example/message"
c6._ready.set()
def boom(self, body):
    raise IOError("upstream refused")
tv.TavilyClient._post = boom
try:
    c6._call("tools/call", {}, timeout=2)
    check("a failing request raises rather than returning silently", False)
except Exception as e:
    check("a failing request raises rather than returning silently",
          isinstance(e, (IOError, OSError, tv.TavilyUnavailable)), type(e).__name__)
finally:
    tv.TavilyClient._post = real_post
# And discovery still turns that into a warning rather than a dead run.
class Failing(object):
    def search(self, q, **kw):
        raise tv.TavilyUnavailable("upstream refused")
msgs = []
kept, cov = tv.discover_providers(Failing(), "Acme", lambda c, k: {"verified": 0},
                                  progress=lambda *a, **k: msgs.append(a))
check("discovery survives it and reports a warning",
      isinstance(cov, dict) and cov["searches_used"] == 0, str(cov["searches_used"]))
check("the failure is surfaced, not hidden",
      any("WARN" in str(m) for m in msgs), str(msgs[:1]))

print("\n{} passed, {} failed".format(len(PASS), len(FAIL)))
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
