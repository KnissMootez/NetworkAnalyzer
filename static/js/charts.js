/* ═══════════════════════════════════════════════════════════
   charts.js — Plotly wrapper matching the dark telecom theme
   ═══════════════════════════════════════════════════════════ */

const Charts = (() => {
  // Nation-aware color palettes
  const NATION_COLORS = {
    fire:  ["#f97316","#ea580c","#fbbf24","#ef4444","#fb923c","#fde68a","#c2410c","#fed7aa"],
    earth: ["#84cc16","#65a30d","#d4a017","#4d7c0f","#a3e635","#fde68a","#3a5c09","#bef264"],
    water: ["#38bdf8","#0ea5e9","#7dd3fc","#0284c7","#a5f3fc","#6ee7b7","#0369a1","#e0f7ff"],
    air:   ["#fbbf24","#f59e0b","#fcd34d","#d97706","#fdba74","#fde68a","#b45309","#fed7aa"],
  };
  const DEFAULT_COLORS = ["#818cf8","#60a5fa","#a78bfa","#34d399","#fb923c","#94a3b8","#f472b6","#facc15"];

  function _getNation() {
    const cls = document.body.className;
    const m = cls.match(/theme-(\w+)/);
    return m ? m[1] : null;
  }

  function _colors() {
    const n = _getNation();
    return (n && NATION_COLORS[n]) || DEFAULT_COLORS;
  }

  function _bg() {
    return getComputedStyle(document.body).getPropertyValue('--mid').trim() || "#131f35";
  }

  function _border() {
    return getComputedStyle(document.body).getPropertyValue('--border').trim() || "#1e2f52";
  }

  function _textColor() {
    return getComputedStyle(document.body).getPropertyValue('--text').trim() || "#dce4f5";
  }

  function _layout(overrides) {
    const bg = _bg();
    const border = _border();
    const text = _textColor();
    const COLORS = _colors();
    return Object.assign({
      paper_bgcolor: bg,
      plot_bgcolor:  bg,
      font: { color: text, family: "Share Tech Mono, monospace", size: 10 },
      margin: { l: 40, r: 16, t: 32, b: 40 },
      colorway: COLORS,
      showlegend: false,
    }, overrides);
  }

  const AXIS_BASE = {
    tickfont: { size: 9 },
  };

  function _axis(overrides) {
    const border = _border();
    return Object.assign({}, AXIS_BASE, {
      gridcolor: border,
      linecolor: border,
      zerolinecolor: border,
    }, overrides);
  }

  const CONFIG = {
    responsive: true,
    displayModeBar: true,
    displaylogo: false,
    modeBarButtonsToRemove: ["select2d","lasso2d","hoverCompareCartesian","hoverClosestCartesian"],
    toImageButtonOptions: { format: "png", scale: 2 },
  };

  // ── Bar ────────────────────────────────────────────────────
  function bar(id, spec) {
    const xVals = (spec.x || []).map(v => String(v));
    const yVals = spec.y || spec.values || [];
    if (!yVals.length) { _noDataMsg(id, spec.title); return; }
    const COLORS = _colors();
    const colors = xVals.map((_, i) => COLORS[i % COLORS.length]);
    Plotly.newPlot(id, [{
      type: "bar", x: xVals, y: yVals,
      marker: { color: colors },
      text: yVals.map(v => typeof v === "number" ? v.toLocaleString() : v),
      textposition: "outside",
      textfont: { size: 9 },
    }], _layout({
      title: { text: spec.title || "", font: { size: 11 } },
      // category axis = discrete labelled bars (never plot ID-like labels on a numeric scale)
      xaxis: _axis({ title: spec.x_label || "", type: "category", automargin: true,
                     tickangle: xVals.length > 6 ? -35 : 0 }),
      yaxis: _axis({ title: spec.y_label || "" }),
    }), CONFIG);
  }

  // ── Pie / Donut ────────────────────────────────────────────
  function pie(id, spec) {
    const vals = spec.values || spec.y || [];
    // Don't render a pie when all slices are equal — it conveys nothing
    if (vals.length > 0 && vals.every(v => v === vals[0])) {
      _noDataMsg(id, spec.title);
      return;
    }
    Plotly.newPlot(id, [{
      type: "pie",
      labels: spec.labels || spec.x, values: vals,
      hole: 0.52,
      marker: { colors: _colors() },
      textinfo: "label+percent",
      textfont: { size: 9 },
    }], _layout({
      title: { text: spec.title || "", font: { size: 11 } },
      showlegend: true,
      legend: { font: { size: 9 }, bgcolor: "transparent", x: 1, y: 0.5 },
    }), CONFIG);
  }

  // ── Line ───────────────────────────────────────────────────
  function line(id, spec) {
    Plotly.newPlot(id, [{
      type: "scatter", mode: "lines+markers",
      x: spec.x, y: spec.y,
      line: { color: _colors()[0], width: 2 },
      marker: { size: 5, color: _colors()[0] },
    }], _layout({
      title: { text: spec.title || "", font: { size: 11 } },
      xaxis: _axis({}), yaxis: _axis({}),
    }), CONFIG);
  }

  // ── Area ───────────────────────────────────────────────────
  function area(id, spec) {
    Plotly.newPlot(id, [{
      type: "scatter", mode: "lines",
      x: spec.x, y: spec.y,
      fill: "tozeroy",
      fillcolor: `${_colors()[0]}26`,
      line: { color: _colors()[0], width: 2 },
    }], _layout({
      title: { text: spec.title || "", font: { size: 11 } },
      xaxis: _axis({}), yaxis: _axis({}),
    }), CONFIG);
  }

  // ── Treemap ────────────────────────────────────────────────
  // A label "looks like" a raw subscriber/phone id (mostly digits, 6+ long)
  function _looksLikeId(label) {
    const head = String(label).split(/\s[–-]\s/)[0].trim();   // strip " – Region" suffix
    return /^\+?\d[\d\s-]{5,}$/.test(head);
  }
  // Pull a trailing category off an id-style label: "2197… – Be Xing Se" -> "Be Xing Se"
  function _trailingCat(label) {
    const m = String(label).split(/\s[–-]\s/);
    return m.length > 1 ? m.slice(1).join(" – ").trim() : null;
  }

  // Per-tile colors: every top-level branch gets its own hue, deeper tiles fade
  function _treemapColors(labels, parents, rootLabel) {
    const COLORS = _colors();
    const parentOf = {};
    labels.forEach((l, i) => { parentOf[l] = parents[i]; });
    const topOf = (l) => {
      let cur = l, g = 0;
      while (parentOf[cur] && parentOf[cur] !== "" && parentOf[cur] !== rootLabel && g++ < 60) cur = parentOf[cur];
      return cur;
    };
    const depthOf = (l) => {
      let cur = l, d = 0, g = 0;
      while (parentOf[cur] && parentOf[cur] !== "" && g++ < 60) { cur = parentOf[cur]; d++; }
      return d;
    };
    const branch = {}; let ci = 0;
    return labels.map((l, i) => {
      if (parents[i] === "") return "#0e1a30";          // root tile
      const t = topOf(l);
      if (!(t in branch)) branch[t] = COLORS[ci++ % COLORS.length];
      const fade = ["", "e6", "bf", "99"][Math.min(depthOf(l) - 1, 3)] || "99";
      return branch[t] + fade;
    });
  }

  function treemap(id, spec) {
    const labels  = spec.labels  || [];
    const parents = spec.parents || [];
    const values  = spec.values  || [];
    if (!labels.length) { _noDataMsg(id, spec.title); return; }

    const rootIdx   = parents.indexOf("") >= 0 ? parents.indexOf("") : 0;
    const rootLabel = labels[rootIdx];
    const parentSet = new Set(parents);
    const leaves    = labels.filter(l => !parentSet.has(l));   // never a parent = leaf

    // ── Guard: a flat grid of raw subscriber IDs is unreadable ──
    const idLeaves = leaves.filter(_looksLikeId);
    const isIdGrid = leaves.length >= 12 && idLeaves.length / leaves.length >= 0.6;

    if (isIdGrid) {
      const _countBar = (counts) => {
        const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 20);
        return _horizBar(id, {
          title: spec.title && !/breakdown/i.test(spec.title) ? spec.title : "Subscribers by Group",
          cats: sorted.map(s => s[0]),
          vals: sorted.map(s => s[1]),
          x_label: "Subscribers",
        });
      };
      // 1a) Roll up by the leaf's parent node (region/segment), when it isn't the root
      const parentCats = {};
      let parented = 0;
      idLeaves.forEach(l => {
        const p = parents[labels.indexOf(l)];
        if (p && p !== "" && p !== rootLabel) { parentCats[p] = (parentCats[p] || 0) + 1; parented++; }
      });
      if (parented / idLeaves.length >= 0.6 && Object.keys(parentCats).length <= 25) {
        return _countBar(parentCats);
      }
      // 1b) Roll up by a trailing category parsed off the label ("… – Region")
      const cats = idLeaves.map(_trailingCat).filter(Boolean);
      if (cats.length / idLeaves.length >= 0.6) {
        const counts = {};
        cats.forEach(c => { counts[c] = (counts[c] || 0) + 1; });
        return _countBar(counts);
      }
      // 2) Values vary -> top-N by value, horizontal
      const leafVals = leaves.map(l => values[labels.indexOf(l)] || 0);
      const vary = new Set(leafVals).size > 1;
      if (vary) {
        const pairs = leaves.map((l, i) => [l, leafVals[i]])
          .sort((a, b) => b[1] - a[1]).slice(0, 15);
        return _horizBar(id, {
          title: spec.title || "Top Subscribers",
          cats: pairs.map(p => String(p[0]).split(/\s[–-]\s/)[0]),
          vals: pairs.map(p => p[1]),
          x_label: spec.value_label || "Value",
        });
      }
      // 3) Pure id list, nothing to aggregate -> clean summary card
      return _summaryCard(id, leaves.length, spec.title);
    }

    // ── Pretty hierarchical treemap ─────────────────────────────
    const TREEMAP_CONFIG = Object.assign({}, CONFIG, { scrollZoom: true });
    Plotly.newPlot(id, [{
      type: "treemap",
      labels, parents, values,
      branchvalues: "total",
      textinfo: "label+value+percent parent",
      textposition: "middle center",
      textfont: { size: 12, family: "Share Tech Mono, monospace", color: "#f1f6ff" },
      insidetextfont: { size: 12, color: "#f1f6ff" },
      outsidetextfont: { size: 11, color: _textColor() },
      pathbar: { visible: true, thickness: 26, side: "top",
                 textfont: { size: 12, color: "#cfe0ff", family: "Share Tech Mono, monospace" } },
      tiling: { pad: 3, packing: "squarify" },
      marker: {
        colors: _treemapColors(labels, parents, rootLabel),
        cornerradius: 6,
        line: { width: 2, color: _bg() },
      },
      hoverlabel: { bgcolor: "#0a111e", bordercolor: _border(),
                    font: { family: "Share Tech Mono, monospace", size: 12, color: "#e2eaf8" } },
      hovertemplate: "<b>%{label}</b><br>%{value}<br>%{percentParent:.1%} of parent · %{percentRoot:.1%} of total<extra></extra>",
    }], _layout({
      title: { text: spec.title || "", font: { size: 11 } },
      paper_bgcolor: _bg(),
      margin: { l: 6, r: 6, t: spec.title ? 34 : 8, b: 6 },
    }), TREEMAP_CONFIG);
  }

  // Horizontal bar — readable for many categories (treemap-grid fallback)
  function _horizBar(id, spec) {
    const COLORS = _colors();
    // largest at top
    const cats = spec.cats.slice().reverse();
    const vals = spec.vals.slice().reverse();
    const colors = cats.map((_, i) => COLORS[(cats.length - 1 - i) % COLORS.length]);
    Plotly.newPlot(id, [{
      type: "bar", orientation: "h",
      x: vals, y: cats,
      marker: { color: colors, line: { width: 0 } },
      text: vals.map(v => typeof v === "number" ? v.toLocaleString() : v),
      textposition: "auto", textfont: { size: 10, color: "#f1f6ff" },
      hovertemplate: "<b>%{y}</b><br>%{x}<extra></extra>",
    }], _layout({
      title: { text: spec.title || "", font: { size: 11 } },
      margin: { l: 130, r: 24, t: spec.title ? 34 : 10, b: 36 },
      xaxis: _axis({ title: spec.x_label || "" }),
      yaxis: _axis({ automargin: true, ticksuffix: "  " }),
      bargap: 0.28,
    }), CONFIG);
  }

  // Clean info card when a treemap would just be a wall of IDs
  function _summaryCard(id, count, title) {
    const el = document.getElementById(id);
    if (!el) return;
    el.style.display = "flex";
    el.style.alignItems = "center";
    el.style.justifyContent = "center";
    el.innerHTML =
      `<div style="text-align:center;font-family:'Share Tech Mono',monospace">
         <div style="font-size:2.4rem;color:#52a8ff;font-weight:700;line-height:1">${count.toLocaleString()}</div>
         <div style="font-size:.8rem;color:#e2eaf8;margin-top:6px;letter-spacing:.05em">${title || "subscribers in this set"}</div>
         <div style="font-size:.62rem;color:#6b87a8;margin-top:10px">A per-subscriber chart isn't meaningful here —<br>use <b style="color:#34d399">⬇ Export Data</b> for the full list.</div>
       </div>`;
  }

  // ── Scatter ────────────────────────────────────────────────
  function scatter(id, spec) {
    Plotly.newPlot(id, [{
      type: "scatter", mode: "markers",
      x: spec.x, y: spec.y,
      marker: {
        size: spec.size || 10,
        color: _colors()[0],
        opacity: 0.7,
      },
      text: spec.labels || [],
      hoverinfo: "text+x+y",
    }], _layout({
      title: { text: spec.title || "", font: { size: 11 } },
      xaxis: _axis({ title: spec.x_label || "" }),
      yaxis: _axis({ title: spec.y_label || "" }),
    }), CONFIG);
  }

  // ── Histogram ─────────────────────────────────────────────
  function histogram(id, spec) {
    Plotly.newPlot(id, [{
      type: "histogram", x: spec.x,
      marker: { color: _colors()[0] },
    }], _layout({
      title: { text: spec.title || "", font: { size: 11 } },
      xaxis: _axis({}), yaxis: _axis({}),
    }), CONFIG);
  }

  // ── Multibar ───────────────────────────────────────────────
  function multibar(id, spec) {
    // spec.y is an object: { series_name: [values...] }
    const traces = Object.entries(spec.y).map(([name, vals], i) => ({
      type: "bar", name, x: spec.x, y: vals,
      marker: { color: _colors()[i % _colors().length] },
    }));
    Plotly.newPlot(id, traces, _layout({
      title: { text: spec.title || "", font: { size: 11 } },
      barmode: "group",
      showlegend: true,
      legend: { font: { size: 9 }, bgcolor: "transparent" },
      xaxis: _axis({}), yaxis: _axis({}),
    }), CONFIG);
  }

  // ── Dual axis (bar + line) ─────────────────────────────────
  function dualAxis(id, spec) {
    // spec.y1 = bar data, spec.y2 = line data, spec.y1_label, spec.y2_label
    Plotly.newPlot(id, [
      { type: "bar", x: spec.x, y: spec.y1, name: spec.y1_label || "bar",
        marker: { color: _colors()[0] }, yaxis: "y" },
      { type: "scatter", mode: "lines+markers", x: spec.x, y: spec.y2,
        name: spec.y2_label || "line",
        line: { color: _colors()[2], width: 2 }, marker: { size: 5 }, yaxis: "y2" },
    ], _layout({
      title: { text: spec.title || "", font: { size: 11 } },
      showlegend: true,
      legend: { font: { size: 9 }, bgcolor: "transparent" },
      xaxis: _axis({}),
      yaxis:  _axis({ title: spec.y1_label || "" }),
      yaxis2: _axis({ title: spec.y2_label || "", overlaying: "y", side: "right" }),
    }), CONFIG);
  }

  // ── Dispatch ───────────────────────────────────────────────
  function _hasData(spec) {
    // Returns true if spec has at least one non-empty data array
    if (Array.isArray(spec.y) && spec.y.length > 0) return true;
    if (Array.isArray(spec.values) && spec.values.length > 0) return true;
    if (Array.isArray(spec.x) && spec.x.length > 0) return true;
    if (Array.isArray(spec.labels) && spec.labels.length > 0) return true;
    return false;
  }

  function _noDataMsg(id, title) {
    const el = document.getElementById(id);
    if (!el) return;
    el.style.display = "flex";
    el.style.alignItems = "center";
    el.style.justifyContent = "center";
    el.innerHTML = `<span style="color:#94a3b8;font-size:12px;font-family:'Share Tech Mono',monospace">
      ${title ? `<strong>${title}</strong><br>` : ""}No chart data available</span>`;
  }

  function render(id, spec) {
    if (!spec || !spec.type) return;
    if (!_hasData(spec)) { _noDataMsg(id, spec.title); return; }
    const t = spec.type.toLowerCase();
    try {
      if (t === "bar")       return bar(id, spec);
      if (t === "pie")       return pie(id, spec);
      if (t === "line")      return line(id, spec);
      if (t === "area")      return area(id, spec);
      if (t === "treemap")   return treemap(id, spec);
      if (t === "scatter")   return scatter(id, spec);
      if (t === "histogram") return histogram(id, spec);
      if (t === "multibar")  return multibar(id, spec);
      if (t === "dual_axis") return dualAxis(id, spec);
      // Fallback: try bar if x/y arrays present
      if (spec.x && spec.y && Array.isArray(spec.y)) return bar(id, spec);
      _noDataMsg(id, spec.title);
    } catch(e) {
      console.warn("[Charts.render] error:", e, spec);
      _noDataMsg(id, spec.title);
    }
  }

  return { render, bar, pie, line, area, treemap, scatter, histogram, multibar, dualAxis };
})();
