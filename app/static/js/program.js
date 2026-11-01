/* Program page: today's session, Mon-Sun plan, per-routine exercises, and an
   opt-in editor. Reads /api/plans/program and /api/training/today and renders
   what they return (no new math). Edits post to the validated /api/plans/*
   endpoints; after each one the program is re-read from the server so the
   page never shows an edit the server did not accept. */
(function () {
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  // Cardio is stored as sets x minutes; the API only has a "reps" field for it.
  const CARDIO = /\b(treadmill|bike|elliptical|jog|run|walk|cycling|cardio|stairs?)\b/i;
  const $ = (id) => document.getElementById(id);

  let program = null;     // { routines, schedule, routine_names }
  let editing = false;
  let busy = false;

  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === false || v == null) continue;
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) {
      if (kid == null || kid === false) continue;
      n.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    }
    return n;
  }
  function toast(msg, kind) {
    const wrap = $("toast-wrap");
    const t = el("div", { class: "toast " + (kind || ""), text: msg });
    wrap.appendChild(t);
    requestAnimationFrame(() => t.classList.add("show"));
    setTimeout(() => { t.classList.remove("show"); setTimeout(() => t.remove(), 250); }, 3000);
  }
  async function getJSON(url) {
    const r = await fetch(url, { credentials: "same-origin" });
    if (r.status === 401) { location.assign("/login"); throw new Error("unauthenticated"); }
    if (!r.ok) { let d = ""; try { d = (await r.json()).error || ""; } catch (_) {} throw new Error(d || "HTTP " + r.status); }
    return r.json();
  }
  async function postJSON(url, body) {
    const r = await fetch(url, { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf }, body: JSON.stringify(body) });
    if (r.status === 401) { location.assign("/login"); throw new Error("session expired"); }
    const d = await r.json().catch(() => ({}));
    if (!r.ok || d.ok === false) throw new Error(d.error || "HTTP " + r.status);
    return d;
  }

  const isCardio = (title) => CARDIO.test(title || "");
  const num = (v) => (v == null || v === "" ? null : Number(v));
  const fmtKg = (w) => (w == null ? "" : "@" + (Math.round(w * 10) / 10) + " kg");

  // sets x reps@kg (strength) or sets x minutes (cardio); weight omitted when none.
  function target(ex) {
    const s = ex.target_sets, r = ex.target_reps;
    if (s == null && r == null) return "no target";
    const unit = isCardio(ex.exercise_title) ? " min" : "";
    return (s == null ? "–" : s) + "×" + (r == null ? "–" : r) + unit + fmtKg(ex.target_weight_kg);
  }
  const localWeekday = () => DAYS[(new Date().getDay() + 6) % 7];

  // ---------------------------------------------------------------- today
  function renderToday(t) {
    const card = $("prog-today"), body = $("prog-today-body");
    body.replaceChildren();
    $("prog-today-name").textContent = t.routine || "Rest";
    if (!t.exercises || !t.exercises.length) {
      body.append(el("p", { class: "muted note-txt m0", text: t.routine && t.routine !== "Rest"
        ? "No exercises in this routine yet." : "Rest day." }));
    } else {
      for (const ex of t.exercises) {
        const last = ex.last_session;
        const lastTxt = last && last.sets && last.sets.length
          ? "Last " + last.date + ": " + last.sets.map((s) =>
              (s.weight_kg == null ? "" : (Math.round(s.weight_kg * 10) / 10) + "×") + (s.reps == null ? "–" : s.reps)).join(", ")
          : "No previous session logged";
        body.append(el("div", { class: "prog-ex" },
          el("div", { class: "prog-ex-main" },
            el("strong", { text: ex.exercise_title }),
            el("span", { class: "chip", text: target(ex) })),
          el("div", { class: "muted micro", text: lastTxt })));
      }
    }
    card.hidden = false;
  }

  // ----------------------------------------------------------------- week
  function renderWeek() {
    const grid = $("prog-week-grid");
    grid.replaceChildren();
    const today = localWeekday();
    for (const d of DAYS) {
      const routine = program.schedule[d] || "Rest";
      const cell = el("div", { class: "prog-day" + (d === today ? " today" : ""), "aria-current": d === today ? "date" : false },
        el("div", { class: "label", text: d }),
        el("div", { class: "prog-day-name" + (routine === "Rest" ? " muted" : ""), text: routine }));
      if (editing) {
        const sel = el("select", { class: "select", "aria-label": "Routine for " + d });
        for (const name of ["Rest", ...program.routine_names]) {
          const o = el("option", { value: name, text: name });
          if (name === routine) o.selected = true;
          sel.append(o);
        }
        sel.addEventListener("change", () => act(() =>
          postJSON("/api/plans/schedule-set", { weekday: d, routine: sel.value }), "Saved " + d));
        cell.append(sel);
      }
      grid.append(cell);
    }
    $("prog-week").hidden = false;
  }

  // ------------------------------------------------------------- routines
  function exerciseRow(routine, ex) {
    const cardio = isCardio(ex.exercise_title);
    if (!editing) {
      return el("div", { class: "prog-ex" },
        el("div", { class: "prog-ex-main" },
          el("strong", { text: ex.exercise_title }),
          el("span", { class: "chip", text: target(ex) })));
    }
    const sets = el("input", { type: "number", min: 1, max: 20, step: 1, inputmode: "numeric", value: ex.target_sets ?? "", "aria-label": ex.exercise_title + " sets" });
    const reps = el("input", { type: "number", min: 1, max: 100, step: 1, inputmode: "numeric", value: ex.target_reps ?? "", "aria-label": ex.exercise_title + (cardio ? " minutes" : " reps") });
    const wt = el("input", { type: "number", min: 0, step: "any", inputmode: "decimal", value: ex.target_weight_kg ?? "", "aria-label": ex.exercise_title + " weight kg" });
    return el("div", { class: "prog-ex editing" },
      el("strong", { text: ex.exercise_title }),
      el("div", { class: "field-row" },
        el("label", { class: "field" }, "Sets", sets),
        el("label", { class: "field" }, cardio ? "Minutes" : "Reps", reps),
        cardio ? null : el("label", { class: "field" }, "Weight (kg)", wt)),
      el("div", { class: "row gap-2" },
        el("button", { class: "save-btn", type: "button", onclick: () => saveExercise(routine, ex.exercise_title, sets, reps, cardio ? null : wt) }, "Save"),
        el("button", { class: "log-more-btn", type: "button", onclick: () => removeExercise(routine, ex.exercise_title) }, "Remove")));
  }

  function addForm(routine, nextOrder) {
    const name = el("input", { type: "text", maxlength: 120, autocomplete: "off", "aria-label": "New exercise in " + routine });
    const sets = el("input", { type: "number", min: 1, max: 20, step: 1, inputmode: "numeric", "aria-label": "New exercise sets" });
    const reps = el("input", { type: "number", min: 1, max: 100, step: 1, inputmode: "numeric", "aria-label": "New exercise reps or minutes" });
    const wt = el("input", { type: "number", min: 0, step: "any", inputmode: "decimal", "aria-label": "New exercise weight kg" });
    return el("div", { class: "prog-ex editing hair-top" },
      el("strong", { text: "Add exercise" }),
      el("div", { class: "field-row" },
        el("label", { class: "field" }, "Name", name),
        el("label", { class: "field" }, "Sets", sets),
        el("label", { class: "field" }, "Reps / min", reps),
        el("label", { class: "field" }, "Weight (kg)", wt)),
      el("button", { class: "save-btn", type: "button", onclick: () => {
        const title = name.value.trim();
        if (!title) { toast("Exercise name is required", "bad"); name.focus(); return; }
        const body = buildBody(routine, title, sets, reps, wt);
        if (!body) return;
        body.order = nextOrder;
        act(() => postJSON("/api/plans/routine-set", body), "Added " + title);
      } }, "Add"));
  }

  function renderRoutines() {
    const host = $("prog-routines");
    host.replaceChildren();
    const scheduledOn = {};
    for (const d of DAYS) { const r = program.schedule[d]; if (r) (scheduledOn[r] = scheduledOn[r] || []).push(d); }
    if (!program.routine_names.length) {
      host.append(el("div", { class: "card" }, el("p", { class: "muted note-txt m0", text: "No routines yet." +
        (editing ? "" : " Press Edit program to create one.") })));
    }
    for (const name of program.routine_names) {
      const exs = program.routines[name] || [];
      const days = scheduledOn[name];
      host.append(el("div", { class: "card" },
        el("div", { class: "row between wrap" },
          el("h3", { class: "card-title", text: name }),
          el("span", { class: "muted micro", text: days ? days.join(" · ") : "not scheduled" })),
        el("div", { class: "vstack gap-2 mt-2" },
          exs.map((ex) => exerciseRow(name, ex)),
          editing ? addForm(name, exs.reduce((m, e) => Math.max(m, e.ex_order || 0), 0) + 1) : null)));
    }
    host.hidden = false;
    $("prog-new").hidden = !editing;
  }

  // -------------------------------------------------------------- actions
  // Returns a routine-set body, or null (after a toast) when a field is invalid.
  function buildBody(routine, exercise, sets, reps, wt) {
    const body = { routine, exercise };
    for (const [key, input, lo, hi] of [["sets", sets, 1, 20], ["reps", reps, 1, 100]]) {
      const v = num(input.value);
      if (v == null) continue;
      if (!Number.isInteger(v) || v < lo || v > hi) {
        toast(key + " must be a whole number " + lo + "–" + hi, "bad"); input.focus(); return null;
      }
      body[key] = v;
    }
    if (wt) {
      const w = num(wt.value);
      if (w != null) {
        if (!(w >= 0)) { toast("weight must be 0 or more", "bad"); wt.focus(); return null; }
        body.weight = w;
      }
    }
    return body;
  }
  function saveExercise(routine, exercise, sets, reps, wt) {
    const body = buildBody(routine, exercise, sets, reps, wt);
    if (body) act(() => postJSON("/api/plans/routine-set", body), "Saved " + exercise);
  }
  function removeExercise(routine, exercise) {
    if (!confirm("Remove " + exercise + " from " + routine + "?")) return;
    act(() => postJSON("/api/plans/routine-remove", { routine, exercise }), "Removed " + exercise);
  }
  async function createRoutine() {
    const routine = $("pn-routine").value.trim(), exercise = $("pn-exercise").value.trim();
    if (!routine || !exercise) { toast("Routine and first exercise names are required", "bad"); return; }
    const body = buildBody(routine, exercise, $("pn-sets"), $("pn-reps"), $("pn-weight"));
    if (!body) return;
    await act(() => postJSON("/api/plans/routine-set", body), "Created " + routine);
    for (const id of ["pn-routine", "pn-exercise", "pn-sets", "pn-reps", "pn-weight"]) $(id).value = "";
  }
  // Run a write, then re-read the program from the server and re-render.
  async function act(write, okMsg) {
    if (busy) return;
    busy = true;
    try {
      await write();
      toast(okMsg, "good");
    } catch (e) {
      toast(e.message || "Save failed", "bad");
    }
    try { await loadProgram(); } catch (e) { toast("Could not reload the program", "bad"); }
    busy = false;
  }

  // ----------------------------------------------------------------- load
  async function loadProgram() {
    program = await getJSON("/api/plans/program");
    renderWeek();
    renderRoutines();
    // Keep today's card in step with edits; a failure here must not hide the plan.
    try { renderToday(await getJSON("/api/training/today")); } catch (_) {}
  }
  function setEditing(on) {
    editing = on;
    const b = $("prog-edit-toggle");
    b.setAttribute("aria-pressed", String(on));
    b.textContent = on ? "Done editing" : "Edit program";
    renderWeek();
    renderRoutines();
  }

  $("prog-edit-toggle").addEventListener("click", () => setEditing(!editing));
  $("pn-create").addEventListener("click", createRoutine);
  loadProgram().then(() => {
    $("prog-status").hidden = true;
    $("prog-edit-toggle").disabled = false;
  }).catch((e) => {
    $("prog-status").textContent = e.message === "unauthenticated" ? "" : "Program unavailable: " + e.message;
  });
})();
