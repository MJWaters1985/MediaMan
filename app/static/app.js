"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtBytes = (n) => { if (!n) return "0 B"; const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; n = Math.abs(n); while (n >= 1024 && i < 4) { n /= 1024; i++; } return n.toFixed(i > 2 ? 1 : 0) + " " + u[i]; };
const fmtDur = (s) => { if (s == null) return ""; s = Math.round(s); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60); return h ? `${h}h ${m}m` : m ? `${m}m ${s % 60}s` : `${s}s`; };
const ago = (t) => { if (!t) return ""; const d = Date.now() / 1000 - t; return d < 60 ? "just now" : d < 3600 ? Math.floor(d / 60) + "m ago" : d < 86400 ? Math.floor(d / 3600) + "h ago" : new Date(t * 1000).toLocaleDateString(); };

async function api(path, opts = {}) {
  const o = { headers: { "Content-Type": "application/json" }, ...opts };
  if (o.body && typeof o.body !== "string") o.body = JSON.stringify(o.body);
  const r = await fetch(path, o);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || r.statusText);
  return j;
}
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 3200); }
async function act(fn, ok) { try { const r = await fn(); if (ok) toast(typeof ok === "function" ? ok(r) : ok); refresh(); return r; } catch (e) { toast("⚠ " + e.message); } }

function pathHTML(p, root) {
  if (!p) return '<span class="muted">—</span>';
  let rel = p;
  if (root && p.startsWith(root + "/")) rel = p.slice(root.length + 1);
  const i = rel.lastIndexOf("/");
  return `<span class="path"><span class="dir">${esc(rel.slice(0, i + 1))}</span>${esc(rel.slice(i + 1))}</span>`;
}
function conf(c) {
  const pct = Math.round((c || 0) * 100);
  const col = c >= 0.8 ? "var(--ok)" : c >= 0.6 ? "var(--warn)" : "var(--bad)";
  return `<span class="conf"><i><b style="width:${pct}%;background:${col}"></b></i>${pct}%</span>`;
}
function setHTML(el, html) { if (el._html !== html) { el._html = html; el.innerHTML = html; } }
const badge = (s) => `<span class="badge ${esc(s)}">${esc(s)}</span>`;

let STATUS = {}, SETTINGS = {};
let tab = location.hash.slice(1) || "overview";

// ---------------------------------------------------------------- tabs
function showTab(t) {
  tab = t; location.hash = t;
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === t));
  $$(".tab").forEach((s) => s.classList.toggle("active", s.id === t));
  refresh(true);
}
$$("#tabs button").forEach((b) => b.onclick = () => showTab(b.dataset.tab));
window.addEventListener("hashchange", () => { const t = location.hash.slice(1); if (t && t !== tab && $("#" + t)) showTab(t); });

