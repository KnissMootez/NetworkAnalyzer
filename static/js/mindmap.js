/* ═══════════════════════════════════════════════════════════
   mindmap.js — Interactive drill-down tree / mind map.

   Features
   ─────────────────────────────────────────────────────────
   • Two layouts: horizontal Tree (default) and Radial — toggle live
   • Card nodes: label + value, colored accent, glow on root/suggestion
   • Edge-anchored curved links, color-matched to their target
   • Click a node to expand / collapse its branch (animated)
   • Toolbar: Expand all · Collapse · Fit · Layout · Fullscreen · PNG
   • Real fullscreen on the map itself (re-fits, fills the screen)
   • Zoom (wheel) + pan (drag), hover highlight + rich tooltip
   ═══════════════════════════════════════════════════════════ */

const Mindmap = (() => {

  // Vivid palette — pill bg / border / label text / accent+value
  const PAL = {
    red:    { pill:"#2a0f12", border:"#e0584f", label:"#ffc4bf", accent:"#ff6b61" },
    amber:  { pill:"#2a1c05", border:"#e0a030", label:"#ffe0a8", accent:"#ffb340" },
    blue:   { pill:"#0c1c34", border:"#3d8fe0", label:"#aed6ff", accent:"#52a8ff" },
    purple: { pill:"#1c0f30", border:"#9866e6", label:"#d9bcff", accent:"#b07bff" },
    green:  { pill:"#08231a", border:"#2fb56b", label:"#a8efc8", accent:"#3fd986" },
    teal:   { pill:"#06201f", border:"#22b3a3", label:"#9bf0e6", accent:"#34d6c6" },
  };
  const ROOT = { pill:"#10203a", border:"#5a86c8", label:"#eef4ff", accent:"#8ab4f0" };
  const SUGG = { pill:"#241c02", border:"#e6c020", label:"#ffeea0", accent:"#ffd633" };
  const _p = c => PAL[c] || PAL.blue;

  function render(containerEl, data) {
    if (!data || !containerEl) return;
    containerEl.innerHTML = "";

    // ── DOM scaffold ─────────────────────────────────────────
    const rootDiv = document.createElement("div");
    rootDiv.className = "mm-root";

    // collapsible header — click anywhere on it to fold/unfold the whole panel
    const header = document.createElement("div");
    header.className = "mm-header";
    const title = document.createElement("span");
    title.className = "mm-title";
    title.textContent = "⬡ " + (data.title || data.center || "Mind Map");
    const caret = document.createElement("button");
    caret.className = "mm-collapse"; caret.type = "button"; caret.textContent = "▾";
    caret.title = "Collapse / expand";
    header.appendChild(title); header.appendChild(caret);

    const bar = document.createElement("div");
    bar.className = "mm-toolbar";

    const canvas = document.createElement("div");
    canvas.className = "mm-canvas";

    const legend = document.createElement("div");
    legend.className = "mm-legend";
    legend.innerHTML =
      `<span class="mm-leg-item"><i class="mm-leg-root"></i>Topic</span>` +
      `<span class="mm-leg-item"><i class="mm-leg-data"></i>Data</span>` +
      `<span class="mm-leg-item"><i class="mm-leg-sugg"></i>✦ Action</span>` +
      `<span class="mm-hint">click = expand · ◎ = solo a branch · scroll = zoom · drag = pan</span>`;

    rootDiv.appendChild(header);
    rootDiv.appendChild(bar);
    rootDiv.appendChild(canvas);
    rootDiv.appendChild(legend);
    containerEl.appendChild(rootDiv);

    header.onclick = () => {
      const collapsed = rootDiv.classList.toggle("mm-collapsed");
      caret.textContent = collapsed ? "▸" : "▾";
      if (!collapsed) setTimeout(() => fit(false), 90);
    };

    const tip = document.createElement("div");
    tip.className = "mm-tooltip";
    tip.style.display = "none";
    canvas.appendChild(tip);

    // ── SVG ──────────────────────────────────────────────────
    const svg = d3.create("svg").attr("class", "mm-svg");
    const defs = svg.append("defs");

    // glow
    const glow = defs.append("filter").attr("id", "mm-glow")
      .attr("x", "-60%").attr("y", "-60%").attr("width", "220%").attr("height", "220%");
    glow.append("feGaussianBlur").attr("stdDeviation", "4").attr("result", "b");
    const gm = glow.append("feMerge");
    gm.append("feMergeNode").attr("in", "b");
    gm.append("feMergeNode").attr("in", "SourceGraphic");

    const gZoom = svg.append("g");
    const linkG = gZoom.append("g").attr("fill", "none");
    const nodeG = gZoom.append("g");
    canvas.appendChild(svg.node());

    const zoom = d3.zoom().scaleExtent([0.15, 3])
      .on("zoom", e => gZoom.attr("transform", e.transform));
    svg.call(zoom).on("dblclick.zoom", null);

    // ── Hierarchy ────────────────────────────────────────────
    const _name = d => {
      const n = d.label ?? d.name ?? d.center ?? d.title ?? d.id;
      return (n == null || n === "undefined") ? "" : String(n);
    };
    const toH = d => ({
      name:  _name(d),
      value: (d.value === 0 || d.value) ? String(d.value) : null,
      color: d.color || null,
      type:  d.type || "root",
      children: (d.nodes || d.branches || d.children || []).map(toH),
    });
    const root = d3.hierarchy(toH(data));
    root.x0 = 0; root.y0 = 0;

    let uid = 0;
    root.each(d => { d.id = ++uid; });

    // default: root + first level open, deeper levels collapsed
    root.each(d => {
      if (d.depth >= 1 && d.children) { d._children = d.children; d.children = null; }
    });

    let layout = "down";   // "down" (top→bottom) | "right" (left→right) | "radial"
    let view   = root;     // current focus root — lets you solo / drill into one branch
    const LAYOUTS = { down: "Top-Down", right: "Left-Right", radial: "Radial" };

    // ── Per-node style + sizing ──────────────────────────────
    function style(d) {
      const t = d.data.type;
      if (t === "root")       return { fs:13, bold:true,  pal:ROOT, kind:"root" };
      if (t === "suggestion") return { fs:10, bold:false, pal:SUGG, kind:"sugg" };
      const pal = _p(d.data.color);
      return { fs: d.depth === 1 ? 11 : 10, bold: d.depth === 1, pal, kind:"data" };
    }
    function measure(d) {
      const st = style(d);
      const max = st.kind === "root" ? 30 : 26;
      const raw = d.data.name || "";
      d._label = raw.length > max ? raw.slice(0, max - 1) + "…" : raw;
      const charW = st.fs * 0.62;
      const labW = d._label.length * charW;
      const valW = d.data.value ? String(d.data.value).length * st.fs * 0.58 : 0;
      const iconW = st.kind === "sugg" ? 14 : 0;
      d._w = Math.max(72, Math.round(Math.max(labW, valW) + 30 + iconW));
      d._h = (d.data.value ? 2 : 1) * (st.fs + 5) + 13;
    }
    root.each(measure);

    // ── Layout engines (operate on the current `view` root) ──
    function place() {
      const desc = view.descendants();
      const maxW = d3.max(desc, d => d._w) || 120;
      const maxH = d3.max(desc, d => d._h) || 40;
      if (layout === "radial") {
        const radius = 120 + desc.length * 11;
        d3.cluster().size([2 * Math.PI, radius])
          .separation((a, b) => (a.parent === b.parent ? 1.4 : 2.4) / Math.max(1, a.depth - view.depth + 1))(view);
      } else if (layout === "down") {
        d3.tree().nodeSize([maxW + 34, maxH + 74])
          .separation((a, b) => a.parent === b.parent ? 1 : 1.3)(view);
      } else { // right
        d3.tree().nodeSize([maxH + 24, maxW + 96])
          .separation((a, b) => a.parent === b.parent ? 1 : 1.25)(view);
      }
    }
    // screen-space coords of a node's center
    function xy(d) {
      if (layout === "radial") return d3.pointRadial(d.x, d.y);
      if (layout === "down")   return [d.x, d.y];
      return [d.y, d.x];       // right
    }
    function linkPath(s, t) {
      const [sx, sy] = xy(s), [tx, ty] = xy(t);
      if (layout === "down") {
        const y0 = sy + s._h / 2, y1 = ty - t._h / 2, my = (y0 + y1) / 2;
        return `M${sx},${y0}C${sx},${my} ${tx},${my} ${tx},${y1}`;
      }
      if (layout === "right") {
        const x0 = sx + s._w / 2, x1 = tx - t._w / 2, mx = (x0 + x1) / 2;
        return `M${x0},${sy}C${mx},${sy} ${mx},${ty} ${x1},${ty}`;
      }
      const mx = (sx + tx) / 2, my = (sy + ty) / 2;
      const dx = tx - sx, dy = ty - sy, len = Math.hypot(dx, dy) || 1;
      const k = 18;
      return `M${sx},${sy}Q${mx - dy / len * k},${my + dx / len * k} ${tx},${ty}`;
    }

    // ── Fit to view ──────────────────────────────────────────
    function dims() {
      return [canvas.clientWidth || 720, canvas.clientHeight || 480];
    }
    const MIN_READABLE = 0.55;
    function fit(animate, fitAll) {
      const [W, H] = dims();
      svg.attr("width", W).attr("height", H);
      const ns = view.descendants();
      if (!ns.length) return;
      let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
      ns.forEach(d => {
        const [cx, cy] = xy(d);
        x0 = Math.min(x0, cx - d._w / 2); x1 = Math.max(x1, cx + d._w / 2);
        y0 = Math.min(y0, cy - d._h / 2); y1 = Math.max(y1, cy + d._h / 2);
      });
      const bw = (x1 - x0) || 1, bh = (y1 - y0) || 1;
      const pad = 44;
      let s = Math.min((W - pad) / bw, (H - pad) / bh, 1.4);
      let tx, ty;
      if (!fitAll && s < MIN_READABLE) {
        // Too many nodes to fit legibly. Keep them at a readable size and anchor
        // to the start so you scroll/pan through them — far better than a tiny strip.
        s = MIN_READABLE;
        if (layout === "down") { tx = W / 2 - s * xy(view)[0]; ty = pad / 2 - s * y0; }
        else                   { tx = pad / 2 - s * x0;       ty = H / 2 - s * xy(view)[1]; }
      } else {
        tx = (W - s * (x0 + x1)) / 2;
        ty = (H - s * (y0 + y1)) / 2;
      }
      const tform = d3.zoomIdentity.translate(tx, ty).scale(s);
      (animate ? svg.transition().duration(500) : svg).call(zoom.transform, tform);
    }

    // ── Render / update ──────────────────────────────────────
    const DUR = 480;
    function update(source) {
      // Measure any node that just became visible. `root.each(measure)` at boot
      // only walks expanded .children, so collapsed (._children) deeper nodes are
      // unmeasured until expanded — without this they render as "undefined" + no card.
      view.descendants().forEach(d => { if (d._w == null) measure(d); });
      place();
      const s0 = source ? xy(source) : [0, 0];
      const nodes = view.descendants();
      const links = view.links();

      // links
      const link = linkG.selectAll("path").data(links, d => d.target.id);
      link.exit().transition().duration(DUR)
        .attr("d", () => linkPath(source || view, source || view))
        .attr("stroke-opacity", 0).remove();
      const linkEnter = link.enter().append("path")
        .attr("d", () => { const o = { ...view, x: source ? source.x0 : view.x0, y: source ? source.y0 : view.y0 };
                            return linkPath(o, o); })
        .attr("stroke-opacity", 0)
        .attr("stroke-width", d => d.target.depth === 1 ? 2 : 1.3)
        .attr("stroke-dasharray", d => d.target.data.type === "suggestion" ? "5,4" : null)
        .attr("stroke", d => (d.target.data.type === "suggestion" ? SUGG : style(d.target).pal).accent);
      linkEnter.merge(link).transition().duration(DUR)
        .attr("d", d => linkPath(d.source, d.target))
        .attr("stroke-opacity", d => d.target.depth === 1 ? 0.6 : 0.4);

      // nodes
      const node = nodeG.selectAll("g.mmn").data(nodes, d => d.id);

      const enter = node.enter().append("g").attr("class", "mmn")
        .attr("transform", `translate(${s0[0]},${s0[1]})`)
        .attr("opacity", 0)
        .style("cursor", d => (d.children || d._children) ? "pointer" : "default")
        .on("click", (e, d) => { e.stopPropagation(); toggle(d); })
        .on("mousemove", (e, d) => showTip(e, d))
        .on("mouseleave", hideTip);

      enter.append("rect").attr("class", "mm-card")
        .attr("rx", 9).attr("ry", 9)
        .attr("x", d => -d._w / 2).attr("y", d => -d._h / 2)
        .attr("width", d => d._w).attr("height", d => d._h);
      enter.append("rect").attr("class", "mm-accent")
        .attr("rx", 2).attr("ry", 2)
        .attr("x", d => -d._w / 2 + 4).attr("y", d => -d._h / 2 + 5)
        .attr("width", 3).attr("height", d => d._h - 10);
      enter.append("text").attr("class", "mm-lbl").attr("text-anchor", "middle");
      enter.append("text").attr("class", "mm-val").attr("text-anchor", "middle");
      // expand/collapse badge
      const badge = enter.append("g").attr("class", "mm-badge");
      badge.append("circle").attr("r", 8);
      badge.append("text").attr("class", "mm-badge-sign").attr("text-anchor", "middle").attr("dy", "0.32em");

      // focus ("solo") button — top-left, revealed on hover
      const fbtn = enter.append("g").attr("class", "mm-focus")
        .on("click", (e, d) => { e.stopPropagation(); focus(d); });
      fbtn.append("title").text("Solo this branch");
      fbtn.append("circle").attr("r", 8).attr("fill", "#0a111e").attr("stroke", "#5a86c8").attr("stroke-width", 1.3);
      fbtn.append("text").attr("text-anchor", "middle").attr("dy", "0.32em")
        .attr("font-size", 9).attr("fill", "#8ab4f0").attr("pointer-events", "none").text("◎");

      const all = enter.merge(node);

      all.select(".mm-card")
        .attr("fill", d => style(d).pal.pill)
        .attr("stroke", d => style(d).pal.border)
        .attr("stroke-width", d => d.data.type === "root" ? 2 : 1.3)
        .attr("stroke-dasharray", d => d.data.type === "suggestion" ? "5,4" : null)
        .attr("filter", d => (d.data.type === "root" || d.data.type === "suggestion") ? "url(#mm-glow)" : null);
      all.select(".mm-accent").attr("fill", d => style(d).pal.accent);

      all.select(".mm-lbl")
        .attr("fill", d => style(d).pal.label)
        .attr("font-size", d => style(d).fs)
        .attr("font-weight", d => style(d).bold ? 600 : 400)
        .attr("dy", d => d.data.value ? "-0.35em" : "0.34em")
        .text(d => (d.data.type === "suggestion" ? "✦ " : "") + d._label);
      all.select(".mm-val")
        .attr("fill", d => style(d).pal.accent)
        .attr("font-size", d => style(d).fs * 0.9)
        .attr("dy", "1.05em")
        .text(d => d.data.value || "");

      all.select(".mm-badge")
        .attr("transform", d => `translate(${d._w / 2},${-d._h / 2})`)
        .style("display", d => (d.children || d._children) ? null : "none")
        .select("circle")
        .attr("fill", d => d._children ? style(d).pal.accent : "#0a111e")
        .attr("stroke", d => style(d).pal.accent);
      all.select(".mm-badge-sign")
        .attr("fill", d => d._children ? "#06101e" : style(d).pal.accent)
        .text(d => d._children ? "+" : "–");

      all.transition().duration(DUR)
        .attr("transform", d => { const [x, y] = xy(d); return `translate(${x},${y})`; })
        .attr("opacity", 1);

      node.exit().transition().duration(DUR)
        .attr("transform", `translate(${s0[0]},${s0[1]})`)
        .attr("opacity", 0).remove();

      // focus ("solo") button — appears on hover for any branch node
      all.select(".mm-focus")
        .attr("transform", d => `translate(${-d._w / 2},${-d._h / 2})`)
        .style("display", d => (d.children || d._children) && d !== view ? null : "none");

      view.each(d => { d.x0 = d.x; d.y0 = d.y; });
    }

    function toggle(d) {
      if (!d.children && !d._children) return;
      if (d.children) { d._children = d.children; d.children = null; }
      else { d.children = d._children; d._children = null; }
      update(d);
    }

    // Solo a branch: make it the view root so siblings disappear and you can
    // drill into just this one. Breadcrumb / Back / Home restore the full tree.
    function focus(d) {
      if (d === view || (!d.children && !d._children)) return;
      view = d;
      if (d._children) { d.children = d._children; d._children = null; }
      update(d); renderCrumb(); setTimeout(() => fit(true), 60);
    }
    function focusUp() {
      if (view.parent) { view = view.parent; update(view); renderCrumb(); setTimeout(() => fit(true), 60); }
    }
    function focusHome() {
      if (view === root) return;
      view = root; update(root); renderCrumb(); setTimeout(() => fit(true), 60);
    }

    // ── Tooltip ──────────────────────────────────────────────
    function showTip(e, d) {
      const r = canvas.getBoundingClientRect();
      const tag = d.data.type === "suggestion" ? "✦ Suggested action"
                : d.data.type === "root" ? "Topic" : "Data point";
      tip.innerHTML =
        `<div class="mm-tip-tag">${tag}</div>` +
        `<div class="mm-tip-name">${esc(d.data.name)}</div>` +
        (d.data.value ? `<div class="mm-tip-val">${esc(d.data.value)}</div>` : "") +
        ((d.children || d._children)
          ? `<div class="mm-tip-hint">${d._children ? "click to expand" : "click to collapse"}</div>` : "");
      tip.style.display = "block";
      let x = e.clientX - r.left + 14, y = e.clientY - r.top + 14;
      x = Math.min(x, r.width - tip.offsetWidth - 8);
      y = Math.min(y, r.height - tip.offsetHeight - 8);
      tip.style.left = x + "px";
      tip.style.top = y + "px";
    }
    function hideTip() { tip.style.display = "none"; }
    const esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

    // ── Bulk expand / collapse (within current view) ─────────
    function expandAll() {
      view.each(d => { if (d._children) { d.children = d._children; d._children = null; } });
      update(view); setTimeout(() => fit(true), 60);
    }
    function collapseAll() {
      view.each(d => { if (d !== view && d.children) { d._children = d.children; d.children = null; } });
      update(view); setTimeout(() => fit(true), 60);
    }

    // ── PNG export ───────────────────────────────────────────
    function exportPNG() {
      try {
        const [W, H] = dims();
        const clone = svg.node().cloneNode(true);
        clone.setAttribute("width", W); clone.setAttribute("height", H);
        const bg = `<rect width="${W}" height="${H}" fill="#070d18"/>`;
        clone.innerHTML = bg + clone.innerHTML;
        const xml = new XMLSerializer().serializeToString(clone);
        const img = new Image();
        img.onload = () => {
          const c = document.createElement("canvas");
          c.width = W * 2; c.height = H * 2;
          const ctx = c.getContext("2d"); ctx.scale(2, 2);
          ctx.drawImage(img, 0, 0);
          const a = document.createElement("a");
          a.download = "mindmap.png"; a.href = c.toDataURL("image/png"); a.click();
        };
        img.src = "data:image/svg+xml;base64," + btoa(unescape(encodeURIComponent(xml)));
      } catch (_) {}
    }

    // ── Fullscreen ───────────────────────────────────────────
    function toggleFs() {
      if (!document.fullscreenElement) rootDiv.requestFullscreen?.().catch(() => {});
      else document.exitFullscreen?.();
    }
    document.addEventListener("fullscreenchange", () => {
      setTimeout(() => fit(false), 120);
    });

    // ── Toolbar ──────────────────────────────────────────────
    const ORDER = ["down", "right", "radial"];
    const layoutBtn = mkBtn("◳", "Cycle layout", () => {
      layout = ORDER[(ORDER.indexOf(layout) + 1) % ORDER.length];
      layoutBtn.querySelector(".mm-btn-lbl").textContent = LAYOUTS[layout];
      update(view); setTimeout(() => fit(true), 60);
    }, LAYOUTS[layout]);

    bar.appendChild(mkBtn("⊕", "Expand all", expandAll));
    bar.appendChild(mkBtn("⊖", "Collapse", collapseAll));
    bar.appendChild(mkBtn("⤿", "Fit", () => fit(true, true)));
    bar.appendChild(layoutBtn);
    bar.appendChild(mkBtn("⤓", "PNG", exportPNG));
    bar.appendChild(mkBtn("⛶", "Fullscreen", toggleFs));

    // Breadcrumb (only visible once you've soloed into a branch)
    const crumb = document.createElement("div");
    crumb.className = "mm-crumb";
    bar.appendChild(crumb);

    function renderCrumb() {
      crumb.innerHTML = "";
      if (view === root) return;
      const home = document.createElement("button");
      home.className = "mm-crumb-btn"; home.textContent = "⌂ All";
      home.onclick = e => { e.stopPropagation(); focusHome(); };
      crumb.appendChild(home);
      view.ancestors().reverse().forEach((n, i, arr) => {
        const sep = document.createElement("span"); sep.className = "mm-crumb-sep"; sep.textContent = "›";
        crumb.appendChild(sep);
        const chip = document.createElement("button");
        chip.className = "mm-crumb-btn" + (n === view ? " active" : "");
        chip.textContent = n.data.name || "—";
        chip.onclick = e => { e.stopPropagation(); if (n !== view) { view = n; update(n); renderCrumb(); setTimeout(() => fit(true), 60); } };
        crumb.appendChild(chip);
      });
    }

    function mkBtn(icon, label, fn, lblText) {
      const b = document.createElement("button");
      b.className = "mm-btn";
      b.type = "button";
      b.title = label;
      b.innerHTML = `<span class="mm-btn-ico">${icon}</span><span class="mm-btn-lbl">${lblText || label}</span>`;
      b.onclick = e => { e.stopPropagation(); fn(); };
      return b;
    }

    // ── Boot ─────────────────────────────────────────────────
    update();
    requestAnimationFrame(() => fit(false));

    // Re-fit when the canvas is resized (initial sizing, fullscreen, panel
    // collapse/expand, window). This is what corrects the very first fit, which
    // runs before the flex layout has given the canvas its real height.
    if (window.ResizeObserver) {
      let raf;
      new ResizeObserver(() => {
        cancelAnimationFrame(raf);
        raf = requestAnimationFrame(() => {
          const [W, H] = dims();
          if (W > 1 && H > 1 && !rootDiv.classList.contains("mm-collapsed")) fit(false);
        });
      }).observe(canvas);
    }
  }

  // ── Treemap → tree converter ───────────────────────────────
  // Turns a Plotly treemap spec {labels,parents,values} into the
  // hierarchy render() consumes. Returns null when the data is a flat
  // wall of raw subscriber IDs (caller should fall back to a bar chart).
  function fromTreemap(spec) {
    const labels = spec.labels || [], parents = spec.parents || [], values = spec.values || [];
    if (!labels.length) return null;

    const idx = {}; labels.forEach((l, i) => { idx[l] = i; });
    const kidsOf = {};
    labels.forEach((l, i) => { (kidsOf[parents[i]] = kidsOf[parents[i]] || []).push(l); });
    const rootLabel = labels[parents.indexOf("") >= 0 ? parents.indexOf("") : 0];

    // bail on a flat grid of phone-number-style leaves
    const parentSet = new Set(parents);
    const leaves = labels.filter(l => !parentSet.has(l));
    const isId = s => /^\+?\d[\d\s-]{5,}$/.test(String(s).split(/\s[–-]\s/).pop().trim());
    const idLeaves = leaves.filter(isId);
    if (leaves.length >= 12 && idLeaves.length / leaves.length >= 0.6) return null;

    const COLORS = ["blue", "amber", "green", "purple", "red", "teal"];
    const fmt = v => {
      const n = Number(v);
      if (!isFinite(n)) return String(v);
      return Number.isInteger(n) ? n.toLocaleString()
                                 : n.toLocaleString(undefined, { maximumFractionDigits: 2 });
    };
    const SEP = /[–—\-]/;                 // – — -  (any dash)
    const strip = (label) => {
      if (label == null) return "";
      const s = String(label);
      const parent = parents[idx[s]];
      if (parent && parent !== "" && s.startsWith(parent)) {
        const rest = s.slice(parent.length).replace(/^[\s–—\-:>·|]+/, "").trim();
        if (rest) return rest;
      }
      const parts = s.split(new RegExp(`\\s${SEP.source}\\s`));
      return (parts[parts.length - 1] || s).trim() || s;
    };
    const build = (label, color, depth) => {
      const v = values[idx[label]];
      return {
        label: (depth === 0 ? String(label ?? "") : strip(label)) || "—",
        value: v != null ? fmt(v) : null,
        type:  depth === 0 ? "root" : "data",
        color: color,
        children: (kidsOf[label] || []).map((k, i) =>
          build(k, depth === 0 ? COLORS[i % COLORS.length] : color, depth + 1)),
      };
    };

    const r = build(rootLabel, null, 0);
    return { title: spec.title || "Breakdown", center: r.label, value: r.value, type: "root", nodes: r.children };
  }

  return { render, fromTreemap };
})();
