/**
 * Relay API Check — 自动扫描模块
 */
(function () {
  const state = {
    scanId: null,
    eventSource: null,
    report: null,
  };

  function $(id) {
    return document.getElementById(id);
  }

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function applyUrlParams() {
    const p = new URLSearchParams(location.search);
    const base = p.get("base");
    const model = p.get("model");
    const channel = p.get("channel");
    if (base) {
      const el = $("base-url");
      if (el) el.value = base;
      const autoBase = $("auto-base-url");
      if (autoBase) autoBase.value = base;
    }
    if (model) {
      const el = $("model");
      if (el) el.value = model;
    }
    if (channel === "claude" && typeof setEndpoint === "function") {
      setEndpoint("anthropic");
    } else if (channel === "gpt" && typeof setEndpoint === "function") {
      setEndpoint("auto");
    }
    if (p.get("mode") === "auto") {
      switchMode("auto");
    }
  }

  window.switchMode = function switchMode(mode) {
    const manual = $("mode-manual");
    const auto = $("mode-auto");
    const btnM = $("mode-manual-btn");
    const btnA = $("mode-auto-btn");
    if (!manual || !auto) return;
    const isAuto = mode === "auto";
    manual.style.display = isAuto ? "none" : "";
    auto.style.display = isAuto ? "flex" : "none";
    btnM?.classList.toggle("active", !isAuto);
    btnA?.classList.toggle("active", isAuto);
  };

  function setAutoStatus(text, type) {
    const dot = $("auto-status-dot");
    const label = $("auto-status-text");
    if (dot) dot.className = "status-dot" + (type ? " " + type : "");
    if (label) label.textContent = text;
  }

  function appendStepLog(line) {
    const el = $("auto-step-log");
    if (!el) return;
    const row = document.createElement("div");
    row.className = "auto-step-line";
    row.textContent = line;
    el.appendChild(row);
    el.scrollTop = el.scrollHeight;
  }

  function renderProtocolMatrix(protocols) {
    const el = $("auto-protocol-matrix");
    if (!el) return;
    const eps = ["anthropic", "responses", "chat"];
    const modes = ["sync", "stream"];
    let html = '<table class="auto-matrix"><thead><tr><th>协议</th>';
    modes.forEach((m) => {
      html += `<th>${m}</th>`;
    });
    html += "</tr></thead><tbody>";
    eps.forEach((ep) => {
      const row = protocols?.[ep];
      if (!row) return;
      html += `<tr><td>${ep}</td>`;
      modes.forEach((m) => {
        const cell = row[m];
        const st = cell?.status || "—";
        html += `<td><span class="auto-cell auto-${st}">${st}</span></td>`;
      });
      html += "</tr>";
    });
    html += "</tbody></table>";
    el.innerHTML = html;
  }

  function renderModels(models) {
    const el = $("auto-models-list");
    if (!el) return;
    if (!models?.length) {
      el.innerHTML = '<span class="text-dim">无</span>';
      return;
    }
    el.innerHTML = models
      .slice(0, 40)
      .map(
        (m) =>
          `<span class="auto-model-chip" title="${escapeHtml(m.source)}">${escapeHtml(m.id)}</span>`,
      )
      .join("");
    if (models.length > 40) {
      el.innerHTML += `<span class="auto-model-chip">+${models.length - 40}</span>`;
    }
  }

  function renderAgents(agents) {
    const el = $("auto-agents");
    if (!el) return;
    const entries = Object.values(agents || {});
    if (!entries.length) {
      el.innerHTML = "—";
      return;
    }
    el.innerHTML = entries
      .map(
        (a) =>
          `<div class="auto-agent-card"><strong>${escapeHtml(a.label || a.id)}</strong> <span class="auto-cell auto-${a.status}">${a.status}</span><div class="auto-agent-ev">${escapeHtml(a.evidence || "")}</div></div>`,
      )
      .join("");
  }

  function renderReport(report) {
    if (!report) return;
    state.report = report;
    $("auto-report")?.classList.remove("hidden");
    const summary = report.summary || {};
    const meta = report.meta || {};
    $("auto-summary-headline").textContent = summary.headline || "—";
    $("auto-summary-meta").textContent = [
      meta.baseUrl,
      meta.profile,
      meta.finishedAt ? `完成于 ${meta.finishedAt}` : "",
    ]
      .filter(Boolean)
      .join(" · ");
    renderModels(report.models);
    renderProtocolMatrix(report.protocols);
    renderAgents(report.agents);
  }

  function closeEventSource() {
    if (state.eventSource) {
      state.eventSource.close();
      state.eventSource = null;
    }
  }

  async function startAutoScan() {
    const baseUrl = $("auto-base-url")?.value.trim();
    const apiKey = $("auto-api-key")?.value.trim();
    const profile = $("auto-profile")?.value || "standard";
    if (!baseUrl) return alert("请填写 Base URL");
    if (!apiKey) return alert("请填写 API Key");

    closeEventSource();
    state.scanId = null;
    state.report = null;
    $("auto-report")?.classList.add("hidden");
    const log = $("auto-step-log");
    if (log) log.innerHTML = "";
    setAutoStatus("扫描中…", "loading");
    $("auto-start-btn").disabled = true;

    try {
      const res = await fetch("/api/auto-scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ baseUrl, apiKey, profile }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data.detail || data.message || `HTTP ${res.status}`);
      }
      state.scanId = data.scanId;
      appendStepLog(`任务已创建：${data.scanId}`);
      subscribeEvents(data.scanId);
    } catch (e) {
      setAutoStatus("失败", "err");
      appendStepLog(String(e.message || e));
      $("auto-start-btn").disabled = false;
    }
  }

  function subscribeEvents(scanId) {
    closeEventSource();
    const es = new EventSource(`/api/auto-scan/${scanId}/events`);
    state.eventSource = es;
    es.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (msg.type === "step") {
        const line = `[${msg.status}] ${msg.name}${msg.detail ? " — " + msg.detail : ""}`;
        appendStepLog(line);
        if (msg.progress != null) {
          const bar = $("auto-progress-bar");
          if (bar) bar.style.width = `${Math.round(msg.progress * 100)}%`;
        }
      }
      if (msg.type === "done") {
        closeEventSource();
        renderReport(msg.report);
        setAutoStatus("完成", "ok");
        $("auto-start-btn").disabled = false;
      }
      if (msg.type === "error") {
        closeEventSource();
        appendStepLog("错误：" + (msg.message || "unknown"));
        setAutoStatus("失败", "err");
        $("auto-start-btn").disabled = false;
      }
    };
    es.onerror = () => {
      fetch(`/api/auto-scan/${scanId}`)
        .then((r) => r.json())
        .then((data) => {
          if (data.state === "done" && data.report) {
            renderReport(data.report);
            setAutoStatus("完成", "ok");
          }
        })
        .finally(() => {
          $("auto-start-btn").disabled = false;
        });
      closeEventSource();
    };
  }

  function exportReportJson() {
    if (!state.report) return alert("暂无报告");
    const blob = new Blob([JSON.stringify(state.report, null, 2)], {
      type: "application/json",
    });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `relay-api-scan-${state.scanId || "report"}.json`;
    a.click();
  }

  function openInManual() {
    if (!state.report?.meta?.baseUrl) return;
    $("base-url").value = state.report.meta.baseUrl;
    const rep = state.report.meta.representatives || {};
    const model =
      rep.anthropic || rep.responses || rep.chat || $("model")?.value;
    if (model) $("model").value = model;
    switchMode("manual");
  }

  window.startAutoScan = startAutoScan;
  window.exportAutoScanJson = exportReportJson;
  window.openAutoScanInManual = openInManual;

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", applyUrlParams);
  } else {
    applyUrlParams();
  }
})();