// ---------------------------------------------------------------- overview
function renderOverview() {
  const s = STATUS, hw = s.hardware || {}, q = s.queue?.counts || {};
  const be = hw.backends || {};
  const encRows = Object.entries(be).map(([b, caps]) =>
    `<div>${esc(b)}</div><div>${Object.entries(caps).map(([c, v]) => `${c}${v["10bit"] ? " (10-bit)" : ""}`).join(", ")}</div>`).join("");
  const best = hw.best?.[s.target_codec];
  const pathRows = Object.entries(s.paths || {}).map(([k, v]) =>
    `<div>${esc(k.replace("_dir", ""))}</div><div class="pathcheck"><span class="path">${esc(v.path)}</span> ${v.exists ? (v.writable ? '<span class="ok">✓ writable</span>' : '<span class="bad">read-only</span>') : '<span class="bad">missing</span>'}</div>`).join("");
  const scan = s.scan || {};
  const items = s.items || {};
  const needs = (items.proposed || 0) + (items.unknown || 0) + (items.conflict || 0) + (items.error || 0);
  const imp = s.imports || {};
  setHTML($("#overview"), `
  <div class="grid">
    <div class="card"><h3>Library</h3>
      <div class="big">${needs}</div><div class="muted">files need renaming or review</div>
      <div class="kv" style="margin-top:10px"><div>Organized</div><div>${items.ok || 0}</div><div>Ignored</div><div>${items.ignored || 0}</div>
      <div>Transcode candidates</div><div>${s.candidates || 0}</div></div>
      <div class="toolbar" style="margin:12px 0 0">
        <button class="primary" id="ov-scan" ${scan.running ? "disabled" : ""}>${scan.running ? `Scanning ${scan.done}/${scan.total}…` : "Scan library"}</button>
        <button onclick="showTab('library')">Review</button></div>
      ${scan.running ? `<div class="bar"><b style="width:${scan.total ? scan.done * 100 / scan.total : 0}%"></b></div><div class="muted path">${esc(scan.current)}</div>` : scan.finished ? `<div class="muted" style="margin-top:6px">Last scan ${ago(scan.finished)}</div>` : ""}
    </div>
    <div class="card"><h3>Transcoding</h3>
      <div class="big">${q.running || 0} <span class="muted" style="font-size:15px;font-weight:400">running · ${q.queued || 0} queued</span></div>
      <div class="kv" style="margin-top:10px"><div>Completed</div><div>${q.done || 0}</div><div>Space saved</div><div>${fmtBytes(s.saved_bytes)}</div>
      <div>Failed</div><div>${q.failed || 0}</div><div>Queue</div><div>${s.queue?.paused ? "paused" : s.queue?.in_window ? "active" : "outside time window"}</div></div>
      <div class="toolbar" style="margin:12px 0 0"><button onclick="showTab('queue')">Open queue</button>
      <button id="ov-cand" ${s.candidates ? "" : "disabled"}>Queue ${s.candidates || 0} candidates</button></div>
    </div>
    <div class="card"><h3>Downloads watcher</h3>
      <div class="big">${imp.review || 0}</div><div class="muted">downloads need review</div>
      <div class="kv" style="margin-top:10px"><div>Status</div><div>${s.watcher?.enabled ? (s.watcher.error ? `<span style="color:var(--bad)">${esc(s.watcher.error)}</span>` : "watching") : "disabled"}</div>
      <div>Last check</div><div>${ago(s.watcher?.last_poll) || "—"}</div><div>Waiting</div><div>${imp.waiting || 0}</div><div>Imported</div><div>${imp.imported || 0}</div></div>
      <div class="toolbar" style="margin:12px 0 0"><button onclick="showTab('imports')">Open downloads</button></div>
    </div>
    <div class="card"><h3>Hardware</h3>
      <div class="kv">
        <div>Codec policy</div><div><b>${esc(s.target_codec === "auto" ? "auto (AV1 / HEVC for DV & HDR10+)" : (s.target_codec || "").toUpperCase())}</b></div>
        <div>GPU</div><div>${(hw.devices || []).map((d) => `${esc(d.vendor)} <span class="path">${esc(d.path)}</span>`).join("<br>") || "none passed through"}</div>
        <div>CPU threads</div><div>${hw.cpu_count || "?"}</div>
        <div>Tools</div><div>${Object.entries(hw.tools || {}).map(([k, v]) => `${esc(k)} ${v ? "✓" : "✗"}`).join(" · ")}</div>
        ${encRows}
      </div>
      ${hw.error ? `<p style="color:var(--bad)">${esc(hw.error)}</p>` : ""}
      <div class="toolbar" style="margin:12px 0 0"><button id="ov-hw">Re-detect</button></div>
    </div>
    <div class="card" style="grid-column:1/-1"><h3>Folders</h3><div class="kv">${pathRows}${tlsRow(s.tls)}</div></div>
  </div>`);
  $("#ov-scan").onclick = () => act(() => api("/api/scan", { method: "POST", body: {} }), "Scan started");
  $("#ov-cand").onclick = () => act(() => api("/api/jobs", { method: "POST", body: { all_candidates: true } }), (r) => `Queued ${r.queued} files`);
  $("#ov-hw").onclick = () => act(() => api("/api/hardware/detect", { method: "POST" }), "Hardware re-detected");
}

function tlsRow(t) {
  if (!t) return "";
  let v;
  if (t.enabled && t.cert) {
    const c = t.cert, warn = c.days_left < 14 ? "bad" : "ok";
    v = `<span class="${warn}">✓ HTTPS</span> · ${esc(c.subject)} · ${esc(c.issuer)} · expires ${esc(c.not_after.slice(0, 10))} (${c.days_left} days)${c.chain_length < 2 ? ' · <span class="bad">no intermediate in cert file</span>' : ""}`;
  } else if (t.requested) v = `<span class="bad">HTTPS off: ${esc(t.error)}</span>`;
  else v = '<span class="muted">HTTP (set MEDIAMAN_TLS_CERT/KEY to enable HTTPS)</span>';
  return `<div>web UI</div><div class="pathcheck">${v}</div>`;
}

