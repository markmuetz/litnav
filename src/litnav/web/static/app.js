// litnav web app. No framework. Library text is always rendered with
// textContent, or escaped before the small Markdown renderer runs.
import { Graph } from "/static/graph.js";

const TOKEN = document.querySelector('meta[name="litnav-token"]').content;
const $ = (s) => document.querySelector(s);

function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined && v !== false) e.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k !== null && k !== undefined) e.append(k instanceof Node ? k : String(k));
  return e;
}

async function api(path) {
  const r = await fetch(path, { credentials: "same-origin" });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.headers.get("content-type")?.includes("json") ? r.json() : r.text();
}
async function post(path, body) {
  const r = await fetch(path, { method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Litnav-Token": TOKEN }, body: JSON.stringify(body) });
  const d = await r.json();
  if (!r.ok || d.error) throw new Error(d.error || r.status);
  return d;
}
function status(msg) { $("#status").textContent = msg || ""; if (msg) setTimeout(() => { if ($("#status").textContent === msg) $("#status").textContent = ""; }, 4000); }

// ---- Markdown (escape first, then a few safe transforms) ----
function esc(s) { return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]); }
const KEY = "[A-Za-z0-9_]+(?:[:.\\-][A-Za-z0-9_]+)*";
function inline(s) {
  return s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<i>$2</i>")
    .replace(new RegExp(`\\((${KEY}#[A-Za-z0-9\\-]+#[0-9a-f]{8})\\)`, "g"),
             (m, id) => `(<a class="cid" data-chunk="${id}">${id}</a>)`)
    .replace(new RegExp(`(^|[\\[;\\s(])-?@(${KEY})`, "g"),
             (m, pre, k) => `${pre}<a data-key="${k}">@${k}</a>`)
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}
function md(src) {
  const lines = esc(src || "").split("\n");
  let out = [], list = null, code = false, para = [];
  const flush = () => { if (para.length) { out.push(`<p>${inline(para.join(" "))}</p>`); para = []; } };
  const endList = () => { if (list) { out.push(`</${list}>`); list = null; } };
  for (const l of lines) {
    if (l.startsWith("```")) { flush(); endList(); out.push(code ? "</pre>" : "<pre>"); code = !code; continue; }
    if (code) { out.push(l + "\n"); continue; }
    const h = /^(#{1,4})\s+(.*)/.exec(l);
    const li = /^\s*([-*]|\d+\.)\s+(.*)/.exec(l);
    if (h) { flush(); endList(); out.push(`<h${h[1].length + 1}>${inline(h[2])}</h${h[1].length + 1}>`); }
    else if (li) { flush(); const t = /\d/.test(li[1]) ? "ol" : "ul"; if (list !== t) { endList(); out.push(`<${t}>`); list = t; } out.push(`<li>${inline(li[2])}</li>`); }
    else if (!l.trim()) { flush(); endList(); }
    else { endList(); para.push(l); }
  }
  flush(); endList(); if (code) out.push("</pre>");
  const d = el("div", { class: "md" });
  d.innerHTML = out.join("");
  wireLinks(d);
  return d;
}
function wireLinks(root) {
  root.querySelectorAll("a[data-key]").forEach((a) => a.addEventListener("click", () => openPaper(a.dataset.key)));
  root.querySelectorAll("a[data-chunk]").forEach((a) => a.addEventListener("click", () => openChunk(a.dataset.chunk)));
}

// ---- views ----
function show(view) {
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.view === view));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("on", v.id === `view-${view}`));
  if (view === "graph") graph.activate();
  if (view === "reading") loadReading();
  if (view === "tags") loadTags();
  if (view === "discovery") loadDiscovery();
  history.replaceState(null, "", `#${view}`);
}
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => show(b.dataset.view)));

const keyLink = (k, label) => el("a", { class: "key", onclick: () => openPaper(k) }, label || k);
const tagChip = (t) => el("span", { class: "tag", onclick: () => { $("#lib-tag").value = t; loadLibrary(); show("library"); } }, t);

