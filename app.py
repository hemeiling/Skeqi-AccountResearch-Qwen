"""
Account Research — test app.

    python3 -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    python3 app.py     ->  http://127.0.0.1:5057

Execution model:
  retrieval runs ONCE per company (cacheable), then every selected model synthesises
  over that same evidence package CONCURRENTLY. Each model is an independent session:
  it saves and renders the moment it finishes, and one stall or timeout cannot block
  the others. Credentials stay on the backend.
"""

import concurrent.futures
import io
import os
import json
import re
import tempfile
import threading
import urllib.request
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file, send_from_directory

import access
import batch_service as bs
import confidence as conf
import language_view as lv
import pdf_service as ps
import research_service as rs

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "test_results"
RESULTS_DIR.mkdir(exist_ok=True)
REPORTS_DIR = bs.REPORTS_DIR
BATCHES = {}
BATCH_LOCK = threading.Lock()

app = Flask(__name__)

# Access gate + frame-ancestors policy. Inert unless APP_ACCESS_USERNAME and
# APP_ACCESS_PASSWORD are set, so local development is unchanged.
access.install(app, rs.load_config)

JOBS = {}
JOBS_LOCK = threading.Lock()


def slugify(text, limit=40):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:limit] or "company"


def update(job_id, mutate):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job:
            mutate(job)
            job["updated_at"] = time.time()


def save_run(package, run):
    """One JSON file per model, written the moment that model finishes."""
    stamp = datetime.now().astimezone()
    record = {
        "timestamp": stamp.isoformat(),
        "company": package["company"],
        "website": package["website"],
        "model": run["model"],
        "model_label": run["model_label"],
        "protocol": run["protocol"],
        "endpoint": run["endpoint"],
        "search_enabled": True,
        "evidence_cached": package.get("cached", False),
        "search_queries": package["search_queries"],
        "sources": package["sources"],
        "research_result": conf.normalize_text(run["report"]),
        # What a human reads: "not enough evidence" placeholders removed. The
        # full text and the counts above stay intact for auditing.
        "research_result_display": conf.strip_unsupported(run["report"]),
        "research_result_raw": run["report"],
        "confidence": conf.extract(run["report"]),
        "token_usage": {"input": run["input_tokens"], "output": run["output_tokens"],
                        "total": run["total_tokens"]},
        "latency_seconds": run["latency_seconds"],
        "retrieval_timings": package.get("timings", {}),
        "financial_sources": package.get("financial_sources", {}),
        "apollo_usage": (package.get("apollo") or {}).get("usage", {}),
        "quality": package.get("quality", {}),
        "decision_makers": run.get("decision_makers", []),
        "people_summary": run.get("people_summary", {}),
        "status": run["status"],
        "error": run["error"],
    }
    name = "{}_{}_{}.json".format(slugify(package["company"], 24), run["model"],
                                  stamp.strftime("%Y%m%d-%H%M%S"))
    record["file"] = name
    # The PDF is produced first so its filename is saved WITH the record; writing
    # the JSON first left every history entry without a PDF link.
    try:
        _rec, pdf_name = bs.write_company_output(package["company"], package["website"],
                                                 package, run)
        record["pdf"] = pdf_name
        record["pdf_dir"] = ps.safe_name(package["company"])
    except Exception as e:
        record["pdf"] = None
        record["pdf_error"] = str(e)[:200]
    (RESULTS_DIR / name).write_text(json.dumps(record, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    return record


# ── CRM callback ─────────────────────────────────────────────────────────────
# The engine owns the run; the CRM owns the database. When a run finishes the
# engine POSTs the result to the CRM, which writes it to Neon. Nothing here
# depends on a browser being open, which is the whole point: research the user
# paid for must land even if they closed the tab.
#
# The engine never talks to Neon directly. One shared secret, APP_SERVICE_KEY
# here and ACCOUNT_RESEARCH_SERVICE_KEY there, authenticates the call.
CALLBACK_TIMEOUT = 45


def _callback_target(job):
    """Where to report, per job, falling back to the deployment default."""
    return (job or {}).get("callback_url") or os.environ.get("CRM_CALLBACK_URL") or ""


def notify_crm(job_id, payload):
    """Best effort, and deliberately silent about its own failures.

    A callback that cannot be delivered must never take down a run that already
    succeeded: the report is still on disk and the CRM can still poll for it.
    The outcome is recorded on the job so it is visible rather than guessed at.
    """
    with JOBS_LOCK:
        job = dict(JOBS.get(job_id) or {})
    url = _callback_target(job)
    if not url:
        return False, "CRM_CALLBACK_URL is not set"
    key = (rs.load_config().get("APP_SERVICE_KEY")
           or os.environ.get("APP_SERVICE_KEY") or "").strip()
    body = json.dumps(dict(payload, job_id=job_id)).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "X-AR-Service-Key": key,            # never logged
    })
    try:
        with urllib.request.urlopen(req, timeout=CALLBACK_TIMEOUT) as resp:
            return True, json.loads(resp.read().decode("utf-8") or "{}")
    except Exception as e:
        return False, "{}: {}".format(type(e).__name__, e)[:200]


