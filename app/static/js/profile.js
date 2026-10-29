/* Profile & targets (fork owner decision 2026-10-29, docs/PRODUCT_AUTHORITY.md):
   the logged-in user's own owner_profile/phase/nutrition_targets/planned_times
   config, plus (non-owner only) their profile.json nutrition overlay. Strict
   CSP (script-src 'self', no inline JS/CSS) — everything lives here. DOM is
   built with textContent/createElement only; nothing from the server is ever
   assigned via innerHTML. */
(function () {
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const toastWrap = document.getElementById("toast-wrap");
  const $ = (id) => document.getElementById(id);

  function toast(msg, kind) {
    const el = document.createElement("div");
    el.className = "toast " + (kind || "");
    el.textContent = msg;
    toastWrap.appendChild(el);
    requestAnimationFrame(() => el.classList.add("show"));
    setTimeout(() => { el.classList.remove("show"); setTimeout(() => el.remove(), 250); }, 4200);
  }

  async function req(url, opts) {
    const r = await fetch(url, { credentials: "same-origin", ...opts });
    if (r.status === 401) { location.assign("/login"); throw new Error("session expired"); }
    const d = await r.json().catch(() => ({}));
    if (!r.ok || d.ok === false) throw new Error(d.error || "HTTP " + r.status);
    return d;
  }
  const post = (url, body) => req(url, { method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: JSON.stringify(body) });

  function labelFor(key) {
    return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }

  const PLANNED_LABELS = { wake: "Wake", bed: "Bed", workout: "Workout", dose: "Medication dose" };

  let state = null;

  function renderProfile() {
    const p = state.profile || {};
    $("pf-sex").value = p.sex || "";
    $("pf-dob").value = p.dob || "";
    $("pf-height").value = p.height_cm != null ? p.height_cm : "";
    $("pf-activity").value = p.activity_fallback || "";
  }

  function renderPhase() {
    if (state.phase) $("pf-phase").value = state.phase;
    $("pf-phase-started").textContent = state.phase_started
      ? `Current phase started ${state.phase_started}.`
      : "Phase not set yet.";
  }

  function renderTargets() {
    const t = state.targets || {};
    $("pf-target-kcal").value = t.kcal != null ? t.kcal : "";
    $("pf-target-protein").value = t.protein_g != null ? t.protein_g : "";

    const engine = state.engine || {};
    const reasonEl = $("pf-engine-reason");
    const bandEl = $("pf-engine-band");
    if (engine.status === "ok") {
      reasonEl.hidden = true;
      const kcal = engine.targets && engine.targets.kcal;
      const protein = engine.targets && engine.targets.protein_g;
      bandEl.textContent = kcal
        ? `Computed band: ${kcal.band_low}–${kcal.band_high} kcal · protein ${protein ? protein.target : "—"} g`
        : "";
    } else {
      reasonEl.hidden = false;
      reasonEl.textContent = "Targets engine: " + (engine.reason || engine.error || "not yet available");
      bandEl.textContent = "";
    }
  }

  function microRow(name, target) {
    const row = document.createElement("label");
    row.className = "field";
    row.dataset.name = name;
    const span = document.createElement("span");
    span.textContent = labelFor(name);
    row.appendChild(span);
    const wrap = document.createElement("div");
    wrap.className = "row gap-2";
    const input = document.createElement("input");
    input.type = "number";
    input.step = "any";
    input.min = "0";
    input.value = target;
    wrap.appendChild(input);
    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "btn quiet";
    removeBtn.textContent = "Remove";
    removeBtn.addEventListener("click", () => { row.remove(); refreshMicroAddOptions(); });
    wrap.appendChild(removeBtn);
    row.appendChild(wrap);
    return row;
  }

  function refreshMicroAddOptions() {
    const used = new Set([...$("pf-micro-rows").children].map((row) => row.dataset.name));
    const select = $("pf-micro-add-name");
    select.replaceChildren();
    for (const name of state.catalog.micro_target_names) {
      if (used.has(name)) continue;
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = labelFor(name);
      select.appendChild(opt);
    }
  }

  function phaseTriple(containerId, values) {
    const container = $(containerId);
    container.replaceChildren();
    for (const phase of state.catalog.diet_phases) {
      const label = document.createElement("label");
      label.className = "field";
      label.dataset.phase = phase;
      const span = document.createElement("span");
      span.textContent = labelFor(phase);
      label.appendChild(span);
      const input = document.createElement("input");
      input.type = "number";
      input.step = "any";
      if (values && values[phase] != null) input.value = values[phase];
      label.appendChild(input);
      container.appendChild(label);
    }
  }

  function readPhaseTriple(containerId) {
    const inputs = [...$(containerId).children].map((label) => ({
      phase: label.dataset.phase,
      raw: label.querySelector("input").value.trim(),
    }));
    const filled = inputs.filter((i) => i.raw !== "");
    if (filled.length === 0) return undefined;   // leave unchanged
    if (filled.length !== inputs.length) {
      throw new Error("set all three phases, or clear all three to leave it unchanged");
    }
    const out = {};
    for (const { phase, raw } of inputs) {
      const n = Number(raw);
      if (!Number.isFinite(n)) throw new Error(`${labelFor(phase)} must be a number`);
      out[phase] = n;
    }
    return out;
  }

  function renderNutritionConfig() {
    const unavailable = $("pf-nutrition-config-unavailable");
    const form = $("pf-nutrition-config-form");
    if (!state.per_user_overlay_supported) {
      unavailable.hidden = false;
      unavailable.querySelector("p").textContent = state.per_user_overlay_reason || "unavailable";
      form.hidden = true;
      return;
    }
    unavailable.hidden = true;
    form.hidden = false;

    const cfg = state.nutrition_config || {};
    const rows = $("pf-micro-rows");
    rows.replaceChildren();
    const micros = cfg.nutrient_targets || {};
    for (const name of Object.keys(micros).sort()) {
      rows.appendChild(microRow(name, micros[name]));
    }
    refreshMicroAddOptions();

    phaseTriple("pf-phase-offset-rows", cfg.phase_offsets);
    phaseTriple("pf-protein-per-kg-rows", cfg.protein_per_kg);
  }

  function plannedRow(metric) {
    const row = document.createElement("tr");
    row.dataset.metric = metric;
    const nameTd = document.createElement("td");
    nameTd.textContent = PLANNED_LABELS[metric] || labelFor(metric);
    row.appendChild(nameTd);

    const existing = (state.planned_times || {})[metric];
    const timeTd = document.createElement("td");
    const timeInput = document.createElement("input");
    timeInput.type = "time";
    if (existing) timeInput.value = existing.planned;
    timeTd.appendChild(timeInput);
    row.appendChild(timeTd);

    const tolTd = document.createElement("td");
    const tolInput = document.createElement("input");
    tolInput.type = "number";
    tolInput.min = "5";
    tolInput.max = "240";
    tolInput.placeholder = "30";
    if (existing) tolInput.value = existing.tolerance_min;
    tolTd.appendChild(tolInput);
    row.appendChild(tolTd);

    const actionTd = document.createElement("td");
    const saveBtn = document.createElement("button");
    saveBtn.type = "button";
    saveBtn.className = "btn quiet";
    saveBtn.textContent = "Save";
    saveBtn.addEventListener("click", async () => {
      const status = $("pf-planned-status");
      if (!timeInput.value) { status.textContent = "Set a time first."; return; }
      const body = { metric, time: timeInput.value };
      if (tolInput.value.trim() !== "") body.tolerance = Number(tolInput.value);
      saveBtn.disabled = true;
      status.textContent = "Saving…";
      try {
        await post("/api/profile/planned-time", body);
        status.textContent = "Saved.";
        toast(`${PLANNED_LABELS[metric] || metric} time saved`, "good");
        await load();
      } catch (e) {
        status.textContent = "Couldn't save: " + e.message;
      } finally {
        saveBtn.disabled = false;
      }
    });
    actionTd.appendChild(saveBtn);
    row.appendChild(actionTd);
    return row;
  }

  function renderPlannedTimes() {
    const body = $("pf-planned-body");
    body.replaceChildren();
    for (const metric of state.catalog.planned_metrics) {
      body.appendChild(plannedRow(metric));
    }
  }

  function renderAll() {
    renderProfile();
    renderPhase();
    renderTargets();
    renderNutritionConfig();
    renderPlannedTimes();
  }

  async function load() {
    $("pf-load-status").textContent = "";
    try {
      state = await req("/api/profile");
      renderAll();
    } catch (e) {
      $("pf-load-status").textContent = "Couldn't load profile: " + e.message;
    }
  }

  $("pf-profile-save").addEventListener("click", async () => {
    const status = $("pf-profile-status");
    const body = {};
    if ($("pf-sex").value) body.sex = $("pf-sex").value;
    if ($("pf-dob").value) body.dob = $("pf-dob").value;
    if ($("pf-height").value.trim() !== "") body.height_cm = Number($("pf-height").value);
    if ($("pf-activity").value) body.activity_fallback = $("pf-activity").value;
    if (Object.keys(body).length === 0) { status.textContent = "Nothing to save."; return; }
    const btn = $("pf-profile-save");
    btn.disabled = true;
    status.textContent = "Saving…";
    try {
      await post("/api/profile/profile", body);
      status.textContent = "Saved.";
      toast("Profile saved", "good");
      await load();
    } catch (e) {
      status.textContent = "Couldn't save: " + e.message;
    } finally {
      btn.disabled = false;
    }
  });

  $("pf-phase-save").addEventListener("click", async () => {
    const status = $("pf-phase-status");
    const btn = $("pf-phase-save");
    btn.disabled = true;
    status.textContent = "Saving…";
    try {
      await post("/api/profile/phase", { phase: $("pf-phase").value });
      status.textContent = "Saved.";
      toast("Phase saved", "good");
      await load();
    } catch (e) {
      status.textContent = "Couldn't save: " + e.message;
    } finally {
      btn.disabled = false;
    }
  });

  $("pf-targets-save").addEventListener("click", async () => {
    const status = $("pf-targets-status");
    const kcalRaw = $("pf-target-kcal").value.trim();
    const proteinRaw = $("pf-target-protein").value.trim();
    if (!kcalRaw && !proteinRaw) { status.textContent = "Nothing to save."; return; }
    const btn = $("pf-targets-save");
    btn.disabled = true;
    status.textContent = "Saving…";
    try {
      if (kcalRaw) await post("/api/profile/targets", { name: "kcal", target: Number(kcalRaw) });
      if (proteinRaw) await post("/api/profile/targets", { name: "protein_g", target: Number(proteinRaw) });
      status.textContent = "Saved.";
      toast("Targets saved", "good");
      await load();
    } catch (e) {
      status.textContent = "Couldn't save: " + e.message;
    } finally {
      btn.disabled = false;
    }
  });

  $("pf-micro-add-btn").addEventListener("click", () => {
    const name = $("pf-micro-add-name").value;
    const target = $("pf-micro-add-target").value.trim();
    if (!name || target === "") return;
    const n = Number(target);
    if (!Number.isFinite(n) || n <= 0) {
      $("pf-nutrition-config-status").textContent = "Target must be a positive number.";
      return;
    }
    $("pf-micro-rows").appendChild(microRow(name, n));
    $("pf-micro-add-target").value = "";
    refreshMicroAddOptions();
  });

  $("pf-nutrition-config-save").addEventListener("click", async () => {
    const status = $("pf-nutrition-config-status");
    const patch = {};
    try {
      const micros = {};
      for (const row of $("pf-micro-rows").children) {
        const n = Number(row.querySelector("input").value);
        if (!Number.isFinite(n) || n <= 0) {
          throw new Error(`${labelFor(row.dataset.name)} must be a positive number`);
        }
        micros[row.dataset.name] = n;
      }
      patch.nutrient_targets = micros;
      const offsets = readPhaseTriple("pf-phase-offset-rows");
      if (offsets !== undefined) patch.phase_offsets = offsets;
      const perKg = readPhaseTriple("pf-protein-per-kg-rows");
      if (perKg !== undefined) patch.protein_per_kg = perKg;
    } catch (e) {
      status.textContent = e.message;
      return;
    }
    const btn = $("pf-nutrition-config-save");
    btn.disabled = true;
    status.textContent = "Saving…";
    try {
      await post("/api/profile/nutrition-config", patch);
      status.textContent = "Saved.";
      toast("Nutrition detail saved", "good");
      await load();
    } catch (e) {
      status.textContent = "Couldn't save: " + e.message;
    } finally {
      btn.disabled = false;
    }
  });

  load();
})();