// ---- library ----
let ITEMS = [], sortKey = "year", sortDir = -1;
async function loadLibrary() {
  const p = new URLSearchParams();
  if ($("#lib-tag").value) p.set("tag", $("#lib-tag").value);
  if ($("#lib-project").value) p.set("project", $("#lib-project").value);
  ITEMS = await api(`/api/items?${p}`);
  renderLibrary();
}
function renderLibrary() {
  const q = $("#lib-q").value.toLowerCase().split(/\s+/).filter(Boolean);
  let rows = ITEMS.filter((r) => q.every((w) => `${r.key} ${r.title} ${r.authors}`.toLowerCase().includes(w)));
  rows.sort((a, b) => ((a[sortKey] ?? "") > (b[sortKey] ?? "") ? 1 : (a[sortKey] ?? "") < (b[sortKey] ?? "") ? -1 : 0) * sortDir);
  $("#lib-count").textContent = `${rows.length} of ${ITEMS.length}`;
  const tb = $("#lib-rows");
  tb.replaceChildren(...rows.slice(0, 600).map((r) => el("tr", {},
    el("td", {}, keyLink(r.key)), el("td", {}, r.year ?? ""), el("td", {}, r.first_author || ""),
    el("td", {}, r.title || el("span", { class: "muted" }, "(no metadata)")),
    el("td", {}, (r.tags || "").split(",").filter(Boolean).map(tagChip)),
    el("td", {}, r.has_pdf ? el("a", { href: `/pdf/${encodeURIComponent(r.key)}`, target: "_blank" }, "PDF") : "",
                  r.sharing === "confidential" ? el("span", { class: "muted" }, " conf.") : ""))));
  if (rows.length > 600) tb.append(el("tr", {}, el("td", { colspan: 6, class: "muted" }, `… ${rows.length - 600} more: filter to narrow`)));
}
$("#lib-q").addEventListener("input", renderLibrary);
$("#lib-tag").addEventListener("change", loadLibrary);
$("#lib-project").addEventListener("change", loadLibrary);
document.querySelectorAll("th[data-sort]").forEach((th) => th.addEventListener("click", () => {
  if (sortKey === th.dataset.sort) sortDir = -sortDir; else { sortKey = th.dataset.sort; sortDir = sortKey === "year" ? -1 : 1; }
  renderLibrary();
}));

// ---- search ----
function resultCard(r) {
  return el("div", { class: "result" },
    el("div", { class: "head" }, keyLink(r.key), " ", r.title || "", r.year ? ` (${r.year})` : ""),
    el("div", { class: "sec" }, r.section_title || r.kind || "", " ", el("a", { class: "cid", onclick: () => openChunk(r.id) }, r.id)),
    el("div", { class: "text" }, r.text));
}
$("#search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const p = new URLSearchParams({ q: $("#search-q").value, mode: $("#search-mode").value, k: 20 });
  if ($("#search-papers").checked) p.set("papers", "1");
  if ($("#search-year").value) p.set("year", $("#search-year").value);
  $("#search-results").replaceChildren(el("p", { class: "muted" }, "searching…"));
  try {
    const d = await api(`/api/search?${p}`);
    $("#search-note").textContent = d.semantic ? "" : "Keyword search only: no embeddings yet (run `litnav index`).";
    $("#search-results").replaceChildren(...(d.results.length ? d.results.map((r) =>
      $("#search-papers").checked
        ? el("div", { class: "result" }, el("div", { class: "head" }, keyLink(r.key), " ", r.title, r.year ? ` (${r.year})` : ""),
            r.best_passage ? el("div", { class: "text" }, r.best_passage.text.slice(0, 400) + "…") : "")
        : resultCard(r)) : [el("p", { class: "muted" }, "No results.")]));
  } catch (err) { $("#search-results").replaceChildren(el("p", { class: "err" }, String(err))); }
});

