// Citation network: canvas force layout with degree-aware springs (a hub's
// pull is shared out by degree, so nodes with 100+ edges stay stable).
const PALETTE = ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc948",
  "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac", "#86bcb6", "#d37295"];
const SPRING_LEN = 55, SPRING_K = 0.06, REPULSE = 1600, GRAVITY = 0.012, MAX_V = 60;

export class Graph {
  constructor(canvas, tip, stats, onOpen, api) {
    Object.assign(this, { canvas, tip, stats, onOpen, api });
    this.ctx = canvas.getContext("2d");
    this.loaded = false; this.running = false;
    this.cam = { x: 0, y: 0, k: 1 }; this.hover = -1; this.drag = -1; this.match = null;
    this.mode = "decade";
    new ResizeObserver(() => this.resize()).observe(canvas);
    this.events();
  }

  async activate() {
    if (this.loaded) { this.resize(); return; }
    this.loaded = true;
    const d = await this.api("/api/graph");
    if (!d.nodes.length) { this.stats.textContent = "No graph yet: run `litnav graph`."; return; }
    const idx = new Map(d.nodes.map((n, i) => [n.key, i]));
    this.nodes = d.nodes;
    this.edges = d.links.map((l) => [idx.get(l.source), idx.get(l.target)]).filter(([a, b]) => a !== undefined && b !== undefined);
    const N = this.N = this.nodes.length;
    this.deg = new Float32Array(N);
    for (const [a, b] of this.edges) { this.deg[a]++; this.deg[b]++; }
    this.springK = Float32Array.from(this.edges, ([a, b]) => SPRING_K * Math.min(1, 12 / Math.max(1, Math.min(this.deg[a], this.deg[b]))));
    this.r = Float32Array.from(this.nodes, (n) => 2.5 + Math.sqrt(n.in || 0) * 1.6);
    this.px = new Float32Array(N); this.py = new Float32Array(N);
    this.vx = new Float32Array(N); this.vy = new Float32Array(N);
    const R = 30 * Math.sqrt(N);
    for (let i = 0; i < N; i++) { const a = Math.random() * 2 * Math.PI, r = R * Math.sqrt(Math.random()); this.px[i] = Math.cos(a) * r; this.py[i] = Math.sin(a) * r; }
    this.alpha = 1;
    this.colorBy(this.mode);
    this.stats.textContent = `${N} papers, ${this.edges.length} citations within the library, ${d.candidates.length} outside works cited by several`;
    this.resize();
    this.run();
  }

  colorBy(mode) {
    this.mode = mode;
    if (!this.nodes) return;
    if (mode === "tag") {
      const counts = new Map();
      for (const n of this.nodes) for (const t of n.tags || []) if (t !== "bulk_imported") counts.set(t, (counts.get(t) || 0) + 1);
      const top = [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, PALETTE.length - 1).map((x) => x[0]);
      this.legend = top.map((t, i) => [t, PALETTE[i]]);
      this.color = this.nodes.map((n) => { const t = (n.tags || []).find((x) => top.includes(x)); return t ? PALETTE[top.indexOf(t)] : "#bbb"; });
    } else {
      const decs = [...new Set(this.nodes.map((n) => n.year ? Math.floor(n.year / 10) * 10 : null).filter(Boolean))].sort();
      const pick = (dec) => PALETTE[(decs.length - 1 - decs.indexOf(dec)) % PALETTE.length];
      this.legend = decs.slice().reverse().map((x) => [`${x}s`, pick(x)]);
      this.color = this.nodes.map((n) => n.year ? pick(Math.floor(n.year / 10) * 10) : "#bbb");
    }
    this.draw();
  }

  highlight(q) {
    const w = q.toLowerCase().split(/\s+/).filter(Boolean);
    this.match = w.length && this.nodes ? this.nodes.map((n) => w.every((x) => `${n.key} ${n.title} ${n.first_author}`.toLowerCase().includes(x))) : null;
    this.draw();
  }

  run() {
    if (this.running) return;
    this.running = true;
    const step = () => {
      if (!this.canvas.offsetParent) { this.running = false; return; }   // view hidden
      for (let s = 0; s < 2 && this.alpha > 0.005; s++) this.tick();
      this.draw();
      if (this.alpha > 0.005 || this.drag >= 0) requestAnimationFrame(step); else this.running = false;
    };
    requestAnimationFrame(step);
  }