// ---------------------------------------------------------------- library
let libFilter = "proposed,unknown,conflict,error", libQuery = "", libItems = [];
$$("#lib-filters button").forEach((b) => b.onclick = () => {
  $$("#lib-filters button").forEach((x) => x.classList.toggle("active", x === b));
  libFilter = b.dataset.f; loadLibrary();
});
let searchT; $("#lib-search").oninput = (e) => { clearTimeout(searchT); searchT = setTimeout(() => { libQuery = e.target.value; loadLibrary(); }, 300); };
$("#lib-scan").onclick = () => act(() => api("/api/scan", { method: "POST", body: {} }), "Scan started — results appear as files are identified");
$("#lib-all").onchange = (e) => $$("#lib-table input[data-id]").forEach((c) => c.checked = e.target.checked);
const selected = (sel) => $$(`${sel} input[data-id]:checked`).map((c) => +c.dataset.id);
$$("#lib-bulk [data-bulk]").forEach((b) => b.onclick = async () => {
  const ids = selected("#lib-table"), k = b.dataset.bulk;
  if (k === "apply-high") return act(() => api("/api/items/apply", { method: "POST", body: { min_confidence: 0.8 } }), (r) => `Applied ${r.applied}${r.errors.length ? `, ${r.errors.length} failed` : ""}`);
  if (!ids.length) return toast("Select some files first");
  if (k === "apply") return act(() => api("/api/items/apply", { method: "POST", body: { ids } }), (r) => `Applied ${r.applied}${r.errors.length ? `, ${r.errors.length} failed: ${r.errors[0].error}` : ""}`);
  if (k === "transcode") return act(() => api("/api/jobs", { method: "POST", body: { item_ids: ids } }), (r) => `Queued ${r.queued}`);
  if (k === "ignore") { for (const id of ids) await api(`/api/items/${id}/ignore`, { method: "POST", body: {} }); toast(`Ignored ${ids.length}`); loadLibrary(); }
});

