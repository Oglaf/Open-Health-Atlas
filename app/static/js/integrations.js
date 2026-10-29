/* Integrations & privacy (task-36). Renders /api/integrations/sources
   verbatim: every status/date/chip is computed server-side (see that
   route's docstring for the exact "connected/stale/none" rules), so this
   file only builds row text/markup — no status math here. */
(function () {
  if (!window.HermesCharts) return;
  const { fetchJSON } = window.HermesCharts;

  const FLAG_CLASS = { connected: "in", stale: "bd", none: "bd", retired: "bd", not_running: "out", unknown: "bd" };

  function row(name, chipText, flagClass, sub) {
    const el = document.createElement("div");
    el.className = "src-row";
    const left = document.createElement("div");
    const nameEl = document.createElement("div");
    nameEl.className = "strong";
    nameEl.textContent = name;
    left.appendChild(nameEl);
    if (sub) {
      const subEl = document.createElement("div");
      subEl.className = "muted micro mt-1";
      subEl.textContent = sub;
      left.appendChild(subEl);
    }
    const chip = document.createElement("span");
    chip.className = "flag " + flagClass;
    chip.textContent = chipText;
    el.append(left, chip);
    return el;
  }

  // "Connected · last sync <date>" / "Stale · last sync <date>" / "No data
  // yet" — the shared chip-text shape for the two date-backed sources.
  function syncChip(status, date) {
    if (status === "connected") return `Connected · last sync ${date}`;
    if (status === "stale") return `Stale · last sync ${date}`;
    return "No data yet";
  }

  function hevyRow(h) {
    h = h || {};
    return row("Hevy (training)", syncChip(h.status, h.latest_date), FLAG_CLASS[h.status] || "bd");
  }

  function wearableRow(w) {
    w = w || {};
    const apple = w.apple || {};
    // Apple is retired by product contract and no longer feeds
    // w.status/latest_date — it's shown as its own honest, non-error state.
    const appleBit = apple.since
      ? `Apple: retired ${apple.since} · archive imports occasionally`
      : "Apple: retired";
    const sub = `${appleBit} · Fitbit data through ${w.fitbit_latest || "— (none yet)"}` +
      (w.collector_last_run
        ? ` · collector ran ${w.collector_last_run}`
        : " · the collector has not run yet");
    return row("Wearable", syncChip(w.status, w.latest_date), FLAG_CLASS[w.status] || "bd", sub);
  }

  async function boot() {
    const list = document.getElementById("src-list");
    let data;
    try {
      data = await fetchJSON("/api/integrations/sources");
    } catch (e) {
      const p = document.createElement("p");
      p.className = "muted note-txt";
      p.textContent = "Sources unavailable: " + e.message;
      list.appendChild(p);
      return;
    }
    list.append(
      hevyRow(data.hevy),
      wearableRow(data.wearable),
    );
  }
  boot();
})();