def worker(job_id, company, website, models, use_cache, force=False, known_contacts=None):
    t_wall = time.time()

    # Stage keys in the order the pipeline reaches them, so a percentage can be
    # derived from real progress rather than a timer.
    STAGE_ORDER = ("discover", "official", "listing", "queries", "site", "search",
                   "financial", "apollo", "contacts", "dedupe", "evidence",
                   "quality", "model")
    seen_stages = set()

    def progress(stage, message, **extra):
        def m(job):
            job["stages"].append({"stage": stage, "message": message,
                                  "at": round(time.time() - t_wall, 1)})
            if extra.get("queries"):
                job["search_queries"] = extra["queries"]
        update(job_id, m)
        # Heartbeat to the CRM. It doubles as the liveness signal that stops the
        # job being swept as interrupted, so it is sent for every stage.
        seen_stages.add(stage)
        pct = int(round(len(seen_stages) / (len(STAGE_ORDER) + 3) * 100))
        threading.Thread(target=notify_crm, args=(job_id, {
            "event": "progress", "stage": stage, "progress_percent": min(pct, 95),
            "warning": message if str(message).startswith("WARN") else None,
        }), daemon=True).start()

    try:
        cfg = rs.load_config()

        package = rs.load_cached_evidence(company, website) if use_cache else None
        if package:
            progress("cache", "Using cached web research ({} sources, {}s old)".format(
                len(package["sources"]), package.get("age_seconds", 0)))
        else:
            package = rs.build_shared_evidence(company, website, cfg, progress,
                                               known_contacts=known_contacts)

        quality = package.get("quality", {})
        update(job_id, lambda j: j.update(
            sources=package["sources"], search_queries=package["search_queries"],
            evidence_cached=package.get("cached", False),
            financial_sources=package.get("financial_sources", {}),
            apollo_usage=(package.get("apollo") or {}).get("usage", {}),
            quality=quality,
            retrieval_timings=package.get("timings", {}),
            retrieval_seconds=round(time.time() - t_wall, 1),
            phase="synthesis"))

        # Hard quality guard: never spend tokens turning nothing into a
        # confident-looking 19-section report. The user decides explicitly.
        if quality.get("blocking") and not force:
            quality["can_force"] = True
            update(job_id, lambda j: j.update(
                status="needs_review", phase="needs_review",
                message="Research retrieval incomplete.",
                wall_seconds=round(time.time() - t_wall, 1)))
            # Not a failure and not a report: nothing is stored, and the job is
            # closed so it stops blocking the next attempt for this company.
            notify_crm(job_id, {"event": "failed",
                                "error": "Retrieval incomplete - nothing was generated."})
            return

        def run_model(model):
            started = time.time()
            update(job_id, lambda j: j["models"][model].update(
                status="generating", started_at=started))
            try:
                run = rs.synthesize_with_fallback(
                    model, company, website, package["evidence"], cfg,
                    apollo_people=(package.get("apollo") or {}).get("people"),
                    progress=lambda m: progress("model", m))
            except Exception as e:                       # never let one model kill the job
                run = {"model": model, "model_label": rs.MODEL_LABELS.get(model, model),
                       "status": 0, "report": "", "endpoint": "?", "protocol": "DashScope Native",
                       "input_tokens": None, "output_tokens": None, "total_tokens": None,
                       "latency_seconds": round(time.time() - started, 1),
                       "decision_makers": [], "people_summary": {},
                       "error": str(e)[:300]}
            ok = run["status"] == 200 and run["report"]
            saved = save_run(package, run) if ok else None
            state = ("complete" if ok
                     else "access_denied" if run.get("access_denied")
                     else "timeout" if run["status"] in (0, 504) else "failed")

            def m(job):
                job["models"][model].update(
                    status=state, elapsed=run["latency_seconds"], result=saved,
                    error=run["error"], model_used=run.get("model_used"),
                    fallback_used=bool(run.get("fallback_used")),
                    models_tried=run.get("models_tried") or [],
                    token_usage={
                        "input": run["input_tokens"], "output": run["output_tokens"],
                        "total": run["total_tokens"]})
            update(job_id, m)
            return model

        # All selected models run concurrently over the SAME evidence package.
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(models)) as pool:
            list(pool.map(run_model, models))

        update(job_id, lambda j: j.update(
            status="done", phase="done",
            wall_seconds=round(time.time() - t_wall, 1)))

        # The report is finished. Hand it to the CRM to store; this is the ONLY
        # path by which a completed run reaches Neon.
        with JOBS_LOCK:
            snap = dict(JOBS.get(job_id) or {})
        saved_any = False
        for m in (snap.get("models") or {}).values():
            if m.get("status") == "complete" and m.get("result"):
                ok, detail = notify_crm(job_id, {
                    "event": "completed",
                    "company_name": company,
                    "website": website,
                    "model": m.get("model"),
                    "record": m["result"],
                    "warnings": [st["message"] for st in (snap.get("stages") or [])
                                 if str(st.get("message", "")).startswith("WARN")],
                })
                saved_any = saved_any or ok
                update(job_id, lambda j, ok=ok, d=detail:
                       j.update(crm_persisted=ok, crm_detail=d))
        if not saved_any and (snap.get("models") or {}):
            # Say so loudly on the job: the run cost money and may not be stored.
            update(job_id, lambda j: j.update(
                crm_persisted=False,
                message="Research finished but the CRM callback did not confirm storage."))

    except rs.RetrievalError as e:
        # Retrieval exhausted every fallback and found nothing usable. This is a
        # reviewable outcome with next steps, not an opaque failure.
        update(job_id, lambda j: j.update(
            status="needs_review", phase="needs_review",
            message="Research retrieval incomplete.",
            quality={"level": "insufficient", "sources": 0, "blocking": True,
                     "identity_verified": False, "official_evidence": False,
                     "overview_evidence": False, "products_evidence": False,
                     "direct_site_sources": 0, "web_sources": 0,
                     "retrieval_waves": [], "can_force": False,
                     "reasons": [rs.FAILURE_REASONS.get(e.reason, str(e))]},
            website=e.website or j.get("website"),
            wall_seconds=round(time.time() - t_wall, 1)))
        notify_crm(job_id, {"event": "failed",
                            "error": "Retrieval incomplete - nothing was generated."})
    except Exception as e:
        update(job_id, lambda j: j.update(status="error", phase="error",
                                          message=str(e)[:300],
                                          wall_seconds=round(time.time() - t_wall, 1)))
        notify_crm(job_id, {"event": "failed", "error": str(e)[:400]})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/config")