function rootOf(p) {
  for (const k of ["movies_dir", "tv_dir", "downloads_dir", ...(SETTINGS.extra_scan_dirs || [])]) {
    const r = SETTINGS[k] || k; if (p && r && r.startsWith("/") && p.startsWith(r + "/")) return r;
  }
  return "";
}
function itemRows(items, opts = {}) {
  if (!items.length) return `<div class="empty">${opts.empty || "Nothing here."}</div>`;
  return `<table><thead><tr>${opts.select ? "<th></th>" : ""}<th>Current file</th><th></th><th>Jellyfin name</th><th class="hide-sm">Identified as</th><th>Status</th><th></th></tr></thead><tbody>
  ${items.map((it) => {
    const i = it.info || {};
    const what = i.type === "episode"
      ? `${esc(i.title)} · S${String(i.season ?? "?").padStart(2, "0")}${(i.episodes || []).map((e) => "E" + String(e).padStart(2, "0")).join("") || (i.date ? " " + esc(i.date) : "")}`
      : `${esc(i.title || "?")}${i.year ? ` (${i.year})` : ""}`;
    const same = it.proposed_path === it.path;
    return `<tr>
      ${opts.select ? `<td><input type="checkbox" data-id="${it.id}"></td>` : ""}
      <td>${pathHTML(it.path, rootOf(it.path))}<div class="signals">${esc(it.codec || "")}${it.height ? " " + it.height + "p" : ""}${it.hdr ? " HDR" : ""} · ${fmtBytes(it.size)}${it.skipped ? ` · <span style="color:var(--muted)" title="Won't be queued by Queue all candidates; queue it yourself to retry">skipped: ${esc(it.skipped)}</span>` : it.transcode_hint && it.transcode_hint.includes("→") ? ` · <span style="color:var(--accent)">${esc(it.transcode_hint)}</span>` : ""}</div></td>
      <td class="arrow">${same ? "" : "→"}</td>
      <td>${same ? '<span class="muted">already correct</span>' : pathHTML(it.proposed_path, rootOf(it.proposed_path))}${it.message ? `<div class="signals">${esc(it.message)}</div>` : ""}</td>
      <td class="hide-sm"><span class="badge">${esc(i.type || "?")}</span> ${what}<br>${conf(it.confidence)} <span class="signals">${esc((i.signals || []).join(", "))}${i.tmdb_match ? " · tmdb ✓" : i.tmdb_match === false ? " · tmdb ✗" : ""}</span></td>
      <td>${badge(it.status)}</td>
      <td><div class="actions">
        ${["proposed", "conflict", "error"].includes(it.status) && it.proposed_path ? `<button class="small primary" data-a="apply" data-id="${it.id}">${it.source === "import" ? "Import" : "Apply"}</button>` : ""}
        <button class="small" data-a="edit" data-id="${it.id}">Edit</button>
        <button class="small" data-a="details" data-id="${it.id}">Details</button>
        ${it.status !== "ignored" ? `<button class="small" data-a="ignore" data-id="${it.id}">Ignore</button>` : `<button class="small" data-a="unignore" data-id="${it.id}">Restore</button>`}
      </div></td></tr>`;
  }).join("")}</tbody></table>`;
}
async function loadLibrary() {
  const params = new URLSearchParams({ source: "scan", q: libQuery, limit: 500 });
  if (libFilter === "transcode") params.set("transcodable", "true"); else if (libFilter) params.set("status", libFilter);
  const r = await api("/api/items?" + params);
  libItems = r.items;
  $("#lib-count").textContent = `${r.total} file${r.total === 1 ? "" : "s"}${r.total > r.items.length ? ` (showing ${r.items.length})` : ""}`;
  setHTML($("#lib-table"), itemRows(r.items, { select: true, empty: libFilter.startsWith("proposed") ? "Nothing needs attention. Run a scan to check the library." : "No files." }));
}
document.addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-a]"); if (!b) return;
  const id = +b.dataset.id, a = b.dataset.a;
  if (a === "apply") return act(() => api(`/api/items/${id}/apply`, { method: "POST", body: {} }), (r) => `${r.result}: ${r.path.split("/").pop()}`);
  if (a === "ignore" || a === "unignore") return act(() => api(`/api/items/${id}/ignore`, { method: "POST", body: { ignore: a === "ignore" } }), a === "ignore" ? "Ignored" : "Restored");
  if (a === "edit") return editDialog(id);
  if (a === "details") return detailsDialog(id);
  if (a === "job") { const [jid, action] = b.dataset.id.split(":"); return act(() => api(`/api/jobs/${jid}/${action}`, { method: "POST" })); }
  if (a === "imp") { const [iid, action] = b.dataset.id.split(":"); return act(() => api(`/api/imports/${iid}/${action}`, { method: "POST" }), action === "retry" ? "Retrying import" : "Done"); }
});

