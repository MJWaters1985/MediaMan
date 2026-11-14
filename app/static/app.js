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
  const res = s.resources || {};
  const be = hw.backends || {};
  const encRows = Object.entries(be).map(([b, caps]) =>
    `<div>${esc(b)}</div><div>${Object.entries(caps).map(([c, v]) => `${c}${v["10bit"] ? " (10-bit)" : ""}`).join(", ")}</div>`).join("");
  const best = hw.best?.[s.target_codec];
  const pathRows = Object.entries(s.paths || {}).map(([k, v]) =>
    `<div>${esc(k.replace("_dir", ""))}</div><div class="pathcheck"><span class="path">${esc(v.path)}</span> ${v.exists ? (v.writable ? '<span class="ok">✓ writable</span>' : '<span class="bad">read-only</span>') : '<span class="bad">missing</span>'}${v.free != null ? ` <span class="muted">· ${(v.free / 1024 ** 3).toFixed(0)} GB free</span>` : ""}</div>`).join("");
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
      ${(s.copies || []).map((c) => copyBar(c, true)).join("")}
      <div class="kv" style="margin-top:10px"><div>Status</div><div>${s.watcher?.enabled ? (s.watcher.error ? `<span style="color:var(--bad)">${esc(s.watcher.error)}</span>` : "watching") : "disabled"}</div>
      <div>Last check</div><div>${ago(s.watcher?.last_poll) || "—"}</div><div>Waiting</div><div>${imp.waiting || 0}</div><div>Imported</div><div>${imp.imported || 0}</div></div>
      <div class="toolbar" style="margin:12px 0 0"><button onclick="showTab('imports')">Open downloads</button></div>
    </div>
    <div class="card"><h3>Hardware</h3>
      <div class="kv">
        <div>Codec policy</div><div><b>${esc(s.target_codec === "auto" ? "auto (AV1 / HEVC for DV & HDR10+)" : (s.target_codec || "").toUpperCase())}</b></div>
        <div>GPU</div><div>${(hw.devices || []).map((d) => `${esc(d.vendor)} <span class="path">${esc(d.path)}</span>`).join("<br>") || "none passed through"}</div>
        <div>Limits</div><div>${res.cpus || hw.cpu_count || "?"} CPUs${(res.memory || hw.memory) ? ` · ${((res.memory || hw.memory) / 1024 ** 3).toFixed(0)} GB memory` : ""}</div>
        <div>Tools</div><div>${Object.entries(hw.tools || {}).map(([k, v]) => `${esc(k)} ${v ? "✓" : "✗"}`).join(" · ")}</div>
        ${encRows}
      </div>
      ${resourceNote(res)}
      ${hw.error ? `<p style="color:var(--bad)">${esc(hw.error)}</p>` : ""}
      <div class="toolbar" style="margin:12px 0 0"><button id="ov-hw">Re-detect</button></div>
    </div>
    <div class="card" style="grid-column:1/-1"><h3>Folders</h3><div class="kv">${pathRows}${tlsRow(s.tls)}</div></div>
  </div>`);
  $("#ov-scan").onclick = () => act(() => api("/api/scan", { method: "POST", body: {} }), "Scan started");
  $("#ov-cand").onclick = () => act(() => api("/api/jobs", { method: "POST", body: { all_candidates: true } }), (r) => `Queued ${r.queued} files`);
  $("#ov-hw").onclick = () => act(() => api("/api/hardware/detect", { method: "POST" }), "Hardware re-detected");
}

// CPU/memory limits too low for 4K transcodes, and where to change them (TrueNAS, not MediaMan)
function resourceNote(r) {
  if (!r?.warnings?.length) return "";
  return `<div class="note warn">${r.warnings.map((w) => `<div>${esc(w)}</div>`).join("")}<div class="muted">${esc(r.hint)}</div></div>`;
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

function lookupsOn() { return !!(SETTINGS.identify_with_jellyfin && SETTINGS.jellyfin_url && SETTINGS.jellyfin_api_key); }

function rootOf(p) {
  for (const k of ["movies_dir", "tv_dir", "downloads_dir", ...(SETTINGS.movies_more_dirs || []), ...(SETTINGS.tv_more_dirs || []), ...(SETTINGS.extra_scan_dirs || [])]) {
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
      <td>${pathHTML(it.path, rootOf(it.path))}<div class="signals">${esc(it.codec || "")}${it.height ? " " + it.height + "p" : ""}${it.hdr ? " HDR" : ""} · ${fmtBytes(it.size)}${it.job ? ` · <span style="color:var(--accent)">${it.job.status === "running" ? `transcoding ${Math.round((it.job.progress || 0) * 100)}%` : esc(it.job.status)}</span>` : it.skipped ? ` · <span style="color:var(--muted)" title="Won't be queued by Queue all candidates; queue it yourself to retry">skipped: ${esc(it.skipped)}</span>` : it.transcode_hint && it.transcode_hint.includes("→") ? ` · <span style="color:var(--accent)">${esc(it.transcode_hint)}</span>` : ""}</div></td>
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
      <label></label><label><input type="checkbox" name="lookup" ${lookupsOn() ? "checked" : "disabled"}> Look up / confirm on TMDb${lookupsOn() ? " (via Jellyfin)" : " (set up Jellyfin in Settings)"}</label>
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
function copyBar(c, compact) {
  const pct = c.total ? (100 * c.done / c.total) : 0;
  const what = c.mode === "move" ? "Moving" : "Copying";
  const detail = `${fmtBytes(c.done)} of ${fmtBytes(c.total)} (${pct.toFixed(0)}%)` +
    (c.speed ? ` · ${fmtBytes(c.speed)}/s` : c.done ? " · waiting for the disk" : "") + (c.eta != null ? ` · ${fmtDur(c.eta)} left` : "");
  return `<div class="copy${compact ? " compact" : ""}"><div class="muted">${what} <b>${esc(c.name)}</b> into the library</div>
    <div class="bar"><b style="width:${pct.toFixed(1)}%"></b></div><div class="meta">${detail}</div></div>`;
}

async function loadImports() {
  IMPORTS = await api("/api/imports");
  const copies = STATUS.copies || [];
  const dl = (SETTINGS.downloads_dir || "").replace(/\/$/, "");
  const copiesFor = (imp) => copies.filter((c) => c.src === dl + "/" + imp.entry || c.src.startsWith(dl + "/" + imp.entry + "/"));
  const other = copies.filter((c) => !IMPORTS.some((imp) => copiesFor(imp).includes(c)));
  const w = STATUS.watcher || {};
  $("#watch-state").innerHTML = w.enabled ? (w.error ? `<span style="color:var(--bad)">${esc(w.error)}</span>` : `Watching <span class="path">${esc(SETTINGS.downloads_dir)}</span> · last check ${ago(w.last_poll) || "pending"}`) : "Watcher disabled in Settings";
  if (!IMPORTS.length && !other.length) { setHTML($("#imports-list"), `<div class="empty">No downloads seen yet. Finished torrents in the downloads folder show up here.</div>`); return; }
  setHTML($("#imports-list"), other.map((c) => `<div class="entry">${copyBar(c)}</div>`).join("") + IMPORTS.map((imp) => `
    <div class="entry"><div class="head">${badge(imp.status)}<span class="name">${esc(imp.entry)}</span>
      <span class="muted">${esc(imp.message || "")} · ${ago(imp.updated)}</span>
      <button class="small" data-a="imp" data-id="${imp.id}:retry">Retry</button>
      ${imp.status !== "ignored" ? `<button class="small" data-a="imp" data-id="${imp.id}:ignore">Ignore</button>` : ""}
      <button class="small danger" data-a="imp" data-id="${imp.id}:forget" title="Forget it (it will be picked up again on the next check)">Forget</button></div>
      ${copiesFor(imp).map((c) => copyBar(c)).join("")}
      ${imp.items.length ? itemRows(imp.items) : ""}</div>`).join(""));
}

// ---------------------------------------------------------------- queue
$("#q-toggle").onclick = () => act(() => api(`/api/queue/${STATUS.queue?.paused ? "resume" : "pause"}`, { method: "POST" }));
$("#q-candidates").onclick = () => act(() => api("/api/jobs", { method: "POST", body: { all_candidates: true } }), (r) => `Queued ${r.queued} files`);
$("#q-clear").onclick = () => act(() => api("/api/jobs", { method: "DELETE" }), "Cleared");
// a running job: which step it's on, that step's own progress and time, and (while encoding) speed and time left
function jobProgress(j) {
  const st = j.stage;
  const encoding = !st || st.name === "Encoding";
  const p = encoding ? j.progress : st.progress;
  const bar = p != null ? `<div class="bar"><b style="width:${(p * 100).toFixed(1)}%"></b></div>` : `<div class="bar busy"><b></b></div>`;
  const stepLine = st ? `<div class="step"><b>Step ${st.step} of ${st.of} · ${esc(st.name)}</b>${st.detail ? " — " + esc(st.detail) : ""}${p != null && !encoding ? ` · ${(p * 100).toFixed(0)}%` : ""} · ${fmtDur(st.elapsed)}</div>` : "";
  // steps that read a big file: how much, how fast, and how long is left at that speed
  const b = !encoding && st?.bytes;
  const readLine = b ? `<div class="meta">${fmtBytes(b.done)} of ${fmtBytes(b.total)}${b.rate ? ` · ${fmtBytes(b.rate)}/s` : ""}${b.eta != null ? ` · about ${fmtDur(Math.max(b.eta, 1))} left` : ""}</div>` : "";
  const enc = encoding ? `<div class="meta">${(j.progress * 100).toFixed(1)}% of the film${j.fps ? " · " + j.fps.toFixed(0) + " fps" : ""} ${esc(j.speed || "")}${j.eta ? " · " + fmtDur(j.eta) + " left" : ""}${st ? "" : " · " + esc(j.message || "")}</div>` : "";
  const steps = st ? `<div class="steps">${st.steps.map((n, i) => `<span class="${i + 1 < st.step ? "done" : i + 1 === st.step ? "on" : ""}">${esc(n)}</span>`).join("")}</div>` : "";
  return stepLine + bar + readLine + enc + steps;
}

// a finished job: how long each step took (the encode, and the steps after it the next file overlaps with)
function stepTimes(t) {
  if (!t) return "";
  const parts = Object.entries(t).filter(([, s]) => s >= 1).map(([n, s]) => `${esc(n)} ${fmtDur(Math.round(s))}`);
  return parts.length ? `<div class="meta">Time: ${parts.join(" · ")}</div>` : "";
}

async function loadQueue() {
  const jobs = await api("/api/jobs");
  const q = STATUS.queue || {};
  $("#q-toggle").textContent = q.paused ? "▶ Resume queue" : "⏸ Pause queue";
  $("#q-state").textContent = q.paused ? "Queue paused — the running job is frozen and continues when you resume" : !q.in_window ? `Outside transcode window (${SETTINGS.transcode_window}) — the running job is frozen until it opens` : "";
  setHTML($("#q-resources"), resourceNote(STATUS.resources));
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
      ${active ? jobProgress(j) : `<div class="meta">${esc(j.message || j.reason || "")}${saved}${j.finished ? " · " + ago(j.finished) : ""}</div>${stepTimes(j.steps)}`}</div>`;
  }).join(""));
}

