"""Phase 1 acceptance tests: the app shell serves, is themed, and is locked down.
Updated in Phase 2: app routes require a session, so shell tests use `authed`."""


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_dashboard_renders(authed):
    r = authed.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "OpenHealthAtlas" in html
    # Navigation contract. Order lives in app/__init__.py NAV.
    for item in ("Dashboard", "Consistency", "Body", "Nutrition",
                 "Recovery", "Data Explorer", "Daily log", "Profile &amp; targets"):
        assert item in html, f"nav item {item} missing from shell"
    # Mind, External care, Labs and Insight Explorer were removed from this
    # fork (owner decision, 2026-10-28 — see docs/PRODUCT_AUTHORITY.md).
    for item in ("Mind", "External care", "Labs", "Insight Explorer"):
        assert item not in html, f"removed nav item {item} still in shell"
    # Plans/Models/Chat/Export left the nav for the Settings drawer.
    for row in ("Wearable sync", "Export", "Vault notes"):
        assert row in html, f"settings drawer row {row} missing from shell"
    # task-50 item 6 (owner C3): the Program editor UI (page + drawer link)
    # was removed — /api/plans/* + health.py subcommands stay agent-only.
    assert "Program</span>" not in html
    assert "Sign out" in html  # logout control shows for a live session


def test_stub_pages_render(authed):
    for path in ("/training", "/nutrition", "/export",
                 "/vault-notes", "/consistency", "/data", "/models", "/log", "/profile"):
        r = authed.get(path)
        assert r.status_code == 200, f"{path} did not render"


def test_removed_feature_urls_404(authed):
    """Mind, External care, Labs, Insight Explorer, the athletic/strength-ratio/
    mobility drill pages and the legacy /chat bookmark were removed from this
    fork (owner decision, 2026-10-28 — see docs/PRODUCT_AUTHORITY.md)."""
    for path in (
        "/mind", "/mind/brain-dump", "/mind/prescriber-questions",
        "/care", "/labs", "/insights", "/insights/conversations", "/chat",
        "/training/athletic/strength", "/training/mobility",
    ):
        assert authed.get(path).status_code == 404, f"{path} did not 404"


def test_body_page_has_hevy_import_button(authed):
    """§hevy-panel-import (fork owner decision 2026-10-28): "an import button
    at the very top" of the Body page, next to the period dropdown."""
    html = authed.get("/training").get_data(as_text=True)
    assert 'id="hevy-import-btn"' in html
    assert "Import Hevy CSV" in html
    assert 'id="hevy-import-file"' in html
    assert 'id="hevy-import-panel"' in html
    # "at the very top" — the button sits in the header row, before the body
    # content and before the period dropdown it was asked to sit next to.
    assert (html.index('id="hevy-import-btn"') < html.index('data-dd="body"')
            < html.index('class="body-overview"'))


def test_program_page_removed_redirects_to_training(authed):
    """task-50 item 6 (owner C3, UI-only removal): the Program & schedule
    editor page is gone — /program now 302s to /training, the closest
    surviving page. The editor DOM + program.js must not exist anywhere,
    and Body must not have picked them back up. The backend
    (/api/plans/*, bridge, health.py routine subcommands) is untouched —
    that's covered by tests/test_plans_api.py, not here."""
    r = authed.get("/program")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/training")
    body = authed.get("/training").get_data(as_text=True)
    assert "js/program.js" not in body
    assert 'id="program-editor-root"' not in body
    # Body still carries the weight:strength-ratio chart (unrelated to the
    # editor removal — just guarding against regression). The Goals card was
    # separately removed (owner decision, 2026-10-28).
    assert 'id="ch-wsr"' in body


def test_legacy_plans_redirects_to_training(authed):
    """The Plans page dissolved in the nav flip; its program editor moved to
    Body, then to Settings (/program), then task-50 removed the editor UI
    entirely. Bookmarks to /plans must survive (302 straight to /training,
    not a 404)."""
    r = authed.get("/plans")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/training")


def test_consistency_page_links_to_habit_ledger(authed):
    """task-35: the Habit-ledger card is a whole-card link to the drill
    sub-page, plus an explicit "all ›" chip (design image 11)."""
    html = authed.get("/consistency").get_data(as_text=True)
    assert "/consistency/habits" in html


def test_consistency_habits_page_renders(authed):
    r = authed.get("/consistency/habits")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Habit ledger" in html
    assert "js/consistency_habits.js" in html


