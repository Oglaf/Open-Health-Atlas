/* Wearable sync page: renders /api/models/status verbatim (the status math —
   fresh/stale/never — is computed server-side). No chart library needed. */
(function () {
  const $ = (id) => document.getElementById(id);

  async function boot() {
    let d;
    try {
      const res = await fetch("/api/models/status", { credentials: "same-origin" });
      if (res.status === 401) { location.assign("/login"); return; }
      d = await res.json();
      if (!res.ok || d.ok === false) throw new Error(d.error || "HTTP " + res.status);
    } catch (e) {
      $("ws-last-run").textContent = "unavailable: " + e.message;
      return;
    }
    const okRun = d.collector_last_ok;
    const anyRun = d.collector_last_run || {};
    $("ws-last-ok").textContent = (okRun && okRun.completed_at) || "never";
    if (anyRun.completed_at) {
      const st = anyRun.status || "?";
      $("ws-last-run").textContent = `${anyRun.completed_at} (${st}` +
        (anyRun.error_code ? `: ${anyRun.error_code}` : "") + ")";
    } else {
      $("ws-last-run").textContent = "no run recorded — the collector has never written to this account";
    }
    $("ws-latest").textContent = d.fitbit_latest || "no wearable data yet";
    $("ws-fresh").textContent = d.fitbit_freshness || "never";
    $("ws-days30").textContent = d.fitbit_days_30 != null ? d.fitbit_days_30 : "—";
    if (!d.fitbit_latest && !anyRun.completed_at) {
      $("ws-fresh").textContent = "No sync yet — start the collector container";
    }
  }
  boot();
})();
