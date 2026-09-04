"use strict";

const $ = (id) => document.getElementById(id);
const runBtn = $("run"), errorEl = $("error");
let busy = false;          // guards against duplicate submissions
let current = null;        // { runs, sources, search_queries, ... }
let activeTab = 0;

// ---------------------------------------------------------------- utilities
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/** Minimal Markdown -> HTML. Enough for headings, lists, tables, bold and code. */
function md(src) {
  const lines = String(src || "").split("\n");
  let out = "", inUl = false, inTable = false;
  const inline = (t) => esc(t)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[(\d+)\]/g, '<span class="pill">$1</span>')
    // Canonical confidence tags (server-normalised); the raw variants are kept as
    // fallbacks so historical records still render with the right colour.
    .replace(/(Not enough evidence \/ 证据不足|Not enough evidence|证据不足)/gi, '<span class="tagn">$1</span>')
    .replace(/(Likely \/ 可能|Likely \/ partially supported|Likely|部分支持)/g, '<span class="tagl">$1</span>')
    .replace(/(Verified \/ 已验证|Verified|已验证)/g, '<span class="tagv">$1</span>')
    .replace(/(https?:\/\/[^\s<)]+)/g, '<a href="$1" target="_blank" rel="noopener">$1</a>');
  const closeUl = () => { if (inUl) { out += "</ul>"; inUl = false; } };
  const closeTable = () => { if (inTable) { out += "</tbody></table>"; inTable = false; } };

  for (let raw of lines) {
    const line = raw.trimEnd();
    if (/^\s*\|.*\|\s*$/.test(line)) {
      const cells = line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      if (/^[\s:|-]+$/.test(line.replace(/\|/g, ""))) continue;   // separator row
      if (!inTable) {
        closeUl();
        out += "<table><thead><tr>" + cells.map((c) => `<th>${inline(c)}</th>`).join("") + "</tr></thead><tbody>";
        inTable = true;
      } else {
        out += "<tr>" + cells.map((c) => `<td>${inline(c)}</td>`).join("") + "</tr>";
      }
      continue;
    }
    closeTable();
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) { closeUl(); out += `<h${Math.min(h[1].length + 1, 4)}>${inline(h[2])}</h${Math.min(h[1].length + 1, 4)}>`; continue; }
    const li = line.match(/^\s*[-*+]\s+(.*)$/) || line.match(/^\s*\d+\.\s+(.*)$/);
    if (li) { if (!inUl) { out += "<ul>"; inUl = true; } out += `<li>${inline(li[1])}</li>`; continue; }
    if (!line.trim()) { closeUl(); continue; }
    closeUl();
    out += `<p>${inline(line)}</p>`;
  }
  closeUl(); closeTable();
  return out;
}

// ---------------------------------------------------------------- rendering
function renderSources(sources) {
  const el = $("sources");
  if (!sources || !sources.length) { el.innerHTML = '<li class="empty">No sources.</li>'; return; }
  el.innerHTML = sources.map((s) => `
    <li>
      <span class="t">[${s.id}] ${esc(s.title || "(untitled)")}</span>
      <span class="pill${s.official ? " off" : ""}">${esc(s.source_type || "source")}</span>
      <span class="d">${esc(s.domain || "")}</span><br>
      <a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.url)}</a>
    </li>`).join("");
}

function renderQueries(queries) {
  $("queries").innerHTML = (queries && queries.length)
    ? queries.map((q, i) => `<li>${i + 1}. ${esc(q)}</li>`).join("")
    : '<li class="empty">—</li>';
}