// ---------------------------------------------------------------- settings
const FIELDS = [
  ["Folders (paths inside the container)", [
    ["movies_dir", "Movies library", "folder", "The folder Jellyfin's Movies library uses. Browse lists the folders mounted into MediaMan."],
    ["movies_more_dirs", "More Movies folders", "folders", "Optional, one per line: other Movies folders (e.g. on another pool). Files there stay where they are; new imports go to the folder that already has that title, otherwise the Movies library above."],
    ["tv_dir", "TV library", "folder", "The folder Jellyfin's Shows library uses."],
    ["tv_more_dirs", "More TV folders", "folders", "Optional, one per line. A series is never split: new episodes go to whichever folder already has the show."],
    ["extra_scan_dirs", "Extra folders to sort", "folders", "One per line. Files found here get proposals that move them into Movies/TV."],
    ["downloads_dir", "qBittorrent finished folder", "folder", "Where qBittorrent puts finished downloads."],
  ]],
  ["Downloads watcher", [
    ["watcher_enabled", "Watch downloads", "bool", ""],
    ["import_mode", "Import by", "select:copy|hardlink|move", "Copy keeps the torrent seeding. Hardlink only works when downloads and library share one host-path mount."],
    ["auto_import_min_confidence", "Import downloads automatically", "choice:0.9=When at least 90% sure of the title|0.75=When at least 75% sure (recommended)|0.6=When at least 60% sure|2=Never — always ask me", "Downloads MediaMan is less sure about wait under Downloads for you to check. The Library shows how sure it is about each file."],
    ["auto_transcode_imports", "Transcode imports automatically", "bool", "Off: imports are only queued when you choose to transcode them."],
    ["watcher_interval", "Check every (s)", "number", ""],
    ["watcher_settle_seconds", "Settle time (s)", "number", "Files must be unchanged this long before importing."],
    ["min_video_size_mb", "Min video size (MB)", "number", "Smaller videos in a download are treated as samples."],
  ]],
  ["Naming", [
    ["naming_include_ids", "Add [tmdbid-…] to folders", "bool", "Helps Jellyfin match exactly. Needs Jellyfin lookups or an existing ID."],
    ["naming_include_episode_title", "Episode titles in file names", "bool", ""],
    ["auto_apply_scan_min_confidence", "Rename library files automatically", "choice:0=Never — always ask me (recommended)|0.95=When at least 95% sure of the title|0.9=When at least 90% sure|0.8=When at least 80% sure", "After a library scan, renames MediaMan is this sure about happen without asking; the rest wait in the Library for your approval."],
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
    ["dv_policy", "Dolby Vision", "select:mel_only|convert|skip", "Everything converts to HEVC profile 8.1 (HDR10 base + DV metadata). mel_only: profile 7 discs with a full enhancement layer (FEL) are left alone, because converting drops that extra picture detail; MEL discs and profile 8 convert. convert: all of them. skip: none."],
    ["audio_policy", "Audio", "select:copy|eac3_lossless", "eac3_lossless converts TrueHD/DTS-HD/FLAC/PCM to EAC3 640k."],
    ["min_savings_pct", "Min savings (%)", "number", "Results that save less are discarded and the original kept."],
    ["concurrent_jobs", "Parallel jobs", "number", ""],
    ["overlap_finishing", "Start the next file while one finishes", "bool", "On: once a file's encoding is done, the next file starts while the first is still being put together (joining, HDR metadata, audio and subtitles, the final check). Those steps mostly copy files, so the CPU would otherwise sit idle. At most one file finishes per parallel job, and the space it still needs is kept free."],
    ["resume_queue_on_startup", "Resume transcoding after a restart", "bool", "On: after MediaMan, the app or the NAS restarts, interrupted jobs continue (chunked encodes from their last finished chunk). Off: the queue comes back paused until you press Resume queue. A queue you paused yourself stays paused either way."],
    ["transcode_window", "Only run between", "text", "e.g. 01:00-07:00. Empty = any time. Outside the window a running job is frozen mid-film and continues when it reopens."],
    ["keep_originals", "Keep originals", "bool", "Move replaced files into .mediaman-trash in the library instead of deleting them."],
    ["encoder_priority", "Encoder priority", "select:low|normal", "low: encoders yield CPU and disk to everything else on the NAS (file shares, Jellyfin playback). Encodes only slow down when the NAS is busy."],
    ["device_path", "GPU render node", "text", "Empty: use the GPU whose encoders work (checked at startup)."],
  ]],
  ["Integrations (optional)", [
    ["jellyfin_url", "Jellyfin URL", "text", "e.g. https://nas.example:8920 — refreshes the library after changes and looks up titles."],
    ["jellyfin_api_key", "Jellyfin API key", "secret", "Dashboard → API Keys", "jellyfin"],
    ["jellyfin_verify_tls", "Verify Jellyfin TLS", "bool", ""],
    ["identify_with_jellyfin", "Look up titles via Jellyfin", "bool", "Uses Jellyfin's TMDb search (no TMDb account needed) to confirm titles and years, add TMDb IDs and raise confidence."],
    ["qbittorrent_url", "qBittorrent Web UI", "text", "Optional: only import torrents qBittorrent reports complete."],
    ["qbittorrent_username", "qBittorrent user", "text", ""],
    ["qbittorrent_password", "qBittorrent password", "secret", "", "qbittorrent"],
  ]],
];
const REPO = "https://github.com/MJWaters1985/MediaMan";
const COFFEE = "https://buymeacoffee.com/mjwaters1985";

