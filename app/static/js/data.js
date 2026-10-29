/* Data page (design v-data; restyle-only, real substance kept — v2.1 note:
   the raw trend charts moved off the dashboard; the dashboard stays a
   consolidated read-only scoreboard). daily_metrics rows are
   provenance-tagged — apple renders solid/accent, fitbit dashed/warn,
   filterable. Chart chrome mirrors charts-common's lineOpt/tip/pal, but
   hand-rolled on a TIME axis (not lineOpt's category axis) because
   provenance series carry independent, gap-broken date sets — same reason
   recovery.js/labs.js hand-roll their dual-axis/markLine charts instead of
   calling the shared builder. */
(function () {
  if (typeof echarts === "undefined" || !window.HermesCharts) return;
  const { localISO, isoDaysAgo, fetchJSON, pal, tip, barsOpt, lineOpt } = window.HermesCharts;

  const state = { days: 90, source: "all", metrics: [], body: [], subjective: [],
    sleepLog: [], hevyVolume: [], water: [] };
  const charts = {};

  function chart(id) {
    const el = document.getElementById(id);
    if (!el) return null;
    if (charts[id]) charts[id].dispose();
    el.style.height = "";   // undo the empty-state collapse when data arrives
    charts[id] = echarts.init(el);
    return charts[id];
  }

  // Empty charts collapse to a short strip so a sparse account doesn't render
  // a wall of 230px blank cards.
  function empty(id, label) {
    const c = chart(id);
    if (!c) return;
    document.getElementById(id).style.height = "84px";
    c.resize();
    c.setOption({ graphic: { type: "text", left: "center", top: "middle",
      style: { text: label || "No data yet", fill: pal().muted, fontSize: 13 } } });
  }

  /* design chart frame (pal()) on a time axis — grid/tooltip/xAxis/yAxis
     chrome mirrors HermesCharts.lineOpt exactly, but xAxis stays "time" so
     each provenance series keeps its own independent, gap-broken dates. */
  function timeAxes() {
    const P = pal();
    return {
      grid: { left: 10, right: 16, top: 26, bottom: 22, containLabel: true },
      tooltip: Object.assign(tip(P), {
        trigger: "axis",
        axisPointer: { type: "line", lineStyle: { color: P.grid, type: "dashed" } },
      }),
      xAxis: { type: "time",
        axisLine: { lineStyle: { color: P.grid } },
        axisLabel: { color: P.muted, fontSize: 10, hideOverlap: true },
        axisTick: { show: false } },
      yAxis: { type: "value", scale: true, splitNumber: 4,
        splitLine: { lineStyle: { color: P.grid } },
        axisLabel: { color: P.muted, fontSize: 10 } },
    };
  }

  /* rows -> {apple: [[date, v]…], …} with explicit null gaps >3 days so lines
     BREAK instead of interpolating across missing weeks (review finding). */
  function bySource(rows, field) {
    const out = {};
    for (const r of rows) {
      if (r[field] == null) continue;
      if (state.source !== "all" && r.source !== state.source) continue;
      const arr = (out[r.source] = out[r.source] || []);
      if (arr.length) {
        const prev = arr[arr.length - 1][0];
        if ((new Date(r.date) - new Date(prev)) / 86400000 > 3) {
          const gapDay = new Date(prev); gapDay.setDate(gapDay.getDate() + 1);
          arr.push([localISO(gapDay), null]);
        }
      }
      arr.push([r.date, r[field]]);
    }
    return out;
  }

  /* design chart frame (pal()) on a CATEGORY axis — the bar/stacked-bar
     counterpart to timeAxes() above, same grid/tooltip/xAxis/yAxis chrome
     factored out of fieldBarChart/sleepStagesChart/workoutsChart/
     hevyVolumeChart (review finding: byte-identical boilerplate hand-rolled
     4x). Callers Object.assign a `series` (and override yAxis/legend/grid
     as needed) exactly like timeAxes() callers already do. */
  function categoryAxes(cats) {
    const P = pal();
    return {
      grid: { left: 10, right: 16, top: 26, bottom: 18, containLabel: true },
      tooltip: Object.assign(tip(P), { trigger: "axis", axisPointer: { type: "shadow" } }),
      xAxis: { type: "category", data: cats,
        axisLine: { lineStyle: { color: P.grid } },
        axisLabel: { color: P.muted, fontSize: 10, hideOverlap: true },
        axisTick: { show: false } },
      yAxis: { type: "value", splitNumber: 4, splitLine: { lineStyle: { color: P.grid } },
        axisLabel: { color: P.muted, fontSize: 10 } },
    };
  }

  const SRC_STYLE = { apple: { colorKey: "accent", dash: false }, fitbit: { colorKey: "warn", dash: true } };

  function metricChart(id, field) {
    const groups = bySource(state.metrics, field);
    const names = Object.keys(groups);
    if (!names.length) return empty(id);
    const P = pal();
    const showSymbol = !(state.days === "all" || state.days > 90);
    const series = names.map((src) => {
      const style = SRC_STYLE[src] || SRC_STYLE.apple;
      const color = P[style.colorKey];
      return { name: src, type: "line", data: groups[src], smooth: true, connectNulls: false,
        symbol: showSymbol ? "circle" : "none", symbolSize: 5,
        lineStyle: { width: 2, type: style.dash ? "dashed" : "solid", color },
        itemStyle: { color } };
    });
    const option = Object.assign(timeAxes(), { series });
    if (names.length > 1) option.legend = { top: 0, right: 0, textStyle: { color: P.muted, fontSize: 10 } };
    chart(id).setOption(option);
  }

  function bodyChart() {
    const w = state.body.filter(r => r.weight_kg != null).map(r => [r.date, r.weight_kg]);
    const wa = state.body.filter(r => r.waist_cm != null).map(r => [r.date, r.waist_cm]);
    if (!w.length && !wa.length) return empty("ch-body", "Log weight/waist to see trends");
    const P = pal();
    const o = timeAxes();
    o.grid.right = 44;
    // right axis stays unnamed (no "cm" label) — it would collide with the
    // top-right legend; the card title already states "kg / cm" (same
    // no-axis-name-on-a-legended-secondary-axis call as recovery.js rSleep).
    o.yAxis = [Object.assign({}, o.yAxis, { name: "kg" }),
               Object.assign({}, o.yAxis, { splitLine: { show: false } })];
    o.legend = { top: 0, right: 0, textStyle: { color: P.muted, fontSize: 10 } };
    o.series = [
      { name: "weight", type: "line", data: w, smooth: true, symbol: "circle", symbolSize: 6,
        lineStyle: { width: 2.4, color: P.accent }, itemStyle: { color: P.accent } },
      { name: "waist", type: "line", data: wa, smooth: true, symbol: "circle", symbolSize: 6, yAxisIndex: 1,
        lineStyle: { width: 2, color: P.good }, itemStyle: { color: P.good } },
    ];
    chart("ch-body").setOption(o);
  }

  /* fork 2026-10-28: mood (Mind), medication/vitals (safety) and weather/air
     charts were removed with their cards (owner decision). */


  /* Per-source Map(date -> value) for one daily_metrics column, respecting
     the Source filter. Like bySource() but WITHOUT the line-chart gap-break
     insertion (a bar/category chart has no line to break — a missing day is
     just an absent bar) and keyed for O(1) date lookup, since the bar/multi-
     field charts below need to align several fields/sources onto one shared
     category axis. */
  function sourceGroups(rows, field) {
    const out = {};
    for (const r of rows) {
      if (r[field] == null) continue;
      if (state.source !== "all" && r.source !== state.source) continue;
      (out[r.source] = out[r.source] || new Map()).set(r.date, r[field]);
    }
    return out;
  }

  function unionDates(groups) {
    const set = new Set();
    Object.values(groups).forEach((m) => m.forEach((_v, d) => set.add(d)));
    return [...set].sort();
  }

  /* Additive daily_metrics quantity (kcal/min/km/count) as bars — owner:
     "not everything is a line". Provenance-safe: when both Apple and Fitbit
     report the same field, each source gets its OWN bar series (grouped, not
     stacked/averaged) rather than silently merging two readings into one
     number, same rule metricChart() already enforces for lines. `color` is
     used only in the common single-source case; a real second source falls
     back to the shared apple/fitbit palette so it stays visually consistent
     with every other provenance chart on this page. */
  function fieldBarChart(id, field, color, emptyLabel) {
    const groups = sourceGroups(state.metrics, field);
    const names = Object.keys(groups);
    if (!names.length) return empty(id, emptyLabel);
    const dates = unionDates(groups);
    const cats = dates.map((d) => d.slice(5));
    const P = pal();
    const multi = names.length > 1;
    const o = categoryAxes(cats);
    if (!multi) o.grid.top = 22;
    o.series = names.map((src) => {
      const style = SRC_STYLE[src] || SRC_STYLE.apple;
      return { name: src, type: "bar",
        data: dates.map((d) => (groups[src].has(d) ? groups[src].get(d) : null)),
        itemStyle: { color: multi ? P[style.colorKey] : color, borderRadius: 4 },
        barWidth: multi ? "38%" : "52%" };
    });
    if (multi) o.legend = { top: 0, right: 0, textStyle: { color: P.muted, fontSize: 10 } };
    chart(id).setOption(o);
  }

  /* Heart-rate range (hr_min/avg/max — one owner bullet, one card): each of
     the 3 fields keeps its OWN provenance split via bySource() (color =
     field, dash = source), so up to 6 lines can appear but sources are never
     merged — same discipline as metricChart(), just for 3 related columns at
     once (mirrors the existing Weight & waist combo card's one-card-per-
     measurement-family precedent). */
  function hrRangeChart() {
    const P = pal();
    const showSymbol = !(state.days === "all" || state.days > 90);
    const fields = [["hr_min", "min", P.good], ["hr_avg", "avg", P.accent], ["hr_max", "max", P.bad]];
    const series = [];
    for (const [field, label, color] of fields) {
      const groups = bySource(state.metrics, field);
      for (const src of Object.keys(groups)) {
        const style = SRC_STYLE[src] || SRC_STYLE.apple;
        series.push({
          name: src === "apple" ? label : `${label} (${src})`,
          type: "line", data: groups[src], smooth: true, connectNulls: false,
          symbol: showSymbol ? "circle" : "none", symbolSize: 4,
          lineStyle: { width: 2, type: style.dash ? "dashed" : "solid", color },
          itemStyle: { color },
        });
      }
    }
    if (!series.length) return empty("ch-hrrange", "No hr_min/avg/max rows yet");
    const o = Object.assign(timeAxes(), { series });
    o.legend = { top: 0, right: 0, textStyle: { color: P.muted, fontSize: 10 } };
    chart("ch-hrrange").setOption(o);
  }

  /* body_metrics is already deduped to one row/date server-side (task-52
     /api/dash/body — same manual-beats-hevy tie-break as weight/waist), so
     these two need no source split, unlike the daily_metrics fields above. */
  function measurementsChart() {
    const P = pal();
    const pick = (f) => state.body.filter((r) => r[f] != null).map((r) => [r.date, r[f]]);
    const chest = pick("chest_cm"), hip = pick("hip_cm");
    const arm = pick("arm_cm"), thigh = pick("thigh_cm"), neck = pick("neck_cm");
    if (![chest, hip, arm, thigh, neck].some((a) => a.length))
      return empty("ch-measurements", "Log tape measurements to see trends");
    const o = timeAxes();
    o.grid.right = 44;
    // dual axis by SCALE, not body region — chest/hip run ~90-110cm, the
    // limb measurements ~30-60cm; one shared axis would flatten the smaller
    // three (same reasoning as the Weight & waist card's dual axis above).
    o.yAxis = [Object.assign({}, o.yAxis, { name: "cm (chest/hip)" }),
               Object.assign({}, o.yAxis, { name: "cm (arm/thigh/neck)", splitLine: { show: false } })];
    o.legend = { top: 0, right: 0, textStyle: { color: P.muted, fontSize: 10 } };
    const mk = (name, data, color, yAxisIndex, dash) => ({
      name, type: "line", data, smooth: true, symbol: "circle", symbolSize: 4, yAxisIndex,
      lineStyle: { width: 2, color, type: dash ? "dashed" : "solid" }, itemStyle: { color } });
    o.series = [
      mk("chest", chest, P.accent, 0), mk("hip", hip, P.warn, 0, true),
      mk("arm", arm, P.good, 1), mk("thigh", thigh, P.bad, 1, true), mk("neck", neck, P.hero, 1),
    ].filter((s) => s.data.length);
    chart("ch-measurements").setOption(o);
  }

  function bodyFatChart() {
    const rows = state.body.filter((r) => r.body_fat_pct != null).map((r) => [r.date, r.body_fat_pct]);
    if (!rows.length) return empty("ch-bodyfat", "Log body-fat % to see trend");
    const P = pal();
    const o = timeAxes();
    o.series = [{ type: "line", data: rows, smooth: true, connectNulls: false,
      symbol: "circle", symbolSize: 5,
      lineStyle: { width: 2.2, color: P.accent }, itemStyle: { color: P.accent } }];
    chart("ch-bodyfat").setOption(o);
  }

  /* Sleep-stage minutes (sleep_log — a different table from daily_metrics.
     sleep_hours, already charted above): stacked bar, same composition-chart
     shape as recovery.js's stagesChart (deliberately mirrored so the two
     pages agree visually), currently unseeded on the demo DB (Fitbit
     sleep-stage sync awaits) so this renders the honest-empty state. */
  function sleepStagesChart() {
    const rows = state.sleepLog.filter((r) =>
      r.deep_min != null || r.rem_min != null || r.light_min != null || r.awake_min != null);
    if (!rows.length)
      return empty("ch-sleepstages", "Sleep stages appear once Fitbit sleep-stage sync lands");
    const P = pal();
    const cats = rows.map((r) => r.date.slice(5));
    const stages = [["Deep", "deep_min", P.accent], ["REM", "rem_min", P.good],
                     ["Light", "light_min", P.warn], ["Awake", "awake_min", P.bad]];
    const o = categoryAxes(cats);
    o.tooltip.valueFormatter = (v) => (v == null ? "—" : (v / 60).toFixed(1) + " h");
    o.legend = { top: 0, right: 0, textStyle: { color: P.muted, fontSize: 10 } };
    o.yAxis.axisLabel.formatter = (v) => (v / 60) + "h";
    o.series = stages.map(([name, field, color], i) => ({
      name, type: "bar", stack: "x", data: rows.map((r) => r[field]),
      itemStyle: { color, borderRadius: i === stages.length - 1 ? [3, 3, 0, 0] : 0 },
      barWidth: "46%", emphasis: { focus: "series" },
    }));
    chart("ch-sleepstages").setOption(o);
  }

  /* sleep_log.time_asleep_hours — actual time-asleep duration (distinct
     from daily_metrics.sleep_hours, the wearable in-bed duration charted
     above, and from the stage-minutes stacked bar just above). Simple line,
     same shape as bodyFatChart (task-52 fix wave: this stream was fetched
     via /api/dash/sleep but had no chart of its own — review finding). */
  function timeAsleepChart() {
    const rows = state.sleepLog.filter((r) => r.time_asleep_hours != null)
      .map((r) => [r.date, r.time_asleep_hours]);
    if (!rows.length) return empty("ch-timeasleep", "Time asleep appears once Fitbit sleep-stage sync lands");
    const P = pal();
    const o = timeAxes();
    o.series = [{ type: "line", data: rows, smooth: true, connectNulls: false,
      symbol: "circle", symbolSize: 5,
      lineStyle: { width: 2.2, color: P.accent }, itemStyle: { color: P.accent } }];
    chart("ch-timeasleep").setOption(o);
  }

  /* Hydration — sourced from the existing /api/nutrition/water endpoint (the
     Nutrition page's own well-tested query) rather than a new dash.py copy;
     fetched once as "all" and range-sliced client-side like mood/day-rating
     below, since /api/nutrition/water's whitelist has no "90" option. */
  function waterChart() {
    const cutoff = state.days === "all" ? "" : isoDaysAgo(state.days);
    const rows = state.water.filter((r) => r.date >= cutoff && r.water_ml != null);
    if (!rows.length) return empty("ch-water", "Log water to see the trend");
    const cats = rows.map((r) => r.date.slice(5));
    const vals = rows.map((r) => r.water_ml);
    chart("ch-water").setOption(barsOpt(cats, vals, pal().accent, false));
  }

  /* hevy_sets daily training volume, aggregated server-side (SUM(weight*reps)
     + set count — a client can't re-derive that sum without every set row).
     Bar = volume (the additive quantity), line on a secondary axis = set
     count, same dual-axis-combo shape as Sleep hours/quality on Recovery. */
  function hevyVolumeChart() {
    const rows = state.hevyVolume;
    if (!rows.length) return empty("ch-hevyvol", "No Hevy sets logged in range");
    const P = pal();
    const cats = rows.map((r) => r.date.slice(5));
    const o = categoryAxes(cats);
    o.grid.right = 44;
    o.legend = { top: 0, right: 0, textStyle: { color: P.muted, fontSize: 10 } };
    o.yAxis = [Object.assign({}, o.yAxis, { name: "kg" }),
               Object.assign({}, o.yAxis, { splitLine: { show: false } })];
    o.series = [
      { name: "volume", type: "bar", data: rows.map((r) => r.volume_kg),
        itemStyle: { color: P.accent, borderRadius: 4 }, barWidth: "52%" },
      { name: "sets", type: "line", yAxisIndex: 1, data: rows.map((r) => r.sets),
        symbol: "circle", symbolSize: 5,
        lineStyle: { width: 2, color: P.good }, itemStyle: { color: P.good } },
    ];
    chart("ch-hevyvol").setOption(o);
  }

  /* day_rating (1-3): no calendar-heatmap idiom fits this card's small
     footprint (that DOM widget owns a full page section elsewhere), so this
     is the "else dots" fallback the brief calls for — a scatter dot per day,
     colored by rating on a fixed 1-3 axis, same semantic colors as the chip
     family (good/warn/bad) used everywhere else on the page. */
  function dayRatingChart() {
    const cutoff = state.days === "all" ? "" : isoDaysAgo(state.days);
    const rows = state.subjective.filter((r) => r.date >= cutoff && r.day_rating != null);
    if (!rows.length) return empty("ch-dayrating", "Rate a day to see the trend");
    const P = pal();
    const colorFor = (v) => (v >= 3 ? P.good : v === 2 ? P.warn : P.bad);
    const o = timeAxes();
    o.yAxis.min = 1; o.yAxis.max = 3; o.yAxis.scale = false; o.yAxis.interval = 1;
    o.series = [{
      type: "scatter", symbolSize: 10,
      data: rows.map((r) => ({ value: [r.date, r.day_rating], itemStyle: { color: colorFor(r.day_rating) } })),
    }];
    chart("ch-dayrating").setOption(o);
  }

  /* §body-composition (fork owner decision 2026-10-27): the panel only
     renders health.py's `body-composition` read-model; it computes no
     numbers itself. Independent of the page's day-range dropdown — scans
     are episodic, so this always asks for a generous fixed
     window rather than tracking Week/Month/Quarter/Year/All. */
  const BC_TREND_DAYS = 3650;

  function bcFlag(evaluation) {
    if (!evaluation) return '<span class="muted micro">—</span>';
    return `<span class="flag ${evaluation === "Standard" ? "in" : "out"}">${escapeHtml(evaluation)}</span>`;
  }
  function bcRef(lo, hi) {
    if (lo == null && hi == null) return "—";
    if (lo == null) return "< " + hi;
    if (hi == null) return "> " + lo;
    return lo + "–" + hi;
  }
  const BC_COMPONENT_LABEL = {
    weight: "Weight", body_fat: "Body fat", inorganic_salt: "Inorganic salt",
    protein: "Protein", body_water: "Body water", muscle: "Muscle",
    skeletal_muscle: "Skeletal muscle",
  };
  const BC_SEGMENT_LABEL = {
    left_arm: "Left arm", right_arm: "Right arm", trunk: "Trunk",
    left_leg: "Left leg", right_leg: "Right leg",
  };

  function bcComponentsRows(components) {
    return components.map((c) => `<tr>
      <td class="strong">${escapeHtml(BC_COMPONENT_LABEL[c.component] || c.component)}</td>
      <td class="num">${c.value_kg} <span class="muted micro">kg</span></td>
      <td class="muted">${escapeHtml(bcRef(c.ref_low, c.ref_high))}</td>
      <td class="muted">${c.pct_of_weight != null ? c.pct_of_weight + "%" : "—"}</td>
      <td>${bcFlag(c.evaluation)}</td>
    </tr>`).join("");
  }
  function bcSegmentRows(segments) {
    return segments.map((s) => `<tr>
      <td class="strong">${escapeHtml(BC_SEGMENT_LABEL[s.segment] || s.segment)}</td>
      <td class="num">${s.mass_kg} <span class="muted micro">kg</span></td>
      <td class="muted">${s.pct_of_standard != null ? s.pct_of_standard + "%" : "—"}</td>
      <td>${bcFlag(s.evaluation)}</td>
    </tr>`).join("");
  }
  function bcImpedanceRows(rows) {
    const byFreq = {};
    rows.forEach((r) => { (byFreq[r.frequency_khz] = byFreq[r.frequency_khz] || {})[r.segment] = r.ohms; });
    return Object.keys(byFreq).sort((a, b) => a - b).map((freq) => {
      const row = byFreq[freq];
      const cell = (seg) => (row[seg] != null ? row[seg] : "—");
      return `<tr><td class="strong">${escapeHtml(freq)} kHz</td>
        <td class="num">${cell("right_arm")}</td><td class="num">${cell("left_arm")}</td>
        <td class="num">${cell("trunk")}</td><td class="num">${cell("right_leg")}</td>
        <td class="num">${cell("left_leg")}</td></tr>`;
    }).join("");
  }

  const BC_TREND_CHARTS = ["ch-bc-muscle", "ch-bc-visceral"];

  function bodyCompositionRender(data) {
    const emptyEl = document.getElementById("bc-empty");
    const bodyEl = document.getElementById("bc-body");
    if (!data || data.insufficient_data) {
      emptyEl.hidden = false; bodyEl.hidden = true;
      BC_TREND_CHARTS.forEach((id) => empty(id, "No scan yet"));
      return;
    }
    emptyEl.hidden = true; bodyEl.hidden = false;
    const s = data.latest;
    document.getElementById("bc-meta").textContent =
      [s.date, s.tested_at, s.device, s.age_years ? s.age_years + "y" : null,
       s.height_cm ? s.height_cm + " cm" : null].filter(Boolean).join(" · ");
    document.getElementById("bc-score").textContent = s.body_score != null ? s.body_score : "—";
    document.getElementById("bc-bmi").textContent =
      s.bmi != null ? `BMI ${s.bmi}${s.bmi_band ? " · " + s.bmi_band : ""}` : "BMI —";
    document.getElementById("bc-bodytype").textContent = s.body_type || "—";
    document.getElementById("bc-components").innerHTML = bcComponentsRows(s.components || []);
    document.getElementById("bc-fat").innerHTML = bcSegmentRows((s.segments && s.segments.fat) || []);
    document.getElementById("bc-muscle").innerHTML = bcSegmentRows((s.segments && s.segments.muscle) || []);
    document.getElementById("bc-impedance").innerHTML = bcImpedanceRows(s.impedance || []);
    document.getElementById("bc-obesity").textContent = s.obesity_degree_pct != null
      ? `${s.obesity_degree_pct}%${s.obesity_degree_band ? " · " + s.obesity_degree_band : ""}` : "—";
    document.getElementById("bc-target").textContent = s.target_weight_kg != null ? s.target_weight_kg + " kg" : "—";
    document.getElementById("bc-control").textContent =
      [s.weight_control_kg, s.fat_control_kg, s.muscle_control_kg].every((v) => v == null) ? "—"
        : `${s.weight_control_kg ?? "—"} / ${s.fat_control_kg ?? "—"} / ${s.muscle_control_kg ?? "—"} kg`;
    document.getElementById("bc-visceral").textContent = s.visceral_fat_grade != null ? s.visceral_fat_grade : "—";
    document.getElementById("bc-bmr").textContent = s.bmr_kcal != null ? s.bmr_kcal + " kcal" : "—";
    document.getElementById("bc-otherfat").textContent =
      [s.fat_free_mass_kg != null ? s.fat_free_mass_kg + " kg" : "—",
       s.subcutaneous_fat_pct != null ? s.subcutaneous_fat_pct + "%" : "—"].join(" / ");
    document.getElementById("bc-othersmi").textContent =
      [s.smi_kg_m2 ?? "—", s.body_age ?? "—", s.whr ?? "—"].join(" / ");

    const trend = data.trend || [];
    if (!trend.length) {
      BC_TREND_CHARTS.forEach((id) => empty(id, "Not enough scans in this window yet"));
      return;
    }
    const dates = trend.map((t) => t.date);
    const P = pal();
    // Weight / fat % already trend on the existing Body cards (a scan mirrors
    // into body_metrics); only metrics with no other chart get one here.
    // symbol:"circle" so a single scan still draws a point.
    chart("ch-bc-muscle").setOption(lineOpt(dates, [
      { n: "Skeletal muscle", d: trend.map((t) => t.skeletal_muscle_kg), c: P.good, symbol: "circle" },
    ]));
    chart("ch-bc-visceral").setOption(lineOpt(dates, [
      { n: "Visceral fat grade", d: trend.map((t) => t.visceral_fat_grade), c: P.warn, symbol: "circle" },
    ]));
  }

  async function loadBodyComposition() {
    try {
      bodyCompositionRender(await fetchJSON(`/api/dash/body-composition?days=${BC_TREND_DAYS}`));
    } catch (e) {
      const emptyEl = document.getElementById("bc-empty");
      emptyEl.hidden = false; emptyEl.textContent = "Could not load body composition: " + e.message;
      document.getElementById("bc-body").hidden = true;
      BC_TREND_CHARTS.forEach((id) => empty(id, e.message));
    }
  }

  const FEED_LABEL = {
    "bridge.day-rating": "Day rating", "bridge.log": "Logged", "bridge.eat": "Ate a portion",
    "bridge.log-food": "Logged food", "bridge.log-set": "Logged set",
    "bridge.log-commitment": "Word", "bridge.checkin": "Check-in",
  };
  function escapeHtml(x) { const d = document.createElement("div"); d.textContent = x; return d.innerHTML; }
  async function loadFeed() {
    try {
      const { items } = await fetchJSON("/api/log/recent");
      const feed = document.getElementById("activity-feed");
      // The API selects write attempts before limiting and preserves outcome.
      if (!items.length) return;
      feed.innerHTML = items.slice(0, 12).map(it => {
        let detail = "";
        try { const d = JSON.parse(it.detail); detail = (d.args || []).join(" "); } catch (_) {}
        const when = new Date(it.ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
        const command = escapeHtml(it.event.replace("bridge.", ""));
        const label = it.status === "succeeded" ? (FEED_LABEL[it.event] || command)
          : `${it.status === "failed" ? "Failed" : "Not confirmed"}: ${command}`;
        return `<li><span class="feed-when">${when}</span><span class="feed-what">${label} <span class="dim">${escapeHtml(detail)}</span></span></li>`;
      }).join("");
    } catch (_) { /* feed is non-critical */ }
  }

  function renderRange() {
    metricChart("ch-rhr", "resting_hr");
    metricChart("ch-hrv", "hrv_ms");
    metricChart("ch-sleep", "sleep_hours");
    metricChart("ch-steps", "steps");
    bodyChart();
    // task-52 additions — daily_metrics/body_metrics extra columns ride
    // along on the same state.metrics/state.body already loaded above.
    metricChart("ch-resp", "respiratory_rate");
    metricChart("ch-spo2", "spo2_pct");
    metricChart("ch-walkhr", "walking_hr_avg");
    hrRangeChart();
    fieldBarChart("ch-activekcal", "active_energy_kcal", pal().good, "No active-energy rows yet");
    fieldBarChart("ch-basalkcal", "basal_energy_kcal", pal().warn, "No basal-energy rows yet");
    fieldBarChart("ch-exercise", "exercise_min", pal().accent, "No exercise-minutes rows yet");
    fieldBarChart("ch-distance", "distance_km", pal().good, "No distance rows yet");
    fieldBarChart("ch-flights", "flights", pal().warn, "No flights-climbed rows yet");
    measurementsChart();
    bodyFatChart();
    sleepStagesChart();
    timeAsleepChart();
    hevyVolumeChart();
    dayRatingChart();
    waterChart();
  }
  function renderAll() { renderRange(); loadFeed(); }

  async function loadRange() {
    const [m, b, s, hv] = await Promise.all([
      fetchJSON(`/api/dash/metrics?days=${state.days}`),
      fetchJSON(`/api/dash/body?days=${state.days}`),
      fetchJSON(`/api/dash/sleep?days=${state.days}`),
      fetchJSON(`/api/dash/hevy-volume?days=${state.days}`),
    ]);
    state.metrics = m.rows; state.body = b.rows; state.sleepLog = s.rows;
    state.hevyVolume = hv.rows;
  }

  const RANGE_CHARTS = ["ch-rhr", "ch-hrv", "ch-sleep", "ch-steps", "ch-body",
    "ch-resp", "ch-spo2", "ch-walkhr", "ch-hrrange",
    "ch-activekcal", "ch-basalkcal", "ch-exercise", "ch-distance", "ch-flights",
    "ch-measurements", "ch-bodyfat", "ch-sleepstages", "ch-timeasleep",
    "ch-hevyvol"];

  async function boot() {
    const [subj, water, range] = await Promise.allSettled([
      // These datasets are filtered locally, including when All is selected.
      fetchJSON("/api/dash/subjective?days=all"),
      fetchJSON("/api/nutrition/water?days=all"),
      loadRange(),
    ]);
    if (subj.status === "fulfilled") state.subjective = subj.value.rows;
    if (water.status === "fulfilled") state.water = water.value.rows;
    renderAll();
    if (range.status === "rejected") RANGE_CHARTS.forEach(id => empty(id, range.reason.message));
    if (subj.status === "rejected") empty("ch-dayrating", subj.reason.message);
    if (water.status === "rejected") empty("ch-water", water.reason.message);
    loadBodyComposition();
  }

  // task-50 item 3: standard .dd widget (ui-common owns the .on state) —
  // replaces the old native pill row; dd label -> the exact same 7/30/90/
  // 365/all windows the pills used ("Quarter" = 90d, same as the old "90d"
  // pill — see the template comment).
  const RANGE_DAYS = { Week: 7, Month: 30, Quarter: 90, Year: 365, All: "all" };
  window.HermesUI.onRange("range", async (val) => {
    state.days = RANGE_DAYS[val];
    try {
      await loadRange();
      renderRange();
    } catch (e) {
      RANGE_CHARTS.forEach(id => empty(id, e.message));
    }
  });

  document.getElementById("source-filter").addEventListener("change", (ev) => {
    state.source = ev.target.value;
    renderRange();
  });

  // Category filter (design .dd, task 21): purely client-side show/hide of
  // the [data-cat] card wrappers — every card's data is already loaded, so
  // hiding/showing is real filtering, not a stub. Re-shown chart containers
  // go from display:none to their real size, so echarts needs an explicit
  // resize() or the canvas stays at its stale (zero) dimensions.
  window.HermesUI.onRange("category", (cat) => {
    const want = cat === "All" ? null : cat.toLowerCase();
    document.querySelectorAll("[data-cat]").forEach((card) => {
      card.hidden = want !== null && card.dataset.cat !== want;
    });
    Object.entries(charts).forEach(([id, c]) => {
      if (!document.getElementById(id).closest("[hidden]")) c.resize();
    });
  });

  window.addEventListener("themechange", renderAll);
  window.addEventListener("resize", () => Object.values(charts).forEach(c => c.resize()));
  state.source = document.getElementById("source-filter").value || "all";
  boot();
})();