function metaBlock(run, data) {
  const fin = run.financial_sources || data.financial_sources || {};
  const ap = run.apollo_usage || data.apollo_usage || {};
  const rows = [
    ["Model", run.model_label || run.model],
    ["Protocol", run.protocol || "DashScope Native"],
    ["Endpoint", run.endpoint],
    ["Search enabled", "true (turbo)"],
    ["Search queries", (data.search_queries || []).length],
    ["Sources", (data.sources || []).length],
    ["Input tokens", run.token_usage?.input ?? "—"],
    ["Output tokens", run.token_usage?.output ?? "—"],
    ["Total tokens", run.token_usage?.total ?? "—"],
    ["Latency", (run.latency_seconds ?? "—") + " s"],
    ["Timestamp", run.timestamp ? new Date(run.timestamp).toLocaleString() : "—"],
  ];
  // Financial sourcing. "Yahoo Finance used" reports whether a Yahoo Finance URL
  // actually landed in the evidence, not whether a query mentioned it.
  if (Object.keys(fin).length) {
    rows.push(["Company listing", fin.public_company
      ? `Public · ${fin.ticker}${fin.listed_name ? " · " + fin.listed_name : ""}`
      : (fin.listing_status || "private/unlisted").replace(/_/g, " ")]);
    rows.push(["Yahoo Finance used", fin.yahoo_finance_used ? "Yes" : "No"]);
    if (!fin.yahoo_finance_used && !fin.public_company) {
      rows.push(["Yahoo Finance note", "Not applicable — private/unlisted company"]);
    }
  }
  // Apollo is counted as API calls, never as LLM tokens.
  if (Object.keys(ap).length) {
    rows.push(["Apollo enrichment", ap.configured
      ? (ap.status || "—").replace(/_/g, " ") : "not configured"]);
    if (ap.configured) {
      rows.push(["Apollo API calls", ap.calls ?? 0]);
      rows.push(["Apollo people returned", ap.people_returned ?? 0]);
      rows.push(["Apollo people retained", ap.people_retained ?? 0]);
      const credits = Object.entries(ap.credits || {});
      if (credits.length) {
        rows.push(["Apollo usage headers", credits.map(([k, v]) => `${k}: ${v}`).join(" · ")]);
      }
      if ((ap.errors || []).length) rows.push(["Apollo notes", ap.errors.join("; ")]);
    }
  }
  let html = `<h3>Research Metadata</h3><dl>${rows
    .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("")}</dl>`;
  const yurls = fin.yahoo_finance_urls || [];
  if (yurls.length) {
    html += `<p class="finurl">Yahoo Finance source: ${yurls
      .map((u) => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(u)}</a>`).join(", ")}</p>`;
  }
  return html;
}

function comparisonTable(data) {
  const head = "<tr><th>Model</th><th>Sources</th><th>Input</th><th>Output</th><th>Total</th><th>Time</th></tr>";
  const body = data.runs.map((r) => `<tr>
      <td>${esc(r.model_label)}</td>
      <td>${(data.sources || []).length}</td>
      <td>${r.token_usage?.input ?? "—"}</td>
      <td>${r.token_usage?.output ?? "—"}</td>
      <td>${r.token_usage?.total ?? "—"}</td>
      <td>${r.latency_seconds ?? "—"} s</td></tr>`).join("");
  return `<h2>Comparison / 模型对比</h2><table><thead>${head}</thead><tbody>${body}</tbody></table>
          <p class="empty">Same evidence set for every model. Review the outputs yourself — no winner is chosen automatically.</p>`;
}


// ------------------------------------------------- progressive rendering
let job = null;          // latest job snapshot
let historyView = null;  // a loaded past result, when viewing history

function statusLabel(m) {
  if (m.status === "complete") {
    const via = m.fallback_used && m.model_used ? ` · via ${m.model_used}` : "";
    return `Complete / 已完成 · ${m.elapsed}s${via}`;
  }
  if (m.status === "access_denied") return "Unavailable / 未开通 · requires activation";
  if (m.status === "generating") return `Generating / 生成中 · ${m.elapsed ?? 0}s`;
  if (m.status === "timeout") return `Timed out / 超时 · ${m.elapsed ?? "—"}s`;
  if (m.status === "failed") return "Failed / 失败";
  return "Pending / 待处理";
}

function renderProgress(j) {
  $("progress").hidden = false;
  $("stages").innerHTML = j.stages.map((s) =>
    `<li><span class="tick">✓</span>${esc(s.message)}<span class="at">${s.at}s</span></li>`).join("");
  $("modelstatus").innerHTML = j.model_order.map((id) => {
    const m = j.models[id];
    const tried = (m.models_tried || []).length
      ? `<span class="tried">unavailable: ${esc(m.models_tried.join(", "))}</span>` : "";
    return `<li><span class="name">${esc(m.label)}</span>
            <span class="st-${m.status}">${esc(statusLabel(m))}</span>${tried}</li>`;
  }).join("");
}

function metaCard(run, sourceCount) {
  const t = run.token_usage || {};
  const cells = [
    ["Status / 状态", "Complete / 已完成"], ["Sources / 来源", sourceCount],
    ["Input tokens / 输入", t.input ?? "—"], ["Output tokens / 输出", t.output ?? "—"],
    ["Total tokens / 合计", t.total ?? "—"], ["Latency / 耗时", (run.latency_seconds ?? "—") + " s"],
  ];
  const head = `<div class="cardhead">${cells
    .map(([k, v]) => `<div><b>${esc(k)}</b>${esc(v)}</div>`).join("")}</div>`;
  // The PDF is written the moment the model finishes, so offer it right here.
  if (!run.pdf || !run.pdf_dir) return head;
  const url = languagePdfUrl(run.company || "", run.model);
  const title = `${run.company || ""} — ${run.model_label || run.model || "report"}`;
  return head + `<div class="rowacts pdfacts">
      <a href="${url}" data-pdf="${esc(url)}" data-pdftitle="${esc(title)}">View PDF / 查看PDF</a>
      <a href="${url}">Download PDF / 下载PDF</a>
    </div>`;
}

function retrievalTimings(j) {
  const t = j.retrieval_timings || {};
  if (!Object.keys(t).length) return "";
  const order = [["query_generation", "Query generation"], ["official_site", "Official site"],
                 ["web_retrieval", "Web retrieval"], ["source_processing", "Source processing"],
                 ["evidence_build", "Evidence build"], ["retrieval_total", "Retrieval total"]];
  const parts = order.filter(([k]) => t[k] !== undefined)
    .map(([k, l]) => `<span>${l}: ${t[k]}s</span>`).join("");
  const models = j.model_order.filter((id) => j.models[id].status === "complete")
    .map((id) => `<span>${j.models[id].label} synthesis: ${j.models[id].elapsed}s</span>`).join("");
  const wall = j.wall_seconds ? `<span><strong>Total wall-clock: ${j.wall_seconds}s</strong></span>` : "";
  return `<div class="timings">${parts}${models}${wall}</div>`;
}

function comparisonTable(j) {
  const rows = j.model_order.map((id) => {
    const m = j.models[id];
    const t = m.token_usage || {};
    const done = m.status === "complete";
    return `<tr>
      <td>${esc(m.label)}</td>
      <td class="st-${m.status}">${esc(statusLabel(m).split(" · ")[0])}</td>
      <td>${done ? (j.sources || []).length : "—"}</td>
      <td>${done ? (t.input ?? "—") : "—"}</td>
      <td>${done ? (t.output ?? "—") : "—"}</td>
      <td>${done ? (t.total ?? "—") : "—"}</td>
      <td>${m.elapsed != null ? m.elapsed + "s" : "—"}</td></tr>`;
  }).join("");
  return `<h2>Comparison</h2>
    <table><thead><tr><th>Model / 模型</th><th>Status / 状态</th><th>Sources / 来源</th><th>Input / 输入</th>
    <th>Output / 输出</th><th>Total / 合计</th><th>Time / 耗时</th></tr></thead><tbody>${rows}</tbody></table>
    <p class="empty">Same evidence package for every model — retrieval ran once. 所有模型共用同一证据集，检索仅运行一次。
       Review the outputs yourself; no winner is chosen automatically.</p>
    ${retrievalTimings(j)}`;
}

function render() {
  if (historyView) return renderHistory(historyView);
  if (!job) return;
  const j = job;
  $("results").hidden = false;
  renderSources(j.sources);
  renderQueries(j.search_queries);
  renderProgress(j);

  if (j.status === "needs_review") {
    $("tabs").innerHTML = "";
    $("report").innerHTML = needsReviewHtml(j);
    $("meta").innerHTML = "";
    wireNeedsReview();
    return;
  }

  const multi = j.model_order.length > 1;
  const labels = j.model_order.map((id) => {
    const m = j.models[id];
    return `<span class="dot ${m.status}"></span>${esc(m.label)}`;
  });
  if (multi) labels.push("Comparison / 对比");
  $("tabs").innerHTML = labels
    .map((l, i) => `<button class="tab${i === activeTab ? " active" : ""}" data-i="${i}">${l}</button>`)
    .join("");
  $("tabs").querySelectorAll(".tab").forEach((b) =>
    b.addEventListener("click", () => { activeTab = +b.dataset.i; render(); }));

  if (multi && activeTab === j.model_order.length) {
    $("report").innerHTML = comparisonTable(j);
    $("meta").innerHTML = "";
    return;
  }
  const m = j.models[j.model_order[Math.min(activeTab, j.model_order.length - 1)]];
  if (m.status === "complete" && m.result) {
    $("report").innerHTML = metaCard(m.result, (j.sources || []).length)
      + withRoster(md(selectLanguage(
                     m.result.research_result_display || m.result.research_result, getLang())),
                   m.result.decision_makers, m.result.people_summary, m.result.company);
    $("meta").innerHTML = qualityBlock(j.quality) + metaBlock(m.result, j) + retrievalTimings(j);
  } else if (m.status === "access_denied") {
    // Not a retrieval failure and not an application error: the account simply
    // does not have this model activated. Say that in plain language.
    const others = (m.models_tried || []).filter((x) => x !== m.label);
    $("report").innerHTML = `<div class="incomplete">
        <h2>${esc(m.label)} — Unavailable / 未开通<br><span class="cn">该模型需付费开通</span></h2>
        <p>${esc(ACTIVATION_MSG)}<br><span class="cn">${esc(ACTIVATION_MSG_CN)}</span></p>
        ${others.length ? `<p class="rowmsg">Also unavailable: ${esc(others.join(", "))}</p>` : ""}
        <p class="rowmsg">Research evidence was collected normally — this only affects report generation.
           <span class="cn">证据检索正常完成，仅影响报告生成；已保存的报告与PDF不受影响。</span></p>
      </div>`;
    $("meta").innerHTML = qualityBlock(j.quality);
  } else if (m.status === "failed" || m.status === "timeout") {
    $("report").innerHTML = `<p class="error">${esc(m.label)}: ${esc(m.status)} — ${esc(m.error || "no response")}</p>`;
    $("meta").innerHTML = "";
  } else {
    $("report").innerHTML = `<p class="waiting">${esc(m.label)} — ${esc(statusLabel(m))}. Other finished models are available in their tabs.</p>`;
    $("meta").innerHTML = "";
  }
}

function renderHistory(d) {
  $("results").hidden = false;
  renderSources(d.sources);
  renderQueries(d.search_queries);
  $("progress").hidden = true;
  $("tabs").innerHTML = "";
  $("report").innerHTML = metaCard(d, (d.sources || []).length)
    + withRoster(md(selectLanguage(
                   d.research_result_display || d.research_result, getLang())),
                 d.decision_makers, d.people_summary, d.company);
  $("meta").innerHTML = qualityBlock(d.quality) + metaBlock(d, d);
}


// ------------------------------------------------- decision makers
const BADGE_CLASS = { Apollo: "bg-apollo", Official: "bg-official", Web: "bg-web" };

const EMAIL_CLASS = (st) => st.startsWith("Verified") ? "em-ok"
  : st.startsWith("Unverified") || st === "Pending" ? "em-warn" : "em-none";

/** Contact cell: the address only if Apollo actually returned one. */
function contactCell(p) {
  const status = p.email_status || "Not available";
  const bits = [];
  if (p.email) {
    bits.push(`<a href="mailto:${esc(p.email)}">${esc(p.email)}</a>`);
  } else {
    bits.push('<span class="muted">No email / 无邮箱</span>');
  }
  bits.push(`<span class="embadge ${EMAIL_CLASS(status)}">${esc(status)}</span>`);
  if (p.linkedin_url) {
    bits.push(`<a class="slink" href="${esc(p.linkedin_url)}" target="_blank" rel="noopener">LinkedIn</a>`);
  }
  return bits.join("<br>");
}

/** Key Contacts & Decision Makers, built from structured data.
 *  Rendered from the Apollo/web merge rather than from model output: an email
 *  address is the one field that must never be produced by a language model. */
function decisionMakersHtml(people, summary, company) {
  if (!people || !people.length) return "";
  const rows = people.map((p) => `<tr>
      <td>${esc(p.name)}</td>
      <td>${esc(p.title || "—")}</td>
      <td>${esc(p.department || "—")}</td>
      <td>${esc(p.seniority || "—")}</td>
      <td>${esc(p.location || "—")}</td>
      <td class="contactcell">${contactCell(p)}</td>
      <td>${esc(p.why || "—")}</td>
      <td class="srccell">${(p.sources || []).map((sname) =>
        `<span class="sbadge ${BADGE_CLASS[sname] || "bg-web"}">${esc(sname)}</span>`).join("")}
      </td></tr>`).join("");
  const s = summary || {};
  const note = `<p class="rostnote">${esc(company || "")}${company ? " · " : ""}${s.total ?? people.length} contacts ·
      ${s.with_email ?? 0} with a business email (${s.verified_email ?? 0} verified) ·
      ${s.merged ?? 0} confirmed by more than one source ·
      ${s.apollo_only ?? 0} Apollo only · ${s.web_only ?? 0} web only${
      (s.departments || []).length ? ` · departments: ${esc((s.departments || []).join(", "))}` : ""}</p>`;
  return `<div class="roster">
      <h3>Key Contacts &amp; Decision Makers / 关键联系人与决策者</h3>
      ${note}
      <table class="contacts">
      <colgroup><col style="width:11%"><col style="width:13%"><col style="width:12%">
        <col style="width:8%"><col style="width:11%"><col style="width:16%">
        <col style="width:23%"><col style="width:6%"></colgroup>
      <thead><tr><th>Name / 姓名</th><th>Title / 职务</th><th>Department / 部门</th><th>Seniority / 级别</th>
      <th>Location / 地点</th><th>Contact / 联系方式</th><th>Why Relevant to SKEQI / 相关性</th><th>Source / 来源</th></tr></thead>
      <tbody>${rows}</tbody></table>
      <p class="rostnote">Emails come from Apollo enrichment only and are never inferred.
         <span class="cn">邮箱仅来自 Apollo 补充，不做推断；留空表示 Apollo 无该联系人的商务邮箱。</span></p>
    </div>`;
}

/** Place the roster directly under the model's own Key Decision Makers heading. */
function withRoster(html, people, summary, company) {
  const block = decisionMakersHtml(people, summary, company);
  if (!block) return html;
  const re = /<h2>[^<]*Key Decision Makers[^<]*<\/h2>/i;
  return re.test(html) ? html.replace(re, (m) => m + block) : html + block;
}


// ------------------------------------------------- retrieval quality
const LEVEL_CLASS = { strong: "lv-strong", acceptable: "lv-ok", weak: "lv-weak",
                      insufficient: "lv-bad" };

/** What retrieval actually did, wave by wave. */
function qualityBlock(q) {
  if (!q || !Object.keys(q).length) return "";
  const waves = (q.retrieval_waves || []).map((w) => `<li>${esc(w.model_label)}${
    w.skipped ? ' <span class="muted">not needed / 未使用</span>'
              : ` — ${w.raw_results} raw, ${w.unique_candidates} usable candidates`}</li>`).join("");
  const denied = (q.models_access_denied || []).length
    ? `<p class="qwarn">Requires activation / 需开通: ${esc(q.models_access_denied.join(", "))}${
        !q.web_search_sources
          ? " — web search returned no sources, so evidence came from the official website only." : ""}</p>`
    : "";
  const checks = [
    ["Company identity / 公司身份", q.identity_verified],
    ["Official source / 官方来源", q.official_evidence],
    ["Company overview / 公司概况", q.overview_evidence],
    ["Products & services / 产品与服务", q.products_evidence],
  ].map(([k, ok]) => `<li>${ok ? "✓" : "✗"} ${esc(k)}</li>`).join("");
  return `<div class="quality">
      <h3>Retrieval / 检索质量 <span class="lvl ${LEVEL_CLASS[q.level] || ""}">${esc(q.level || "—")}</span></h3>
      <p class="qline">Official website / 官网: ${q.direct_site_sources ?? 0} ·
         web / 网络: ${q.web_sources ?? 0} · <strong>${q.sources ?? 0} unique sources retained / 保留来源</strong></p>
      <ul class="waves">${waves}</ul>
      <ul class="checks">${checks}</ul>
      ${denied}
      ${(q.reasons || []).length ? `<p class="qwarn">${esc(q.reasons.join("; "))}</p>` : ""}
    </div>`;
}

/** Hard guard: nothing company-specific was retrieved. The user chooses next. */
function needsReviewHtml(j) {
  const q = j.quality || {};
  return `<div class="incomplete">
      <h2>Research retrieval incomplete<br><span class="cn">研究检索不完整</span></h2>
      <p>No company-specific source could be retrieved for
         <strong>${esc(j.company)}</strong>, so no report was generated and no
         tokens were spent.</p>
      ${(q.reasons || []).length ? `<ul>${q.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
      ${qualityBlock(q)}
      <div class="incactions">
        <button id="retryexpanded" class="primary">Retry Expanded Search / 扩大检索重试</button>
        <button id="editurl" class="mini ghost">Edit URL / 修改官网</button>
        ${q.can_force === false ? "" :
          '<button id="genanyway" class="mini ghost">Generate Anyway / 仍然生成</button>'}
      </div>
      <div id="editurlbox" class="editurlbox" hidden>
        <input id="newurl" type="url" placeholder="https://company.com" value="${esc(j.website || "")}">
        <button id="saveurl" class="mini">Save &amp; Retry / 保存并重试</button>
      </div>
    </div>`;
}