  tick() {
    const { N, px, py, vx, vy, alpha } = this;
    for (let i = 0; i < N; i++) for (let j = i + 1; j < N; j++) {
      let dx = px[i] - px[j], dy = py[i] - py[j];
      const d2 = dx * dx + dy * dy + 0.01;
      if (d2 > 250000) continue;
      const f = REPULSE / d2 * alpha;
      dx *= f; dy *= f; vx[i] += dx; vy[i] += dy; vx[j] -= dx; vy[j] -= dy;
    }
    const { edges, springK, deg } = this;
    for (let e = 0; e < edges.length; e++) {
      const [a, b] = edges[e];
      let dx = px[b] - px[a], dy = py[b] - py[a];
      const d = Math.sqrt(dx * dx + dy * dy) + 0.01;
      const f = springK[e] * (d - SPRING_LEN) / d * alpha;
      dx *= f; dy *= f;
      const bias = deg[a] / (deg[a] + deg[b]);
      vx[a] += dx * 2 * (1 - bias); vy[a] += dy * 2 * (1 - bias);
      vx[b] -= dx * 2 * bias; vy[b] -= dy * 2 * bias;
    }
    for (let i = 0; i < N; i++) {
      vx[i] -= px[i] * GRAVITY * alpha; vy[i] -= py[i] * GRAVITY * alpha;
      vx[i] *= 0.6; vy[i] *= 0.6;
      const sp = Math.hypot(vx[i], vy[i]);
      if (sp > MAX_V) { vx[i] *= MAX_V / sp; vy[i] *= MAX_V / sp; }
      if (i === this.drag) continue;
      px[i] += vx[i]; py[i] += vy[i];
    }
    this.alpha *= 0.985;
    if (!this.fitted && this.alpha < 0.3) { this.fit(); this.fitted = true; }
  }

  resize() {
    const dpr = this.dpr = window.devicePixelRatio || 1;
    this.W = this.canvas.clientWidth; this.H = this.canvas.clientHeight;
    this.canvas.width = this.W * dpr; this.canvas.height = this.H * dpr;
    if (this.nodes && !this.fitted) this.fit();
    this.draw();
  }

  fit(pad = 40) {
    if (!this.N || !this.W) return;
    let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
    for (let i = 0; i < this.N; i++) { x0 = Math.min(x0, this.px[i]); x1 = Math.max(x1, this.px[i]); y0 = Math.min(y0, this.py[i]); y1 = Math.max(y1, this.py[i]); }
    const k = Math.min((this.W - 2 * pad) / (x1 - x0 || 1), (this.H - 2 * pad) / (y1 - y0 || 1));
    this.cam = { k, x: (x0 + x1) / 2, y: (y0 + y1) / 2 };
  }

  toScreen(x, y) { return [(x - this.cam.x) * this.cam.k + this.W / 2, (y - this.cam.y) * this.cam.k + this.H / 2]; }
  toWorld(sx, sy) { return [(sx - this.W / 2) / this.cam.k + this.cam.x, (sy - this.H / 2) / this.cam.k + this.cam.y]; }

  pick(sx, sy) {
    const [x, y] = this.toWorld(sx, sy);
    let best = -1, bd = Infinity;
    for (let i = 0; i < this.N; i++) {
      const d = Math.hypot(this.px[i] - x, this.py[i] - y), lim = this.r[i] + 4 / this.cam.k;
      if (d < lim && d < bd) { bd = d; best = i; }
    }
    return best;
  }

