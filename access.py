"""
Lightweight access gate for the standalone Account Research app.

WHY NOT HTTP BASIC AUTH
-----------------------
This app is embedded in the CRM as a CROSS-ORIGIN iframe. Browsers suppress or
mishandle Basic-auth prompts in that position (Chrome blocks cross-origin auth
prompts; Safari is inconsistent), so the frame would show a blank or broken
panel with no way to authenticate. A normal login form rendered INSIDE the
frame always works and looks like part of the product.

WHAT THIS IS
------------
A single shared credential, held in environment variables, exchanged for an
HMAC-signed session cookie. Deliberately small: enough to keep reports, Apollo
contacts and PDFs off the open internet during a live test, and no more. It is
NOT SSO, it does not talk to the CRM, and it shares no session with it.

The cookie is set `SameSite=None; Secure; Partitioned` so it survives inside a
third-party iframe under CHIPS. Nothing secret reaches the browser: the cookie
carries a username and an expiry, signed; the signing key stays on the server.

Disabled entirely when APP_ACCESS_USERNAME / APP_ACCESS_PASSWORD are unset, so
local development is unchanged.
"""

import base64
import hashlib
import hmac
import time
import urllib.parse

from flask import make_response, redirect, request

COOKIE = "ar_access"
SERVICE_HEADER = "X-AR-Service-Key"
TTL_SECONDS = 12 * 3600
OPEN_PATHS = ("/healthz", "/login", "/logout", "/static/")


def enabled(cfg):
    return bool((cfg or {}).get("APP_ACCESS_USERNAME") and (cfg or {}).get("APP_ACCESS_PASSWORD"))


def service_call_ok(cfg):
    """Server-to-server access for the CRM proxy.

    The CRM calls this engine from its own backend, where a browser login form
    is meaningless. A shared secret in a header is the right shape for that:
    it never reaches a browser, and it is checked in constant time. Unset
    APP_SERVICE_KEY and this path simply does not exist.
    """
    expected = (cfg or {}).get("APP_SERVICE_KEY") or ""
    if not expected:
        return False
    return hmac.compare_digest(request.headers.get(SERVICE_HEADER, ""), expected)


def _secret(cfg):
    """Explicit secret if given, otherwise derived from the password so that
    changing the password immediately invalidates every existing session."""
    explicit = (cfg.get("APP_ACCESS_SECRET") or "").strip()
    if explicit:
        return explicit.encode("utf-8")
    return hashlib.sha256(
        ("ar-access|" + (cfg.get("APP_ACCESS_PASSWORD") or "")).encode("utf-8")).digest()


def _sign(payload, secret):
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def make_token(user, cfg, ttl=TTL_SECONDS):
    payload = "{}|{}".format(user, int(time.time()) + ttl).encode("utf-8")
    body = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return "{}.{}".format(body, _sign(payload, _secret(cfg)))


def valid_token(token, cfg):
    if not token or "." not in token:
        return False
    body, _, sig = token.rpartition(".")
    try:
        payload = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
    except Exception:
        return False
    if not hmac.compare_digest(sig, _sign(payload, _secret(cfg))):
        return False
    try:
        user, _, expiry = payload.decode("utf-8").partition("|")
        return user == (cfg.get("APP_ACCESS_USERNAME") or "") and int(expiry) > time.time()
    except Exception:
        return False


def credentials_ok(user, password, cfg):
    """Constant-time comparison on both fields."""
    return (hmac.compare_digest(user or "", cfg.get("APP_ACCESS_USERNAME") or "")
            and hmac.compare_digest(password or "", cfg.get("APP_ACCESS_PASSWORD") or ""))


def _set_cookie(response, token, secure):
    """Built by hand: `Partitioned` is required for a third-party iframe under
    CHIPS and is not offered by every Werkzeug version's set_cookie()."""
    parts = ["{}={}".format(COOKIE, token), "Path=/", "Max-Age={}".format(TTL_SECONDS),
             "HttpOnly", "SameSite=None" if secure else "SameSite=Lax"]
    if secure:
        parts += ["Secure", "Partitioned"]
    response.headers.add("Set-Cookie", "; ".join(parts))
    return response


def _is_secure():
    """Render terminates TLS upstream, so trust the forwarded protocol."""
    proto = request.headers.get("X-Forwarded-Proto", "")
    return request.is_secure or proto.split(",")[0].strip() == "https"


LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Account Research - Sign in</title>
<style>
:root{--brand:#4F2582;--line:#e6e8ee;--muted:#6b7280;--ink:#1a1c21}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f7f8fa;color:var(--ink);
 font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif}