function wireNeedsReview() {
  const on = (id, fn) => { const el = $(id); if (el) el.addEventListener("click", fn); };
  on("retryexpanded", () => start(false));
  on("genanyway", () => start(false, true));
  on("editurl", () => { $("editurlbox").hidden = !$("editurlbox").hidden; });
  on("saveurl", () => {
    const v = ($("newurl").value || "").trim();
    if (v) { $("website").value = v; start(false); }
  });
}




// ------------------------------------------------- models & system status
// Model entitlement is an account/billing state, not an application error. The
// picker says so plainly and the diagnostic detail lives behind System Status
// rather than in the page header.
let APP_MODELS = [];
let MODEL_HEALTH = {};                // model id -> "available" | "access_denied" | "unknown"

const ACTIVATION_MSG =
  "This model is not currently activated for this account. " +
  "Please choose another available model, or enable billing / model access.";
const ACTIVATION_MSG_CN =
  "该模型尚未为此账户开通。请选择其他可用模型，或开通计费 / 模型权限。";

function modelLabel(m) {
  return MODEL_HEALTH[m.id] === "access_denied"
    ? `${m.label} — Unavailable / 未开通（需付费开通）` : m.label;
}

function renderModelOptions() {
  const keep = { model: $("model").value, batch: $("batchmodel") ? $("batchmodel").value : "" };
  const opts = APP_MODELS.map((m) =>
    `<option value="${esc(m.id)}"${MODEL_HEALTH[m.id] === "access_denied" ? " disabled" : ""}>${esc(modelLabel(m))}</option>`).join("");
  const all = '<option value="all">Run All Three / 运行全部三个模型</option>';
  $("model").innerHTML = opts + all;
  if (keep.model) $("model").value = keep.model;
  const bm = $("batchmodel");
  if (bm) {
    bm.innerHTML = opts + all;
    if (keep.batch) bm.value = keep.batch;
  }
}

async function refreshModelHealth(probe) {
  const d = await fetch(`/api/models/health${probe ? "?probe=1" : ""}`)
    .then((r) => r.json()).catch(() => null);
  if (!d || !d.models) return null;
  MODEL_HEALTH = {};
  d.models.forEach((m) => { MODEL_HEALTH[m.model] = m.state; });
  renderModelOptions();
  renderStatusCards();
  return d;
}

function toggleSystemStatus(e) {
  e.stopPropagation();
  const pop = $("statuspop");
  if (!pop.hidden) { pop.hidden = true; return; }
  pop.hidden = false;
  pop.innerHTML = '<h3>System Status / 系统状态</h3><p class="foot">Checking… 检测中…</p>';
  refreshModelHealth(true).then((d) => {
    if (!d) { pop.innerHTML = '<h3>System Status / 系统状态</h3><p class="foot">Status unavailable. 状态不可用。</p>'; return; }
    const rows = d.models.map((m) => {
      const label = m.state === "available" ? "Available / 可用"
        : m.state === "access_denied" ? "Unavailable / 未开通" : "Unknown / 未知";
      const cls = m.state === "available" ? "s-Completed"
        : m.state === "access_denied" ? "s-Failed" : "s-Pending";
      return `<tr><td class="k">${esc(m.label)}</td>
              <td><span class="badge-st ${cls}">${esc(label)}</span></td></tr>`;
    }).join("");
    pop.innerHTML = `<h3>System Status / 系统状态</h3>
      <table><tbody>
        <tr><td class="k">Provider / 服务商</td><td>${esc(d.provider || "—")}</td></tr>
        <tr><td class="k">Workspace / 工作空间</td><td>${esc(d.workspace || "—")}</td></tr>
        <tr><td class="k">Retrieval / 检索</td><td>Web search · evidence-first / 联网检索 · 证据优先</td></tr>
        ${rows}
      </tbody></table>
      ${d.any_available ? "" : `<p class="foot">${esc(ACTIVATION_MSG)}</p>`}`;
  });
}

// ------------------------------------------------- workspace status cards
// Model / Connection, Saved Reports, Last Generated. Every value comes from a
// response the page already has; nothing here triggers a request of its own.
function renderStatusCards() {
  const m = $("model") ? $("model").value : "";
  const chosen = APP_MODELS.find((x) => x.id === m);
  const card = (id) => document.querySelector(`#${id}`) && document.querySelector(`#${id}`).closest(".statcard");

  const mv = $("sc-model"), mn = $("sc-modelnote"), mc = card("sc-model");
  if (mv) {
    const state = m === "all" ? null : MODEL_HEALTH[m];
    mv.textContent = m === "all" ? "All three models / 全部三个模型"
                                 : (chosen ? chosen.label : "—");
    if (mc) mc.classList.remove("is-ok", "is-err");
    if (m === "all") {
      const any = APP_MODELS.some((x) => MODEL_HEALTH[x.id] === "available");
      mn.textContent = any ? "DashScope · at least one model ready / 至少一个模型可用"
                           : "DashScope · activation required / 需开通";
      if (mc) mc.classList.add(any ? "is-ok" : "is-err");
    } else if (state === "available") {
      mn.textContent = "DashScope · connected / 已连接";
      if (mc) mc.classList.add("is-ok");
    } else if (state === "access_denied") {
      mn.textContent = "Activation required / 需开通付费";
      if (mc) mc.classList.add("is-err");
    } else {
      mn.textContent = "DashScope · status unknown / 状态未知";
    }
  }

  const rv = $("sc-reports"), rn = $("sc-reportsnote");
  if (rv) {
    rv.textContent = String(REPORT_LIBRARY.length);
    rn.textContent = REPORT_LIBRARY.length
      ? "In the report library / 报告库中" : "No reports yet / 暂无报告";
  }

  const lv = $("sc-last"), ln = $("sc-lastnote");
  if (lv) {
    // completed_pdfs() returns newest first; take the first dated entry.
    const latest = REPORT_LIBRARY.find((r) => reportDate(r.file)) || REPORT_LIBRARY[0];
    if (latest) {
      lv.textContent = reportDate(latest.file) || "—";
      ln.textContent = latest.company;
    } else {
      lv.textContent = "—";
      ln.textContent = "No reports yet / 暂无报告";
    }
  }
}