// ---- paper ----
const DEPTHS = ["deep", "read", "skim", "scan", "comment"];
async function openChunk(id) {
  const key = id.split("#")[0], slug = id.split("#")[1];
  await openPaper(key);
  if (slug && slug !== "caption" && slug !== "text") showSection(key, slug);
}
async function showSection(key, slug) {
  const box = $("#section-view");
  if (!box) return;
  box.replaceChildren(el("p", { class: "muted" }, "loading…"));
  try {
    const d = await api(`/api/item/${encodeURIComponent(key)}/section/${encodeURIComponent(slug)}`);
    box.replaceChildren(el("h3", {}, d.title), el("div", { class: "section-text" }, d.text));
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch { box.replaceChildren(el("p", { class: "muted" }, "Section not found.")); }
}
async function openPaper(key) {
  show("paper");
  history.replaceState(null, "", `#paper/${encodeURIComponent(key)}`);
  const root = $("#paper");
  root.replaceChildren(el("p", { class: "muted" }, "loading…"));
  let d;
  try { d = await api(`/api/item/${encodeURIComponent(key)}`); }
  catch { root.replaceChildren(el("p", {}, `No paper ${key}.`)); return; }
  const tagsBox = el("span", {});
  const renderTags = (tags) => tagsBox.replaceChildren(...tags.map((t) => el("span", { class: "tag" }, t,
    el("span", { class: "x", title: "remove", onclick: async () => { const r = await post(`/api/item/${d.key}/tags`, { remove: [t] }); renderTags(r.tags); status(r.git); } }, "×"))));
  renderTags(d.tags);
  const addTag = el("input", { placeholder: "add tag", size: 10, onkeydown: async (e) => {
    if (e.key !== "Enter" || !e.target.value.trim()) return;
    try { const r = await post(`/api/item/${d.key}/tags`, { add: [e.target.value.trim()] }); renderTags(r.tags); e.target.value = ""; status(r.git); }
    catch (err) { status(String(err)); }
  } });
  const share = el("select", { onchange: async (e) => { const r = await post(`/api/item/${d.key}/sharing`, { level: e.target.value }); status(r.git); } },
    ...["open", "licensed", "private", "confidential"].map((l) => el("option", { value: l, selected: l === d.sharing }, l)));
  const depth = el("select", {}, ...DEPTHS.map((x) => el("option", { value: x }, x)));
  const rd = d.reading;
  const readingBox = el("div", {}, rd ? `On the reading list (${rd.depth}, ${rd.status})${rd.done ? `: done ${rd.done.depth} ${rd.done.date}` : ""}. ` : "",
    depth, " ", el("button", { onclick: async () => { const r = await post("/api/reading", { action: "add", key: d.key, depth: depth.value }); status(r.git); openPaper(d.key); } }, rd ? "Change depth" : "Add to reading list"),
    " ", el("button", { title: "Only when you have finished it", onclick: async () => { if (!confirm(`Mark ${d.key} as read at depth "${depth.value}"?`)) return; const r = await post("/api/reading", { action: "done", key: d.key, depth: depth.value }); status(r.git); openPaper(d.key); } }, "Mark finished"));
  const list = (keys) => keys.length ? el("ul", {}, ...keys.map((k) => el("li", {}, keyLink(k), " ", d.titles[k] || ""))) : el("p", { class: "muted" }, "none");
  const similarBox = el("div", {}, el("button", { onclick: async (e) => {
    const s = await api(`/api/item/${d.key}/similar`);
    similarBox.replaceChildren(s.length ? el("ul", {}, ...s.map((x) => el("li", {}, keyLink(x.key), " ", x.title || ""))) : el("p", { class: "muted" }, "No embeddings yet."));
  } }, "Find similar papers"));
  const bib = el("pre", {}, d.bibtex || "(no ref.bib)");
  root.replaceChildren(
    el("div", { class: "key muted" }, d.key),
    el("h1", {}, d.title || "(no title)"),
    el("div", {}, d.authors.join("; ")),
    el("div", { class: "muted" }, d.journal || "", " ", d.year || "", d.doi ? " · " : "", d.doi ? el("a", { href: `https://doi.org/${d.doi}`, target: "_blank", rel: "noopener" }, d.doi) : "",
       d.has_pdf ? " · " : "", d.has_pdf ? el("a", { href: `/pdf/${encodeURIComponent(d.key)}`, target: "_blank" }, "Open PDF") : ""),
    el("div", { class: "cols" },
      el("div", {},
        d.summary ? el("div", { class: "box" }, el("h3", {}, "Summary"), d.topic ? el("p", {}, el("i", {}, d.topic)) : "", el("p", {}, d.summary)) : "",
        d.abstract ? el("div", { class: "box" }, el("h3", {}, "Abstract"), el("p", {}, d.abstract)) : "",
        el("div", { class: "box" }, el("h3", {}, "My notes"), d.note ? md(d.note) : el("p", { class: "muted" }, `No notes yet (notes/${d.key}.md).`),
          d.backlinks.length ? el("p", { class: "muted" }, "Cited in: ", d.backlinks.join(", ")) : ""),
        el("div", { class: "box" }, el("h3", {}, "Sections"),
          d.sections.length ? el("ul", { class: "sections" }, ...d.sections.filter((s) => s.kind !== "refs").map((s) =>
            el("li", { style: `margin-left:${(s.level - 1) * 1.2}em`, onclick: () => showSection(d.key, s.slug) }, el("span", { class: "link" }, s.title)))) : el("p", { class: "muted" }, "No text indexed."),
          el("div", { id: "section-view" }))),
      el("div", {},
        el("div", { class: "box" }, el("h3", {}, "Tags"), tagsBox, addTag),
        el("div", { class: "box" }, el("h3", {}, "Sharing"), share),
        el("div", { class: "box" }, el("h3", {}, "Reading"), readingBox),
        el("div", { class: "box" }, el("h3", {}, `Cites (${d.cites.length} in library)`), list(d.cites)),
        el("div", { class: "box" }, el("h3", {}, `Cited by (${d.cited_by.length})`), list(d.cited_by)),
        el("div", { class: "box" }, el("h3", {}, "Similar"), similarBox),
        el("div", { class: "box" }, el("h3", {}, "BibTeX ", el("button", { onclick: () => navigator.clipboard.writeText(d.bibtex).then(() => status("copied")) }, "Copy")), bib))));
  window.scrollTo(0, 0);
}

// ---- tags & projects ----
let TAGS = null;
async function loadTags(force) {
  if (!TAGS || force) TAGS = await api("/api/tags");
  $("#tag-cloud").replaceChildren(...TAGS.tags.map(([t, n]) => el("span", { class: "tag", onclick: () => { $("#lib-tag").value = t; loadLibrary(); show("library"); } }, `${t} (${n})`)));
  $("#project-list").replaceChildren(...(TAGS.projects.length ? TAGS.projects.map((p) => el("span", { class: "tag", onclick: () => { $("#lib-project").value = p; loadLibrary(); show("library"); } }, p)) : [el("p", { class: "muted" }, "No projects.")]));
}

// ---- reading ----
async function loadReading() {
  const d = await api("/api/reading");
  const due = d.due.recall;
  $("#due").replaceChildren(...(due.length ? due.map((c) => el("div", { class: "card" },
    el("div", {}, c.id.startsWith("concept:") ? el("b", {}, c.id) : keyLink(c.id), el("span", { class: "muted" }, ` box ${c.box}`)),
    el("p", {}, c.prompt || el("span", { class: "muted" }, "(no prompt: recall the gist)")),
    ...["forgot", "hard", "medium", "easy"].map((r) => el("button", { onclick: async () => { const x = await post("/api/reading", { action: "rate", id: c.id, rating: r }); status(x.git); loadReading(); } }, r))))
    : [el("p", { class: "muted" }, "Nothing due.")]));
  const groups = {};
  for (const x of d.list) (groups[x.status] = groups[x.status] || []).push(x);
  $("#reading-list").replaceChildren(...["reading", "to read", "done"].filter((s) => groups[s]).map((s) => el("div", {},
    el("h3", {}, `${s} (${groups[s].length})`),
    el("ul", {}, ...groups[s].map((x) => el("li", {}, keyLink(x.key), ` [${x.depth}] `, x.title || "", x.done ? el("span", { class: "muted" }, ` done ${x.done.depth} ${x.done.date}`) : ""))))));
  if (!d.list.length) $("#reading-list").replaceChildren(el("p", { class: "muted" }, "The reading list is empty: add papers from their page."));
}

// ---- ask ----
let HISTORY = [];
async function askInfo() {
  try {
    const s = await api("/api/ask");
    $("#ask-info").textContent = s.ok
      ? `Answered by ${s.backend === "api" ? `the Anthropic API (${s.model})` : "Claude Code"} with read-only tools over your library. Your question and the passages it reads are sent to Anthropic; confidential items are never included.`
      : `Ask is not available: ${s.reason}.`;
  } catch {}
}
$("#ask-clear").addEventListener("click", () => { HISTORY = []; $("#ask-log").replaceChildren(); });
$("#ask-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#ask-q").value.trim();
  if (!q) return;
  $("#ask-q").value = "";
  const tools = el("div", { class: "tool" }), ans = el("div", { class: "a" });
  $("#ask-log").append(el("div", { class: "q" }, q), tools, ans);
  let text = "";
  const r = await fetch("/api/ask", { method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Litnav-Token": TOKEN }, body: JSON.stringify({ question: q, history: HISTORY }) });
  if (!r.ok) { ans.replaceChildren(el("span", { class: "err" }, await r.text())); return; }
  const reader = r.body.getReader(), dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const line = buf.slice(0, i); buf = buf.slice(i + 2);
      if (!line.startsWith("data: ")) continue;
      const ev = JSON.parse(line.slice(6));
      if (ev.type === "text") { text += ev.text; ans.replaceChildren(md(text)); }
      else if (ev.type === "tool") tools.append(el("div", {}, `→ ${ev.name}(${JSON.stringify(ev.input)})`));
      else if (ev.type === "tool_result") tools.append(el("div", {}, `  ${ev.error ? "✗" : "✓"} ${ev.summary}`));
      else if (ev.type === "status") tools.append(el("div", {}, ev.message));
      else if (ev.type === "error") ans.append(el("p", { class: "err" }, ev.message));
    }
  }
  HISTORY.push({ role: "user", content: q }, { role: "assistant", content: text });
});
$("#ask-q").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) $("#ask-form").requestSubmit(); });