def api_config():
    try:
        return jsonify(rs.public_config(rs.load_config()))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/models/health")
def api_models_health():
    """Which configured models this account can actually call.

    Diagnostic only: model ids, HTTP status and the provider's own error code and
    message. No credential value is ever included.
    """
    probe = request.args.get("probe") == "1"
    try:
        cfg = rs.load_config()
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500
    candidates = []
    for m in rs.ALL_MODELS + [x.strip() for x in
                              (cfg.get("AI_MODEL_FALLBACKS") or "").split(",") if x.strip()]:
        if m not in candidates:
            candidates.append(m)
    if probe:
        for model in candidates:
            rs.run_search("ping", cfg, 20, model=model)
    report = rs.access_report()
    rows = []
    for model in candidates:
        rec = report.get(model)
        rows.append({
            "model": model,
            "label": rs.MODEL_LABELS.get(model, model),
            "endpoint": "multimodal-generation"
            if rs.endpoint_for(model, cfg)[1] else "text-generation",
            "state": "unknown" if rec is None else ("available" if rec["ok"] else "access_denied"),
            "code": (rec or {}).get("code", ""),
            "message": (rec or {}).get("message", ""),
        })
    return jsonify({"provider": "Alibaba Bailian / DashScope (native)",
                    "base_url": cfg.get("DASHSCOPE_BASE_URL", ""),
                    "workspace": cfg.get("DASHSCOPE_WORKSPACE_ID", ""),
                    "models": rows,
                    "any_available": any(r["state"] == "available" for r in rows)})