// ------------------------------------------------- display language
// Presentation only. Research is generated and stored ONCE, bilingual; this
// chooses what to show and which PDF variant to fetch. No AI call is made.
// Mirrors language_view.select() on the server - keep the two in step.
const LANG_KEY = "accountResearchDisplayLanguage";
const LANGS = ["en", "zh", "bilingual"];
const CJK = /[\u4e00-\u9fff]/;
const TAG_PAIRS = [
  ["Verified / 已验证", "Verified", "已验证"],
  ["Likely / 可能", "Likely", "可能"],
  ["Not enough evidence / 证据不足", "Not enough evidence", "证据不足"],
];

function getLang() {
  try {
    const v = localStorage.getItem(LANG_KEY);
    if (LANGS.includes(v)) return v;
  } catch (e) { /* private mode: fall through to the default */ }
  return "bilingual";
}

function setLang(v) {
  if (!LANGS.includes(v)) return;
  try { localStorage.setItem(LANG_KEY, v); } catch (e) { /* not fatal */ }
}

/** "Company Overview / 公司概况" -> the side matching the language. */
function splitHeading(text, lang) {
  if (lang === "bilingual" || !text.includes(" / ")) return text;
  const parts = text.split(" / ").map((p) => p.trim());
  const cjk = parts.filter((p) => CJK.test(p));
  const latin = parts.filter((p) => !CJK.test(p));
  if (!cjk.length || !latin.length) return text;
  return (lang === "zh" ? cjk : latin).join(" / ");
}

/** Select one language out of the stored bilingual markdown. */
function selectLanguage(markdown, lang) {
  if (lang === "bilingual" || !markdown) return markdown || "";
  const out = [];
  let block = null;                       // null = shared, "en" or "zh"
  for (const raw of String(markdown).split("\n")) {
    const line = raw.replace(/\s+$/, "");
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) { block = null; out.push(`${h[1]} ${splitHeading(h[2].trim(), lang)}`); continue; }
    const t = line.trim();
    if (/^\*\*English[:：]?\*\*[:：]?$/i.test(t)) { block = "en"; continue; }
    if (/^\*\*中文[:：]?\*\*[:：]?$/.test(t)) { block = "zh"; continue; }
    if (block !== null && block !== lang) continue;
    let kept = line;
    for (const [both, en, zh] of TAG_PAIRS) kept = kept.split(both).join(lang === "en" ? en : zh);
    out.push(kept);
  }
  const cleaned = [];
  let blanks = 0;
  for (const line of out) {
    if (line.trim()) { blanks = 0; cleaned.push(line); }
    else if (++blanks < 2) cleaned.push(line);
  }
  return cleaned.join("\n").trim();
}

/** Every segmented language control on the page shows the one shared choice. */
function renderLangButtons() {
  const lang = getLang();
  document.querySelectorAll(".langbtn").forEach((b) =>
    b.classList.toggle("active", b.dataset.lang === lang));
}

// ------------------------------------------------- embedded PDF viewer
// Browser-native rendering in an <iframe>. No PDF library: the built-in viewer
// already gives scrolling, zoom and page navigation, and stays lightweight.
function reportPdfUrl(dir, file, inline) {
  return `/api/report/${encodeURIComponent(dir)}/${encodeURIComponent(file)}${inline ? "?inline=1" : ""}`;
}

/** PDF for a company in the CURRENT display language, rendered on demand from
 *  the already-saved bilingual research. Never triggers research. */
function languagePdfUrl(company, model) {
  const q = new URLSearchParams({ company, lang: getLang() });
  if (model) q.set("model", model);
  return `/api/report-pdf?${q}`;
}

function openPdf(url, title) {
  const panel = $("pdfpanel");
  const inlineUrl = url.includes("?") ? `${url}&inline=1` : `${url}?inline=1`;
  const dlUrl = url.replace(/[?&]inline=1/, "");
  $("pdftitle").textContent = title || "Report PDF";
  renderLangButtons();                 // the viewer has its own segmented control
  $("pdfnewtab").href = inlineUrl;
  $("pdfdownload").href = dlUrl;
  $("pdffallbacklink").href = inlineUrl;
  $("pdffallback").hidden = true;
  $("pdfframe").src = inlineUrl;
  panel.hidden = false;
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function closePdf() {
  $("pdfpanel").hidden = true;
  $("pdfframe").src = "about:blank";      // stop rendering, release the file
}

/** Any element carrying data-pdf opens in the viewer instead of downloading. */
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-pdf]");
  if (!el) return;
  e.preventDefault();
  openPdf(el.dataset.pdf, el.dataset.pdftitle || "Report PDF");
});

// ------------------------------------------------- history
async function loadHistory() {
  const items = await fetch("/api/history").then((r) => r.json()).catch(() => []);
  $("history").innerHTML = items.length
    ? items.map((h) => `<li>
        <button data-file="${esc(h.file)}">${esc(h.company)} — ${esc(h.model_label)}</button>
        <div class="when">${new Date(h.timestamp).toLocaleString()} · ${h.total_tokens ?? "—"} tokens
          <button class="del" data-delco="${esc(h.company)}">Delete Report / 删除报告</button></div>
      </li>`).join("")
    : '<li class="empty">Nothing saved yet.</li>';
  $("history").querySelectorAll("button[data-file]").forEach((b) =>
    b.addEventListener("click", () => openHistory(b.dataset.file)));
  $("history").querySelectorAll("button[data-delco]").forEach((b) =>
    b.addEventListener("click", () => {
      const company = b.dataset.delco;
      const line = b.parentElement;
      if (line.__armed) {
        deleteReports([company]).then((n) => { if (n) toast("✓ Report deleted / 报告已删除"); });
        return;
      }
      line.__armed = true;
      b.textContent = "Confirm delete? / 确认删除？";
      b.classList.add("armed");
      setTimeout(() => {                       // disarm if left untouched
        line.__armed = false;
        b.textContent = "Delete Report / 删除报告";
        b.classList.remove("armed");
      }, 6000);
    }));
}

async function openHistory(file) {
  const d = await fetch(`/api/history/${encodeURIComponent(file)}`).then((r) => r.json());
  historyView = d; job = null; activeTab = 0;
  render();
  errorEl.hidden = true;
  window.scrollTo({ top: 0, behavior: "smooth" });
}

// ------------------------------------------------- run
async function start(useCache, force) {
  if (busy) return;
  const company = $("company").value.trim();
  if (!company) { errorEl.textContent = "Company name is required. / 请输入公司名称。"; errorEl.hidden = false; return; }

  busy = true; historyView = null; activeTab = 0;
  runBtn.disabled = true;
  runBtn.textContent = "Researching… 研究中…";
  errorEl.hidden = true;
  $("cachebar").hidden = true;

  try {
    const start = await fetch("/api/research", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ company, website: $("website").value.trim(),
                             model: $("model").value, use_cache: !!useCache,
                             force: !!force }),
    }).then((r) => r.json());
    if (start.error) throw new Error(start.error);

    // Poll and re-render continuously so each model appears the moment it lands.
    while (true) {
      const snap = await fetch(`/api/job/${start.job_id}`).then((r) => r.json());
      job = snap;
      render();
      if (snap.status === "error") throw new Error(snap.message);
      if (snap.status !== "running") break;      // "needs_review" renders its own panel
      await new Promise((r) => setTimeout(r, 900));
    }
    loadHistory();
  } catch (e) {
    errorEl.textContent = e.message || String(e);
    errorEl.hidden = false;
  } finally {
    busy = false;
    runBtn.disabled = false;
    runBtn.textContent = "Generate Research / 生成研究报告";
  }
}

async function onRunClick() {
  if (busy) return;
  const company = $("company").value.trim();
  const website = $("website").value.trim();
  const q = new URLSearchParams({ company, website });
  const c = await fetch(`/api/cache-status?${q}`).then((r) => r.json()).catch(() => ({ cached: false }));
  if (c.cached) {
    const mins = Math.round((c.age_seconds || 0) / 60);
    $("cachetext").textContent =
      `Existing web research found / 已有检索结果 — ${c.sources} sources, ${mins < 1 ? "just now / 刚刚" : mins + " min old / 分钟前"}.`;
    $("cachebar").hidden = false;
    return;                       // wait for the user to choose
  }
  start(false);
}

// ------------------------------------------------- init
/** The tab bar sticks under the app bar, whose height depends on the language
 *  picker and the viewport. Measure it instead of hard-coding 59px. */
function syncAppbarHeight() {
  const bar = document.querySelector(".appbar");
  if (bar) document.documentElement.style.setProperty("--appbar-h", `${Math.round(bar.getBoundingClientRect().height)}px`);
}