.box{width:100%;max-width:380px;background:#fff;border:1px solid var(--line);border-radius:12px;
 padding:26px;box-shadow:0 1px 3px rgba(16,24,40,.07)}
.mark{width:38px;height:38px;border-radius:10px;background:var(--brand);color:#fff;display:grid;
 place-items:center;font-weight:700;font-size:14px;margin-bottom:14px}
h1{margin:0 0 3px;font-size:16px;font-weight:650}
h1 .cn{color:var(--muted);font-weight:500;font-size:14px;margin-left:6px}
p.sub{margin:0 0 18px;color:var(--muted);font-size:12.5px}
label{display:block;font-size:12px;font-weight:600;margin:12px 0 5px}
input{width:100%;height:38px;padding:0 11px;border:1px solid var(--line);border-radius:7px;font:inherit}
input:focus{outline:0;border-color:var(--brand);box-shadow:0 0 0 3px #f4f0fa}
button{width:100%;height:38px;margin-top:18px;background:var(--brand);color:#fff;border:0;
 border-radius:7px;font:inherit;font-weight:600;cursor:pointer}
button:hover{background:#5e2f97}
.err{margin:14px 0 0;padding:9px 11px;background:#fdecea;border:1px solid #f2d6d3;border-radius:7px;
 color:#b3261e;font-size:12.5px}
.foot{margin:16px 0 0;font-size:11.5px;color:var(--muted);text-align:center}
</style></head><body>
<form class="box" method="post" action="/login">
  <div class="mark">AR</div>
  <h1>Account Research <span class="cn">客户研究</span></h1>
  <p class="sub">Internal access only <span class="cn">仅限内部访问</span></p>
  <input type="hidden" name="next" value="__NEXT__">
  <label for="u">Username / 用户名</label>
  <input id="u" name="username" autocomplete="username" autofocus required>
  <label for="p">Password / 密码</label>
  <input id="p" name="password" type="password" autocomplete="current-password" required>
  <button type="submit">Sign in / 登录</button>
  __ERROR__
  <p class="foot">This app is independent of the CRM and shares no login with it.<br>
     本应用独立运行，不与 CRM 共享登录。</p>
</form></body></html>"""


def _login_page(next_url, error=""):
    html = LOGIN_PAGE.replace("__NEXT__", (next_url or "/").replace('"', "&quot;"))
    return html.replace("__ERROR__", '<p class="err">{}</p>'.format(error) if error else "")


def install(app, load_config):
    """Attach the gate and the frame-ancestors policy to a Flask app."""

    def cfg():
        try:
            return load_config()
        except Exception:
            return {}

    @app.route("/healthz")
    def healthz():
        return {"ok": True}

    @app.route("/login", methods=["GET", "POST"])
    def login():
        conf = cfg()
        if not enabled(conf):
            return redirect("/")
        nxt = request.values.get("next") or "/"
        if not nxt.startswith("/"):
            nxt = "/"                                  # never redirect off-site
        if request.method == "GET":
            return _login_page(nxt)
        if not credentials_ok(request.form.get("username"), request.form.get("password"), conf):
            time.sleep(0.4)                            # slow down guessing
            return _login_page(nxt, "Incorrect username or password / 用户名或密码有误"), 401
        resp = make_response(redirect(nxt))
        return _set_cookie(resp, make_token(conf["APP_ACCESS_USERNAME"], conf), _is_secure())

    @app.route("/logout")
    def logout():
        resp = make_response(redirect("/login"))
        resp.headers.add("Set-Cookie", "{}=; Path=/; Max-Age=0".format(COOKIE))
        return resp

    @app.before_request
    def _gate():
        conf = cfg()
        if not enabled(conf):
            return None
        path = request.path or "/"
        if any(path == p or path.startswith(p) for p in OPEN_PATHS):
            return None
        if service_call_ok(conf) or valid_token(request.cookies.get(COOKIE), conf):
            return None
        if path.startswith("/api/"):
            return {"error": "Not signed in."}, 401
        target = (request.full_path or "/").rstrip("?")
        return redirect("/login?next=" + urllib.parse.quote(target or "/"))

    @app.after_request
    def _frame_policy(response):
        """Only the approved CRM origin may embed this app. Never `*`."""
        allowed = (cfg().get("ALLOWED_FRAME_ANCESTORS") or "").strip()
        response.headers["Content-Security-Policy"] = (
            "frame-ancestors 'self' " + allowed if allowed else "frame-ancestors 'self'")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    return app
