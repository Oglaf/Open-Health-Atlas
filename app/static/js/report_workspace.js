/* Pain workspace (fork 2026-10-28: the Hermes-backed panel chat was removed;
   selected map regions are a local highlight only). The vendored
   keyboard-accessible body map is reused unchanged. */
(function () {
  "use strict";
  const root = document.getElementById("rw-root");
  if (!root) return;
  const lens = root.dataset.lens;
  const $ = (id) => document.getElementById(id);
  const selection = new Set();
  const idName = new Map();
  let mfFront = null;
  let mfBack = null;
  let nonMuscle = new Set();

  function mfEmpty(node, message) {
    const paragraph = document.createElement("p");
    paragraph.className = "muted note-txt m0";
    paragraph.textContent = message;
    if (node) node.replaceChildren(paragraph);
  }

  (function buildNames() {
    const library = window.BodyMuscles;
    if (!library) return;
    [].concat(library.FRONT_MUSCLES || [], library.BACK_MUSCLES || [])
      .forEach((region) => {
        if (region && region.id) idName.set(region.id, region.name || region.id);
      });
  })();

  function regionLabel(id) {
    return idName.get(id) || id.replace(/-/g, " ");
  }

  function renderSelection() {
    const ids = Array.from(selection);
    $("rw-selcount").textContent = ids.length
      ? `${ids.length} selected` : "none selected";
    const chips = ids.map((id) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "mchip on";
      button.textContent = regionLabel(id) + " ×";
      button.title = "remove " + regionLabel(id);
      button.addEventListener("click", () => {
        selection.delete(id);
        renderSelection();
      });
      return button;
    });
    const strip = $("rw-asking-chips");
    if (strip) strip.replaceChildren(...chips);
    if (mfFront) mfFront.mark(selection);
    if (mfBack) mfBack.mark(selection);
  }

  function toggleSelect(id) {
    if (nonMuscle.has(id)) return;
    if (selection.has(id)) selection.delete(id);
    else selection.add(id);
    renderSelection();
  }

  async function loadMap() {
    const front = $("mf-front");
    const back = $("mf-back");
    if (!window.MuscleFigure || !window.BodyMuscles) {
      [front, back].forEach((node) => mfEmpty(node, "The body map could not load."));
      return;
    }
    let data;
    try {
      data = (await requestJSON(
        "/api/training/muscle-map?lens=" + encodeURIComponent(lens)
      )).result;
    } catch (error) {
      [front, back].forEach((node) => mfEmpty(node, error.message));
      return;
    }
    nonMuscle = new Set(data.non_muscle || []);
    mfFront = window.MuscleFigure.create(front, Object.assign({ view: "front" }, {
      interactive: true,
      onRegionClick: toggleSelect,
      actionable: () => true,
    }));
    mfBack = window.MuscleFigure.create(back, Object.assign({ view: "back" }, {
      interactive: true,
      onRegionClick: toggleSelect,
      actionable: () => true,
    }));
    mfFront.update(data);
    mfBack.update(data);
    window.MuscleFigure.legend($("mf-legend-f"), data);
    window.MuscleFigure.legend($("mf-legend-b"), data);
    $("rw-maphint").textContent = lens === "pain"
      ? "Colour shows logged pain (0–10 NRS). Use Enter, Space, or click to select one or more regions."
      : "Use Enter, Space, or click to select regions.";
    if (window.HermesPhysio && lens === "pain") window.HermesPhysio.renderPhysioLoop(data);
    const wanted = new URLSearchParams(location.search).get("region");
    if (wanted && idName.has(wanted) && !nonMuscle.has(wanted)) {
      selection.add(wanted);
      renderSelection();
    }
  }
  loadMap();
})();