@app.route("/api/cache-status")
def api_cache_status():
    """Lets the UI offer 'Use cached research / Refresh web research' before spending calls."""
    company = (request.args.get("company") or "").strip()
    website = (request.args.get("website") or "").strip()
    pkg = rs.load_cached_evidence(company, website) if company else None
    if not pkg:
        return jsonify({"cached": False})
    return jsonify({"cached": True, "sources": len(pkg["sources"]),
                    "age_seconds": pkg.get("age_seconds", 0),
                    "built_at": pkg.get("built_at")})


@app.route("/api/research", methods=["POST"])
def api_research():
    body = request.get_json(silent=True) or {}
    company = (body.get("company") or "").strip()
    website = (body.get("website") or "").strip()
    choice = (body.get("model") or "").strip()
    # Contacts the CRM already holds, attached by its proxy. Never required.
    known_contacts = body.get("known_contacts") or []
    callback_url = (body.get("callback_url") or "").strip()
    use_cache = bool(body.get("use_cache"))
    force = bool(body.get("force"))          # explicit "Generate Anyway"
    if not company:
        return jsonify({"error": "Company name is required."}), 400
    models = rs.ALL_MODELS if choice == "all" else [choice]
    if choice != "all" and choice not in rs.ALL_MODELS:
        return jsonify({"error": "Unknown model: {}".format(choice)}), 400

    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {
            "status": "running", "phase": "retrieval", "message": "",
            "company": company, "website": website,
            "stages": [], "search_queries": [], "sources": [],
            "evidence_cached": False, "retrieval_timings": {},
            "financial_sources": {}, "apollo_usage": {}, "quality": {},
            "callback_url": callback_url,
            "models": {m: {"model": m, "label": rs.MODEL_LABELS[m], "status": "pending",
                           "elapsed": None, "result": None, "error": None,
                           "token_usage": None, "started_at": None}
                       for m in models},
            "model_order": models,
        }
    threading.Thread(target=worker,
                     args=(job_id, company, website, models, use_cache, force,
                           known_contacts),
                     daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/job/<job_id>")
def api_job(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        snapshot = json.loads(json.dumps(job)) if job else None
    if not snapshot:
        return jsonify({"error": "unknown job"}), 404
    # Live elapsed for models still running.
    now = time.time()
    for m in snapshot["models"].values():
        if m["status"] == "generating" and m["started_at"]:
            m["elapsed"] = round(now - m["started_at"], 1)
    return jsonify(snapshot)


@app.route("/api/history")
def api_history():
    items = []
    for path in sorted(RESULTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime,
                       reverse=True)[:40]:
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        items.append({"file": path.name, "company": d.get("company"),
                      "model_label": d.get("model_label", d.get("model")),
                      "timestamp": d.get("timestamp"),
                      "total_tokens": (d.get("token_usage") or {}).get("total")})
    return jsonify(items)


def enrich_record(record):
    """Fill in fields that older saved records predate, on read.

    Reports written before the display filter and the PDF-ordering fix are still
    perfectly good research; they just lack the derived fields, and recomputing
    beats asking the user to regenerate (and re-pay for) them.
    """
    if not record.get("research_result_display"):
        record["research_result_display"] = conf.strip_unsupported(
            record.get("research_result_raw") or record.get("research_result") or "")
    if not record.get("pdf") and record.get("company"):
        prior = bs.existing_report(record["company"]) or {}
        match = next((r for r in prior.get("runs", [])
                      if r.get("model") == record.get("model")), None)
        if match and match.get("pdf"):
            record["pdf"] = match["pdf"]
            record["pdf_dir"] = ps.safe_name(record["company"])
    return record


@app.route("/api/history/<path:filename>")
def api_history_item(filename):
    """Load a past result from disk - no model call."""
    path = (RESULTS_DIR / filename).resolve()
    if path.parent != RESULTS_DIR.resolve() or not path.exists():
        return jsonify({"error": "not found"}), 404
    return jsonify(enrich_record(json.loads(path.read_text(encoding="utf-8"))))


# --------------------------------------------------------------- reports / PDF
@app.route("/api/report/<path:folder>/<path:filename>")
def api_report_file(folder, filename):
    """Serve a generated PDF or research JSON.

    ?inline=1 serves the PDF for display instead of download, which is what the
    embedded viewer needs; without it the browser saves the file as before.
    """
    d = (REPORTS_DIR / folder).resolve()
    if d.parent != REPORTS_DIR.resolve() or not (d / filename).exists():
        return jsonify({"error": "not found"}), 404
    inline = request.args.get("inline") == "1"
    return send_from_directory(
        d, filename, as_attachment=filename.lower().endswith(".pdf") and not inline)


@app.route("/api/report-pdf")
def api_report_pdf():
    """Serve this company's PDF in the requested display language.

    Rendered on demand from the SAME saved bilingual research JSON and cached on
    disk. No model call, no re-retrieval - switching language never costs tokens.
    """
    company = (request.args.get("company") or "").strip()
    lang = lv.normalize(request.args.get("lang"))
    model = (request.args.get("model") or "").strip() or None
    if not company:
        return jsonify({"error": "company is required"}), 400
    try:
        path, name = bs.language_pdf(company, lang, model)
    except Exception as e:
        return jsonify({"error": "Could not render PDF: {}".format(e)[:200]}), 500
    if not path or not path.exists():
        return jsonify({"error": "no saved report for that company"}), 404
    inline = request.args.get("inline") == "1"
    return send_from_directory(path.parent, name, as_attachment=not inline)


@app.route("/api/render", methods=["POST"])
def api_render():
    """Render a STORED research record. No model call, no retrieval.

    The CRM keeps the canonical bilingual record in Neon and posts it here to
    get a language-selected view or a PDF back. That keeps report storage in the
    database while the rendering (reportlab, the language selector, the
    confidence filter) stays with the engine that produced the record.

        {"record": {...}, "lang": "en|zh|bilingual", "format": "markdown|pdf"}
    """
    body = request.get_json(silent=True) or {}
    record = body.get("record")
    if not isinstance(record, dict) or not record.get("research_result"):
        return jsonify({"error": "record with research_result is required"}), 400
    lang = lv.normalize(body.get("lang"))
    fmt = (body.get("format") or "markdown").strip().lower()

    if fmt == "markdown":
        text = lv.select(conf.strip_unsupported(record.get("research_result") or ""), lang)
        return jsonify({
            "language": lang,
            "markdown": text,
            "company": record.get("company", ""),
            "decision_makers": record.get("decision_makers", []),
            "people_summary": record.get("people_summary", {}),
            "sources": record.get("sources", []),
        })

    if fmt == "pdf":
        name = ps.pdf_filename(record.get("company", "Company"), record.get("model_label"),
                               None, multi_model=True, lang=lang)
        tmp = Path(tempfile.gettempdir()) / "ar-render" / name
        tmp.parent.mkdir(parents=True, exist_ok=True)
        try:
            ps.build_pdf(record, tmp, lang=lang)
        except Exception as e:
            return jsonify({"error": "Could not render PDF: {}".format(e)[:200]}), 500
        return send_file(tmp, mimetype="application/pdf",
                         as_attachment=request.args.get("inline") != "1",
                         download_name=name)

    return jsonify({"error": "format must be markdown or pdf"}), 400


@app.route("/api/render-portfolio", methods=["POST"])
def api_render_portfolio():
    """Combined portfolio PDF from POSTED records. No disk, no model.

    The CRM holds the canonical records in Neon and sends the ones to include,
    so the portfolio no longer depends on what happens to be on this service's
    filesystem.  {"records": [...], "lang": "...", "title": "..."}
    """
    body = request.get_json(silent=True) or {}
    records = [r for r in (body.get("records") or []) if isinstance(r, dict)]
    if not records:
        return jsonify({"error": "records is required"}), 400
    lang = lv.normalize(body.get("lang"))
    name = "Account_Research_Portfolio_{}_{}.pdf".format(
        lv.SUFFIX[lang], datetime.now().strftime("%Y-%m-%d"))
    out = Path(tempfile.gettempdir()) / "ar-render" / name
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        ps.build_portfolio_pdf(records, out, title=body.get("title") or None, lang=lang)
    except Exception as e:
        return jsonify({"error": "Could not build portfolio: {}".format(e)[:200]}), 500
    return send_file(out, mimetype="application/pdf",
                     as_attachment=request.args.get("inline") != "1", download_name=name)


@app.route("/api/render-zip", methods=["POST"])
def api_render_zip():
    """ZIP of one PDF per posted record, in the requested language."""
    body = request.get_json(silent=True) or {}
    records = [r for r in (body.get("records") or []) if isinstance(r, dict)]
    if not records:
        return jsonify({"error": "records is required"}), 400
    lang = lv.normalize(body.get("lang"))
    tmp = Path(tempfile.gettempdir()) / "ar-render" / "zip"
    tmp.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rec in records:
            fname = ps.pdf_filename(rec.get("company", "Company"), rec.get("model_label"),
                                    None, multi_model=True, lang=lang)
            path = tmp / fname
            try:
                ps.build_pdf(rec, path, lang=lang)
            except Exception:
                continue                       # one bad record must not fail the archive
            z.write(path, arcname=fname)
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name="account_research_{}_{}.zip".format(
                         lv.SUFFIX[lang], datetime.now().strftime("%Y-%m-%d")))


@app.route("/api/reports")
def api_reports():
    return jsonify([{ "company": r["company"], "model": r["model"],
                      "dir": r["dir"], "file": r["file"] } for r in bs.completed_pdfs()])


@app.route("/api/reports/delete", methods=["POST"])
def api_reports_delete():
    """Delete saved reports by COMPANY NAME. The browser never supplies a path:
    the directory is derived and bounds-checked server-side."""
    body = request.get_json(silent=True) or {}
    names = body.get("companies")
    if isinstance(names, str):
        names = [names]
    if not isinstance(names, list) or not names:
        return jsonify({"ok": False, "error": "companies is required"}), 400
    if len(names) > 200:
        return jsonify({"ok": False, "error": "too many companies in one request"}), 400
    results = [bs.delete_report(str(n), results_dir=RESULTS_DIR) for n in names]
    deleted = [r for r in results if r.get("ok")]
    return jsonify({"ok": True, "deleted": len(deleted), "requested": len(results),
                    "results": results})


@app.route("/api/reports/zip")
def api_reports_zip():
    """ZIP of completed reports in the requested language - never waits for the
    batch to finish. Language PDFs are rendered from saved data as needed."""
    lang = lv.normalize(request.args.get("lang"))
    files = bs.language_pdfs(lang=lang)
    if not files:
        return jsonify({"error": "no completed reports yet"}), 404
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f["path"], arcname=f["file"])
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name="account_research_reports_{}_{}.zip".format(
                         lv.SUFFIX[lang], datetime.now().strftime("%Y-%m-%d")))