function aboutCard() {
  const ext = (href, text) => `<a href="${href}" target="_blank" rel="noopener noreferrer">${text}</a>`;
  return `<div class="card settings-group about"><h3>About</h3>
    <div class="about-row"><img src="/static/icon.svg" alt="" width="56" height="56">
      <div><div><b>MediaMan ${esc(STATUS.version || "")}</b></div>
        <div class="muted">Organizes, imports and transcodes a Jellyfin library. Free software under the
        ${ext(REPO + "/blob/main/LICENSE", "GNU GPL v3 or later")}. Copyright © 2026 Mike Waters.</div>
        <div class="about-links">${ext(REPO, "Source code")} · ${ext(REPO + "/blob/main/CHANGELOG.md", "What's new")} · ${ext(REPO + "/issues", "Report a problem")}</div>
      </div></div>
    <p class="muted" style="margin:14px 0 8px">MediaMan is a free, one-person project. If it saves you time or disk space, you can support it:</p>
    <a class="coffee" href="${COFFEE}" target="_blank" rel="noopener noreferrer">☕ Buy me a coffee</a>
  </div>`;
}

function passwordCard() {
  const a = STATUS.auth || {};
  return `<div class="card settings-group"><h3>Web UI password</h3>
    <form id="pw-form" class="form" autocomplete="off">
      <label>Status</label><div>${a.enabled ? "<b>On</b> — your browser asks for it (any user name)" : "<b>Off</b> — anyone who can reach this page can use MediaMan"}</div>
      ${a.enabled ? '<label>Current password</label><input type="password" name="current" autocomplete="current-password">' : ""}
      <label>New password</label><input type="password" name="new" autocomplete="new-password">
      <label>Repeat new password</label><input type="password" name="new2" autocomplete="new-password">
      <div class="hint">At least 8 characters. Stored only as a salted hash. Forgotten it? Delete <code>auth.json</code> from MediaMan's config folder and restart the app.</div>
      <label></label><div style="display:flex;gap:8px"><button class="primary" type="submit">${a.enabled ? "Change password" : "Set password"}</button>${a.enabled ? '<button type="button" class="danger" id="pw-remove">Remove password</button>' : ""}</div>
    </form></div>`;
}

