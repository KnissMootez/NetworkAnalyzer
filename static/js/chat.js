/* ═══════════════════════════════════════════════════════════
   chat.js — Streaming chat UI
   ═══════════════════════════════════════════════════════════ */

const Chat = (() => {
  let streaming     = false;
  let streamEl      = null;   // <div> currently receiving tokens
  let thinkBuf      = "";     // think tokens accumulator
  let thinkEl       = null;   // <div> inside think <details>
  let stepsEl       = null;   // <div> inside steps <details>
  let chartContEl   = null;   // container for agent chart
  let pendingProposal = false;
  let stepCount     = 0;

  // ── Render a user message ─────────────────────────────────
  function addUser(text) {
    const div = document.createElement("div");
    div.className = "msg-user";
    div.textContent = text;
    _append(div);
  }

  // ── Start a new streaming agent bubble ───────────────────
  function startStream() {
    streaming  = true;
    thinkBuf   = "";
    stepCount  = 0;

    const wrap = document.createElement("div");
    wrap.className = "msg-agent stream-cursor";

    const lbl = document.createElement("div");
    lbl.className = "msg-label";
    lbl.textContent = "COPILOT";
    wrap.appendChild(lbl);

    const body = document.createElement("div");
    body.className = "msg-body";
    wrap.appendChild(body);

    streamEl = body;

    // Think expander (hidden until think tokens arrive, then opens automatically)
    const thinkDetails = document.createElement("details");
    thinkDetails.className = "think-block";
    thinkDetails.style.display = "none";
    thinkDetails.open = true;
    thinkDetails.innerHTML = `<summary>💭 thinking...</summary>`;
    const thinkContent = document.createElement("div");
    thinkContent.className = "think-content";
    thinkDetails.appendChild(thinkContent);
    thinkEl = thinkContent;
    wrap.appendChild(thinkDetails);

    // Steps expander (hidden until step events arrive)
    const stepsDetails = document.createElement("details");
    stepsDetails.className = "steps-block";
    stepsDetails.style.display = "none";
    stepsDetails.innerHTML = `<summary>◈ reasoning steps</summary>`;
    const stepsContent = document.createElement("div");
    stepsContent.className = "steps-content";
    stepsDetails.appendChild(stepsContent);
    stepsEl = stepsContent;
    wrap.appendChild(stepsDetails);

    // Chart slot
    const chartCont = document.createElement("div");
    chartCont.className = "msg-chart";
    chartCont.style.display = "none";
    chartContEl = chartCont;
    wrap.appendChild(chartCont);

    _append(wrap);
    return wrap;
  }

  // ── Token arrives ─────────────────────────────────────────
  function appendToken(token) {
    if (!streamEl) return;
    streamEl.textContent += token;
    _scrollBottom();
  }

  // ── Think token ───────────────────────────────────────────
  function appendThinkToken(token) {
    thinkBuf += token;
    if (thinkEl) {
      thinkEl.textContent = thinkBuf;
      const details = thinkEl.closest("details");
      if (details) details.style.display = "";
      _scrollBottom();
    }
  }

  // ── Step SQL event ────────────────────────────────────────
  function addStep(ev) {
    if (!stepsEl) return;
    stepCount++;
    const details = stepsEl.closest("details");
    if (details) {
      details.style.display = "";
      details.querySelector("summary").textContent = `◈ ${stepCount} reasoning step${stepCount > 1 ? "s" : ""}`;
    }
    const row = document.createElement("div");
    row.innerHTML = `<span class="step-sql">▸ ${ev.tag}: ${_esc(ev.sql)}</span> `
                  + `<span class="step-result">(${ev.rows} rows)</span>`;
    stepsEl.appendChild(row);
    _scrollBottom();
  }

  // ── Done — finalise the bubble ────────────────────────────
  function finalize(result) {
    streaming = false;
    if (!streamEl) return;

    const wrap = streamEl.closest(".msg-agent");
    if (wrap) wrap.classList.remove("stream-cursor");

    // Flash the offending cell sites on the coverage map for "issues" questions.
    // A non-issue answer sends an empty list, which just clears any stale flash.
    if (typeof App !== "undefined" && App.flashCoverage) {
      try { App.flashCoverage(result.map_flash || []); } catch (e) {}
    }

    // Replace streamed text with clean result text (handles markdown bolding etc.)
    if (result.text) streamEl.textContent = result.text;

    // Elapsed time + action bar
    if (wrap) {
      const bar = document.createElement("div");
      bar.className = "msg-action-bar";
      if (result.elapsed) {
        const t = document.createElement("span");
        t.className = "msg-elapsed";
        t.textContent = `⏱ ${result.elapsed}s`;
        bar.appendChild(t);
      }
      // Copy button
      const cp = document.createElement("button");
      cp.className = "msg-action-btn";
      cp.textContent = "⎘ Copy";
      cp.onclick = () => {
        navigator.clipboard.writeText(result.text || streamEl?.textContent || "");
        cp.textContent = "✓ Copied";
        setTimeout(() => cp.textContent = "⎘ Copy", 1500);
      };
      bar.appendChild(cp);
      // Fullscreen button
      const fs = document.createElement("button");
      fs.className = "msg-action-btn";
      fs.textContent = "⛶ Fullscreen";
      fs.onclick = () => _fullscreen(wrap);
      bar.appendChild(fs);

      // Strategy Diagram + Mind Map buttons — silent extensions, no user bubble
      if (result.response_type !== "proposal" && result.response_type !== "error") {
        const sdBtn = document.createElement("button");
        sdBtn.className = "msg-action-btn msg-stratmap-btn";
        sdBtn.textContent = "◈ Strategy Diagram";
        sdBtn.onclick = () => {
          if (typeof App !== "undefined") App.sendSilent(
            "Based on the analysis above, output CONCLUDE with a short \"text\" field (one sentence) and the strategy_diagram field populated. " +
            "Use real segment counts from the data. Do NOT include mindmap. Do NOT re-run any queries."
          );
        };
        bar.appendChild(sdBtn);

        const mmBtn = document.createElement("button");
        mmBtn.className = "msg-action-btn msg-mindmap-btn";
        mmBtn.textContent = "⬡ Mind Map";
        mmBtn.onclick = () => {
          if (typeof App !== "undefined") App.sendSilent(
            'Based on the analysis above, generate a mind map. Output ONLY this line, nothing else: ' +
            'CONCLUDE: {"text":"<one sentence>","mindmap":{"center":"<3-5 word topic>","nodes":[' +
            '{"id":"n1","label":"<top region or segment>","value":"<real count>","color":"red","type":"data","children":[' +
            '{"id":"n1a","label":"<key fact about this segment>","type":"data","color":"red"},' +
            '{"id":"n1s","label":"<specific action to take>","type":"suggestion","color":"red"}]},' +
            '{"id":"n2","label":"<second region or segment>","value":"<real count>","color":"amber","type":"data","children":[' +
            '{"id":"n2a","label":"<key fact>","type":"data","color":"amber"},' +
            '{"id":"n2s","label":"<specific action>","type":"suggestion","color":"amber"}]},' +
            '{"id":"n3","label":"<third segment>","value":"<real count>","color":"blue","type":"data","children":[' +
            '{"id":"n3s","label":"<action>","type":"suggestion","color":"blue"}]}]}} ' +
            'Use 3-5 top branches from real data. Each branch needs at least one suggestion child. ' +
            'Do NOT include chart, strategy_diagram, or recommendations. Do NOT re-run queries.'
          );
        };
        bar.appendChild(mmBtn);
      }

      // Export MSISDNs button
      if (result.extract_sql) {
        const expBtn = document.createElement("button");
        expBtn.className = "msg-action-btn msg-export-btn";
        expBtn.textContent = "⬇ Export Data";
        expBtn.onclick = () => _exportMsisdn(result.extract_sql, expBtn, result);
        bar.appendChild(expBtn);

        // Send to Ops Portal dropdown
        const opsWrap = document.createElement("div");
        opsWrap.className = "msg-ops-wrap";

        const opsBtn = document.createElement("button");
        opsBtn.className = "msg-action-btn msg-ops-btn";
        opsBtn.textContent = "↗ Send to Ops";

        const opsMenu = document.createElement("div");
        opsMenu.className = "msg-ops-menu";
        opsMenu.style.display = "none";

        const opsActions = [
          { label: "📋 Campaign Manager", type: "campaign" },
          { label: "💬 SMS Queue",        type: "sms"      },
          { label: "📊 Send Export to Reports", type: "report" },
          { label: "📡 Flag Network Team",type: "network_flag" },
        ];

        opsActions.forEach(({ label, type }) => {
          const item = document.createElement("div");
          item.className = "msg-ops-item";
          item.textContent = label;
          item.onclick = () => {
            opsMenu.style.display = "none";
            _promptAttachCsv(type, result, opsBtn, opsWrap);
          };
          opsMenu.appendChild(item);
        });

        opsBtn.onclick = (e) => {
          e.stopPropagation();
          const open = opsMenu.style.display !== "none";
          document.querySelectorAll(".msg-ops-menu").forEach(m => m.style.display = "none");
          opsMenu.style.display = open ? "none" : "block";
        };

        document.addEventListener("click", () => { opsMenu.style.display = "none"; }, { once: false });

        opsWrap.appendChild(opsBtn);
        opsWrap.appendChild(opsMenu);
        bar.appendChild(opsWrap);
      }

      // Recommendations button
      if (result.recommendations && result.recommendations.length) {
        const recBtn = document.createElement("button");
        recBtn.className = "msg-action-btn msg-rec-btn";
        recBtn.textContent = "💡 Recommendations";
        const recBox = document.createElement("div");
        recBox.className = "msg-rec-box";
        recBox.style.display = "none";
        result.recommendations.forEach((r, i) => {
          const item = document.createElement("div");
          item.className = "msg-rec-item";
          const label = document.createElement("span");
          label.className = "msg-rec-num";
          label.textContent = `${i + 1}`;
          const text = document.createElement("p");
          text.className = "msg-rec-text";
          text.textContent = typeof r === "string" ? r : (r.text || r.recommendation || r.action || (r.title && r.description ? `${r.title} — ${r.description}` : r.title || r.description || JSON.stringify(r)));
          item.appendChild(label);
          item.appendChild(text);
          recBox.appendChild(item);
        });
        recBtn.onclick = () => {
          const visible = recBox.style.display !== "none";
          recBox.style.display = visible ? "none" : "block";
          recBtn.classList.toggle("active", !visible);
        };
        bar.appendChild(recBtn);
        wrap.appendChild(bar);
        wrap.appendChild(recBox);
      } else {
        wrap.appendChild(bar);
      }
    }

    // Proposal styling
    if (result.response_type === "proposal") {
      wrap && wrap.classList.add("msg-proposal");
      wrap && (wrap.querySelector(".msg-label").textContent = "PROPOSAL — AWAITING CONFIRMATION");
      document.getElementById("proposal-actions").style.display = "flex";
      pendingProposal = true;
    }
    if (result.response_type === "error")     wrap && wrap.classList.add("msg-error");
    if (result.response_type === "success")   wrap && wrap.classList.add("msg-success");
    if (result.response_type === "cancelled") wrap && wrap.classList.add("msg-error");

    // Clarify — drill-down path choices and/or a numeric baseline input
    if (result.response_type === "clarify" && wrap &&
        ((Array.isArray(result.options) && result.options.length) || (result.input && result.input.template))) {
      const opts = document.createElement("div");
      opts.className = "clarify-options";
      const lockAll = () => opts.querySelectorAll("button,input").forEach(x => x.disabled = true);

      // numeric baseline input — type your own threshold instead of "above average"
      if (result.input && result.input.template) {
        const row = document.createElement("div");
        row.className = "clarify-input-row";
        const field = document.createElement("input");
        field.type = "number";
        field.className = "clarify-input";
        field.placeholder = result.input.placeholder || "enter a number";
        const submit = () => {
          const v = field.value.trim();
          if (v === "") { field.focus(); return; }
          lockAll();
          apply.classList.add("picked");
          if (typeof App !== "undefined") App.sendSilent(result.input.template.replace("{value}", v));
        };
        const apply = document.createElement("button");
        apply.className = "clarify-btn clarify-apply";
        apply.textContent = "Apply";
        apply.onclick = submit;
        field.addEventListener("keydown", e => { if (e.key === "Enter") submit(); });
        row.appendChild(field);
        if (result.input.unit) {
          const u = document.createElement("span");
          u.className = "clarify-unit";
          u.textContent = result.input.unit;
          row.appendChild(u);
        }
        row.appendChild(apply);
        opts.appendChild(row);
      }

      // preset / "let the agent decide" buttons
      (result.options || []).forEach(o => {
        const b = document.createElement("button");
        b.className = "clarify-btn";
        b.innerHTML = `<span class="clarify-arrow">▸</span> ${_esc(o.label)}`;
        b.onclick = () => {
          lockAll();
          b.classList.add("picked");
          // sendSilent: run the refined query without echoing it as a user
          // bubble — the picked chip is the visible record of the choice
          if (typeof App !== "undefined") App.sendSilent(o.query);
        };
        opts.appendChild(b);
      });

      wrap.appendChild(opts);
    }

    // Chart
    if (result.chart && chartContEl) {
      chartContEl.style.display = "";

      // Hierarchical "Breakdown" treemaps render as an interactive drill-down
      // tree instead of a cramped Plotly treemap (raw-ID grids return null → bar)
      let _tree = null;
      if (result.chart.type === "treemap" && typeof Mindmap !== "undefined") {
        try { _tree = Mindmap.fromTreemap(result.chart); } catch (e) { _tree = null; }
      }
      if (_tree) {
        chartContEl.innerHTML = "";
        const mmCont = document.createElement("div");
        mmCont.className = "mindmap-wrap";
        chartContEl.appendChild(mmCont);
        setTimeout(() => Mindmap.render(mmCont, _tree), 60);
      } else {
        const id = "chart-" + Date.now();
        chartContEl.innerHTML = `<div id="${id}" class="chart-box" style="height:280px;width:100%"></div>`;
        // Force reflow so Plotly sees real dimensions, then render
        const _capturedChart = result.chart;
        const _capturedId    = id;
        setTimeout(() => {
          const el = document.getElementById(_capturedId);
          if (el) {
            void el.offsetWidth; // force reflow
            Charts.render(_capturedId, _capturedChart);
            // Second pass in case first render had 0 width
            setTimeout(() => { try { Plotly.relayout(_capturedId, {}); } catch(e) {} }, 150);
          }
        }, 50);
      }
    }

    // Strategy diagram
    if (result.strategy_diagram && wrap) {
      const diagCont = document.createElement("div");
      wrap.appendChild(diagCont);
      setTimeout(() => StrategyDiagram.render(diagCont, result.strategy_diagram), 80);
    }

    // Mind map (builds its own toolbar + legend)
    if (result.mindmap && wrap) {
      const mmCont = document.createElement("div");
      mmCont.className = "mindmap-wrap";
      wrap.appendChild(mmCont);
      setTimeout(() => Mindmap.render(mmCont, result.mindmap), 80);
    }

    // Full think log (from non-streaming run)
    if (result.think_log && result.think_log.length && !thinkBuf) {
      if (thinkEl) {
        thinkEl.textContent = result.think_log.join("\n\n---\n\n");
        const d = thinkEl.closest("details");
        if (d) d.style.display = "";
      }
    }

    streamEl    = null;
    thinkEl     = null;
    stepsEl     = null;
    chartContEl = null;
    _scrollBottom();
  }

  function clearPending() {
    document.getElementById("proposal-actions").style.display = "none";
    pendingProposal = false;
  }

  function clear() {
    document.getElementById("chat-messages").innerHTML = `
      <div class="chat-welcome">
        <div class="welcome-title">READY FOR ANALYSIS</div>
        <div class="welcome-sub">Ask about network KPIs · subscriber campaigns<br>5G upsell · FWA conversion · 3G migration</div>
      </div>`;
    clearPending();
    streaming = false;
    streamEl  = null;
  }

  function isStreaming() { return streaming; }
  function hasPending()  { return pendingProposal; }

  function _append(el) {
    const container = document.getElementById("chat-messages");
    const welcome = container.querySelector(".chat-welcome");
    if (welcome) welcome.remove();
    container.appendChild(el);
    _scrollBottom();
  }

  function _scrollBottom() {
    const c = document.getElementById("chat-messages");
    // Only auto-scroll if user is already near the bottom (within 150px)
    if (c.scrollHeight - c.scrollTop - c.clientHeight < 150)
      c.scrollTop = c.scrollHeight;
  }

  function _esc(s) {
    return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  }

  function _fullscreen(el) {
    if (!document.fullscreenElement) {
      el.requestFullscreen().catch(() => {});
    } else {
      document.exitFullscreen();
    }
  }

  function currentText() { return streamEl ? streamEl.textContent : ""; }

  async function _exportMsisdn(sql, btn, result) {
    const orig = btn.textContent;
    btn.textContent = "⏳ Extracting...";
    btn.disabled = true;
    try {
      const res = await fetch("/export/msisdn", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sql, filename: "subscribers_export.csv" })
      });
      if (!res.ok) {
        const err = await res.json();
        alert("Export failed: " + (err.detail || res.statusText));
        btn.textContent = orig;
        btn.disabled = false;
        return;
      }
      const blob = await res.blob();
      const url  = URL.createObjectURL(blob);
      const a    = document.createElement("a");
      a.href     = url;
      a.download = "export.csv";
      a.click();
      URL.revokeObjectURL(url);
      btn.textContent = "✓ Downloaded";
      setTimeout(() => { btn.textContent = orig; btn.disabled = false; }, 2000);
    } catch (e) {
      alert("Export error: " + e.message);
      btn.textContent = orig;
      btn.disabled = false;
    }
  }

  function _promptAttachCsv(type, result, btn, opsWrap) {
    // Remove any existing prompt
    const existing = opsWrap.querySelector(".msg-ops-attach-prompt");
    if (existing) existing.remove();

    const prompt = document.createElement("div");
    prompt.className = "msg-ops-attach-prompt";
    prompt.innerHTML = `
      <span>Attach CSV?</span>
      <button class="msg-ops-attach-yes">Yes</button>
      <button class="msg-ops-attach-no">No</button>
    `;

    prompt.querySelector(".msg-ops-attach-yes").onclick = () => {
      prompt.remove();
      _sendToOps(type, result, btn, true);
    };
    prompt.querySelector(".msg-ops-attach-no").onclick = () => {
      prompt.remove();
      _sendToOps(type, result, btn, false);
    };

    opsWrap.appendChild(prompt);
  }

  async function _sendToOps(type, result, btn, attachCsv = false) {
    const orig = btn.textContent;
    btn.textContent = "⏳ Sending...";
    btn.disabled = true;

    // Grab the rendered answer text from the DOM — most reliable source
    const msgWrap    = btn.closest(".msg-wrap");
    const answerEl   = msgWrap ? msgWrap.querySelector(".msg-body") : null;
    const answerText = (answerEl ? answerEl.textContent : result.text || "").trim();
    const firstLine  = answerText.split(".")[0].trim().slice(0, 100);
    const title      = firstLine || `Agent ${type} — ${new Date().toLocaleString()}`;

    const payload = {};
    if (result.extract_sql)     payload.sql             = result.extract_sql;
    if (result.recommendations) payload.recommendations = result.recommendations;

    // For sms and campaign — resolve real MSISDNs from the SQL before sending
    if ((type === 'sms' || type === 'campaign') && result.extract_sql) {
      try {
        const r = await fetch("/export/msisdns", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sql: result.extract_sql })
        });
        if (r.ok) {
          const data = await r.json();
          payload.msisdns   = data.msisdns;
          payload.msisdn_count = data.count;
        }
      } catch {}
    }

    // For sms — extract draft message from recommendations (first one that looks like a message)
    if (type === 'sms' && result.recommendations && result.recommendations.length) {
      const recs = result.recommendations;
      const smsRec = recs.find(r => {
        const t = typeof r === 'string' ? r : (r.text || '');
        return t.toLowerCase().includes('sms') || t.includes("'") || t.toLowerCase().includes('message') || t.toLowerCase().includes('dear');
      });
      const fallback = typeof recs[0] === 'string' ? recs[0] : (recs[0].text || '');
      const raw = smsRec ? (typeof smsRec === 'string' ? smsRec : smsRec.text || '') : fallback;
      // Extract quoted text if present, otherwise use full rec
      const quoted = raw.match(/['"]([^'"]{20,})['"]/);
      payload.message = quoted ? quoted[1] : raw.slice(0, 300);
    }

    // Attach CSV if requested
    if (attachCsv && result.extract_sql) {
      try {
        const r = await fetch("/export/save", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sql: result.extract_sql, title, summary: answerText.slice(0, 300), send_type: type })
        });
        if (r.ok) {
          const data = await r.json();
          payload.filename  = data.filename;
          payload.row_count = data.rows;
        }
      } catch {}
    }

    try {
      const res = await fetch("/ops/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          type,
          title,
          summary: answerText.slice(0, 300),
          payload,
        })
      });
      if (!res.ok) {
        const err = await res.json();
        alert("Send failed: " + (err.detail || res.statusText));
        btn.textContent = orig;
        btn.disabled = false;
        return;
      }
      btn.textContent = "✓ Sent to Ops";
      btn.style.background = "var(--green, #3ecf8e)";
      setTimeout(() => {
        btn.textContent = orig;
        btn.style.background = "";
        btn.disabled = false;
      }, 2500);
    } catch (e) {
      alert("Send error: " + e.message);
      btn.textContent = orig;
      btn.disabled = false;
    }
  }

  return { addUser, startStream, appendToken, appendThinkToken, addStep, finalize, clear, clearPending, isStreaming, hasPending, currentText };
})();