@app.route("/api/reports/compile", methods=["POST"])
def api_reports_compile():
    """Combine saved reports into one portfolio PDF. Uses zero AI tokens."""
    body = request.get_json(silent=True) or {}
    companies = body.get("companies") or None
    order = (body.get("order") or "upload").strip()
    lang = lv.normalize(body.get("lang"))
    selected = bool(companies)
    try:
        out, included = bs.compile_portfolio(companies, order, rs.load_config(), selected, lang)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": "Compile failed: {}".format(e)[:250]}), 500
    return jsonify({"file": out.name, "companies": included, "count": len(included),
                    "language": lang, "url": "/api/portfolio/{}".format(out.name)})


@app.route("/api/portfolio/<path:filename>")
def api_portfolio(filename):
    path = (REPORTS_DIR / filename).resolve()
    if path.parent != REPORTS_DIR.resolve() or not path.exists():
        return jsonify({"error": "not found"}), 404
    inline = request.args.get("inline") == "1"
    return send_from_directory(REPORTS_DIR, filename, as_attachment=not inline)


# --------------------------------------------------------------- batch
@app.route("/api/reports/exists")
def api_reports_exists():
    names = [n for n in (request.args.get("companies") or "").split("||") if n.strip()]
    return jsonify({name: bs.has_report(name) for name in names})