async function findItem(id) {
  const all = [...libItems, ...impItems()];
  return all.find((x) => x.id === id) || (await api(`/api/items?limit=5000`)).items.find((x) => x.id === id);
}
async function editDialog(id) {
  const it = await findItem(id); const i = it.info || {};
  const f = $("#dlg-form");
  f.innerHTML = `<h2>Correct identification</h2>
    <p class="path muted">${esc(it.path)}</p>
    <div class="form">
      <label>Type</label><select name="type"><option value="movie">Movie</option><option value="episode">TV episode</option></select>
      <label>Title / series</label><input name="title" value="${esc(i.title || "")}">
      <label>Year</label><input name="year" value="${esc(i.year || "")}" inputmode="numeric">
      <label class="ep">Season</label><input class="ep" name="season" value="${esc(i.season ?? "")}" inputmode="numeric">
      <label class="ep">Episode(s)</label><input class="ep" name="episodes" value="${esc((i.episodes || []).join(","))}" placeholder="e.g. 3 or 3,4">
      <label class="mv">Edition</label><input class="mv" name="edition" value="${esc(i.edition || "")}" placeholder="e.g. Director's Cut">
      <label>TMDb ID</label><input name="tmdb" value="${esc(i.ids?.tmdb || "")}" inputmode="numeric">
      <label></label><label><input type="checkbox" name="lookup" ${SETTINGS.tmdb_api_key ? "checked" : "disabled"}> Look up / confirm on TMDb${SETTINGS.tmdb_api_key ? "" : " (add an API key in Settings)"}</label>
    </div>
    <div class="dlg-actions"><button value="cancel">Cancel</button><button value="reid" class="hide-sm">Re-run automatic</button><button value="save" class="primary">Save &amp; preview</button></div>`;
  const sync = () => { const ep = f.type.value === "episode"; $$(".ep", f).forEach((x) => x.style.display = ep ? "" : "none"); $$(".mv", f).forEach((x) => x.style.display = ep ? "none" : ""); };
  f.type.value = i.type === "episode" ? "episode" : "movie"; f.type.onchange = sync; sync();
  const dlg = $("#dlg"); dlg.showModal();
  dlg.onclose = async () => {
    if (dlg.returnValue === "save") {
      const body = Object.fromEntries(["type", "title", "year", "season", "episodes", "edition", "tmdb"].map((k) => [k, f[k].value]));
      body.lookup = f.lookup.checked;
      await act(() => api(`/api/items/${id}`, { method: "PUT", body }), (r) => r.proposed_path ? "New name: " + r.proposed_path.split("/").slice(-2).join("/") : "Saved");
    } else if (dlg.returnValue === "reid") {
      await act(() => api(`/api/items/${id}/reidentify`, { method: "POST" }), "Re-identified");
    }
    dlg.onclose = null;
  };
}
async function detailsDialog(id) {
  const it = await findItem(id);
  const f = $("#dlg-form");
  f.innerHTML = `<h2>File details</h2><p class="path">${esc(it.path)}</p><p class="muted">Probing…</p><div class="dlg-actions"><button value="cancel">Close</button></div>`;
  $("#dlg").showModal();
  try {
    const d = await api(`/api/items/${id}/probe`);
    const s = d.summary, p = d.plan;
    f.innerHTML = `<h2>File details</h2><p class="path">${esc(it.path)}</p>
      <div class="kv">
        <div>Video</div><div>${esc(s.vcodec)} ${esc(s.profile || "")} · ${s.width}×${s.height} · ${s.fps} fps · ${s.bit_depth}-bit${s.hdr ? " · HDR (" + esc(s.color_transfer) + ")" : ""}${s.dovi ? " · Dolby Vision" : ""}</div>
        <div>Bitrate</div><div>${(s.vbitrate / 1e6).toFixed(1)} Mb/s video · ${fmtBytes(s.size)} · ${fmtDur(s.duration)}</div>
        <div>Audio</div><div>${(s.audio || []).map((a) => `${esc(a.codec)} ${a.channels}ch${a.lang ? " " + esc(a.lang) : ""}`).join(", ") || "none"}</div>
        <div>Subtitles</div><div>${(s.subs || []).map((x) => `${esc(x.codec)}${x.lang ? " " + esc(x.lang) : ""}`).join(", ") || "none"}</div>
        <div>Transcode plan</div><div><b>${p.transcode ? "yes" : "no"}</b> — ${esc(p.reason)}${p.backend ? ` · ${esc(p.codec)} on ${esc(p.backend)}${p.crf ? ` · CRF ${p.crf}${p.search ? " (refined by VMAF search before encoding)" : ""}` : ""}` : ""}${p.note ? ` (${esc(p.note)})` : ""}</div>
        <div>Identification</div><div class="signals">${esc(JSON.stringify(it.info))}</div>
      </div>
      ${(p.attempts || []).map((a) => `<h4 style="margin:12px 0 4px">${esc(a.label)}</h4><pre>${esc(a.cmd)}</pre>`).join("")}
      <div class="dlg-actions"><button value="cancel">Close</button>
        <button value="force" ${p.transcode ? "" : ""}>${p.transcode ? "Transcode" : "Transcode anyway"}</button></div>`;
    $("#dlg").onclose = () => {
      if ($("#dlg").returnValue === "force") act(() => api("/api/jobs", { method: "POST", body: { item_ids: [id], force: !p.transcode } }), (r) => r.queued ? "Queued" : "Already queued");
      $("#dlg").onclose = null;
    };
  } catch (e) { f.innerHTML += `<p style="color:var(--bad)">${esc(e.message)}</p>`; }
}