(async function init() {
  renderLangButtons();
  syncAppbarHeight();
  window.addEventListener("resize", syncAppbarHeight);
  document.addEventListener("click", (e) => {
    const b = e.target.closest(".langbtn");
    if (!b) return;
    setLang(b.dataset.lang);
    renderLangButtons();
    render();                         // re-render from data already in the page
    loadDoneList();                   // library links carry the language
    if (!$("pdfpanel").hidden) closePdf();     // stale-language PDF
  });
  $("pdfclose").addEventListener("click", closePdf);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("pdfpanel").hidden) closePdf();
  });
  // Some browsers refuse to render PDFs inline; offer the tab instead of a blank box.
  $("pdfframe").addEventListener("error", () => { $("pdffallback").hidden = false; });
  const cfg = await fetch("/api/config").then((r) => r.json()).catch(() => ({ models: [] }));
  APP_MODELS = cfg.models || [];
  renderModelOptions();
  renderStatusCards();
  // Ask for cached state first, then probe once so the picker can label models
  // that need activation. The probe result is remembered server-side.
  refreshModelHealth().then((d) => {
    if (d && d.models.every((m) => m.state === "unknown")) refreshModelHealth(true);
  });
  $("sysstatus").addEventListener("click", toggleSystemStatus);
  document.addEventListener("click", (e) => {
    if (!e.target.closest("#sysstatus, #statuspop")) $("statuspop").hidden = true;
    document.querySelectorAll(".acts details[open]").forEach((d) => {
      if (!d.contains(e.target)) d.open = false;
    });
  });
  const lib = $("libsearch");
  if (lib) lib.addEventListener("input", renderLibrary);
  initLibrary();
  loadDoneList();                       // status cards need the library on load
  $("model").addEventListener("change", renderStatusCards);
  runBtn.addEventListener("click", onRunClick);
  $("usecache").addEventListener("click", () => start(true));
  $("refresh").addEventListener("click", () => start(false));
  loadHistory();
})();

// ==========================================================================
// Batch Research / 批量研究
// ==========================================================================
let batchItems = [];      // [{company, website, status, ...}]
let batchHeaders = [];
let batchRows = [];
let batchId = null;
let batchPolling = false;

function fmt(n) { return n == null ? "—" : Number(n).toLocaleString(); }

/* Rows are keyed by company and updated in place.

   The previous version assigned tbody.innerHTML on every poll, rebuilding every
   row 1.5s apart: a focused input lost its caret, typed text was replaced by
   server state, and ordinary text selection was cleared. Nothing here replaces
   a node unless its rendered content actually changed, and a row the user is
   editing is not touched at all. */
const rowEls = new Map();          // company -> <tr>
const CELLS = 9;

function setHTML(el, html) {
  if (el.__h !== html) { el.__h = html; el.innerHTML = html; }
}

function idx(company) { return batchItems.findIndex((x) => x.company === company); }

function renderBatchSelCount() {
  const el = $("batchselcount");
  if (!el) return;
  const n = batchItems.filter((i) => i.selected).length;
  el.textContent = n ? `${n} of ${batchItems.length} selected / 已选 ${n} 家` : "";
}

function renderBatchTable() {
  const tb = document.querySelector("#batchtable tbody");
  if (!batchItems.length) {
    rowEls.clear();
    tb.innerHTML = '<tr><td colspan="9" class="empty">Upload a company list to begin.</td></tr>';
    tb.__empty = true;
    renderBatchSelCount();
    return;
  }
  // The template ships a placeholder row; __empty is unset on the very first
  // render, so detect the placeholder itself rather than trusting the flag.
  if (tb.__empty || tb.querySelector("td.empty")) { tb.innerHTML = ""; tb.__empty = false; }
  bindBatchTable();

  const seen = new Set();
  batchItems.forEach((it) => {
    seen.add(it.company);
    let tr = rowEls.get(it.company);
    if (!tr) {
      tr = document.createElement("tr");
      tr.dataset.co = it.company;
      for (let c = 0; c < CELLS; c++) tr.appendChild(document.createElement("td"));
      const box = document.createElement("input");
      box.type = "checkbox";
      box.dataset.co = it.company;
      tr.children[0].appendChild(box);
      tr.children[5].className = "num";
      tr.children[6].className = "num";
      tr.children[7].className = "num";
      tb.appendChild(tr);
      rowEls.set(it.company, tr);
    }
    const mode = it._editingUrl ? "edit-url" : it._editingCompany ? "edit-company" : "view";
    const editing = mode !== "view";

    const box = tr.children[0].querySelector("input");
    if (box && document.activeElement !== box && box.checked !== !!it.selected) {
      box.checked = !!it.selected;
    }

    // An editing row is rendered once, on entry, then left alone so polling
    // cannot disturb the caret, the selection or unsaved text.
    if (editing) {
      if (mode !== tr.dataset.mode) {
        tr.dataset.mode = mode;
        setHTML(tr.children[2], websiteCell(it));
        setHTML(tr.children[3], statusCell(it));
      }
      return;
    }
    tr.dataset.mode = mode;

    setHTML(tr.children[1], esc(it.company));
    setHTML(tr.children[2], websiteCell(it));
    setHTML(tr.children[3], statusCell(it));
    setHTML(tr.children[4], esc(it.model || "—"));
    setHTML(tr.children[5], String(it.sources ?? "—"));
    setHTML(tr.children[6], fmt(it.tokens));
    setHTML(tr.children[7], it.seconds != null ? it.seconds + "s" : "—");
    setHTML(tr.children[8], actionsCell(it));
  });

  for (const [company, tr] of [...rowEls.entries()]) {
    if (!seen.has(company)) { tr.remove(); rowEls.delete(company); }
  }
  renderBatchSelCount();
}

function statusCell(it) {
  const cls = "s-" + String(it.status || "Pending").replace(/\s+/g, "");
  return `<span class="badge-st ${cls}">${esc(it.status || "Pending")}${statusZh(it.status)}</span>`
       + statusDetail(it);
}

/* One delegated listener for the table, attached once. Per-render re-binding
   was only needed because rows used to be recreated. */
function bindBatchTable() {
  const tb = document.querySelector("#batchtable tbody");
  if (tb.__bound) return;
  tb.__bound = true;
  tb.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-act]");
    if (btn) rowAction(btn.dataset.act, btn.dataset.co);
  });
  tb.addEventListener("change", (e) => {
    const box = e.target.closest('input[type="checkbox"][data-co]');
    if (!box) return;
    const i = idx(box.dataset.co);
    if (i >= 0) batchItems[i].selected = box.checked;
    renderBatchSelCount();
  });
}

const MIN_HEALTHY_SOURCES = 5;

/* Provenance line: how many unique sources the saved report actually used, and
   whether it came from the current retrieval pipeline or an older one. The
   count is the evidence retained in the report, not raw search hits. */
function provenance(it) {
  if (it.report_version == null && it.sources == null) return "";
  const n = it.sources ?? 0;
  const legacy = (it.report_version || 1) < 2;
  const low = n < MIN_HEALTHY_SOURCES;
  const version = legacy
    ? '<span class="ver legacy">Legacy / 旧版</span>'
    : '<span class="ver current">Current / 当前版</span>';
  let warn = "";
  if (low && legacy) {
    warn = '<div class="rowmsg err">⚠ Limited-source legacy report / 旧版低来源报告</div>';
  } else if (low) {
    warn = '<div class="rowmsg">Limited evidence available / 可用证据有限</div>';
  }
  return `<div class="prov">Sources: <b>${n}</b> · Report version: ${version}</div>${warn}`;
}

/* A low-source legacy report is the case where rerunning genuinely helps, so
   that is the only place the regenerate button is promoted to primary. */
/* Regenerate normally lives in the row's overflow menu. A low-source legacy
   report is the one case where rerunning genuinely helps, so there it is
   promoted into the row itself rather than hidden behind the menu. */
function staleHint(it) {
  const stale = (it.report_version || 1) < 2 && (it.sources ?? 0) < MIN_HEALTHY_SOURCES;
  return stale
    ? `<div class="rowacts"><button data-act="regenerate" data-co="${esc(it.company)}"
         class="primaryact">Regenerate with current research / 使用当前研究重新生成</button></div>`
    : "";
}

/* Display-only Chinese for status badges. The status VALUES stay English
   because they are compared as data throughout (and drive the CSS classes). */
const STATUS_ZH = {
  "Existing Report": " / 已有报告", "Completed": " / 已完成", "Failed": " / 失败",
  "Timed Out": " / 超时", "Pending": " / 待处理", "Searching": " / 检索中",
  "Researching": " / 研究中", "Generating": " / 生成中", "PDF Generating": " / 生成PDF",
  "Skipped": " / 已跳过",
};
function statusZh(st) { return STATUS_ZH[st] || ""; }

const WSTAT = {
  provided:        ['ok',   '✓ Provided / 已提供'],
  auto_discovered: ['ok',   '✓ Auto-discovered / 自动识别'],
  manual:          ['ok',   '✓ Manually corrected / 已手动修正'],
  unverified:      ['warn', '⚠ Could not verify / 无法验证'],
};

/* Website column. Every row can be corrected by hand, not just failed ones —
   a wrong site can also be auto-discovered or come from the upload. */