@app.route("/api/batch/upload", methods=["POST"])
def api_batch_upload():
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "No file uploaded."}), 400
    try:
        headers, rows = bs.parse_upload(f.filename, f.read())
    except Exception as e:
        return jsonify({"error": "Could not read file: {}".format(e)[:200]}), 400
    if not headers:
        return jsonify({"error": "The file appears to be empty."}), 400
    mapping = bs.guess_mapping(headers, rows)
    preview = bs.build_items(rows, mapping)
    return jsonify({"headers": headers, "rows": rows[:500], "mapping": mapping,
                    "items": preview, "row_count": len(rows)})


@app.route("/api/batch/start", methods=["POST"])
def api_batch_start():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400
    # Never trust the client payload shape: normalise and validate server-side.
    items, skipped = bs.normalize_items(body.get("items"))
    choice = str(body.get("model") or "").strip()
    use_existing = bool(body.get("use_existing", True))
    if not items:
        return jsonify({"error": "No valid companies supplied "
                                 "(each item needs a company name).",
                        "skipped": skipped}), 400
    models = rs.ALL_MODELS if choice == "all" else [choice]
    if choice != "all" and choice not in rs.ALL_MODELS:
        return jsonify({"error": "Unknown model: {}".format(choice)}), 400

    batch_id = body.get("batch_id") or uuid.uuid4().hex[:12]
    batch = bs.Batch(batch_id, items, models, use_existing=use_existing)
    with BATCH_LOCK:
        BATCHES[batch_id] = batch

    def go():
        try:
            batch.run(rs.load_config())
        except Exception as e:
            batch.status = "error"
            batch.stage = str(e)[:200]
            batch.save()

    threading.Thread(target=go, daemon=True).start()
    return jsonify({"batch_id": batch_id, "accepted": len(items), "skipped": skipped})