// ---------------------------------------------------------------- imports
let IMPORTS = [];
const impItems = () => IMPORTS.flatMap((x) => x.items || []);
$("#poll-now").onclick = () => act(() => api("/api/watcher/poll", { method: "POST" }), "Checking downloads folder…");
async function loadImports() {
  IMPORTS = await api("/api/imports");
  const w = STATUS.watcher || {};
  $("#watch-state").innerHTML = w.enabled ? (w.error ? `<span style="color:var(--bad)">${esc(w.error)}</span>` : `Watching <span class="path">${esc(SETTINGS.downloads_dir)}</span> · last check ${ago(w.last_poll) || "pending"}`) : "Watcher disabled in Settings";
  if (!IMPORTS.length) { setHTML($("#imports-list"), `<div class="empty">No downloads seen yet. Finished torrents in the downloads folder show up here.</div>`); return; }
  setHTML($("#imports-list"), IMPORTS.map((imp) => `
    <div class="entry"><div class="head">${badge(imp.status)}<span class="name">${esc(imp.entry)}</span>
      <span class="muted">${esc(imp.message || "")} · ${ago(imp.updated)}</span>
      <button class="small" data-a="imp" data-id="${imp.id}:retry">Retry</button>
      ${imp.status !== "ignored" ? `<button class="small" data-a="imp" data-id="${imp.id}:ignore">Ignore</button>` : ""}
      <button class="small danger" data-a="imp" data-id="${imp.id}:forget" title="Forget it (it will be picked up again on the next check)">Forget</button></div>
      ${imp.items.length ? itemRows(imp.items) : ""}</div>`).join(""));
}

// ---------------------------------------------------------------- queue
$("#q-toggle").onclick = () => act(() => api(`/api/queue/${STATUS.queue?.paused ? "resume" : "pause"}`, { method: "POST" }));
$("#q-candidates").onclick = () => act(() => api("/api/jobs", { method: "POST", body: { all_candidates: true } }), (r) => `Queued ${r.queued} files`);
$("#q-clear").onclick = () => act(() => api("/api/jobs", { method: "DELETE" }), "Cleared");
async function loadQueue() {
  const jobs = await api("/api/jobs");
  const q = STATUS.queue || {};
  $("#q-toggle").textContent = q.paused ? "▶ Resume queue" : "⏸ Pause queue";
  $("#q-state").textContent = q.paused ? "Queue paused — running jobs finish" : !q.in_window ? `Outside transcode window (${SETTINGS.transcode_window})` : "";
  if (!jobs.length) { setHTML($("#queue-list"), `<div class="empty">Queue is empty. Queue files from the Library tab or let imports add them.</div>`); return; }
  setHTML($("#queue-list"), jobs.map((j) => {
    const name = j.path.split("/").pop(), dir = j.path.split("/").slice(-3, -1).join("/");
    const active = j.status === "running" || j.status === "paused";
    const saved = j.new_size && j.orig_size ? ` · ${fmtBytes(j.orig_size)} → ${fmtBytes(j.new_size)}` : j.orig_size ? ` · ${fmtBytes(j.orig_size)}` : "";
    return `<div class="job"><div class="head">${badge(j.status)}<span class="name">${esc(name)} <span class="muted" style="font-weight:400">${esc(dir)}</span></span>
      <div class="actions">
        ${j.status === "running" ? `<button class="small" data-a="job" data-id="${j.id}:pause">Pause</button>` : ""}
        ${j.status === "paused" ? `<button class="small" data-a="job" data-id="${j.id}:resume">Resume</button>` : ""}
        ${j.status === "queued" ? `<button class="small" data-a="job" data-id="${j.id}:top">Move to top</button>` : ""}
        ${["queued", "running", "paused"].includes(j.status) ? `<button class="small danger" data-a="job" data-id="${j.id}:cancel">Cancel</button>` : ""}
        ${["failed", "cancelled"].includes(j.status) ? `<button class="small" data-a="job" data-id="${j.id}:retry">Retry</button>` : ""}
        ${j.status === "skipped" ? `<button class="small" data-a="job" data-id="${j.id}:force">Transcode anyway</button>` : ""}
      </div></div>
      ${active ? `<div class="bar"><b style="width:${(j.progress * 100).toFixed(1)}%"></b></div>` : ""}
      <div class="meta">${active ? `${(j.progress * 100).toFixed(1)}% · ${j.fps ? j.fps.toFixed(0) + " fps" : ""} ${esc(j.speed || "")}${j.eta ? " · " + fmtDur(j.eta) + " left" : ""} · ` : ""}${esc(j.message || j.reason || "")}${saved}${j.finished ? " · " + ago(j.finished) : ""}</div></div>`;
  }).join(""));
}

