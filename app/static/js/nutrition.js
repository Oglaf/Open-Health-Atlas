/* Nutrition pillar: goals and meals (no consumption logging).
   Goals tab: loadTargetCard() renders the targets engine's daily targets
   (app/routes/nutrition.py `/api/nutrition/targets`, fetched once per page
   load and cached) and the read-only phase indicator. Unavailable targets show
   the shared setup disclosure (#nTgtBanner), never a fabricated number; Water
   stays real because the engine always computes its target.
   Meals tab: every recipe (/api/nutrition/recipes) with per-portion nutrients
   from /api/nutrition/recipe/<rid> and each macro as a % of the daily target
   once the engine is ok. The meal-type dd filters on the real
   recipes.meal_type column; the pills only reorder. There is no eat/prep
   control here — the /eat, /prep and /log-food routes stay for agents.
   Supplements tab: product list (current state), Adherence trend (windowed by
   the header dd) and each product's real `notes`. */
(function () {
  if (typeof echarts === "undefined" || !window.HermesCharts) return;
  const { fetchJSON, pal, lineOpt } = window.HermesCharts;
  const RANGE_DAYS = { Day: "1", Week: "7", Month: "30", Year: "365", All: "all" };
  const WINDOW_LABEL = { Day: "past day", Week: "last 7 days", Month: "last 30 days",
    Year: "last year", All: "all time" };
  const range = () => window.HermesUI.range("nutrition") || "Week";
  const esc = (s) => { const d = document.createElement("div"); d.textContent = s == null ? "" : s; return d.innerHTML; };
  const charts = {};

  function chart(id) {
    const el = document.getElementById(id);
    if (charts[id]) charts[id].dispose();
    charts[id] = echarts.init(el);
    return charts[id];
  }
  function empty(id, label) {
    chart(id).setOption({ graphic: { type: "text", left: "center", top: "middle",
      style: { text: label, fill: pal().muted, fontSize: 12 } } });
  }

  const titleCase = (s) => s.charAt(0).toUpperCase() + s.slice(1);

  // One fetch of /api/nutrition/targets per page load, shared by the goals
  // card and the meal cards. A bridge error resolves to a synthetic
  // {status:"error"} so every consumer's `status !== "ok"` check renders the
  // honest state instead of throwing.
  let targetsPromise = null;
  function loadTargets() {
    if (!targetsPromise) {
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
    const banner = document.getElementById("nTgtBanner");
    banner.hidden = targets.status === "ok";
    document.getElementById("nTgtReasonLabel").textContent = targets.status === "error" ? "Request error" : "Reason";
    document.getElementById("nTgtReason").textContent = targets.reason || "The engine did not return a reason.";
    document.getElementById("nTgtReload").hidden = targets.status !== "error";
  }

  /* ------- Goals: daily targets + read-only phase indicator ------- */
  function setTarget(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }
  async function loadTargetCard() {
    const t = await loadTargets();
    const ok = t.status === "ok";
    if (ok) {
      const kt = t.targets.kcal, pt = t.targets.protein_g, ct = t.targets.carbs_g, ft = t.targets.fat_g;
      setTarget("nTgtCal", `${Math.round(kt.target).toLocaleString()} kcal · band ${Math.round(kt.band_low).toLocaleString()}–${Math.round(kt.band_high).toLocaleString()}`);
      setTarget("nTgtProtein", `${Math.round(pt.target)} g`);
      setTarget("nTgtCarbs", `${Math.round(ct.target)} g`);
      setTarget("nTgtFat", `${Math.round(ft.target)} g`);
    } else {
      ["nTgtCal", "nTgtProtein", "nTgtCarbs", "nTgtFat"].forEach((id) => setTarget(id, "—"));
    }

    const microTargets = {};
    if (ok) (t.targets.micros || []).forEach((m) => { microTargets[m.nutrient] = m; });
    [["nTgtFibre", "fibre"], ["nTgtVitD", "vitamin_d"], ["nTgtMg", "magnesium"],
     ["nTgtFe", "iron"], ["nTgtZn", "zinc"], ["nTgtOmega", "omega3_epa_dha"]].forEach(([id, key]) => {
      const mt = microTargets[key];
      setTarget(id, !ok ? "—" : mt && mt.target != null ? `${mt.target} ${mt.unit || ""}`.trim() : "target not configured");
    });

    // Water: engine target, real even when the rest of the engine is
    // insufficient_data (water_ml is the one target it always computes).
    const waterTarget = t.targets && t.targets.water_ml ? t.targets.water_ml.target : null;
    setTarget("nTgtWater", waterTarget != null ? `${waterTarget.toLocaleString()} ml` : "—");

    // PHASE indicator (read-only: the buttons are permanently non-interactive).
    const seg = { cut: document.getElementById("nPhaseCut"),
                  maintain: document.getElementById("nPhaseMaintain"),
                  bulk: document.getElementById("nPhaseBulk") };
    Object.entries(seg).forEach(([key, btn]) => {
      if (!btn) return;
      const active = ok && t.phase.phase === key;
      btn.classList.toggle("on", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });
    const captionEl = document.getElementById("nPhaseCaption");
    if (captionEl) {
      captionEl.textContent = !ok
        ? "Phase unavailable until targets can be calculated."
        : t.phase.set
          ? `Current phase: ${titleCase(t.phase.phase)} (since ${t.phase.started}).`
          : "No phase set — treating as Maintain.";
    }
  }

  /* ------- Meals: recipe cards with per-portion nutrients ------- */
  let recipeCatalog = [];            // every recipe definition
  let targetsSnapshot = null;
  const portionById = new Map();     // recipe_id -> per-portion kcal/protein/carbs/fat
  let activePill = "all";
  // dd label -> the real recipes.meal_type value recipe-tag writes.
  const MEAL_TAG = { Breakfast: "breakfast", Lunch: "lunch", Dinner: "dinner", Snacks: "snack" };
  const MACRO_ROWS = [["kcal", "Calories", "kcal", "kcal"], ["protein", "Protein", "g", "protein_g"],
    ["carbs", "Carbs", "g", "carbs_g"], ["fat", "Fat", "g", "fat_g"]];

  function macroOf(detail, name) {
    const m = (detail.macros || []).find((x) => x.name === name);
    return m ? m.amount : null;
  }

  function renderMenu() {
    const list = document.getElementById("nMenu");
    if (!recipeCatalog.length) {
      list.innerHTML = '<p class="dim">No meals yet. Import one from Cronometer with the toolkit (or your MCP client).</p>';
      return;
    }
    // Untagged recipes (meal_type null) only appear under "All meals" — never
    // guessed from names.
    const meal = window.HermesUI.range("mealtype") || "All meals";
    let items = meal === "All meals" ? recipeCatalog
      : recipeCatalog.filter((r) => r.meal_type === MEAL_TAG[meal]);
    if (activePill === "protein") {
      const protein = (r) => { const p = portionById.get(r.recipe_id); return p && p.protein != null ? p.protein : -1; };
      items = [...items].sort((a, b) => protein(b) - protein(a));
    }
    if (!items.length) {
      list.innerHTML = `<p class="dim">No meals tagged ${esc(meal.toLowerCase())} yet — ` +
        "tag one with <code>recipe-tag</code> (MCP).</p>";
      return;
    }
    list.replaceChildren(...items.map(mealCard));
  }

  function mealCard(r) {
    const p = portionById.get(r.recipe_id);
    const t = targetsSnapshot;
    const ok = t && t.status === "ok";
    const card = document.createElement("div");
    card.className = "card";
    let body = `<div class="row between wrap"><span class="strong">${esc(r.name)}</span>` +
      `<a class="muted micro" href="/nutrition/recipe/${encodeURIComponent(r.recipe_id)}">details →</a></div>` +
      `<p class="muted micro m0 mt-1">${r.grams_per_portion ? Math.round(r.grams_per_portion) + " g per portion" : "portion size not set"}</p>`;
    if (!p || p.kcal == null) {
      body += '<p class="muted micro m0 mt-2">Nutrients pending — set the batch weight.</p>';
    } else {
      body += '<div class="goals-list mt-2">' + MACRO_ROWS.map(([k, label, unit, tkey]) => {
        const v = p[k];
        if (v == null) return "";
        const pct = ok && t.targets[tkey] ? ` · ${Math.round(v / t.targets[tkey].target * 100)}% of daily goal` : "";
        return `<div class="goal-row"><span class="goal-label">${label}</span>` +
          `<span class="goal-val">${Math.round(v)} ${unit}${pct}</span></div>`;
      }).join("") + "</div>";
    }
    card.innerHTML = body;
    return card;
  }

  async function loadRecipes() {
    try {
      const d = await fetchJSON("/api/nutrition/recipes");
      recipeCatalog = d.recipes;
      targetsSnapshot = await loadTargets();
      renderMenu();
      await Promise.all(recipeCatalog.map(async (r) => {
        try {
          const detail = await fetchJSON("/api/nutrition/recipe/" + encodeURIComponent(r.recipe_id));
          portionById.set(r.recipe_id, { kcal: detail.kcal_per_portion, protein: macroOf(detail, "Protein"),
            carbs: macroOf(detail, "Carbs"), fat: macroOf(detail, "Fat") });
        } catch (_) { /* card shows the pending line */ }
      }));
      renderMenu();
    } catch (e) {
      document.getElementById("nMenu").innerHTML = `<p class="dim">${esc(e.message)}</p>`;
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
    const el = document.getElementById("nSuppNotes");
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
    const el = document.getElementById("nSupps");
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
    document.getElementById("nAdhWindow").textContent = WINDOW_LABEL[r];
    document.getElementById("nAdhCap").textContent = `taken vs planned · ${WINDOW_LABEL[r]}`;
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

  loadTargetCard(); loadRecipes(); loadSupplements(); loadSupplementsAdherence();
})();
