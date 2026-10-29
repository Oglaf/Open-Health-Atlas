/* Daily log (fork owner decision 2026-10-29, docs/PRODUCT_AUTHORITY.md
   "manual daily capture"): day rating, evening check-in, water, supplements
   and pain. Strict CSP (script-src 'self', no inline JS/CSS) — everything
   lives here. DOM is built with textContent/createElement only; nothing from
   the server is ever assigned via innerHTML. */
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

  function isoDaysAgo(n) {
    const d = new Date();
    d.setDate(d.getDate() - n);
    return d.toISOString().slice(0, 10);
  }

  function labelFor(key) {
    return key.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }

  // ----------------------------------------------------------------- day rating
  async function loadDay() {
    const dateInput = $("dl-day-date");
    if (!dateInput.value) dateInput.value = isoDaysAgo(0);
    dateInput.min = isoDaysAgo(14);
    dateInput.max = isoDaysAgo(0);
    const status = $("dl-day-current");
    try {
      const d = await req("/api/log/day?date=" + encodeURIComponent(dateInput.value));
      status.textContent = d.day_rating
        ? `Already rated ${["", "red", "yellow", "green"][d.day_rating]} for ${d.date}.`
        : `No rating yet for ${d.date}.`;
    } catch (e) {
      status.textContent = "Couldn't load: " + e.message;
    }
  }

  async function saveDayRating(value) {
    const status = $("dl-day-status");
    status.textContent = "Saving…";
    try {
      await post("/api/log/day-rating", { value, date: $("dl-day-date").value });
      status.textContent = "Saved.";
      toast("Day rating saved", "good");
      await loadDay();
    } catch (e) {
      status.textContent = "Couldn't save: " + e.message;
    }
  }

  $("dl-day-date").addEventListener("change", loadDay);
  for (const value of ["green", "yellow", "red"]) {
    $(`dl-day-${value}`).addEventListener("click", () => saveDayRating(value));
  }

  // ------------------------------------------------------------- evening check-in
  async function loadEvening(commitmentId) {
    const status = $("dl-word-status");
    try {
      const d = await req("/api/log/evening?commitment_id=" + (commitmentId || 0));
      const hasCommitments = d.commitments.length > 0;
      $("dl-commitment-empty").hidden = hasCommitments;
      $("dl-word-form").hidden = !hasCommitments;
      if (hasCommitments) {
        const select = $("dl-commit-select");
        const keepValue = select.value;
        select.replaceChildren(...d.commitments.map((c) => {
          const opt = document.createElement("option");
          opt.value = String(c.id);
          opt.textContent = c.name;
          return opt;
        }));
        select.value = (keepValue && d.commitments.some((c) => String(c.id) === keepValue))
          ? keepValue : String(d.commitments[0].id);
        $("dl-word-current").textContent = d.word
          ? `Already logged today: ${d.word.status}${d.word.why ? " — " + d.word.why : ""}.`
          : "Not logged yet today.";
      }
      const checkins = d.checkins || {};
      if (checkins.energy != null) $("dl-checkin-energy").value = checkins.energy;
      if (checkins.focus != null) $("dl-checkin-focus").value = checkins.focus;
      if (checkins.mood != null) $("dl-checkin-mood").value = checkins.mood;
    } catch (e) {
      status.textContent = "Couldn't load: " + e.message;
    }
  }

  $("dl-commit-select").addEventListener("change", (e) => loadEvening(Number(e.target.value)));

  $("dl-commit-save").addEventListener("click", async () => {
    const status = $("dl-commit-status");
    const name = $("dl-commit-name").value.trim();
    if (!name) { status.textContent = "Name is required."; return; }
    status.textContent = "Saving…";
    try {
      await post("/api/log/commitment", {
        name,
        identity: $("dl-commit-identity").value.trim(),
        trigger: $("dl-commit-trigger").value.trim(),
        floor: $("dl-commit-floor").value.trim(),
      });
      status.textContent = "Saved.";
      toast("Commitment created", "good");
      $("dl-commit-name").value = ""; $("dl-commit-identity").value = "";
      $("dl-commit-trigger").value = ""; $("dl-commit-floor").value = "";
      await loadEvening(0);
    } catch (e) {
      status.textContent = "Couldn't save: " + e.message;
    }
  });

  async function saveWord(status) {
    const el = $("dl-word-status");
    el.textContent = "Saving…";
    try {
      await post("/api/log/word", {
        status, commitment_id: Number($("dl-commit-select").value),
        why: $("dl-word-why").value.trim(),
      });
      el.textContent = "Saved.";
      toast("Evening check-in saved", "good");
      $("dl-word-why").value = "";
      await loadEvening(Number($("dl-commit-select").value));
    } catch (e) {
      el.textContent = "Couldn't save: " + e.message;
    }
  }

  for (const value of ["kept", "partly", "broke"]) {
    $(`dl-word-${value}`).addEventListener("click", () => saveWord(value));
  }

  $("dl-checkin-save").addEventListener("click", async () => {
    const status = $("dl-checkin-status");
    const readings = [
      ["energy", $("dl-checkin-energy").value],
      ["focus", $("dl-checkin-focus").value],
      ["mood", $("dl-checkin-mood").value],
    ].filter(([, v]) => v !== "");
    if (!readings.length) { status.textContent = "Nothing to save."; return; }
    status.textContent = "Saving…";
    try {
      for (const [kind, value] of readings) {
        await post("/api/log/checkin", { kind, value: Number(value) });
      }
      status.textContent = "Saved.";
      toast("Spot readings saved", "good");
    } catch (e) {
      status.textContent = "Couldn't save: " + e.message;
    }
  });

  // ------------------------------------------------------------------------ water
  function renderWaterTotal(totalMl, targetMl) {
    $("dl-water-total").textContent = targetMl
      ? `Today: ${totalMl} ml of ${targetMl} ml target.`
      : `Today: ${totalMl} ml.`;
  }

  async function loadWater() {
    try {
      const d = await req("/api/dash/today");
      renderWaterTotal(d.water_ml || 0, null);
    } catch (e) {
      $("dl-water-total").textContent = "Couldn't load: " + e.message;
    }
  }

  async function addWater(ml) {
    const status = $("dl-water-status");
    if (!Number.isInteger(ml) || ml < 1 || ml > 3000) {
      status.textContent = "Enter a whole number of ml, 1-3000."; return;
    }
    status.textContent = "Saving…";
    try {
      const d = await post("/api/log/water", { water_ml: ml });
      status.textContent = "Saved.";
      toast(`+${ml} ml logged`, "good");
      const result = d.result || {};
      renderWaterTotal(result.total_ml != null ? result.total_ml : null, result.target_ml);
      if (result.total_ml == null) await loadWater();
    } catch (e) {
      status.textContent = "Couldn't save: " + e.message;
    }
  }

  $("dl-water-250").addEventListener("click", () => addWater(250));
  $("dl-water-500").addEventListener("click", () => addWater(500));
  $("dl-water-custom-add").addEventListener("click", () => {
    addWater(Number($("dl-water-custom").value));
  });

  // ----------------------------------------------------------------- supplements
  async function loadSupplements() {
    const el = $("dl-supplements");
    try {
      const d = await req("/api/nutrition/supplements");
      if (!d.products.length) {
        el.replaceChildren();
        const p = document.createElement("p");
        p.className = "muted note-txt";
        p.textContent = "No active supplements yet — add one via MCP "
          + "(supplement_products); it will show up here to log.";
        el.appendChild(p);
        return;
      }
      const takenBySupp = new Map(d.today.map((r) => [r.supplement_id, r.taken]));
      el.replaceChildren(...d.products.map((p) => {
        const row = document.createElement("div");
        row.className = "field-row";
        const info = document.createElement("div");
        info.className = "grow";
        const name = document.createElement("div");
        name.className = "strong";
        name.textContent = p.name;
        info.appendChild(name);
        if (p.dose != null) {
          const dose = document.createElement("div");
          dose.className = "muted micro";
          dose.textContent = `${p.dose} ${p.unit || ""}${p.schedule ? " · " + p.schedule : ""}`;
          info.appendChild(dose);
        }
        row.appendChild(info);
        const taken = takenBySupp.has(p.supplement_id) ? takenBySupp.get(p.supplement_id) : null;
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn quiet";
        btn.textContent = taken ? "Taken today ✓" : "Mark taken";
        btn.addEventListener("click", async () => {
          const status = $("dl-supplement-status");
          status.textContent = "Saving…";
          try {
            await post("/api/log/supplement", { supplement: p.name, taken: taken ? 0 : 1 });
            status.textContent = "Saved.";
            toast(`${p.name} logged`, "good");
            await loadSupplements();
          } catch (e) {
            status.textContent = "Couldn't save: " + e.message;
          }
        });
        row.appendChild(btn);
        return row;
      }));
    } catch (e) {
      el.replaceChildren();
      const p = document.createElement("p");
      p.className = "muted note-txt";
      p.textContent = "Couldn't load: " + e.message;
      el.appendChild(p);
    }
  }

  // ----------------------------------------------------------------------- pain
  let painCatalog = null;

  function fillSelect(id, values, { placeholder } = {}) {
    const select = $(id);
    select.replaceChildren();
    if (placeholder) {
      const opt = document.createElement("option");
      opt.value = ""; opt.textContent = placeholder;
      select.appendChild(opt);
    }
    for (const value of values) {
      const opt = document.createElement("option");
      opt.value = value;
      opt.textContent = labelFor(value);
      select.appendChild(opt);
    }
  }

  async function loadPainCatalog() {
    painCatalog = await req("/api/log/pain-catalog");
    fillSelect("dl-pain-region", painCatalog.regions, { placeholder: "Select a region" });
    fillSelect("dl-pain-side", painCatalog.sides);
    fillSelect("dl-pain-quality", painCatalog.qualities, { placeholder: "Not set" });
    fillSelect("dl-pain-pattern", painCatalog.patterns, { placeholder: "Not set" });
    $("dl-pain-intensity").min = painCatalog.intensity_min;
    $("dl-pain-intensity").max = painCatalog.intensity_max;

    const flagsEl = $("dl-pain-flags");
    flagsEl.replaceChildren(...painCatalog.red_flags.map((flag) => {
      const label = document.createElement("label");
      label.className = "field inline";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = flag;
      input.addEventListener("change", () => {
        const any = [...flagsEl.querySelectorAll("input:checked")].length > 0;
        $("dl-pain-flag-note").hidden = !any;
      });
      const span = document.createElement("span");
      span.textContent = labelFor(flag);
      label.append(input, span);
      return label;
    }));
  }

  function renderPainRecent(items) {
    const body = $("dl-pain-recent-body");
    body.replaceChildren();
    for (const row of items) {
      const tr = document.createElement("tr");
      for (const value of [row.date, labelFor(row.region), row.side, row.intensity, row.quality || "—"]) {
        const td = document.createElement("td");
        td.textContent = value;
        tr.appendChild(td);
      }
      body.appendChild(tr);
    }
  }

  async function loadPainRecent() {
    try {
      const d = await req("/api/log/pain-recent");
      renderPainRecent(d.items);
    } catch (e) {
      $("dl-pain-status").textContent = "Couldn't load recent reports: " + e.message;
    }
  }

  $("dl-pain-save").addEventListener("click", async () => {
    const status = $("dl-pain-status");
    const region = $("dl-pain-region").value;
    if (!region) { status.textContent = "Select a region."; return; }
    const intensityRaw = $("dl-pain-intensity").value;
    if (intensityRaw === "") { status.textContent = "Enter an intensity."; return; }
    const body = { region, intensity: Number(intensityRaw) };
    if ($("dl-pain-side").value) body.side = $("dl-pain-side").value;
    if ($("dl-pain-quality").value) body.quality = $("dl-pain-quality").value;
    if ($("dl-pain-pattern").value) body.pattern = $("dl-pain-pattern").value;
    const flags = [...$("dl-pain-flags").querySelectorAll("input:checked")].map((i) => i.value);
    if (flags.length) body.flags = flags;
    const note = $("dl-pain-note").value.trim();
    if (note) body.note = note;
    status.textContent = "Saving…";
    try {
      await post("/api/log/pain", body);
      status.textContent = "Saved.";
      toast("Pain logged", "good");
      $("dl-pain-note").value = "";
      await loadPainRecent();
    } catch (e) {
      status.textContent = "Couldn't save: " + e.message;
    }
  });

  loadDay();
  loadEvening(0);
  loadWater();
  loadSupplements();
  loadPainCatalog().then(loadPainRecent).catch((e) => {
    $("dl-pain-status").textContent = "Couldn't load pain catalog: " + e.message;
  });
})();