async function savePassword(current, next) {
  const r = await api("/api/auth/password", { method: "POST", body: { current, new: next } }).catch((e) => { toast("⚠ " + e.message); return null; });
  if (!r) return;
  toast(next ? "Password saved — sign in with it when your browser asks" : "Password removed");
  setTimeout(() => location.reload(), 1200);
}

// Folder picker: only folders mounted into the container (TrueNAS host paths / volumes) can be browsed.
async function folderPicker(start, onPick) {
  const dlg = $("#dlg"), f = $("#dlg-form");
  let cur = null;
  const show = async (path, fallback) => {
    let d;
    try { d = await api("/api/fs?path=" + encodeURIComponent(path || "")); }
    catch (e) { if (fallback !== undefined) return show(fallback); toast("⚠ " + e.message); return; }
    cur = d.path;
    const crumbs = d.path ? d.path.split("/").filter(Boolean).map((n, i, a) => "/" + a.slice(0, i + 1).join("/")) : [];
    const inRoot = (p) => d.roots.some((r) => r === "/" || p === r || p.startsWith(r.replace(/\/$/, "") + "/"));
    f.innerHTML = `<h2>Choose a folder</h2>
      <div class="crumbs"><button type="button" class="link" data-go="">Mounted folders</button>${crumbs.map((c) => inRoot(c)
        ? ` / <button type="button" class="link" data-go="${esc(c)}">${esc(c.split("/").pop())}</button>` : ` / <span class="muted">${esc(c.split("/").pop())}</span>`).join("")}</div>
      <div class="folder-list">
        ${d.parent ? `<button type="button" class="folder" data-go="${esc(d.parent)}">↑ Up</button>` : ""}
        ${d.dirs.map((x) => `<button type="button" class="folder" data-go="${esc(x.path)}">📁 ${esc(x.name)}</button>`).join("") || '<div class="muted" style="padding:10px">No subfolders</div>'}
        ${d.truncated ? '<div class="muted" style="padding:6px 10px">Showing the first 2000 folders</div>' : ""}
      </div>
      <p class="muted" style="margin:10px 0 0;font-size:12px">${d.path ? `${esc(d.path)} — ${d.writable ? "writable" : "<b>not writable</b> by MediaMan"}` : "These are the folders mounted into MediaMan. Add more in the app's storage settings in TrueNAS."}</p>
      <div class="dlg-actions">${d.path && d.writable ? '<button type="button" id="fp-new" style="margin-right:auto">New folder…</button>' : ""}<button value="cancel">Cancel</button><button value="pick" class="primary" ${d.path ? "" : "disabled"}>Use this folder</button></div>`;
    $$("[data-go]", f).forEach((b) => b.onclick = () => show(b.dataset.go));
    if ($("#fp-new", f)) $("#fp-new", f).onclick = async () => {
      const name = (prompt(`New folder inside ${d.path}:`) || "").trim();
      if (!name) return;
      if (name.includes("/") || name.startsWith(".")) return toast("⚠ Use a plain folder name (no / and not starting with a dot)");
      try { const n = await api("/api/fs/mkdir", { method: "POST", body: { path: d.path.replace(/\/$/, "") + "/" + name } }); show(n.path); }
      catch (e) { toast("⚠ " + e.message); }
    };
  };
  dlg.onclose = () => { if (dlg.returnValue === "pick" && cur) onPick(cur); dlg.onclose = null; };
  dlg.returnValue = "";
  await show(start, "");
  if (!dlg.open) dlg.showModal();
}

