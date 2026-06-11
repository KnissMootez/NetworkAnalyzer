/* ═══════════════════════════════════════════════════════════
   strategy_diagram.js — Dynamic subscriber strategy flow diagram
   ═══════════════════════════════════════════════════════════ */

const StrategyDiagram = (() => {

  const COLOR_MAP = {
    red:    { bg: "#ff4444", light: "#2a1010", border: "#ff6666", text: "#ffaaaa" },
    amber:  { bg: "#ff9900", light: "#2a1a00", border: "#ffbb44", text: "#ffd080" },
    blue:   { bg: "#2979ff", light: "#0d1a33", border: "#5599ff", text: "#99bbff" },
    purple: { bg: "#9c27b0", light: "#1e0a26", border: "#cc44dd", text: "#dd88ee" },
    green:  { bg: "#00cc66", light: "#003319", border: "#44ee88", text: "#88ffbb" },
    teal:   { bg: "#00bcd4", light: "#002a30", border: "#44ddee", text: "#88eeff" },
  };

  function _colors(name) {
    return COLOR_MAP[name] || COLOR_MAP["blue"];
  }

  function _fmt(n) {
    if (n == null) return "";
    return Number(n).toLocaleString();
  }

  /**
   * Render a strategy_diagram JSON object into containerEl.
   * @param {HTMLElement} containerEl
   * @param {Object} diagram  — { title, segments, strategies }
   */
  function render(containerEl, diagram) {
    if (!diagram || !diagram.segments || !diagram.strategies) return;

    containerEl.innerHTML = "";
    containerEl.className = "strat-diagram";

    // Title
    const title = document.createElement("div");
    title.className = "strat-title";
    title.textContent = diagram.title || "Migration Strategy";
    containerEl.appendChild(title);

    // Main layout: segments | arrows | strategies
    const layout = document.createElement("div");
    layout.className = "strat-layout";
    containerEl.appendChild(layout);

    // Left: segments column
    const segCol = document.createElement("div");
    segCol.className = "strat-col strat-col-segs";
    layout.appendChild(segCol);

    // Middle: connector arrows column (SVG)
    const arrowCol = document.createElement("div");
    arrowCol.className = "strat-col strat-col-arrows";
    layout.appendChild(arrowCol);

    // Right: strategies column
    const strCol = document.createElement("div");
    strCol.className = "strat-col strat-col-strats";
    layout.appendChild(strCol);

    // Legend — one swatch per unique segment color, ordered by urgency
    const URGENCY_ORDER = ["red", "amber", "purple", "blue", "teal", "green"];
    const usedColors = [...new Map(
      diagram.segments.map(x => [x.color, x])
    ).values()].sort((a, b) => URGENCY_ORDER.indexOf(a.color) - URGENCY_ORDER.indexOf(b.color));

    const COLOR_LABELS = {
      red:    "Highest priority — full device replacement needed",
      amber:  "Medium priority — device swap or plan change",
      purple: "Voice migration — VoLTE activation required",
      blue:   "Low effort — software / config change only",
      teal:   "FWA candidates — fixed wireless opportunity",
      green:  "Upsell opportunity — revenue growth potential",
    };

    if (usedColors.length) {
      const legend = document.createElement("div");
      legend.className = "strat-legend";
      usedColors.forEach(item => {
        const c = _colors(item.color);
        const entry = document.createElement("div");
        entry.className = "strat-legend-entry";
        const swatch = document.createElement("span");
        swatch.className = "strat-legend-swatch";
        swatch.style.background = c.bg;
        const lbl = document.createElement("span");
        lbl.className = "strat-legend-text";
        lbl.textContent = COLOR_LABELS[item.color] || item.color;
        entry.appendChild(swatch);
        entry.appendChild(lbl);
        legend.appendChild(entry);
      });
      containerEl.appendChild(legend);
    }

    // Build a map from strategy label → index
    const stratIndex = {};
    diagram.strategies.forEach((s, i) => { stratIndex[s.label] = i; });

    // Render segment boxes & record their positions for arrow drawing
    const segEls = [];
    diagram.segments.forEach((seg) => {
      const c = _colors(seg.color);
      const box = document.createElement("div");
      box.className = "strat-seg-box";
      box.style.borderColor = c.border;
      box.style.background  = c.light;

      const label = document.createElement("div");
      label.className = "strat-seg-label";
      label.style.color = c.text;
      label.textContent = seg.label;
      box.appendChild(label);

      if (seg.count != null) {
        const badge = document.createElement("div");
        badge.className = "strat-seg-count";
        badge.style.background = c.bg;
        badge.textContent = _fmt(seg.count) + " subs";
        box.appendChild(badge);
      }

      if (seg.strategy) {
        const arrow = document.createElement("div");
        arrow.className = "strat-seg-arrow-hint";
        arrow.style.color = c.border;
        arrow.textContent = "→ " + seg.strategy;
        box.appendChild(arrow);
      }

      segCol.appendChild(box);
      segEls.push({ el: box, seg });
    });

    // Render strategy boxes
    const stratEls = [];
    diagram.strategies.forEach((strat) => {
      const c = _colors(strat.color);
      const box = document.createElement("div");
      box.className = "strat-strat-box";
      box.style.borderColor = c.border;
      box.style.background  = c.light;

      const header = document.createElement("div");
      header.className = "strat-strat-header";

      const lbl = document.createElement("span");
      lbl.className = "strat-strat-label";
      lbl.style.color = c.text;
      lbl.textContent = strat.label;
      header.appendChild(lbl);

      if (strat.tier) {
        const tier = document.createElement("span");
        tier.className = "strat-tier-badge";
        tier.style.background = c.bg;
        tier.textContent = strat.tier;
        header.appendChild(tier);
      }

      box.appendChild(header);

      if (strat.actions && strat.actions.length) {
        const ul = document.createElement("ul");
        ul.className = "strat-actions";
        strat.actions.forEach(a => {
          const li = document.createElement("li");
          li.textContent = a;
          ul.appendChild(li);
        });
        box.appendChild(ul);
      }

      strCol.appendChild(box);
      stratEls.push({ el: box, strat });
    });

    // Draw SVG connectors after layout is in DOM
    requestAnimationFrame(() => _drawArrows(arrowCol, segEls, stratEls, diagram, stratIndex));
  }

  function _drawArrows(arrowCol, segEls, stratEls, diagram, stratIndex) {
    const arrowRect = arrowCol.getBoundingClientRect();
    if (!arrowRect.width) return; // not visible yet

    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("width",  "100%");
    svg.setAttribute("height", "100%");
    svg.style.position = "absolute";
    svg.style.top = "0"; svg.style.left = "0";
    svg.style.overflow = "visible";
    arrowCol.style.position = "relative";
    arrowCol.appendChild(svg);

    diagram.segments.forEach((seg, si) => {
      if (!seg.strategy) return;
      const ti = stratIndex[seg.strategy];
      if (ti == null) return;

      const segRect   = segEls[si].el.getBoundingClientRect();
      const stratRect = stratEls[ti].el.getBoundingClientRect();

      const x1 = 0;
      const y1 = segRect.top   + segRect.height / 2 - arrowRect.top;
      const x2 = arrowRect.width;
      const y2 = stratRect.top + stratRect.height / 2 - arrowRect.top;

      const c = _colors(seg.color);

      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      const cx1 = arrowRect.width * 0.4;
      const cx2 = arrowRect.width * 0.6;
      path.setAttribute("d", `M ${x1} ${y1} C ${cx1} ${y1}, ${cx2} ${y2}, ${x2} ${y2}`);
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", c.border);
      path.setAttribute("stroke-width", "2");
      path.setAttribute("opacity", "0.7");
      svg.appendChild(path);

      // Arrow head
      const arrow = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
      arrow.setAttribute("points", `${x2},${y2} ${x2-8},${y2-4} ${x2-8},${y2+4}`);
      arrow.setAttribute("fill", c.border);
      arrow.setAttribute("opacity", "0.8");
      svg.appendChild(arrow);
    });
  }

  return { render };
})();