// ---------------------------------------------------------------- settings
const FIELDS = [
  ["Folders (paths inside the container)", [
    ["movies_dir", "Movies library", "text", "Jellyfin movies library host path, e.g. mounted at /media/movies"],
    ["tv_dir", "TV library", "text", "Jellyfin shows library host path"],
    ["extra_scan_dirs", "Extra folders to sort", "lines", "One per line. Files found here get proposals that move them into Movies/TV."],
    ["downloads_dir", "qBittorrent finished folder", "text", ""],
  ]],
  ["Downloads watcher", [
    ["watcher_enabled", "Watch downloads", "bool", ""],
    ["import_mode", "Import by", "select:copy|hardlink|move", "Copy keeps the torrent seeding. Hardlink only works when downloads and library share one host-path mount."],
    ["auto_import_min_confidence", "Auto-import confidence", "number", "0–1. Below this a download waits for your review."],
    ["auto_transcode_imports", "Transcode imports automatically", "bool", ""],
    ["watcher_interval", "Check every (s)", "number", ""],
    ["watcher_settle_seconds", "Settle time (s)", "number", "Files must be unchanged this long before importing."],
    ["min_video_size_mb", "Min video size (MB)", "number", "Smaller videos in a download are treated as samples."],
  ]],
  ["Naming", [
    ["naming_include_ids", "Add [tmdbid-…] to folders", "bool", "Helps Jellyfin match exactly. Needs TMDb or an existing ID."],
    ["naming_include_episode_title", "Episode titles in file names", "bool", ""],
    ["auto_apply_scan_min_confidence", "Auto-apply scan proposals ≥", "number", "0 = always wait for approval."],
  ]],
  ["Transcoding", [
    ["target_codec", "Codec", "select:auto|av1|hevc|h264", "auto: AV1 (most efficient) for SDR/HDR10/HLG; HEVC for Dolby Vision and HDR10+ so players keep the dynamic HDR."],
    ["preferred_backend", "Encoder", "select:software|auto|nvenc|qsv|vaapi", "software: SVT-AV1/x265 on the CPU (best quality per bit, keeps all HDR). auto: GPU for SDR titles only."],
    ["vmaf_target", "Quality target (VMAF)", "number", "Each title gets the highest CRF whose sample encodes still score this. 95 ≈ visually lossless; 0 = fixed CRF."],
    ["av1_preset", "AV1 speed preset", "number", "SVT-AV1 0–13; lower is slower and slightly more efficient. 5 is a good balance."],
    ["x265_preset", "HEVC preset", "select:slow|medium|slower|veryslow|fast", ""],
    ["quality_bias", "CRF bias (fixed mode)", "number", "Only used when the VMAF target is 0: added to the default CRF."],
    ["max_height", "Max height", "number", "0 keeps the source resolution."],
    ["hdr_policy", "HDR10 / HLG", "select:transcode|skip", "transcode keeps 10-bit, colour tags, mastering display and light levels."],
    ["dv_policy", "Dolby Vision", "select:convert|skip", "convert: profile 7/8 → HEVC profile 8.1 (HDR10 base + DV metadata; the profile 7 enhancement layer is dropped)."],
    ["audio_policy", "Audio", "select:copy|eac3_lossless", "eac3_lossless converts TrueHD/DTS-HD/FLAC/PCM to EAC3 640k."],
    ["min_savings_pct", "Min savings (%)", "number", "Results that save less are discarded and the original kept."],
    ["concurrent_jobs", "Parallel jobs", "number", ""],
    ["transcode_window", "Only run between", "text", "e.g. 01:00-07:00. Empty = any time."],
    ["keep_originals", "Keep originals", "bool", "Move replaced files into .mediaman-trash in the library instead of deleting them."],
    ["device_path", "GPU render node", "text", ""],
  ]],
  ["Integrations (optional)", [
    ["tmdb_api_key", "TMDb API key", "secret", "v3 key or v4 read token — confirms titles/years, adds IDs, raises confidence.", "tmdb"],
    ["jellyfin_url", "Jellyfin URL", "text", "e.g. https://nas.example:8920 — refreshes the library after changes."],
    ["jellyfin_api_key", "Jellyfin API key", "secret", "Dashboard → API Keys", "jellyfin"],
    ["jellyfin_verify_tls", "Verify Jellyfin TLS", "bool", ""],
    ["qbittorrent_url", "qBittorrent Web UI", "text", "Optional: only import torrents qBittorrent reports complete."],
    ["qbittorrent_username", "qBittorrent user", "text", ""],
    ["qbittorrent_password", "qBittorrent password", "secret", "", "qbittorrent"],
  ]],
];
function renderSettings() {
  const s = SETTINGS;
  $("#settings").innerHTML = `<form id="set-form">${FIELDS.map(([title, fields]) => `
    <div class="card settings-group"><h3>${esc(title)}</h3><div class="form">
    ${fields.map(([k, label, type, hint, test]) => {
      let input;
      if (type === "bool") input = `<label style="color:var(--text)"><input type="checkbox" name="${k}" ${s[k] ? "checked" : ""}> enabled</label>`;
      else if (type.startsWith("select:")) input = `<select name="${k}">${type.slice(7).split("|").map((o) => `<option ${s[k] === o ? "selected" : ""}>${o}</option>`).join("")}</select>`;
      else if (type === "lines") input = `<textarea name="${k}" rows="3">${esc((s[k] || []).join("\n"))}</textarea>`;
      else input = `<div style="display:flex;gap:6px"><input style="flex:1" name="${k}" type="${type === "secret" ? "password" : type === "number" ? "number" : "text"}" step="any" value="${esc(s[k] ?? "")}" autocomplete="off">${test ? `<button type="button" class="small" data-test="${test}">Test</button>` : ""}</div>`;
      return `<label>${esc(label)}</label>${input}${hint ? `<div class="hint">${esc(hint)}</div>` : ""}`;
    }).join("")}</div></div>`).join("")}
    <div class="toolbar"><button class="primary" type="submit">Save settings</button></div></form>`;
  $("#set-form").onsubmit = async (e) => {
    e.preventDefault();
    const body = {};
    FIELDS.flatMap((g) => g[1]).forEach(([k, , type]) => { const el = e.target[k]; body[k] = type === "bool" ? el.checked : type === "number" ? Number(el.value) : el.value; });
    SETTINGS = await act(() => api("/api/settings", { method: "PUT", body }), "Settings saved") || SETTINGS;
  };
  $$("[data-test]").forEach((b) => b.onclick = async () => {
    await api("/api/settings", { method: "PUT", body: Object.fromEntries(FIELDS.flatMap((g) => g[1]).filter((f) => f[0].startsWith(b.dataset.test)).map(([k, , t]) => [k, t === "bool" ? $("#set-form")[k].checked : $("#set-form")[k].value])) });
    const r = await api(`/api/settings/test/${b.dataset.test}`, { method: "POST" }).catch((e) => ({ result: e.message }));
    toast(`${b.dataset.test}: ${r.result}`);
  });
}

