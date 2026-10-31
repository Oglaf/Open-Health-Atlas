/* Nutrition pillar: goals and meals (no consumption logging).
   Goals tab: the targets engine's daily targets (`/api/nutrition/targets`,
   fetched once and re-fetched after an edit) drawn as a calorie ring split by
   macro, a live diet-phase switch, and micronutrient chips. Editing goes
   through the existing validated /api/profile routes: calorie and protein
   overrides (/targets), phase (/phase) and micronutrient targets
   (/nutrition-config, non-owner accounts only — the bootstrap owner reads
   installation config). Unavailable targets show the shared setup help
   (#nTgtBanner), never a fabricated number.
   Meals tab: every recipe with per-portion nutrients (/api/nutrition/recipe/
   <rid>), a macro split bar and each macro as a % of the daily goal, plus
   create / edit / delete through /api/nutrition/meal (health.py meal-save and
   meal-delete). Editing sends only the fields you changed, so imported recipes
   keep their stored precision. Bar widths use the CSSOM (CSP: no inline
   styles). The /eat, /prep and /log-food routes stay for
   agents; nothing here logs consumption.
   Supplements tab: product list (current state), Adherence trend (windowed by
   the header dd) and each product's real `notes`. */
(function () {
  if (typeof echarts === "undefined" || !window.HermesCharts) return;
  const { fetchJSON, pal, lineOpt } = window.HermesCharts;
  const RANGE_DAYS = { Day: "1", Week: "7", Month: "30", Year: "365", All: "all" };
  const WINDOW_LABEL = { Day: "past day", Week: "last 7 days", Month: "last 30 days",
    Year: "last year", All: "all time" };
  const range = () => window.HermesUI.range("nutrition") || "Week";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => { const d = document.createElement("div"); d.textContent = s == null ? "" : s; return d.innerHTML; };
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const toastWrap = $("toast-wrap");
  const charts = {};

  function toast(msg, kind) {
    const el = document.createElement("div");
    el.className = "toast " + (kind || "");
    el.textContent = msg;
    toastWrap.appendChild(el);
    requestAnimationFrame(() => el.classList.add("show"));
    setTimeout(() => { el.classList.remove("show"); setTimeout(() => el.remove(), 250); }, 3400);
  }
  async function postJSON(url, body) {
    const r = await fetch(url, { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: JSON.stringify(body || {}) });
    if (r.status === 401) { location.assign("/login"); throw new Error("session expired"); }
    const d = await r.json().catch(() => ({}));
    if (!r.ok || d.ok === false) throw new Error(d.error || "HTTP " + r.status);
    return d;
  }
  function status(id, text, bad) {
    const el = $(id);
    if (!el) return;
    el.textContent = text || "";
    el.classList.toggle("err", !!bad);
  }

  function chart(id) {
    const el = $(id);
    if (charts[id]) charts[id].dispose();
    charts[id] = echarts.init(el);
    return charts[id];
  }
  function empty(id, label) {
    chart(id).setOption({ graphic: { type: "text", left: "center", top: "middle",
      style: { text: label, fill: pal().muted, fontSize: 12 } } });
  }

  const titleCase = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  const fmt = (n) => Math.round(n).toLocaleString();
  const PHASE_HELP = {
    cut: "Eat below maintenance to lose fat; protein stays high to protect muscle.",
    maintain: "Eat at maintenance to hold your weight.",
    bulk: "Eat above maintenance to support muscle gain.",
  };
  const MICRO_LABEL = { vitamin_d: "Vitamin D", vitamin_b12: "Vitamin B12", vitamin_c: "Vitamin C",
    omega3_epa_dha: "Omega-3 EPA+DHA" };
  const microLabel = (key) => MICRO_LABEL[key] || titleCase(key.replace(/_/g, " "));
  // The ten nutrients the targets engine requires (all of them, or it computes
  // no targets at all), in display order: vitamins, minerals, then omega-3.
  const MICRO_ORDER = ["vitamin_d", "vitamin_b12", "vitamin_c", "folate", "magnesium", "calcium",
    "potassium", "iron", "zinc", "omega3_epa_dha"];
  const MICRO_MICROGRAM = new Set(["vitamin_d", "vitamin_b12", "folate"]);
  const microUnit = (key, engineUnit) =>
    engineUnit ? (engineUnit === "ug" ? "µg" : engineUnit) : (MICRO_MICROGRAM.has(key) ? "µg" : "mg");
  const microRank = (key) => { const i = MICRO_ORDER.indexOf(key); return i < 0 ? MICRO_ORDER.length : i; };
  function microNames() {
    const known = (profileInfo && profileInfo.catalog && profileInfo.catalog.micro_target_names) || [];
    return [...MICRO_ORDER, ...known.filter((n) => !MICRO_ORDER.includes(n))];
  }

  // The targets engine, shared by the goals card and the meal cards. A bridge
  // error resolves to a synthetic {status:"error"} so every consumer's
  // `status !== "ok"` check renders the honest state instead of throwing.
  let targetsPromise = null;
  function loadTargets(force) {
    if (force || !targetsPromise) {
      targetsPromise = fetchJSON("/api/nutrition/targets")
        .then((d) => d.result || { status: "error", reason: "The target response was incomplete." })
        .catch((error) => ({ status: "error", reason: error.message || "The target request failed." }))
        .then((targets) => {
          renderTargetHelp(targets);
          return targets;
        });
    }
    return targetsPromise;
  }

  function renderTargetHelp(targets) {
    const banner = $("nTgtBanner");
    banner.hidden = targets.status === "ok";
    $("nTgtReasonLabel").textContent = targets.status === "error" ? "Request error" : "Reason";
    $("nTgtReason").textContent = targets.reason || "The engine did not return a reason.";
    $("nTgtReload").hidden = targets.status !== "error";
  }

  /* ------- Goals ------- */
  let profileInfo = null;   // /api/profile: catalog + whether micro targets are editable
  async function loadProfileInfo() {
    try { profileInfo = await fetchJSON("/api/profile"); } catch (_) { profileInfo = null; }
  }

  function setText(id, text) { const el = $(id); if (el) el.textContent = text; }

  function renderRing(t) {
    const ring = $("nRing");
    if (t.status !== "ok") { ring.classList.add("ng-empty"); return; }
    const p = t.targets.protein_g.target * 4, c = t.targets.carbs_g.target * 4, f = t.targets.fat_g.target * 9;
    const total = p + c + f;
    if (!(total > 0)) { ring.classList.add("ng-empty"); return; }
    ring.classList.remove("ng-empty");
    ring.style.setProperty("--a", (p / total * 100).toFixed(2) + "%");
    ring.style.setProperty("--b", ((p + c) / total * 100).toFixed(2) + "%");
    const share = (kcal) => Math.round(kcal / total * 100) + "% of calories";
    ring.dataset.split = `protein ${share(p)}, carbs ${share(c)}, fat ${share(f)}`;
    ring.setAttribute("aria-label", "Macro split of the daily calorie goal: " + ring.dataset.split);
  }

  function microTile(m) {
    const tile = document.createElement("div");
    tile.className = "ng-tile";
    const name = document.createElement("span");
    name.className = "label";
    name.textContent = microLabel(m.nutrient);
    const value = document.createElement("span");
    value.className = "ng-tile-val";
    const amount = document.createElement("b");
    amount.textContent = m.target;
    const unit = document.createElement("span");
    unit.textContent = " " + microUnit(m.nutrient, m.unit);
    value.append(amount, unit);
    tile.append(name, value);
    return tile;
  }

  function renderMicros(t) {
    const box = $("nMicroChips");
    const micros = (t.status === "ok" ? (t.targets.micros || []) : []).filter((m) => m.target != null)
      .sort((a, b) => microRank(a.nutrient) - microRank(b.nutrient));
    const canEdit = !!(profileInfo && profileInfo.per_user_overlay_supported);
    if (!micros.length) {
      box.replaceChildren(Object.assign(document.createElement("span"), {
        className: "muted micro",
        textContent: canEdit
          ? "No targets set yet. Choose Edit targets to enter the ten daily targets your goals are calculated from."
          : t.status === "ok" ? "No micronutrient targets configured." : "Available once targets are calculated.",
      }));
    } else {
      box.replaceChildren(...micros.map(microTile));
    }
    $("nMicroEditBtn").hidden = !canEdit;
    setText("nMicroNote", profileInfo && !profileInfo.per_user_overlay_supported
      ? "Micronutrient targets for this account come from the installation configuration and can't be edited here."
      : "");
  }

  async function renderGoals(t) {
    const ok = t.status === "ok";
    renderRing(t);
    if (ok) {
      const kt = t.targets.kcal, pt = t.targets.protein_g, ct = t.targets.carbs_g, ft = t.targets.fat_g;
      setText("nTgtCal", fmt(kt.target));
      setText("nTgtProtein", `${Math.round(pt.target)} g`);
      setText("nTgtCarbs", `${Math.round(ct.target)} g`);
      setText("nTgtFat", `${Math.round(ft.target)} g`);
      setText("nTgtBand", `Maintenance band ${fmt(kt.band_low)}–${fmt(kt.band_high)} kcal · ${titleCase(t.phase.phase)} phase`);
    } else {
      ["nTgtCal", "nTgtProtein", "nTgtCarbs", "nTgtFat"].forEach((id) => setText(id, "—"));
      setText("nTgtBand", "Maintenance band appears once targets are calculated.");
    }
    // Water: engine target, real even when the rest of the engine is
    // insufficient_data (water_ml is the one target it always computes).
    const waterTarget = t.targets && t.targets.water_ml ? t.targets.water_ml.target : null;
    setText("nTgtWater", waterTarget != null ? (waterTarget / 1000).toFixed(1) + " L" : "—");

    const seg = { cut: $("nPhaseCut"), maintain: $("nPhaseMaintain"), bulk: $("nPhaseBulk") };
    Object.entries(seg).forEach(([key, btn]) => {
      const active = ok && t.phase.phase === key;
      btn.classList.toggle("on", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });
    setText("nPhaseCaption", !ok
      ? "Phase is applied once targets can be calculated. You can still pick one."
      : t.phase.set
        ? `Current phase: ${titleCase(t.phase.phase)} (since ${t.phase.started}).`
        : "No phase set — treating as Maintain.");
    setText("nPhaseHelp", ok ? PHASE_HELP[t.phase.phase] || "" : "");
    $("nGoalEditBtn").hidden = !ok;
    renderMicros(t);
  }

  async function reloadGoals() {
    const t = await loadTargets(true);
    await renderGoals(t);
    targetsSnapshot = t;
    renderMenu();
    return t;
  }

  // Calories / protein overrides
  $("nGoalEditBtn").addEventListener("click", async () => {
    const t = await loadTargets();
    if (t.status !== "ok") return;
    $("nGoalKcal").value = Math.round(t.targets.kcal.target);
    $("nGoalProtein").value = Math.round(t.targets.protein_g.target);
    status("nGoalStatus", "");
    $("nGoalForm").hidden = false;
    $("nGoalEditBtn").hidden = true;
    $("nGoalKcal").focus();
  });
  function closeGoalForm() {
    $("nGoalForm").hidden = true;
    $("nGoalEditBtn").hidden = false;
  }
  $("nGoalCancel").addEventListener("click", closeGoalForm);
  $("nGoalForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const t = await loadTargets();
    const wanted = [["kcal", "nGoalKcal", t.targets.kcal.target, 500, 10000, "Calories"],
      ["protein_g", "nGoalProtein", t.targets.protein_g.target, 10, 500, "Protein"]];
    const changes = [];
    for (const [name, id, current, lo, hi, label] of wanted) {
      const n = parseFloat($(id).value);
      if (!Number.isFinite(n) || n < lo || n > hi) {
        status("nGoalStatus", `${label} must be between ${lo} and ${hi}.`, true);
        return;
      }
      if (Math.round(n) !== Math.round(current)) changes.push([name, n]);
    }
    if (!changes.length) { closeGoalForm(); return; }
    const btn = $("nGoalSave");
    btn.disabled = true;
    status("nGoalStatus", "Saving…");
    try {
      for (const [name, target] of changes) await postJSON("/api/profile/targets", { name, target });
      closeGoalForm();
      await reloadGoals();
      toast("Goals saved", "good");
    } catch (e) {
      status("nGoalStatus", "Couldn't save: " + e.message, true);
    } finally { btn.disabled = false; }
  });

  // Diet phase
  $("nPhaseSeg").addEventListener("click", async (ev) => {
    const btn = ev.target.closest("button[data-phase]");
    if (!btn || btn.getAttribute("aria-pressed") === "true") return;
    const buttons = [...$("nPhaseSeg").querySelectorAll("button")];
    buttons.forEach((b) => (b.disabled = true));
    status("nPhaseStatus", "Saving…");
    try {
      await postJSON("/api/profile/phase", { phase: btn.dataset.phase });
      await reloadGoals();
      status("nPhaseStatus", "");
      toast(`Phase set to ${titleCase(btn.dataset.phase)}`, "good");
    } catch (e) {
      status("nPhaseStatus", "Couldn't save: " + e.message, true);
    } finally { buttons.forEach((b) => (b.disabled = false)); }
  });

  // Micronutrient targets (non-owner accounts). The engine needs all ten, so the
  // editor is one field per required nutrient: nothing to add or remove.
  function microField(name, target, unit) {
    const label = document.createElement("label");
    label.className = "field";
    const text = document.createElement("span");
    text.textContent = `${microLabel(name)} (${unit})`;
    const input = document.createElement("input");
    input.type = "number"; input.min = "0"; input.step = "any";
    input.dataset.micro = name;
    input.value = target == null ? "" : target;
    label.append(text, input);
    return label;
  }
  $("nMicroEditBtn").addEventListener("click", async () => {
    const t = await loadTargets();
    const current = {};
    (t.status === "ok" ? (t.targets.micros || []) : []).forEach((m) => { if (m.target != null) current[m.nutrient] = m; });
    $("nMicroFields").replaceChildren(...microNames().map((n) =>
      microField(n, current[n] && current[n].target, microUnit(n, current[n] && current[n].unit))));
    status("nMicroStatus", "");
    $("nMicroForm").hidden = false;
    $("nMicroChips").hidden = true;
    $("nMicroEditBtn").hidden = true;
  });
  function closeMicroForm() {
    $("nMicroForm").hidden = true;
    $("nMicroChips").hidden = false;
    $("nMicroEditBtn").hidden = !(profileInfo && profileInfo.per_user_overlay_supported);
  }
  $("nMicroCancel").addEventListener("click", closeMicroForm);
  $("nMicroForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const targets = {};
    for (const label of $("nMicroFields").children) {
      const input = label.children[1];
      const n = parseFloat(input.value);
      if (!Number.isFinite(n) || n <= 0) {
        status("nMicroStatus", `Every nutrient needs a target above zero — ${microLabel(input.dataset.micro)} is missing.`, true);
        return;
      }
      targets[input.dataset.micro] = n;
    }
    const btn = $("nMicroSave");
    btn.disabled = true;
    status("nMicroStatus", "Saving…");
    try {
      await postJSON("/api/profile/nutrition-config", { nutrient_targets: targets });
      closeMicroForm();
      await reloadGoals();
      toast("Micronutrient targets saved", "good");
    } catch (e) {
      status("nMicroStatus", "Couldn't save: " + e.message, true);
    } finally { btn.disabled = false; }
  });

  /* ------- Meals ------- */
  let recipeCatalog = [];            // every recipe definition (/recipes)
  let targetsSnapshot = null;
  const detailById = new Map();      // recipe_id -> /recipe/<rid> payload
  let activePill = "all";
  let pendingDelete = null;          // recipe_id awaiting the second click
  let editing = null;                // {rid, originals} while the form edits an existing meal
  // dd label -> the real recipes.meal_type value.
  const MEAL_TAG = { Breakfast: "breakfast", Lunch: "lunch", Dinner: "dinner", Snacks: "snack" };
  const GOAL_ROWS = [["kcal", "Calories", "kcal", "kcal"], ["protein", "Protein", "g", "protein_g"],
    ["carbs", "Carbs", "g", "carbs_g"], ["fat", "Fat", "g", "fat_g"]];
  // Tracked micronutrients shown as chips: stored name -> [engine key, label, canonical unit].
  const MEAL_MICROS = { "Fiber": ["fibre", "Fibre", "g"], "Vitamin D": ["vitamin_d", "Vitamin D", "ug"],
    "Magnesium": ["magnesium", "Magnesium", "mg"], "Iron": ["iron", "Iron", "mg"], "Zinc": ["zinc", "Zinc", "mg"] };
  const unitKey = (u) => { u = (u || "").trim().toLowerCase(); return u === "µg" || u === "mcg" ? "ug" : u; };

  function perPortion(d) {
    if (!d || d.kcal_per_portion == null) return null;
    const m = (name) => { const x = (d.macros || []).find((y) => y.name === name); return x ? x.amount : null; };
    return { kcal: d.kcal_per_portion, protein: m("Protein"), carbs: m("Carbs"), fat: m("Fat") };
  }

  function renderMenu() {
    const list = $("nMenu");
    if (!recipeCatalog.length) {
      list.innerHTML = '<p class="dim">No meals yet. Use “+ New meal” to add your first one.</p>';
      return;
    }
    // Untagged recipes (meal_type null) only appear under "All meals".
    const meal = window.HermesUI.range("mealtype") || "All meals";
    let items = meal === "All meals" ? recipeCatalog
      : recipeCatalog.filter((r) => r.meal_type === MEAL_TAG[meal]);
    if (activePill === "protein") {
      const protein = (r) => { const p = perPortion(detailById.get(r.recipe_id)); return p && p.protein != null ? p.protein : -1; };
      items = [...items].sort((a, b) => protein(b) - protein(a));
    }
    if (!items.length) {
      list.innerHTML = `<p class="dim">No meals tagged ${esc(meal.toLowerCase())} yet — edit a meal to set its type.</p>`;
      return;
    }
    list.replaceChildren(...items.map(mealCard));
  }

  function mealCard(r) {
    const rid = r.recipe_id;
    const d = detailById.get(rid);
    const p = perPortion(d);
    const t = targetsSnapshot;
    const ok = t && t.status === "ok";
    const card = document.createElement("div");
    card.className = "card";
    const confirming = pendingDelete === rid;
    let html = `<div class="ng-meal-head"><div><span class="strong">${esc(r.name)}</span>` +
      (r.meal_type ? ` <span class="chip muted">${esc(r.meal_type)}</span>` : "") + `</div>` +
      `<div class="ng-meal-actions">` +
      (confirming
        ? `<span class="micro">Delete this meal?</span><button class="ng-btn danger" data-act="confirm" type="button">Delete</button>` +
          `<button class="ng-btn" data-act="cancel" type="button">Keep</button>`
        : `<a class="muted micro" href="/nutrition/recipe/${encodeURIComponent(rid)}">details →</a>` +
          `<button class="ng-btn" data-act="edit" type="button">Edit</button>` +
          `<button class="ng-btn danger" data-act="delete" type="button">Delete</button>`) +
      `</div></div>`;
    if (!p) {
      html += `<p class="muted micro m0 mt-2">${d ? "Nutrients pending — set the batch weight." : "Loading…"}` +
        `${r.grams_per_portion ? "" : " Portion size not set."}</p>`;
    } else {
      const pk = (p.protein || 0) * 4, ck = (p.carbs || 0) * 4, fk = (p.fat || 0) * 9;
      const total = pk + ck + fk;
      const w = (x) => (total > 0 ? (x / total * 100).toFixed(1) : 0) + "%";
      html += `<div class="ng-meal-kcal"><span class="n">${Math.round(p.kcal)}</span><span class="muted micro">kcal per portion` +
        `${r.grams_per_portion ? " · " + Math.round(r.grams_per_portion) + " g" : ""}</span></div>` +
        `<div class="ng-stack mt-2" role="img" aria-label="Macro split of one portion">` +
        `<span class="ng-p" data-w="${w(pk)}"></span><span class="ng-c" data-w="${w(ck)}"></span>` +
        `<span class="ng-f" data-w="${w(fk)}"></span></div>` +
        `<div class="ng-meal-legend">` +
        `<span><span class="ng-dot ng-p"></span>Protein ${Math.round(p.protein || 0)} g</span>` +
        `<span><span class="ng-dot ng-c"></span>Carbs ${Math.round(p.carbs || 0)} g</span>` +
        `<span><span class="ng-dot ng-f"></span>Fat ${Math.round(p.fat || 0)} g</span></div>`;
      if (ok) {
        html += '<div class="goals-list mt-2">' + GOAL_ROWS.map(([k, label, , tkey]) => {
          const v = p[k];
          const target = t.targets[tkey];
          if (v == null || !target) return "";
          const pct = v / target.target * 100;
          return `<div class="goal-row"><span class="goal-label">${label}</span>` +
            `<div class="barbg"><div class="barfill ng-fill" data-w="${Math.min(100, pct).toFixed(1)}%"></div></div>` +
            `<span class="goal-val">${Math.round(pct)}% of daily goal</span></div>`;
        }).join("") + "</div>";
      } else {
        html += '<p class="muted micro m0 mt-2">Set up your targets to see each meal as a share of your daily goal.</p>';
      }
      const microTargets = {};
      if (ok) (t.targets.micros || []).forEach((m) => { microTargets[m.nutrient] = m; });
      const chips = (d.micronutrients || []).filter((m) => MEAL_MICROS[m.name] && m.amount != null).map((m) => {
        const [key, label] = MEAL_MICROS[m.name];
        const mt = microTargets[key];
        const share = mt && mt.target && unitKey(mt.unit) === unitKey(m.unit)
          ? ` · ${Math.round(m.amount / mt.target * 100)}%` : "";
        return `<span class="chip muted">${label} ${m.amount} ${esc(m.unit || "")}${share}</span>`;
      });
      if (chips.length) html += `<div class="ng-chips mt-2">${chips.join("")}</div>`;
    }
    card.innerHTML = html;
    // The CSP forbids inline style attributes; widths are applied through the CSSOM.
    card.querySelectorAll("[data-w]").forEach((el) => { el.style.width = el.dataset.w; });
    card.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", () => mealAction(b.dataset.act, r, b)));
    return card;
  }

  async function mealAction(act, r, btn) {
    if (act === "edit") { openMealForm(r); return; }
    if (act === "delete") { pendingDelete = r.recipe_id; renderMenu(); return; }
    if (act === "cancel") { pendingDelete = null; renderMenu(); return; }
    if (act === "confirm") {
      btn.disabled = true;
      try {
        await postJSON(`/api/nutrition/meal/${encodeURIComponent(r.recipe_id)}/delete`);
        toast(`Deleted ${r.name}`, "good");
        pendingDelete = null;
        if (editing && editing.rid === r.recipe_id) closeMealForm();
        await loadRecipes();
      } catch (e) {
        toast("Couldn't delete: " + e.message, "bad");
        btn.disabled = false;
      }
    }
  }

  // The meal form: create, or edit an existing meal (only changed fields are sent).
  const NUT_INPUTS = () => [...$("nMealForm").querySelectorAll("input[data-nut]")];
  const num = (el) => { const v = String(el.value).trim(); return v === "" ? null : Number(v); };
  // Prefill value for a stored nutrient, only when its unit is the form's unit.
  function prefillMicro(d, storedName, unit) {
    const m = (d.micronutrients || []).find((x) => x.name === storedName);
    return m && m.amount != null && unitKey(m.unit) === unit ? m.amount : null;
  }
  function openMealForm(r) {
    const form = $("nMealForm");
    status("mf-status", "");
    form.reset();
    $("mf-portions").value = 1;
    editing = null;
    $("nMealFormTitle").textContent = r ? "Edit meal" : "New meal";
    $("mf-save").textContent = r ? "Save changes" : "Save meal";
    if (r) {
      const d = detailById.get(r.recipe_id) || {};
      const rec = d.recipe || {};
      const gpp = rec.grams_per_portion || r.grams_per_portion || "";
      const portions = rec.portions || (rec.batch_grams && gpp ? Math.max(1, Math.round(rec.batch_grams / gpp)) : 1);
      const p = perPortion(d) || {};
      const orig = {
        kcal: p.kcal != null ? p.kcal : null, protein_g: p.protein != null ? p.protein : null,
        carbs_g: p.carbs != null ? p.carbs : null, fat_g: p.fat != null ? p.fat : null,
        fiber_g: prefillMicro(d, "Fiber", "g"), vitamin_d_ug: prefillMicro(d, "Vitamin D", "ug"),
        magnesium_mg: prefillMicro(d, "Magnesium", "mg"), iron_mg: prefillMicro(d, "Iron", "mg"),
        zinc_mg: prefillMicro(d, "Zinc", "mg"),
      };
      const ingredients = (d.ingredients || []).map((i) =>
        [i.amount != null ? `${i.amount} ${i.unit || ""}`.trim() : "", i.name].filter(Boolean).join(" ")).join("\n");
      $("mf-name").value = r.name;
      $("mf-type").value = r.meal_type || "";
      $("mf-gpp").value = gpp;
      $("mf-portions").value = portions;
      NUT_INPUTS().forEach((el) => { const v = orig[el.dataset.nut]; el.value = v == null ? "" : v; });
      $("mf-ingredients").value = ingredients;
      editing = { rid: r.recipe_id, originals: orig, ingredients, gpp: Number(gpp) || null, portions };
    }
    form.hidden = false;
    form.scrollIntoView({ block: "nearest", behavior: "smooth" });
    $("mf-name").focus();
  }
  function closeMealForm() {
    $("nMealForm").hidden = true;
    editing = null;
  }
  $("nMealNew").addEventListener("click", () => openMealForm(null));
  $("mf-cancel").addEventListener("click", closeMealForm);

  $("nMealForm").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const name = $("mf-name").value.trim();
    const gpp = num($("mf-gpp"));
    const portions = num($("mf-portions"));
    if (!name) { status("mf-status", "Give the meal a name.", true); return; }
    if (gpp == null || !(gpp >= 1 && gpp <= 5000)) { status("mf-status", "Grams per portion must be between 1 and 5000.", true); return; }
    if (portions == null || !Number.isInteger(portions) || portions < 1 || portions > 100) {
      status("mf-status", "Portions must be a whole number from 1 to 100.", true); return;
    }
    const per = {};
    for (const el of NUT_INPUTS()) {
      const v = num(el);
      if (v != null && !(v >= 0)) { status("mf-status", "Nutrient amounts can't be negative.", true); return; }
      const key = el.dataset.nut;
      if (editing) {
        // send only what changed, so untouched imported values keep their precision
        const was = editing.originals[key];
        if (v != null && v !== was) per[key] = v;
      } else if (v != null) {
        per[key] = v;
      }
    }
    if (!editing) {
      const missing = ["kcal", "protein_g", "carbs_g", "fat_g"].filter((k) => per[k] == null);
      if (missing.length) { status("mf-status", "Calories, protein, carbs and fat are required.", true); return; }
    }
    const body = { name, meal_type: $("mf-type").value || null, grams_per_portion: gpp, portions, per_portion: per };
    const lines = $("mf-ingredients").value.split("\n").map((l) => l.trim()).filter(Boolean);
    if (editing) {
      body.recipe_id = editing.rid;
      if (lines.join("\n") !== editing.ingredients) body.ingredients = lines;
    } else if (lines.length) {
      body.ingredients = lines;
    }
    const btn = $("mf-save");
    btn.disabled = true;
    status("mf-status", "Saving…");
    try {
      await postJSON("/api/nutrition/meal", body);
      toast(editing ? `Saved ${name}` : `Added ${name}`, "good");
      closeMealForm();
      await loadRecipes();
    } catch (e) {
      status("mf-status", "Couldn't save: " + e.message, true);
    } finally { btn.disabled = false; }
  });

  async function loadRecipes() {
    try {
      const d = await fetchJSON("/api/nutrition/recipes");
      recipeCatalog = d.recipes;
      targetsSnapshot = await loadTargets();
      renderMenu();
      detailById.clear();
      await Promise.all(recipeCatalog.map(async (r) => {
        try { detailById.set(r.recipe_id, await fetchJSON("/api/nutrition/recipe/" + encodeURIComponent(r.recipe_id))); }
        catch (_) { /* card shows the pending line */ }
      }));
      renderMenu();
    } catch (e) {
      $("nMenu").innerHTML = `<p class="dim">${esc(e.message)}</p>`;
    }
  }
  window.HermesUI.onRange("mealtype", renderMenu);

  document.querySelectorAll("#nPills .pill").forEach((b) => {
    b.addEventListener("click", () => {
      activePill = b.dataset.pill;
      document.querySelectorAll("#nPills .pill").forEach((x) => {
        x.classList.toggle("active", x === b);
        x.setAttribute("aria-pressed", x === b ? "true" : "false");
      });
      renderMenu();
    });
  });

  /* ------- Supplements: honest-empty list + notes card -------
     One fetch feeds both the list (current-state, no dd) and the
     Interactions & notes card (each product's own real `notes` text). */
  function renderSuppNotes(products) {
    const el = $("nSuppNotes");
    const noted = products.filter((p) => (p.notes || "").trim());
    if (!noted.length) {
      el.innerHTML = '<p class="dim">No interaction or timing notes captured yet — log one on a ' +
        "supplement (supplement_products.notes) and it appears here.</p>";
      return;
    }
    el.replaceChildren(...noted.map((p) => {
      const row = document.createElement("div");
      row.className = "row gap-2";
      row.innerHTML = `<span class="gap-dot"></span>` +
        `<span><b class="ink">${esc(p.name)}</b> — ${esc(p.notes)}</span>`;
      return row;
    }));
  }

  async function loadSupplements() {
    const el = $("nSupps");
    try {
      const d = await fetchJSON("/api/nutrition/supplements");
      if (!d.products.length) {
        el.innerHTML = '<p class="dim">No supplements configured yet — add one to supplement_products ' +
          "(MCP) and today's log will show up here.</p>";
        renderSuppNotes([]);
        return;
      }
      const takenBySupp = new Map(d.today.map((r) => [r.supplement_id, r.taken]));
      el.replaceChildren(...d.products.map((p) => {
        const row = document.createElement("div");
        row.className = "prod";
        const taken = takenBySupp.has(p.supplement_id) ? takenBySupp.get(p.supplement_id) : null;
        const dot = document.createElement("div");
        dot.className = "dotbtn" + (taken ? " done" : "");
        dot.textContent = taken ? "✓" : "";
        const mid = document.createElement("div");
        mid.className = "grow";
        mid.innerHTML = `<div class="strong">${esc(p.name)}</div>` +
          `<div class="muted micro">${esc(p.dose != null ? p.dose + " " + (p.unit || "") : "")}` +
          `${p.schedule ? " · " + esc(p.schedule) : ""}</div>`;
        const chip = document.createElement("span");
        chip.className = "chip muted";
        chip.textContent = taken == null
          ? (takenBySupp.has(p.supplement_id) ? "intake not confirmed today" : "not logged today")
          : taken ? "taken today" : "not taken today";
        row.append(dot, mid, chip);
        return row;
      }));
      renderSuppNotes(d.products);
    } catch (e) { el.innerHTML = `<p class="dim">${esc(e.message)}</p>`; renderSuppNotes([]); }
  }

  /* ------- Supplements: Adherence trend (real, windowed by header dd) -------
     pct = distinct active supplements logged taken that day / count of
     currently-active supplement_products (formula documented server-side in
     nutrition.py). Honest-empty when there are no active products or no log
     rows in the window. */
  async function loadSupplementsAdherence() {
    const r = range();
    $("nAdhWindow").textContent = WINDOW_LABEL[r];
    $("nAdhCap").textContent = `taken vs planned · ${WINDOW_LABEL[r]}`;
    let d;
    try { d = await fetchJSON(`/api/nutrition/supplements/adherence?days=${RANGE_DAYS[r]}`); }
    catch (_) { empty("nSuppAdh", "Adherence unavailable"); return; }
    if (!d.active_products) {
      empty("nSuppAdh", "No active supplements configured yet — adherence appears once you add some.");
      return;
    }
    if (!d.rows.length) {
      empty("nSuppAdh", "No taken/not-taken confirmations in this window.");
      return;
    }
    chart("nSuppAdh").setOption(lineOpt(
      d.rows.map((r2) => r2.date.slice(5)),
      [{ n: "%", d: d.rows.map((r2) => r2.pct), c: pal().accent, area: true }],
      0, 100, { animation: false }));
  }

  window.addEventListener("themechange", loadSupplementsAdherence);
  window.addEventListener("resize", () => Object.values(charts).forEach((c) => c && c.resize()));
  // header range dd -> re-window the Supplements tab's Adherence card.
  window.HermesUI.onRange("nutrition", loadSupplementsAdherence);
  // returning to the Supplements tab: its chart may have rendered hidden.
  window.HermesUI.onTab("nutri", (tab) => {
    if (tab === "supp") Object.values(charts).forEach((c) => c && c.resize());
  });

  loadProfileInfo().then(() => loadTargets()).then(renderGoals);
  loadRecipes(); loadSupplements(); loadSupplementsAdherence();
})();