def test_recovery_page_renders(authed):
    r = authed.get("/recovery")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Recovery" in html
    for item in ("Sleep hours", "Sleep stages", "HRV", "Resting HR"):
        assert item in html, f"{item!r} missing from /recovery"
    # task-30: each KPI label drills to its /recovery/<metric> sub-page
    for href in ("/recovery/sleep", "/recovery/readiness",
                 "/recovery/hrv", "/recovery/rhr"):
        assert href in html, f"KPI drill link {href} missing from /recovery"


def test_recovery_drill_pages_render(authed):
    # the three real-data metric drills share one template
    for metric, title in (("sleep", "Sleep"), ("hrv", "HRV"), ("rhr", "Resting HR")):
        r = authed.get(f"/recovery/{metric}")
        assert r.status_code == 200, f"/recovery/{metric} did not render"
        html = r.get_data(as_text=True)
        assert "recovery breakdown" in html
        assert "js/recovery_detail.js" in html


def test_recovery_readiness_drill_has_real_engine(authed):
    r = authed.get("/recovery/readiness")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # T48: the engine is real now — no more "no engine yet"/"score pending"
    # placeholders; the engine detail cards remain available.
    assert "Component breakdown" in html
    assert "What's dragging it down" in html
    assert "Muscle recovery" in html
    assert "Soreness summary" in html
    assert "js/readiness_detail.js" in html


def test_recovery_drill_unknown_metric_404(authed):
    assert authed.get("/recovery/bogus").status_code == 404


def test_nutrition_page_renders(authed):
    r = authed.get("/nutrition")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    for tab in ("Goals", "Meals", "Supplements"):
        assert f">{tab}<" in html, f"tab {tab!r} missing from /nutrition"
    assert ">Intake<" not in html and ">Recipes<" not in html
    # the range dd survives for the Supplements adherence card
    assert 'data-dd="nutrition"' in html
    for opt in ("Day", "Week", "Month", "Year", "All time"):
        assert f">{opt}<" in html, f"range option {opt!r} missing"
    # consumption logging is gone from the page (routes/CLI stay agent-reachable)
    for gone in ("Logged today", "Log coverage", "Food quality score", "log a batch",
                 "Log by grams", "nBatchForm", "nEntries", "nKpiKcal", "nGaps",
                 "portions in freezer"):
        assert gone not in html, f"{gone!r} should be removed from /nutrition"
    # meals keep the meal-type dd and an ordering pill row
    assert 'id="nPills"' in html
    for pill in ("All", "High protein"):
        assert f">{pill}<" in html, f"filter pill {pill!r} missing"
    assert "Closes today" not in html


def test_nutrition_goals_tab_is_editable(authed):
    html = authed.get("/nutrition").get_data(as_text=True)
    for label in ("Calories", "Protein", "Carbs", "Fat", "Water", "Micronutrients",
                  "Diet phase", "Edit goals", "Edit targets"):
        assert label in html, f"goals element {label!r} missing"
    # the phase indicator is a live control now
    for phase in ("Cut", "Maintain", "Bulk"):
        assert f">{phase}<" in html
    assert 'id="nPhaseSeg"' in html and "aria-disabled" not in html
    # inline edit forms
    for form_id in ("nGoalForm", "nMicroForm", "nRing"):
        assert f'id="{form_id}"' in html
    assert 'id="nTgtReason"' in html
    assert 'id="nutrition-target-setup"' in html


def test_nutrition_meals_tab_can_create_edit_and_delete(authed):
    html = authed.get("/nutrition").get_data(as_text=True)
    assert "+ New meal" in html and 'id="nMealForm"' in html
    for field in ("mf-name", "mf-type", "mf-gpp", "mf-portions", "mf-ingredients",
                  "mf-save", "mf-cancel"):
        assert f'id="{field}"' in html, f"meal form field {field!r} missing"
    for nutrient in ("kcal", "protein_g", "carbs_g", "fat_g", "fiber_g", "vitamin_d_ug",
                     "magnesium_mg", "iron_mg", "zinc_mg"):
        assert f'data-nut="{nutrient}"' in html, f"meal nutrient {nutrient!r} missing"


def test_nutrition_page_uses_no_inline_styles(authed):
    # the CSP is style-src 'self': inline style attributes are silently dropped
    html = authed.get("/nutrition").get_data(as_text=True)
    assert 'style="' not in html
    js = authed.get("/static/js/nutrition.js").get_data(as_text=True)
    assert 'style="' not in js