// ---------------------------------------------------------------- log
let logLevel = "";
$$("#log-filters button").forEach((b) => b.onclick = () => { $$("#log-filters button").forEach((x) => x.classList.toggle("active", x === b)); logLevel = b.dataset.l; loadLog(); });
async function loadLog() {
  const rows = await api("/api/log?limit=400&level=" + logLevel);
  setHTML($("#log-list"), `<div class="card" style="padding:0">${rows.map((r) => `<div class="log-row"><span class="muted">${new Date(r.ts * 1000).toLocaleString()}</span> <span class="lv-${r.level}">${r.level.toUpperCase()}</span> <b>${esc(r.area)}</b> ${esc(r.msg)}</div>`).join("") || '<div class="empty">No log entries.</div>'}</div>`);
}

// ---------------------------------------------------------------- refresh loop
let busy = false;
async function refresh(force) {
  if (busy) return; busy = true;
  try {
    STATUS = await api("/api/status");
    if (force || !SETTINGS.movies_dir) SETTINGS = await api("/api/settings");
    if (tab === "overview") renderOverview();
    else if (tab === "library" && (force || !document.querySelector("#lib-table input:checked"))) await loadLibrary();
    else if (tab === "imports") await loadImports();
    else if (tab === "queue") await loadQueue();
    else if (tab === "settings" && force) renderSettings();
    else if (tab === "log") await loadLog();
  } catch (e) { console.error(e); } finally { busy = false; }
}
setInterval(() => { if (!$("#dlg").open && tab !== "settings") refresh(); }, 3000);
showTab(tab);