function websiteCell(it) {
  if (it._editingUrl) {
    const failed = it.status === "Failed" || it.status === "Timed Out";
    return `<div class="rowedit">
      <label>Company</label><div class="rowmsg">${esc(it.company)}</div>
      <label>Website</label>
      <input type="url" data-edit="url" data-co="${esc(it.company)}" placeholder="https://..."
             value="${esc(it.website || "")}">
      <div class="rowacts">
        <button data-act="cancelUrl" data-co="${esc(it.company)}">Cancel / 取消</button>
        <button data-act="saveUrl" data-co="${esc(it.company)}">${failed ? "Save &amp; Retry" : "Save"}</button>
      </div>
    </div>`;
  }
  const url = it.resolved_website || it.website || "";
  const link = url
    ? `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(url.replace(/^https?:\/\//, ""))}</a>`
    : '<span class="empty">none supplied</span>';
  const st = WSTAT[it.website_status];
  const tag = st ? `<div class="wstat ${st[0]}">${st[1]}</div>` : "";
  return `${link}${tag}
    <div class="rowacts">
      <button data-act="editUrl" data-co="${esc(it.company)}">Edit URL / 修改网址</button>
      <button data-act="editCompany" data-co="${esc(it.company)}">Edit Company / 修改公司</button>
    </div>`;
}

/* Row-level explanation and actions. */
function statusDetail(it) {
  if (it._editingCompany) {
    return `<div class="rowedit">
      <label>Company name</label>
      <input type="text" data-edit="company" data-co="${esc(it.company)}" value="${esc(it.company)}">
      <label>Website</label>
      <input type="url" data-edit="companyUrl" data-co="${esc(it.company)}" placeholder="https://..."
             value="${esc(it.website || "")}">
      <div class="rowacts">
        <button data-act="cancelCompany" data-co="${esc(it.company)}">Cancel / 取消</button>
        <button data-act="saveCompany" data-co="${esc(it.company)}">Save / 保存</button>
        <button data-act="saveCompanyRegen" data-co="${esc(it.company)}">Save &amp; Regenerate / 保存并重新生成</button>
      </div>
    </div>`;
  }
  if (it._confirmDelete) {
    return `<div class="rowconfirm">
        <div class="rowmsg err">⚠ Delete this saved report? / 删除该报告？此操作不可撤销。</div>
        <div class="rowacts">
          <button data-act="cancelDelete" data-co="${esc(it.company)}">Cancel / 取消</button>
          <button data-act="confirmDelete" data-co="${esc(it.company)}" class="del">Delete Report / 删除报告</button>
        </div>
      </div>`;
  }
  if (it._pendingChoice) {
    return `<div class="rowmsg">Website updated. Existing report was generated using the previous website.
        <span class="cn">官网已更新，现有报告基于旧官网生成。</span></div>
      <div class="rowacts">
        <button data-act="keepExisting" data-co="${esc(it.company)}">Keep Existing Report / 保留现有报告</button>
        <button data-act="regenerate" data-co="${esc(it.company)}">Regenerate Research / 重新研究</button>
      </div>`;
  }
  if (it.status === "Completed" && (it.pdfs || []).length) return provenance(it) + staleHint(it);
  if (it.status === "Existing Report") {
    return `<div class="rowmsg">${esc(it.note || "Existing report found — no AI call made.")}</div>
            ${provenance(it)}${staleHint(it)}`;
  }
  if (it.status === "Failed" || it.status === "Timed Out") {
    // Recovery choices stay inline: they are about THIS failure, not generic actions.
    return `<div class="rowmsg err">${esc(it.error || "Unknown error")}</div>
      <div class="rowacts">
        <button data-act="retry" data-co="${esc(it.company)}" class="primaryact">Retry / 重试</button>
        <button data-act="retryNoSite" data-co="${esc(it.company)}">Retry Without Website / 不用官网重试</button>
        <button data-act="editUrl" data-co="${esc(it.company)}">Edit URL / 修改官网</button>
      </div>`;
  }
  return "";
}

/* Row actions as a compact [View] [PDF] [⋯] group.
   The overflow is a native <details> so no extra state has to survive polling,
   and the cell is only rewritten when its HTML actually changes. */
function actionsCell(it) {
  const dir = it.report_dir || safeDir(it.company);
  const co = esc(it.company);
  if (!hasReport(it) || !(it.pdfs || []).length) {
    if (it.status === "Failed" || it.status === "Timed Out") return "";
    return `<div class="acts">
        <details><summary>⋯</summary><div class="menu">
          <button data-act="editCompany" data-co="${co}">Edit Company / 修改公司</button>
          <button data-act="editUrl" data-co="${co}">Edit URL / 修改官网</button>
        </div></details>
      </div>`;
  }
  const pdfUrl = languagePdfUrl(it.company);
  return `<div class="acts">
      <a class="abtn" href="/api/report/${encodeURIComponent(dir)}/research.json"
         target="_blank" rel="noopener">View / 查看</a>
      <a class="abtn" href="${esc(pdfUrl)}" data-pdf="${esc(pdfUrl)}" data-pdftitle="${co}">PDF</a>
      <details><summary>⋯</summary><div class="menu">
        <button data-act="editCompany" data-co="${co}">Edit Company / 修改公司</button>
        <button data-act="editUrl" data-co="${co}">Edit URL / 修改官网</button>
        <div class="divider"></div>
        <a href="${esc(pdfUrl)}">Download PDF / 下载PDF</a>
        <button data-act="regenerate" data-co="${co}">Regenerate / 重新生成</button>
        <div class="divider"></div>
        <button data-act="deleteReport" data-co="${co}" class="del">Delete Report / 删除报告</button>
      </div></details>
    </div>`;
}

/* One-company reruns. Refresh explicitly bypasses the saved report. */
/* Non-blocking success feedback. No overlay, no focus steal, no second modal. */
function toast(message) {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    el.className = "toast";
    document.body.appendChild(el);
  }
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(el.__t);
  el.__t = setTimeout(() => el.classList.remove("show"), 2600);
}

/* A deleted report leaves the company row intact — name, website and upload
   record stay, only the saved artifacts go. */
function clearReportState(it) {
  it.status = "Pending";
  it.note = null; it.pdfs = []; it.report_dir = null;
  it.tokens = 0; it.sources = null; it.seconds = null;
  it.model = null; it._pendingChoice = false;
}

async function deleteReports(companies) {
  const r = await fetch("/api/reports/delete", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ companies }),
  }).then((x) => x.json()).catch((e) => ({ ok: false, error: e.message }));
  if (!r.ok) {
    $("batcherror").textContent = r.error || "Delete failed.";
    $("batcherror").hidden = false;
    return 0;
  }
  const ok = new Set((r.results || []).filter((x) => x.ok).map((x) => x.company));
  batchItems.forEach((it) => { if (ok.has(it.company)) clearReportState(it); });
  renderBatchTable();
  loadDoneList();
  loadHistory();
  return r.deleted;
}

function readInput(sel, co) {
  const el = document.querySelector(`[data-edit="${sel}"][data-co="${CSS.escape(co)}"]`);
  return el ? el.value.trim() : "";
}

/* Saving a URL never costs tokens. It updates the record; rerunning is a
   separate, explicit choice. */
function applyWebsite(it, url) {
  it.website = url;
  it.resolved_website = url;
  it.website_status = url ? "manual" : null;
  it._editingUrl = false;
  it._editingCompany = false;
}

function hasReport(it) {
  return it.status === "Completed" || it.status === "Existing Report";
}

function rowAction(act, co) {
  const i = idx(co);
  if (i < 0) return;
  const it = batchItems[i];
  if (act === "deleteReport") { it._confirmDelete = true; renderBatchTable(); return; }
  if (act === "cancelDelete")  { it._confirmDelete = false; renderBatchTable(); return; }
  if (act === "confirmDelete") {
    it._confirmDelete = false;
    deleteReports([it.company]).then((n) => { if (n) toast("✓ Report deleted"); });
    return;
  }
  if (act === "editUrl")        { it._editingUrl = true; renderBatchTable(); return; }
  if (act === "cancelUrl")      { it._editingUrl = false; renderBatchTable(); return; }
  if (act === "editCompany")    { it._editingCompany = true; renderBatchTable(); return; }
  if (act === "cancelCompany")  { it._editingCompany = false; renderBatchTable(); return; }
  if (act === "keepExisting")   { it._pendingChoice = false; renderBatchTable(); return; }

  if (act === "saveUrl") {
    const url = readInput("url", co);
    const wasFailed = it.status === "Failed" || it.status === "Timed Out";
    const had = hasReport(it);
    applyWebsite(it, url);
    if (wasFailed) {                         // Save & Retry
      it.status = "Pending"; it.error = null; it.error_reason = null; it.pdfs = [];
      renderBatchTable();
      runRow({ ...it, selected: true, trust_website: !!url, ignore_website: !url }, false);
      return;
    }
    it._pendingChoice = had;                 // offer keep-or-regenerate
    renderBatchTable();
    return;
  }

  if (act === "saveCompany" || act === "saveCompanyRegen") {
    const name = readInput("company", co) || it.company;
    const url = readInput("companyUrl", co);
    it.company = name;
    applyWebsite(it, url);
    if (act === "saveCompanyRegen") {
      it._pendingChoice = false;
      it.status = "Pending"; it.error = null; it.error_reason = null; it.pdfs = [];
      renderBatchTable();
      runRow({ ...it, selected: true, trust_website: !!url, ignore_website: !url }, false);
      return;
    }
    it._pendingChoice = hasReport(it);
    renderBatchTable();
    return;
  }

  if (act === "regenerate") {
    if (!confirm(`Regenerate research for ${it.company}?\n\n`
               + `This will make a new AI research call and consume tokens.`)) return;
    it._pendingChoice = false;
    it.status = "Pending"; it.note = null; it.pdfs = [];
    it.report_version = null; it.limited_sources = false; it.sources = null;
    renderBatchTable();
    runRow({ ...it, selected: true, pdfs: [],
             trust_website: it.website_status === "manual" && !!it.website }, false);
    return;
  }

  // Retry / Retry Without Website. `item` is built here: the previous version
  // referenced an undefined variable, so both actions threw before running.
  if (act === "retry" || act === "retryNoSite") {
    const item = { ...it, selected: true, status: "Pending",
                   error: null, error_reason: null, pdfs: [] };
    if (act === "retryNoSite") { item.ignore_website = true; item.website = ""; }
    it.status = "Pending"; it.error = null; it.error_reason = null; it.pdfs = [];
    renderBatchTable();
    runRow(item, false);
  }
}