@app.route("/api/batch/<batch_id>")
def api_batch_status(batch_id):
    with BATCH_LOCK:
        batch = BATCHES.get(batch_id)
    if batch:
        return jsonify(batch.snapshot())
    saved = bs.load_batch(batch_id)          # resume view after a restart
    if saved:
        return jsonify(saved)
    return jsonify({"error": "unknown batch"}), 404


@app.route("/api/batch/<batch_id>/stop", methods=["POST"])
def api_batch_stop(batch_id):
    with BATCH_LOCK:
        batch = BATCHES.get(batch_id)
    if not batch:
        return jsonify({"error": "unknown batch"}), 404
    batch.stop_requested = True
    return jsonify({"ok": True})


@app.route("/api/batches")
def api_batches():
    return jsonify(bs.list_batches())


if __name__ == "__main__":
    try:
        cfg = rs.load_config()
        print("configuration: {}".format(cfg["_source_file"]))
        print("access gate: {}".format(
            "on (APP_ACCESS_USERNAME set)" if access.enabled(cfg)
            else "OFF - set APP_ACCESS_USERNAME/APP_ACCESS_PASSWORD before exposing publicly"))
    except Exception as e:
        raise SystemExit("Startup failed: {}".format(e))
    # A managed host assigns PORT and requires binding to every interface; a
    # laptop should stay on loopback. HOST makes that explicit rather than
    # silently opening the app to the local network.
    port = int(os.environ.get("PORT", "5057"))
    host = os.environ.get("HOST", "127.0.0.1")
    print("Account Research -> http://{}:{}".format(host, port))
    app.run(host=host, port=port, debug=False, threaded=True)