// ---- graph ----
const graph = new Graph($("#graph-canvas"), $("#graph-tip"), $("#graph-stats"), openPaper, api);
$("#graph-q").addEventListener("input", (e) => graph.highlight(e.target.value));
$("#graph-color").addEventListener("change", (e) => graph.colorBy(e.target.value));
const candToggle = () => graph.showCandidates($("#graph-cands").checked, +$("#graph-mincites").value);
$("#graph-cands").addEventListener("change", candToggle);
$("#graph-mincites").addEventListener("change", () => { if ($("#graph-cands").checked) candToggle(); });

// ---- discovery ----
async function tofetch(action, row) {
  const r = await post("/api/to-fetch", { action, ...row });
  status(r.git);
  loadDiscovery();
}
async function loadDiscovery() {
  const rows = await api("/api/to-fetch");
  const live = rows.filter((r) => r.status !== "skip");
  $("#tofetch").replaceChildren(...(live.length ? [el("table", { class: "items" }, el("tbody", {}, ...live.map((r) => el("tr", {},
    el("td", {}, r.first_author || "", " ", r.year || ""),
    el("td", {}, r.title || "", r.note ? el("div", { class: "muted" }, r.note) : ""),
    el("td", {}, r.doi ? el("a", { href: `https://doi.org/${r.doi}`, target: "_blank", rel: "noopener",
                                   onclick: () => r.status || tofetch("opened", { doi: r.doi }) }, r.status === "opened" ? "opened" : "Open") : "",
                 " ", el("button", { onclick: () => tofetch("skip", { doi: r.doi }) }, "Skip"))))))]
    : [el("p", { class: "muted" }, "Nothing on the list.")]));
  const min = +$("#cand-min").value || 5;
  let cs;
  try { cs = await api(`/api/candidates?min_cites=${min}&limit=150`); }
  catch (err) { $("#cands").replaceChildren(el("p", { class: "muted" }, String(err))); return; }
  $("#cand-count").textContent = `${cs.length} shown`;
  $("#cands").replaceChildren(...cs.map((c) => el("div", { class: "result" },
    el("div", { class: "head" }, `${c.first_author || ""} ${c.year || ""}: `, c.title || ""),
    el("div", { class: "sec" }, `cited by ${c.cited_by_library} of your papers: `,
      ...(c.citers || []).slice(0, 8).flatMap((k) => [keyLink(k), " "]), (c.citers || []).length > 8 ? "…" : ""),
    c.maybe_held ? el("div", { class: "sec" }, "Maybe already held as ", keyLink(c.maybe_held)) : "",
    el("div", {}, c.doi ? el("a", { href: `https://doi.org/${c.doi}`, target: "_blank", rel: "noopener" }, c.doi) : "(no DOI)", " ",
      c.listed ? el("span", { class: "muted" }, `[${c.listed}]`)
        : el("button", { onclick: () => tofetch("add", { doi: c.doi, title: c.title, first_author: c.first_author, year: c.year, source: `candidates:${c.cited_by_library}` }) }, "Want"),
      " ", c.listed === "skip" ? "" : el("button", { onclick: () => tofetch(c.listed ? "skip" : "add_skip", { doi: c.doi, title: c.title, first_author: c.first_author, year: c.year, source: `candidates:${c.cited_by_library}` }) }, "Not interested")))));
}
$("#cand-min").addEventListener("change", loadDiscovery);

// ---- start ----
(async () => {
  try {
    const t = await api("/api/tags");
    TAGS = t;
    for (const [tag, n] of t.tags) $("#lib-tag").append(el("option", { value: tag }, `${tag} (${n})`));
    for (const p of t.projects) $("#lib-project").append(el("option", { value: p }, p));
    await loadLibrary();
    askInfo();
    const v = decodeURIComponent(location.hash.slice(1));
    const qp = new URLSearchParams(location.search);
    if (qp.get("cands")) { $("#graph-cands").checked = true; $("#graph-mincites").value = qp.get("cands"); candToggle(); }
    if (v.startsWith("paper/")) openPaper(v.slice(6));
    else if (v && document.getElementById(`view-${v}`)) show(v);
  } catch (err) { status(String(err)); }
})();