/** View PDF (embedded) + Download PDF for one saved report. */
function pdfActions(dir, pdf, company) {
  if (!pdf) return "";
  const url = languagePdfUrl(company || "");
  return `<a href="${esc(url)}" data-pdf="${esc(url)}" data-pdftitle="${esc(company || "")} — ${esc(pdf.model || "report")}">View PDF / 查看PDF</a>
          <a href="${esc(url)}">Download PDF</a>`;
}

// Mirror of pdf_service.safe_name for building report URLs.
function safeDir(name) {
  return String(name || "Company").replace(/[\\/:*?"<>|]+/g, " ").trim().replace(/\s+/g, "_").slice(0, 48) || "Company";
}

/** Batch progress as KPI cards plus one secondary metrics line. */
function renderBatchSummary(s) {
  const el = $("batchsummary");
  if (!s) { el.hidden = true; return; }
  el.hidden = false;
  const mins = (v) => v == null ? "—" : (v >= 60 ? Math.round(v / 60) + "m" : Math.round(v) + "s");
  const inProgress = s.current ? 1 : 0;
  const cards = [
    ["Completed / 已完成", `${s.completed} / ${s.total}`, "is-done"],
    ["In Progress / 进行中", inProgress, "is-run"],
    ["Failed / 失败", s.failed, s.failed ? "is-fail" : ""],
    ["Remaining / 剩余", s.remaining, ""],
  ].map(([k, v, cls]) =>
    `<div class="kpi ${cls}"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`).join("");
  const metrics = [
    ["Total tokens / 总Token数", fmt(s.total_tokens)],
    ["Avg tokens/company / 单家平均Token", fmt(s.avg_tokens)],
    ["Avg time/company / 单家平均耗时", s.avg_seconds != null ? s.avg_seconds + "s" : "—"],
    ["Elapsed / 已用时", mins(s.elapsed_seconds)],
    ["Est. remaining / 预计剩余", mins(s.eta_seconds)],
  ].map(([k, v]) => `<span><b>${esc(k)}</b>${esc(v)}</span>`).join("");
  const stage = s.current || s.stage
    ? `<span><b>Now / 当前</b>${esc(s.current || "—")}${s.stage ? " · " + esc(s.stage) : ""}</span>` : "";
  el.innerHTML = `<div class="kpis">${cards}</div>
                  <div class="metrics">${stage}${metrics}</div>`;
}

let REPORT_LIBRARY = [];

/** Date is read from the saved PDF filename (…_YYYY-MM-DD.pdf) - no new API. */
function reportDate(file) {
  const m = String(file || "").match(/(\d{4}-\d{2}-\d{2})\.pdf$/);
  return m ? m[1] : "";
}

/** Companies ticked in the report library. Selection is the library's own —
 *  it is no longer borrowed from the batch table, where "Compile Selected"
 *  used to read a selection the Reports tab never showed. */
const librarySelection = new Set();

function visibleLibraryRows() {
  const q = ($("libsearch") ? $("libsearch").value : "").trim().toLowerCase();
  return REPORT_LIBRARY.filter((r) =>
    !q || (r.company || "").toLowerCase().includes(q) || (r.model || "").toLowerCase().includes(q));
}

function librarySelected() {
  return REPORT_LIBRARY.filter((r) => librarySelection.has(r.company)).map((r) => r.company);
}

function renderLibrary() {
  const rows = visibleLibraryRows();
  const count = $("libcount");
  if (count) {
    count.textContent = REPORT_LIBRARY.length
      ? `${rows.length} / ${REPORT_LIBRARY.length} reports · 共 ${REPORT_LIBRARY.length} 份报告`
      : "";
  }
  $("donelist").innerHTML = rows.length
    ? rows.map((r) => {
        const url = languagePdfUrl(r.company);
        const co = esc(r.company);
        const on = librarySelection.has(r.company);
        const json = `/api/report/${encodeURIComponent(r.dir || safeDir(r.company))}/research.json`;
        return `<li class="${on ? "sel" : ""}">
          <input type="checkbox" class="libcb" data-co="${co}"${on ? " checked" : ""}
                 aria-label="Select ${co}">
          <span class="co">${co}</span>
          <span class="mt">${esc(r.model || "")}${reportDate(r.file) ? " · " + reportDate(r.file) : ""}</span>
          <span class="sp acts">
            <a class="abtn" href="${esc(json)}" target="_blank" rel="noopener">View / 查看</a>
            <a class="abtn" href="${esc(url)}" data-pdf="${esc(url)}" data-pdftitle="${co}">PDF</a>
            <details><summary>⋯</summary><div class="menu">
              <a href="${esc(url)}">Download PDF / 下载PDF</a>
              <button data-libact="refresh" data-co="${co}">Refresh / 刷新</button>
              <div class="divider"></div>
              <button data-libact="delete" data-co="${co}" class="del">Delete Report / 删除报告</button>
            </div></details>
          </span></li>`;
      }).join("")
    : `<li class="empty">${REPORT_LIBRARY.length
        ? "No reports match that search. 未找到匹配的报告。"
        : "None yet. 暂无报告。"}</li>`;
  syncLibrarySelectionUI();
}

/** Reflect librarySelection onto the rows already on screen. No re-render. */
function syncLibrarySelectionUI() {
  document.querySelectorAll("#donelist .libcb").forEach((cb) => {
    const on = librarySelection.has(cb.dataset.co);
    if (cb.checked !== on) cb.checked = on;
    cb.closest("li").classList.toggle("sel", on);
  });
  const n = librarySelected().length;
  const sc = $("libselcount");
  if (sc) sc.textContent = n ? `${n} selected / 已选 ${n} 份` : "";
}

async function loadDoneList() {
  REPORT_LIBRARY = await fetch("/api/reports").then((r) => r.json()).catch(() => []);
  // Drop ticks for reports that no longer exist.
  const live = new Set(REPORT_LIBRARY.map((r) => r.company));
  [...librarySelection].forEach((c) => { if (!live.has(c)) librarySelection.delete(c); });
  renderLibrary();
  renderStatusCards();
}

/** Report-library actions. Bound once; the list is re-rendered wholesale. */
function initLibrary() {
  const list = $("donelist");
  if (!list) return;
  list.addEventListener("change", (e) => {
    const cb = e.target.closest(".libcb");
    if (!cb) return;
    if (cb.checked) librarySelection.add(cb.dataset.co);
    else librarySelection.delete(cb.dataset.co);
    syncLibrarySelectionUI();
  });
  list.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-libact]");
    if (!b) return;
    const co = b.dataset.co;
    b.closest("details").open = false;
    if (b.dataset.libact === "delete") {
      if (!confirm(`Delete the saved report for ${co}?\n\n`
                 + `删除 ${co} 的已保存报告？`)) return;
      deleteReports([co]).then((n) => { if (n) toast("✓ Report deleted / 已删除报告"); });
      return;
    }
    if (b.dataset.libact === "refresh") {
      if (!confirm(`Refresh research for ${co}?\n\n`
                 + `This makes a new AI research call and consumes tokens.\n`
                 + `这将发起新的 AI 研究请求并消耗 tokens。`)) return;
      const existing = REPORT_LIBRARY.find((r) => r.company === co);
      runRow({ company: co, website: "", selected: true, status: "Pending",
               pdfs: [], model: existing ? existing.model : null }, false);
      toast("Refreshing… see Batch Research for progress / 刷新中，进度见批量研究");
    }
  });

  $("libselall").addEventListener("click", () => {
    visibleLibraryRows().forEach((r) => librarySelection.add(r.company));
    syncLibrarySelectionUI();
  });
  $("libselnone").addEventListener("click", () => { librarySelection.clear(); syncLibrarySelectionUI(); });

  $("libdelsel").addEventListener("click", () => {
    const targets = librarySelected();
    const bar = $("libconfirm");
    if (!targets.length) {
      bar.hidden = false;
      bar.innerHTML = "<span>Select at least one report. <span class=\"cn\">请至少选择一份报告。</span></span>";
      return;
    }
    bar.hidden = false;
    bar.innerHTML = `<span>⚠ Delete ${targets.length} saved report${targets.length === 1 ? "" : "s"}?`
      + `<span class="cn">删除 ${targets.length} 份已保存报告？</span></span>`
      + `<button id="libcancel" class="mini ghost">Cancel / 取消</button>`
      + `<button id="libok" class="mini danger">Delete ${targets.length} / 删除</button>`;
    $("libcancel").onclick = () => { bar.hidden = true; };
    $("libok").onclick = () => {
      bar.hidden = true;
      deleteReports(targets).then((n) => {
        librarySelection.clear();
        if (n) toast(`✓ ${n} report${n === 1 ? "" : "s"} deleted / 已删除 ${n} 份报告`);
      });
    };
  });
}

