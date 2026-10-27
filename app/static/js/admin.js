/* Admin panel (docs/MULTI_USER.md "Admin UI"): family-member management.
   Strict CSP (script-src 'self', no inline JS/CSS) — everything lives here.
   DOM is built with textContent/createElement only; nothing from the server
   is ever assigned via innerHTML. */
(function () {
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const toastWrap = document.getElementById("toast-wrap");
  const $ = (id) => document.getElementById(id);

  // Non-JSON profile keys that hold free text rather than a number. Anything
  // else listed by the server's `keys`/`json_keys` (deploy/bridge_commands.py
  // PROFILE_KEYS/PROFILE_JSON_KEYS is the source of truth) is treated as
  // JSON or numeric respectively — this list only disambiguates the rest.
  const PROFILE_STRING_KEYS = new Set([
    "HERMES_PRIMARY_MEDICATION", "HERMES_MEDICATION_ALIASES",
  ]);

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

  function fmtDate(epochSeconds) {
    if (!epochSeconds) return "—";
    try { return new Date(epochSeconds * 1000).toLocaleDateString(); } catch { return "—"; }
  }

  function labelFor(key) {
    return key.replace(/^HERMES_/, "").replace(/_/g, " ")
      .toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
  }

  let allUsers = [];

  function activeAdminCount() {
    return allUsers.filter((u) => u.role === "admin" && u.status === "active").length;
  }

  function cell(tag, text) {
    const el = document.createElement(tag);
    if (text != null) el.textContent = text;
    return el;
  }

  function buildRoleSelect(user) {
    const select = document.createElement("select");
    select.className = "select";
    select.setAttribute("aria-label", "Role for " + user.display_name);
    for (const value of ["member", "admin"]) {
      const opt = document.createElement("option");
      opt.value = value;
      opt.textContent = value === "admin" ? "Admin" : "Member";
      opt.selected = user.role === value;
      select.appendChild(opt);
    }
    const lastAdmin = user.role === "admin" && user.status === "active" && activeAdminCount() <= 1;
    if (user.is_owner) {
      select.disabled = true;
      select.title = "The bootstrap owner's role is fixed.";
    } else if (user.is_self) {
      select.disabled = true;
      select.title = "You cannot change your own role here.";
    } else if (lastAdmin) {
      select.querySelector('option[value="member"]').disabled = true;
      select.title = "This is the last active admin — promote another admin first.";
    }
    select.addEventListener("change", async () => {
      const role = select.value;
      select.disabled = true;
      try {
        await post(`/api/admin/users/${encodeURIComponent(user.id)}/role`, { role });
        toast(`${user.display_name} is now ${role}`, "good");
        await loadUsers();
      } catch (e) {
        toast("Couldn't change role: " + e.message, "bad");
        select.value = user.role;
        select.disabled = false;
      }
    });
    return select;
  }

  function buildActions(user) {
    const wrap = document.createElement("div");
    wrap.className = "row gap-2";

    const enrollBtn = document.createElement("button");
    enrollBtn.type = "button";
    enrollBtn.className = "btn quiet";
    enrollBtn.textContent = "Enroll link";
    enrollBtn.disabled = user.status !== "active";
    if (user.status !== "active") enrollBtn.title = "Cannot enroll a disabled account.";
    enrollBtn.addEventListener("click", async () => {
      enrollBtn.disabled = true;
      try {
        const d = await post(`/api/admin/users/${encodeURIComponent(user.id)}/enroll-link`, {});
        showEnrollLink(user, d.url, d.expires_in_seconds);
      } catch (e) {
        toast("Couldn't create enrollment link: " + e.message, "bad");
      } finally {
        enrollBtn.disabled = user.status !== "active";
      }
    });
    wrap.appendChild(enrollBtn);

    const toggleBtn = document.createElement("button");
    toggleBtn.type = "button";
    toggleBtn.className = "btn quiet";
    const disabling = user.status === "active";
    toggleBtn.textContent = disabling ? "Disable" : "Enable";
    const lastAdmin = user.role === "admin" && user.status === "active" && activeAdminCount() <= 1;
    if (user.is_owner) {
      toggleBtn.disabled = true;
      toggleBtn.title = "The bootstrap owner cannot be disabled.";
    } else if (disabling && user.is_self) {
      toggleBtn.disabled = true;
      toggleBtn.title = "You cannot disable your own account.";
    } else if (disabling && lastAdmin) {
      toggleBtn.disabled = true;
      toggleBtn.title = "This is the last active admin.";
    }
    toggleBtn.addEventListener("click", async () => {
      const nextStatus = disabling ? "disabled" : "active";
      if (disabling && !confirm(`Disable ${user.display_name}? This immediately signs them out everywhere.`)) return;
      toggleBtn.disabled = true;
      try {
        await post(`/api/admin/users/${encodeURIComponent(user.id)}/status`, { status: nextStatus });
        toast(`${user.display_name} is now ${nextStatus}`, "good");
        await loadUsers();
      } catch (e) {
        toast("Couldn't change status: " + e.message, "bad");
        toggleBtn.disabled = false;
      }
    });
    wrap.appendChild(toggleBtn);

    return wrap;
  }

  function buildRow(user) {
    const tr = document.createElement("tr");
    tr.appendChild(cell("td", user.display_name));
    tr.appendChild(cell("td", user.id));

    const roleTd = document.createElement("td");
    roleTd.appendChild(buildRoleSelect(user));
    tr.appendChild(roleTd);

    const statusTd = document.createElement("td");
    const chip = document.createElement("span");
    chip.className = "chip" + (user.status === "active" ? "" : " hist");
    chip.textContent = user.status;
    statusTd.appendChild(chip);
    tr.appendChild(statusTd);

    tr.appendChild(cell("td", fmtDate(user.created_at)));
    tr.appendChild(cell("td", user.is_owner ? "—" : (user.has_profile ? "Customized" : "Default")));

    const actionsTd = document.createElement("td");
    actionsTd.appendChild(buildActions(user));
    tr.appendChild(actionsTd);

    return tr;
  }

  function showEnrollLink(user, url, expiresInSeconds) {
    $("ad-link-card").hidden = false;
    const until = new Date(Date.now() + expiresInSeconds * 1000);
    $("ad-link-hint").textContent =
      `For ${user.display_name} (${user.id}) — valid until ${until.toLocaleTimeString()}.`;
    $("ad-link-url").value = url;
    $("ad-link-card").scrollIntoView({ behavior: "smooth", block: "center" });
  }

  $("ad-link-copy").addEventListener("click", async () => {
    const url = $("ad-link-url").value;
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      toast("Copied", "good");
    } catch {
      $("ad-link-url").select();
      toast("Select and copy manually", "bad");
    }
  });

  function populateProfileUserPicker() {
    const select = $("ad-profile-user");
    const current = select.value;
    select.replaceChildren();
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Select a member…";
    select.appendChild(placeholder);
    for (const user of allUsers) {
      if (user.is_owner) continue;
      const opt = document.createElement("option");
      opt.value = user.id;
      opt.textContent = `${user.display_name} (${user.id})`;
      select.appendChild(opt);
    }
    if ([...select.options].some((o) => o.value === current)) select.value = current;
  }

  async function loadUsers() {
    try {
      const d = await req("/api/admin/users");
      allUsers = d.users || [];
      const body = $("ad-users-body");
      body.replaceChildren();
      if (allUsers.length === 0) {
        const tr = document.createElement("tr");
        const td = cell("td", "No accounts yet.");
        td.colSpan = 7;
        td.className = "muted micro";
        tr.appendChild(td);
        body.appendChild(tr);
      } else {
        for (const user of allUsers) body.appendChild(buildRow(user));
      }
      $("ad-users-status").textContent = "";
      populateProfileUserPicker();
    } catch (e) {
      $("ad-users-status").textContent = "Couldn't load members: " + e.message;
    }
  }

  $("ad-create-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const id = $("ad-new-id").value.trim();
    const display_name = $("ad-new-name").value.trim();
    const role = $("ad-new-role").value;
    const status = $("ad-create-status");
    const btn = $("ad-create-btn");
    if (!/^[a-z0-9][a-z0-9_-]{0,31}$/.test(id)) {
      status.textContent = "Id must be lowercase letters, digits, '-' or '_', up to 32 characters.";
      return;
    }
    btn.disabled = true;
    status.textContent = "Creating…";
    try {
      const d = await post("/api/admin/users", { id, display_name, role });
      status.textContent = "";
      toast(`Created ${d.user.display_name}`, "good");
      $("ad-create-form").reset();
      await loadUsers();
    } catch (e) {
      status.textContent = "Couldn't create: " + e.message;
    } finally {
      btn.disabled = false;
    }
  });

  function buildProfileField(key, jsonKeys, value) {
    const field = document.createElement("label");
    field.className = "field";
    field.dataset.key = key;
    field.dataset.kind = jsonKeys.has(key) ? "json" : (PROFILE_STRING_KEYS.has(key) ? "string" : "number");

    const span = document.createElement("span");
    span.textContent = labelFor(key);
    field.appendChild(span);

    let input;
    if (field.dataset.kind === "json") {
      input = document.createElement("textarea");
      input.rows = 3;
      input.placeholder = "not set (uses installation default)";
      if (value !== undefined) input.value = JSON.stringify(value, null, 2);
    } else {
      input = document.createElement("input");
      input.type = field.dataset.kind === "number" ? "number" : "text";
      if (field.dataset.kind === "number") input.step = "any";
      input.placeholder = "not set (uses installation default)";
      if (value !== undefined) input.value = String(value);
    }
    field.appendChild(input);
    return field;
  }

  function readProfileForm() {
    const data = {};
    for (const field of $("ad-profile-fields").children) {
      const input = field.querySelector("input, textarea");
      const raw = input.value.trim();
      if (!raw) continue;
      const key = field.dataset.key;
      if (field.dataset.kind === "json") {
        data[key] = JSON.parse(raw); // caller catches SyntaxError
      } else if (field.dataset.kind === "number") {
        const n = Number(raw);
        if (!Number.isFinite(n)) throw new Error(`${labelFor(key)} must be a number`);
        data[key] = n;
      } else {
        data[key] = raw;
      }
    }
    return data;
  }

  async function loadProfileForUser(userId) {
    const status = $("ad-profile-status");
    $("ad-profile-form").hidden = true;
    status.textContent = "";
    if (!userId) return;
    try {
      const d = await req(`/api/admin/users/${encodeURIComponent(userId)}/profile`);
      const jsonKeys = new Set(d.json_keys || []);
      const fields = $("ad-profile-fields");
      fields.replaceChildren();
      for (const key of d.keys || []) {
        fields.appendChild(buildProfileField(key, jsonKeys, d.profile[key]));
      }
      $("ad-profile-form").hidden = false;
    } catch (e) {
      status.textContent = "Couldn't load profile: " + e.message;
    }
  }

  $("ad-profile-user").addEventListener("change", (event) => loadProfileForUser(event.target.value));

  $("ad-profile-save").addEventListener("click", async () => {
    const userId = $("ad-profile-user").value;
    const status = $("ad-profile-status");
    if (!userId) return;
    let data;
    try {
      data = readProfileForm();
    } catch (e) {
      status.textContent = e instanceof SyntaxError ? "Invalid JSON in one of the fields." : e.message;
      return;
    }
    const btn = $("ad-profile-save");
    btn.disabled = true;
    status.textContent = "Saving…";
    try {
      await post(`/api/admin/users/${encodeURIComponent(userId)}/profile`, { profile: data });
      status.textContent = "Saved.";
      toast("Profile saved", "good");
      await loadUsers();
    } catch (e) {
      status.textContent = "Couldn't save: " + e.message;
    } finally {
      btn.disabled = false;
    }
  });

  $("ad-profile-reset").addEventListener("click", async () => {
    const userId = $("ad-profile-user").value;
    if (!userId) return;
    if (!confirm("Reset this member's profile settings to the installation defaults?")) return;
    const status = $("ad-profile-status");
    try {
      await post(`/api/admin/users/${encodeURIComponent(userId)}/profile/reset`, {});
      toast("Profile reset", "good");
      await loadProfileForUser(userId);
      await loadUsers();
    } catch (e) {
      status.textContent = "Couldn't reset: " + e.message;
    }
  });

  loadUsers();
})();