def test_profile_page_keeps_only_what_nutrition_goals_does_not_cover(authed):
    html = authed.get("/profile").get_data(as_text=True)
    # still here: profile details, the phase offsets / protein per kg, planned times
    for kept in ("pf-profile-save", "pf-phase-offset-rows", "pf-protein-per-kg-rows",
                 "pf-nutrition-config-save", "pf-planned-table"):
        assert f'id="{kept}"' in html, f"{kept} should stay on the Profile page"
    # moved to Nutrition > Goals: the phase, the calorie/protein overrides, the micros
    for moved in ("pf-phase", "pf-phase-save", "pf-target-kcal", "pf-target-protein",
                  "pf-targets-save", "pf-engine-band", "pf-micro-rows", "pf-micro-add-btn"):
        assert f'id="{moved}"' not in html, f"{moved} now lives on Nutrition > Goals"
    assert "Diet phase" not in html and "Daily targets" not in html
    js = authed.get("/static/js/profile.js").get_data(as_text=True)
    for gone in ("pf-phase-save", "pf-targets-save", "pf-micro-add-btn", "nutrient_targets"):
        assert gone not in js
    # the Nutrition setup help points at the tab that now owns those settings
    help_text = authed.get("/nutrition").get_data(as_text=True)
    assert "under Micronutrients below" in help_text
    assert "diet phase, and daily kcal/protein overrides" not in help_text


def test_static_urls_carry_a_content_hash_and_cache_accordingly(authed, app):
    """A redeploy behind a CDN must never pair new HTML with the old JS/CSS."""
    import os
    import re
    pattern = r"/static/js/nutrition\.js\?v=([0-9a-f]{12})"
    html = authed.get("/nutrition").get_data(as_text=True)
    js = re.search(pattern, html)
    assert js, "static URLs must be versioned"
    assert re.search(r"/static/css/panel\.css\?v=[0-9a-f]{12}", html)
    versioned = authed.get(f"/static/js/nutrition.js?v={js.group(1)}")
    assert versioned.status_code == 200
    assert versioned.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert authed.get("/static/js/nutrition.js").headers["Cache-Control"] == "no-cache"
    # the version follows the file's content, so an edited asset gets a new URL
    path = os.path.join(app.static_folder, "js", "nutrition.js")
    original = open(path, "rb").read()
    try:
        with open(path, "ab") as f:
            f.write(b"\n// changed\n")
        again = re.search(pattern, authed.get("/nutrition").get_data(as_text=True))
        assert again and again.group(1) != js.group(1)
    finally:
        with open(path, "wb") as f:
            f.write(original)


def test_security_headers_on_public_page(client):
    r = client.get("/login")
    csp = r.headers.get("Content-Security-Policy", "")
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "DENY"
    # same-origin, NOT no-referrer — Flask-WTF's HTTPS CSRF layer needs the
    # same-origin Referer; no-referrer breaks every secure POST (found live).
    assert r.headers.get("Referrer-Policy") == "same-origin"


def test_csp_pins_no_frontend_outbound(client):
    # §3f vendoring review: the FULL policy is pinned so a regression that
    # quietly opens an outbound channel (connect-src, script-src, …) fails
    # loudly. Every fetch/script/img/style source must stay same-origin
    # (data: images excepted). Change this test only with the policy itself.
    assert client.get("/login").headers.get("Content-Security-Policy") == (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'self'"
    )


def test_vendored_echarts_served(client):
    r = client.get("/static/vendor/echarts.min.js")
    assert r.status_code == 200
    assert b"Apache" in r.data[:400]


def test_vendored_body_muscles_served_with_license(client):
    import hashlib
    import pathlib
    r = client.get("/static/vendor/body-muscles/body-muscles.umd.min.js")
    assert r.status_code == 200
    # byte-unmodified vendoring: pin the sha256 recorded in VENDOR.md (the
    # supply-chain vetting verified this exact hash against a reproducible
    # build of the upstream source)
    assert hashlib.sha256(r.data).hexdigest() == \
        "c7fe91ea3ea05a11f3b684c165af5b7be085d88bd8da9dcc1230e1f181613604"
    vend = pathlib.Path(__file__).resolve().parent.parent / "app" / "static" / "vendor" / "body-muscles"
    assert (vend / "LICENSE").exists() and (vend / "NOTICE").exists()