/* Merge one sub-batch's rows back into the table by company name. A per-company
   action must never disturb the other rows: the earlier version assigned
   batchItems = s.items, which wiped the other 22 companies off the screen. */
/* Local-only UI state, and fields the user may be editing right now. A polling
   response must never put the server's older value back into a field being
   typed into. */
const LOCAL_KEYS = ["_editingUrl", "_editingCompany", "_confirmDelete", "_pendingChoice"];
const EDIT_OWNED = ["company", "website", "resolved_website", "website_status"];

function mergeItem(local, row) {
  const merged = { ...local, ...row };
  for (const k of LOCAL_KEYS) merged[k] = local[k];
  merged.selected = local.selected;
  // While a row is open for editing, the user owns these fields, not the server.
  if (local._editingUrl || local._editingCompany) {
    for (const k of EDIT_OWNED) merged[k] = local[k];
  }
  return merged;
}

function mergeRows(rows) {
  for (const row of rows || []) {
    const i = batchItems.findIndex((x) => x.company === row.company);
    if (i >= 0) batchItems[i] = mergeItem(batchItems[i], row);
    else batchItems.push(row);
  }
}

async function pollBatch(bid, scoped) {
  if (batchPolling && !scoped) return;
  if (!scoped) { batchPolling = true; $("stopbatch").hidden = false; }
  try {
    while (true) {
      const s = await fetch(`/api/batch/${bid}`).then((r) => r.json());
      if (s.error) throw new Error(s.error);
      // Never assign the server array wholesale: that would discard an open
      // editor and any unsaved text along with it.
      if (scoped) mergeRows(s.items);
      else batchItems = (s.items || []).map((row) => {
        const local = batchItems.find((x) => x.company === row.company);
        return local ? mergeItem(local, row) : row;
      });
      renderBatchTable();
      if (!scoped) renderBatchSummary(s);
      loadDoneList();                       // PDFs appear as each company finishes
      if (s.status !== "running" && s.status !== "queued") break;
      await new Promise((r) => setTimeout(r, 1500));
    }
  } catch (e) {
    $("batcherror").textContent = e.message;
    $("batcherror").hidden = false;
  } finally {
    if (!scoped) { batchPolling = false; $("stopbatch").hidden = true; }
  }
}

/* Run a single company without touching the rest of the batch. */
async function runRow(item, useExisting) {
  $("batcherror").hidden = true;
  const r = await fetch("/api/batch/start", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items: [item], model: $("batchmodel").value,
                           use_existing: useExisting }),
  }).then((x) => x.json());
  if (r.error) { $("batcherror").textContent = r.error; $("batcherror").hidden = false; return; }
  pollBatch(r.batch_id, true);              // scoped: merges just this row
}

async function startBatch(items, useExisting) {
  if (!items.length) {
    $("batcherror").textContent = "No companies selected. / 未选择公司。";
    $("batcherror").hidden = false;
    return;
  }
  $("batcherror").hidden = true;
  const body = { items, model: $("batchmodel").value,
                 use_existing: useExisting === undefined ? $("useexisting").checked : useExisting };
  const r = await fetch("/api/batch/start", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }).then((x) => x.json());
  if (r.error) { $("batcherror").textContent = r.error; $("batcherror").hidden = false; return; }
  batchId = r.batch_id;
  pollBatch(batchId, false);
}

function remapItems() {
  const ni = +$("colname").value, si = +$("colsite").value;
  batchItems = batchRows.map((r) => {
    let site = (r[si] || "").trim();
    if (site && !/^https?:\/\//i.test(site)) site = "https://" + site.replace(/^\/+/, "");
    return { company: (r[ni] || "").trim(), website: site, status: "Pending",
             model: null, sources: null, tokens: null, seconds: null,
             pdfs: [], error: null, selected: true };
  }).filter((x) => x.company);
  renderBatchTable();
}

(function initBatch() {
  document.querySelectorAll(".mode").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll(".mode").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    $("mode-single").hidden = b.dataset.mode !== "single";
    $("mode-batch").hidden = b.dataset.mode !== "batch";
    $("mode-reports").hidden = b.dataset.mode !== "reports";
    if (b.dataset.mode === "reports") loadDoneList();
    if (b.dataset.mode === "batch") loadDoneList();
  }));

  $("batchfile").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    const r = await fetch("/api/batch/upload", { method: "POST", body: fd }).then((x) => x.json());
    if (r.error) { $("batcherror").textContent = r.error; $("batcherror").hidden = false; return; }
    $("batcherror").hidden = true;
    batchHeaders = r.headers; batchRows = r.rows;
    const opts = batchHeaders.map((h, i) => `<option value="${i}">${esc(h || "Column " + (i + 1))}</option>`).join("");
    $("colname").innerHTML = opts; $("colsite").innerHTML = opts;
    $("colname").value = r.mapping.name ?? 0;
    $("colsite").value = r.mapping.website ?? 1;
    batchItems = r.items;
    renderBatchTable();
  });

  $("colname").addEventListener("change", remapItems);
  $("colsite").addEventListener("change", remapItems);
  $("deletesel").addEventListener("click", () => {
    const targets = batchItems.filter(
      (i) => i.selected && (i.status === "Existing Report"
                            || (i.status === "Completed" && (i.pdfs || []).length)));
    if (!targets.length) {
      $("batcherror").textContent = "Select companies that have a saved report. / 请选择已有报告的公司。";
      $("batcherror").hidden = false;
      return;
    }
    const bar = $("bulkconfirm");
    bar.hidden = false;
    bar.innerHTML = `<span>⚠ Delete ${targets.length} existing report${targets.length === 1 ? "" : "s"}? `
      + `Their company records and websites will remain; the next run regenerates them.`
      + `<span class="cn">删除 ${targets.length} 份已有报告？公司记录与官网保留，下次运行将重新生成。</span></span>`
      + `<button id="bulkcancel" class="mini ghost">Cancel / 取消</button>`
      + `<button id="bulkok" class="mini danger">Delete ${targets.length} Report${targets.length === 1 ? "" : "s"} / 删除</button>`;
    document.getElementById("bulkcancel").onclick = () => { bar.hidden = true; };
    document.getElementById("bulkok").onclick = () => {
      bar.hidden = true;
      deleteReports(targets.map((t) => t.company))
        .then((n) => { if (n) toast(`✓ ${n} report${n === 1 ? "" : "s"} deleted / 已删除 ${n} 份报告`); });
    };
  });
  $("selectall").addEventListener("click", () => { batchItems.forEach((i) => i.selected = true); renderBatchTable(); });
  $("selectnone").addEventListener("click", () => { batchItems.forEach((i) => i.selected = false); renderBatchTable(); });
  $("runselected").addEventListener("click", () => startBatch(batchItems.filter((i) => i.selected)));
  $("runall").addEventListener("click", () => startBatch(batchItems.map((i) => ({ ...i, selected: true }))));
  $("retryfailed").addEventListener("click", () => startBatch(
    batchItems.filter((i) => ["Failed", "Timed Out"].includes(i.status))
              .map((i) => ({ ...i, status: "Pending", error: null, selected: true }))));
  $("stopbatch").addEventListener("click", () => fetch(`/api/batch/${batchId}/stop`, { method: "POST" }));
  $("zipbtn").addEventListener("click", () => { window.location = `/api/reports/zip?lang=${getLang()}`; });
})();

// -------------------------------------------------- combined portfolio PDF
async function compileReports(onlySelected) {
  // Compile Selected belongs to the report library, so it reads the library's
  // ticks and reports back beside its own button.
  const out = onlySelected ? $("libout") : $("compileout");
  const order = $("order").value;
  const companies = onlySelected ? librarySelected() : null;
  if (onlySelected && !companies.length) {
    out.hidden = false;
    out.style.color = "var(--err)";
    out.textContent = "Select at least one report. / 请至少选择一份报告。";
    return;
  }
  out.hidden = false;
  out.style.color = "var(--muted)";
  out.textContent = "Compiling… 汇总中…";
  try {
    const r = await fetch("/api/reports/compile", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ companies, order, lang: getLang() }),
    }).then((x) => x.json());
    if (r.error) throw new Error(r.error);
    out.style.color = "var(--ok)";
    out.innerHTML = `✓ ${r.count} companies —
      <a href="${esc(r.url)}" data-pdf="${esc(r.url)}" data-pdftitle="Combined portfolio (${r.count} companies)">View Compiled PDF / 查看汇总PDF</a>
      <a href="${esc(r.url)}">Download Compiled PDF</a>`;
  } catch (e) {
    out.style.color = "var(--err)";
    out.textContent = e.message;
  }
}

$("compileall").addEventListener("click", () => compileReports(false));
$("compilesel").addEventListener("click", () => compileReports(true));