function renderSettings() {
  const s = SETTINGS;
  $("#settings").innerHTML = `<div class="toolbar"><span class="spacer"></span><button type="button" class="link" id="rerun-setup">Run the setup wizard again</button></div>` + passwordCard() + `<form id="set-form">${FIELDS.map(([title, fields]) => `
    <div class="card settings-group"><h3>${esc(title)}</h3><div class="form">
    ${fields.map(([k, label, type, hint, test]) => {
      let input;
      if (type === "bool") input = `<label style="color:var(--text)"><input type="checkbox" name="${k}" ${s[k] ? "checked" : ""}> enabled</label>`;
      else if (type.startsWith("choice:")) {
        const opts = type.slice(7).split("|").map((o) => o.split("="));
        const cur = Number(s[k]);
        if (!opts.some(([v]) => Number(v) === cur)) opts.push([String(cur), `Custom (${Math.round(cur * 100)}%)`]);
        input = `<select name="${k}">${opts.map(([v, l]) => `<option value="${esc(v)}" ${Number(v) === cur ? "selected" : ""}>${esc(l)}</option>`).join("")}</select>`;
      }
      else if (type.startsWith("select:")) input = `<select name="${k}">${type.slice(7).split("|").map((o) => `<option ${s[k] === o ? "selected" : ""}>${o}</option>`).join("")}</select>`;
      else if (type === "lines") input = `<textarea name="${k}" rows="3">${esc((s[k] || []).join("\n"))}</textarea>`;
      else if (type === "folders") input = `<div style="display:flex;gap:6px;align-items:flex-start"><textarea style="flex:1" name="${k}" rows="3">${esc((s[k] || []).join("\n"))}</textarea><button type="button" class="small" data-browse="${k}" data-add="1">Add folder…</button></div>`;
      else if (type === "folder") input = `<div style="display:flex;gap:6px"><input style="flex:1" name="${k}" type="text" value="${esc(s[k] ?? "")}" autocomplete="off"><button type="button" class="small" data-browse="${k}">Browse…</button></div>`;
      else input = `<div style="display:flex;gap:6px"><input style="flex:1" name="${k}" type="${type === "secret" ? "password" : type === "number" ? "number" : "text"}" step="any" value="${esc(s[k] ?? "")}" autocomplete="off">${test ? `<button type="button" class="small" data-test="${test}">Test</button>` : ""}</div>`;
      return `<label>${esc(label)}</label>${input}${hint ? `<div class="hint">${esc(hint)}</div>` : ""}`;
    }).join("")}</div></div>`).join("")}
    <div class="toolbar"><button class="primary" type="submit">Save settings</button></div></form>` + aboutCard();
  $("#rerun-setup").onclick = () => startSetup();
  const pw = $("#pw-form");
  if (pw) {
    pw.onsubmit = (e) => {
      e.preventDefault();
      const f = e.target;
      if (f.new.value.length < 8) return toast("⚠ Use at least 8 characters");
      if (f.new.value !== f.new2.value) return toast("⚠ The new passwords don't match");
      savePassword(f.current ? f.current.value : "", f.new.value);
    };
    const rm = $("#pw-remove");
    if (rm) rm.onclick = () => {
      if (!pw.current.value) return toast("⚠ Enter the current password to remove it");
      if (confirm("Remove the password? Anyone who can reach MediaMan will be able to use it.")) savePassword(pw.current.value, "");
    };
  }
  $("#set-form").onsubmit = async (e) => {
    e.preventDefault();
    const body = {};
    FIELDS.flatMap((g) => g[1]).forEach(([k, , type]) => { const el = e.target[k]; body[k] = type === "bool" ? el.checked : (type === "number" || type.startsWith("choice:")) ? Number(el.value) : el.value; });
    SETTINGS = await act(() => api("/api/settings", { method: "PUT", body }), "Settings saved") || SETTINGS;
  };
  $$("[data-browse]").forEach((b) => b.onclick = () => {
    const el = $("#set-form")[b.dataset.browse];
    const start = b.dataset.add ? (el.value.trim().split("\n").pop() || "") : el.value.trim();
    folderPicker(start, (p) => {
      if (b.dataset.add) { if (!el.value.split("\n").map((x) => x.trim()).includes(p)) el.value = (el.value.trim() ? el.value.trim() + "\n" : "") + p; }
      else el.value = p;
    });
  });
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


// ---------------------------------------------------------------- first-run setup wizard
let SETUP = null;      // {step} while the wizard is open
const SETUP_STEPS = ["Welcome", "Folders", "Jellyfin", "qBittorrent", "Automation", "Password"];

function setupField(k, label, type, hint, extra = "") {
  const s = SETTINGS, v = s[k];
  let input;
  if (type === "bool") input = `<label style="color:var(--text)"><input type="checkbox" name="${k}" ${v ? "checked" : ""}> ${extra || "on"}</label>`;
  else if (type.startsWith("select:")) input = `<select name="${k}">${type.slice(7).split("|").map((o) => { const [val, lab] = o.split("="); return `<option value="${esc(val)}" ${String(v) === val ? "selected" : ""}>${esc(lab || val)}</option>`; }).join("")}</select>`;
  else if (type === "folder") input = `<div style="display:flex;gap:6px"><input style="flex:1" name="${k}" value="${esc(v ?? "")}" autocomplete="off"><button type="button" class="small" data-browse="${k}">Browse…</button></div>`;
  else input = `<div style="display:flex;gap:6px"><input style="flex:1" name="${k}" type="${type === "secret" ? "password" : "text"}" value="${esc(v ?? "")}" autocomplete="off"${extra ? ` placeholder="${esc(extra)}"` : ""}></div>`;
  return `<label>${esc(label)}</label>${input}${hint ? `<div class="hint">${hint}</div>` : ""}`;
}

function setupStep(n) {
  switch (n) {
    case 0: return `<h2>Welcome to MediaMan</h2>
      <p>MediaMan keeps a Jellyfin library tidy: it names files the way Jellyfin expects, brings in finished
      downloads, and can shrink files by transcoding them while keeping HDR, Dolby Vision, every audio track and every subtitle.</p>
      <p>This setup takes a couple of minutes. Nothing is renamed, imported or transcoded until you've finished it,
      and everything here can be changed later under Settings.</p>`;
    case 1: return `<h2>Folders</h2>
      <p class="muted">Pick the folders Jellyfin uses for movies and shows, and the folder your download client puts finished downloads in.
      Browse lists the folders mounted into MediaMan.</p>
      <div class="form">
        ${setupField("movies_dir", "Movies", "folder", "")}
        ${setupField("tv_dir", "Shows", "folder", "")}
        ${setupField("downloads_dir", "Finished downloads", "folder", "MediaMan never changes or transcodes files here; it copies or links them into the library.")}
        ${setupField("import_mode", "Bring downloads in by", "select:copy=copying (keeps them seeding; uses extra space)|hardlink=hardlinking (keeps seeding, no extra space; same dataset and mount only)|move=moving (stops seeding)", "")}
      </div><div id="setup-folder-check" class="muted" style="margin-top:10px"></div>`;
    case 2: return `<h2>Jellyfin <span class="muted" style="font-weight:400">(optional)</span></h2>
      <p class="muted">With Jellyfin connected, MediaMan tells it which files changed and uses its TMDb search to confirm titles. Create an API key in Jellyfin under Dashboard → API Keys.</p>
      <div class="form">
        ${setupField("jellyfin_url", "Jellyfin address", "text", "", "https://nas.example:8920")}
        ${setupField("jellyfin_api_key", "API key", "secret", "")}
        ${setupField("jellyfin_verify_tls", "Check its HTTPS certificate", "bool", "Turn off only for a self-signed certificate.")}
        <label></label><div><button type="button" data-test="jellyfin">Test connection</button> <span id="test-jellyfin" class="muted"></span></div>
      </div>`;
    case 3: return `<h2>qBittorrent <span class="muted" style="font-weight:400">(optional)</span></h2>
      <p class="muted">MediaMan works with any download client: it waits until a download's files stop changing. With qBittorrent's Web UI connected, it also waits until qBittorrent reports the download complete.</p>
      <div class="form">
        ${setupField("qbittorrent_url", "Web UI address", "text", "", "http://nas.example:8080")}
        ${setupField("qbittorrent_username", "User name", "text", "")}
        ${setupField("qbittorrent_password", "Password", "secret", "")}
        <label></label><div><button type="button" data-test="qbittorrent">Test connection</button> <span id="test-qbittorrent" class="muted"></span></div>
      </div>`;
    case 4: return `<h2>Automation and safety</h2>
      <p class="muted">These start cautious. You can loosen them once you've seen how MediaMan handles your files.</p>
      <div class="form">
        ${setupField("auto_import_min_confidence", "Import downloads automatically", "select:0.9=When at least 90% sure of the title|0.75=When at least 75% sure (recommended)|0.6=When at least 60% sure|2=Never — always ask me", "Less certain downloads wait under Downloads for you to check.")}
        ${setupField("auto_transcode_imports", "Transcode imports automatically", "bool", "Off: you choose what to transcode from the Library.", "transcode every import")}
        ${setupField("keep_originals", "Keep originals", "bool", "A transcoded file's original moves to a hidden .mediaman-trash folder instead of being deleted. Uses space until you empty it.", "keep them")}
        ${setupField("dv_policy", "Dolby Vision", "select:mel_only=Convert, except profile 7 FEL (recommended)|convert=Convert everything to profile 8.1|skip=Never transcode Dolby Vision", "Profile 7 FEL discs carry extra picture detail that converting removes; most streaming players ignore it anyway.")}
      </div>`;
    case 5: return `<h2>Password <span class="muted" style="font-weight:400">(optional)</span></h2>
      <p class="muted">Without a password, anyone who can reach this page can rename and transcode your files. Your browser will ask for it; any user name works.</p>
      <div class="form">
        <label>Password</label><input type="password" name="setup_pw" autocomplete="new-password">
        <label>Repeat</label><input type="password" name="setup_pw2" autocomplete="new-password">
        <div class="hint">At least 8 characters. Leave empty to skip; you can set one later in Settings.</div>
      </div>`;
  }
}

const SETUP_FIELDS = {1: ["movies_dir", "tv_dir", "downloads_dir", "import_mode"],
  2: ["jellyfin_url", "jellyfin_api_key", "jellyfin_verify_tls"], 3: ["qbittorrent_url", "qbittorrent_username", "qbittorrent_password"],
  4: ["auto_import_min_confidence", "auto_transcode_imports", "keep_originals", "dv_policy"]};

function setupValues(form, step) {
  const out = {};
  for (const k of SETUP_FIELDS[step] || []) {
    const el = form[k]; if (!el) continue;
    out[k] = el.type === "checkbox" ? el.checked : k === "auto_import_min_confidence" ? Number(el.value) : el.value;
  }
  return out;
}

async function checkFolders(form) {
  const box = $("#setup-folder-check"); if (!box) return;
  const rows = [], missing = [];
  for (const [k, label] of [["movies_dir", "Movies"], ["tv_dir", "Shows"], ["downloads_dir", "Finished downloads"]]) {
    const p = form[k].value.trim();
    if (!p) { rows.push(`${label}: <span class="bad">not set</span>`); continue; }
    const d = await api("/api/fs?path=" + encodeURIComponent(p)).catch((e) => ({ error: e.message }));
    if (d.error === "folder not found") missing.push(p);
    rows.push(`${label}: ${d.error ? `<span class="bad">${esc(d.error)}</span>` : d.writable ? '<span class="ok">✓ found, writable</span>' : '<span class="warn">found, but MediaMan can\'t write to it</span>'}`);
  }
  box.innerHTML = rows.join("<br>") + (missing.length
    ? `<div style="margin-top:8px"><button type="button" class="small" id="setup-mkdirs">Create ${missing.length === 1 ? "this folder" : `these ${missing.length} folders`}</button>
       <span class="muted"> — for a new, empty Media storage</span></div>` : "");
  if (missing.length) $("#setup-mkdirs").onclick = async () => {
    for (const p of missing) {
      try { await api("/api/fs/mkdir", { method: "POST", body: { path: p } }); }
      catch (e) { toast(`⚠ ${p}: ${e.message}`); break; }
    }
    checkFolders(form);
  };
}

function renderSetup() {
  const n = SETUP.step, last = SETUP_STEPS.length - 1;
  $("#setup").innerHTML = `<div class="setup card">
    <div class="setup-steps">${SETUP_STEPS.map((s, i) => `<span class="${i === n ? "on" : i < n ? "done" : ""}">${i + 1}. ${esc(s)}</span>`).join("")}</div>
    <form id="setup-form" autocomplete="off">${setupStep(n)}</form>
    <div class="dlg-actions">
      <button type="button" id="setup-skip" class="link" style="margin-right:auto">Skip setup</button>
      ${n ? '<button type="button" id="setup-back">Back</button>' : ""}
      <button type="button" id="setup-next" class="primary">${n === 0 ? "Start" : n === last ? "Finish" : "Next"}</button>
    </div></div>`;
  const f = $("#setup-form");
  $$("[data-browse]", f).forEach((b) => b.onclick = () => folderPicker(f[b.dataset.browse].value.trim(), (p) => { f[b.dataset.browse].value = p; checkFolders(f); }));
  if (n === 1) { checkFolders(f); $$("input", f).forEach((i) => i.onchange = () => checkFolders(f)); }
  $$("[data-test]", f).forEach((b) => b.onclick = async () => {
    const out = $("#test-" + b.dataset.test); out.textContent = "testing…";
    try { SETTINGS = await api("/api/settings", { method: "PUT", body: setupValues(f, n) }); } catch (e) { out.textContent = "⚠ " + e.message; return; }
    const r = await api(`/api/settings/test/${b.dataset.test}`, { method: "POST" }).catch((e) => ({ result: e.message }));
    out.textContent = r.result;
  });
  $("#setup-skip").onclick = () => finishSetup(false);
  if ($("#setup-back")) $("#setup-back").onclick = () => { SETUP.step--; renderSetup(); };
  $("#setup-next").onclick = async () => {
    try {
      if (SETUP_FIELDS[n]) SETTINGS = await api("/api/settings", { method: "PUT", body: setupValues(f, n) });
    } catch (e) { toast("⚠ " + e.message); return; }
    if (n < last) { SETUP.step++; renderSetup(); return; }
    const pw = f.setup_pw.value;
    if (pw || f.setup_pw2.value) {
      if (pw.length < 8) return toast("⚠ Use at least 8 characters, or leave both fields empty");
      if (pw !== f.setup_pw2.value) return toast("⚠ The passwords don't match");
    }
    finishSetup(true, pw);
  };
}

async function finishSetup(completed, password = "") {
  try {
    SETTINGS = await api("/api/settings", { method: "PUT", body: { setup_done: true } });
    if (password) await api("/api/auth/password", { method: "POST", body: { current: "", new: password } });
  } catch (e) { toast("⚠ " + e.message); return; }
  SETUP = null;
  document.body.classList.remove("in-setup");
  if (password) { toast("Password set — sign in when your browser asks"); setTimeout(() => location.replace("/#overview"), 1200); return; }
  showTab("overview");
  if (completed && confirm("Setup done. Scan your library now? It only reads files and suggests renames; nothing changes until you approve.")) {
    act(() => api("/api/scan", { method: "POST", body: {} }), "Library scan started");
  }
}

function startSetup() {
  SETUP = { step: 0 };
  document.body.classList.add("in-setup");
  tab = "setup";
  $$(".tab").forEach((s) => s.classList.toggle("active", s.id === "setup"));
  renderSetup();
}

// ---------------------------------------------------------------- refresh loop
let busy = false;
async function refresh(force) {
  if (busy || SETUP) return; busy = true;
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
(async () => {
  try { SETTINGS = await api("/api/settings"); } catch (e) { console.error(e); }
  if (SETTINGS.setup_done === false) startSetup(); else showTab(tab === "setup" ? "overview" : tab);
})();