  draw() {
    const { ctx, dpr, W, H } = this;
    if (!ctx || !W) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    if (!this.nodes) return;
    const { px, py, cam, edges, hover, match } = this;
    ctx.setTransform(dpr * cam.k, 0, 0, dpr * cam.k, dpr * (W / 2 - cam.x * cam.k), dpr * (H / 2 - cam.y * cam.k));
    const nbr = new Set();
    ctx.lineWidth = 0.6 / cam.k;
    ctx.strokeStyle = hover >= 0 || match ? "rgba(120,120,120,0.07)" : "rgba(120,120,120,0.22)";
    ctx.beginPath();
    for (const [a, b] of edges) {
      if (a === hover || b === hover) { nbr.add(a); nbr.add(b); continue; }
      ctx.moveTo(px[a], py[a]); ctx.lineTo(px[b], py[b]);
    }
    ctx.stroke();
    if (hover >= 0) {
      ctx.lineWidth = 1.2 / cam.k;
      for (const [a, b] of edges) if (a === hover || b === hover) {
        ctx.strokeStyle = a === hover ? "rgba(200,80,40,0.8)" : "rgba(40,110,200,0.8)";   // cites : cited by
        ctx.beginPath(); ctx.moveTo(px[a], py[a]); ctx.lineTo(px[b], py[b]); ctx.stroke();
      }
    }
    const dim = (i) => (hover >= 0 && !nbr.has(i) && i !== hover) || (match && !match[i]);
    for (let i = 0; i < this.N; i++) {
      ctx.globalAlpha = dim(i) ? 0.15 : 1;
      ctx.fillStyle = this.color[i];
      ctx.beginPath(); ctx.arc(px[i], py[i], this.r[i], 0, 2 * Math.PI); ctx.fill();
      if (this.nodes[i].sharing === "confidential") { ctx.strokeStyle = "#000"; ctx.lineWidth = 1 / cam.k; ctx.stroke(); }
    }
    ctx.globalAlpha = 1;
    ctx.font = `${11 / cam.k}px system-ui, sans-serif`;
    const fg = getComputedStyle(this.canvas).color || "#222";
    ctx.fillStyle = fg;
    const thr = 24 / cam.k;
    for (let i = 0; i < this.N; i++) {
      const n = this.nodes[i];
      if (i === hover || nbr.has(i) || (match && match[i]) || (!match && hover < 0 && (n.in || 0) >= thr))
        ctx.fillText(n.key, px[i] + this.r[i] + 2 / cam.k, py[i] + 3 / cam.k);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.font = "11px system-ui, sans-serif";
    (this.legend || []).forEach(([t, c], i) => { ctx.fillStyle = c; ctx.fillRect(8, 8 + i * 15, 10, 10); ctx.fillStyle = fg; ctx.fillText(t, 22, 17 + i * 15); });
  }

  events() {
    const c = this.canvas;
    let pan = null, moved = false;
    const pos = (e) => { const b = c.getBoundingClientRect(); return [e.clientX - b.left, e.clientY - b.top]; };
    c.addEventListener("mousedown", (e) => {
      const [sx, sy] = pos(e); moved = false;
      const i = this.pick(sx, sy);
      if (i >= 0) { this.drag = i; this.alpha = Math.max(this.alpha, 0.1); this.run(); }
      else pan = [sx, sy, this.cam.x, this.cam.y];
    });
    window.addEventListener("mouseup", () => { this.drag = -1; pan = null; });
    c.addEventListener("mousemove", (e) => {
      const [sx, sy] = pos(e);
      if (this.drag >= 0) { moved = true; [this.px[this.drag], this.py[this.drag]] = this.toWorld(sx, sy); this.vx[this.drag] = this.vy[this.drag] = 0; this.draw(); return; }
      if (pan) { moved = true; this.cam.x = pan[2] - (sx - pan[0]) / this.cam.k; this.cam.y = pan[3] - (sy - pan[1]) / this.cam.k; this.draw(); return; }
      const i = this.pick(sx, sy);
      if (i !== this.hover) { this.hover = i; this.draw(); }
      if (i >= 0) {
        const n = this.nodes[i];
        this.tip.style.display = "block";
        this.tip.style.left = `${sx + 14}px`; this.tip.style.top = `${sy + 10}px`;
        this.tip.replaceChildren(Object.assign(document.createElement("b"), { textContent: n.key }), document.createElement("br"),
          document.createTextNode(`${n.title || ""} (${n.year || "?"})`), document.createElement("br"),
          Object.assign(document.createElement("span"), { className: "muted", textContent: `cited by ${n.in} · cites ${n.out} in the library · click to open` }));
      } else this.tip.style.display = "none";
    });
    c.addEventListener("mouseleave", () => { this.hover = -1; this.tip.style.display = "none"; this.draw(); });
    c.addEventListener("click", (e) => {
      if (moved) return;
      const i = this.pick(...pos(e));
      if (i >= 0) this.onOpen(this.nodes[i].key);
    });
    c.addEventListener("wheel", (e) => {
      e.preventDefault();
      const [sx, sy] = pos(e), [wx, wy] = this.toWorld(sx, sy);
      this.cam.k *= Math.exp(-e.deltaY * 0.0015);
      const [nx, ny] = this.toWorld(sx, sy);
      this.cam.x += wx - nx; this.cam.y += wy - ny;
      this.draw();
    }, { passive: false });
  }
}
