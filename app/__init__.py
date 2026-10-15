"""Compose the OpenHealthAtlas panel, authentication and validated broker client.

Protected routes require a server-side passkey session. Health records are
read through request-scoped connections; mutations cross the write broker.
"""
import os

import click
from flask import Flask, jsonify, redirect, request, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from werkzeug.middleware.proxy_fix import ProxyFix

# In-memory limiter: per-worker and, behind a single reverse-proxy peer, one
# shared bucket for clients. This is suitable only for a private single-user
# app whose primary authentication wall is WebAuthn. With ProxyFix below,
# because we do NOT trust X-Forwarded-For: remote_addr remains the socket peer.
limiter = Limiter(key_func=get_remote_address, storage_uri="memory://")
csrf = CSRFProtect()

# (endpoint, label, icon id in the SVG sprite in base.html)
# Canonical pillar navigation. Order is a user-visible UI contract.
NAV = [
    ("core.dashboard", "Dashboard", "dash"),
    ("core.consistency", "Consistency", "consistency"),
    ("core.training", "Body", "training"),
    ("core.mind", "Mind", "mind"),
    ("core.nutrition", "Nutrition", "nutrition"),
    ("core.recovery", "Recovery", "recovery"),
    ("core.care", "External care", "care"),
    ("core.labs", "Labs", "labs"),
    ("core.data", "Data Explorer", "data"),
    ("core.insights", "Insight Explorer", "insights"),
]

# Shown in the mobile bottom tab bar (the rest live behind "More").
MOBILE_TABS = {"core.dashboard", "core.training", "core.nutrition", "core.mind"}

# Endpoints reachable without a session. Everything else is gated.
PUBLIC_ENDPOINTS = {
    "static",
    "core.healthz",
    "auth.login_page",
    "auth.enroll_page",
    "auth.login_options",
    "auth.login_verify",
    "auth.register_options",
    "auth.register_verify",
}


def create_app(overrides: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object("app.config.Config")
    if not app.config.get("PANEL_DB"):
        os.makedirs(app.instance_path, exist_ok=True)
        app.config["PANEL_DB"] = os.path.join(app.instance_path, "panel.db")
    if overrides:
        app.config.update(overrides)

    # A reverse proxy may terminate TLS and forward plain HTTP with
    # X-Forwarded-* headers. Trust ONE hop of X-Forwarded-Proto ONLY, so
    # request.is_secure is True in production — without this, Flask-WTF
    # silently skips its HTTPS referrer check and cookies/URLs see the wrong
    # scheme. Deliberately NOT x_for/x_host: anything that can reach the
    # loopback port directly could spoof those, and x_for would move the
    # rate-limiter key onto an attacker-settable header (fresh bucket per
    # forged IP = no brute-force ceiling), while nothing here builds URLs
    # from the request host (WebAuthn origin/RP come from config).
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)

    limiter.init_app(app)
    csrf.init_app(app)

    from app import auth, db_read, users
    from app.panel_db import close_db, init_db

    init_db(app)
    app.teardown_appcontext(close_db)
    db_read.init_app(app)

    from app.routes.auth import bp as auth_bp
    from app.routes.core import bp as core_bp
    from app.routes.dash import bp as dash_bp
    from app.routes.log import bp as log_bp
    from app.routes.chat import bp as chat_bp
    from app.routes.export import bp as export_bp
    from app.routes.insights import bp as insights_bp
    from app.routes.integrations import bp as integrations_bp
    from app.routes.models import bp as models_bp
    from app.routes.nutrition import bp as nutrition_bp
    from app.routes.labs import bp as labs_bp
    from app.routes.plans import bp as plans_bp
    from app.routes.training import bp as training_bp
    from app.routes.recovery import bp as recovery_bp

    app.register_blueprint(core_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(dash_bp)
    app.register_blueprint(log_bp)
    app.register_blueprint(training_bp)
    app.register_blueprint(plans_bp)
    app.register_blueprint(nutrition_bp)
    app.register_blueprint(recovery_bp)
    app.register_blueprint(labs_bp)
    app.register_blueprint(models_bp)
    app.register_blueprint(export_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(insights_bp)
    app.register_blueprint(integrations_bp)

    @app.before_request
    def require_login():
        endpoint = request.endpoint
        if endpoint is None or endpoint in PUBLIC_ENDPOINTS:
            return None
        # A session whose user_id no longer resolves, or whose user has been
        # disabled, must stop authenticating immediately — not just at
        # natural session expiry.
        if auth.current_session() is not None:
            user = users.current_user()
            if user is not None and user["status"] == "active":
                return None
        if request.method == "GET" and not request.path.startswith("/api/"):
            return redirect(url_for("auth.login_page", next=request.path))
        return jsonify(error="unauthenticated"), 401

    @app.context_processor
    def inject_nav():
        user = users.current_user()
        # The bootstrap owner's display name always tracks the live
        # HERMES_DISPLAY_NAME config, exactly as a single-user install
        # behaved before — the stored row value is only a migration artifact.
        display_name = (
            user["display_name"]
            if user is not None and not users.is_owner(user)
            else app.config["HERMES_DISPLAY_NAME"]
        )
        return {
            "NAV": NAV,
            "MOBILE_TABS": MOBILE_TABS,
            "logged_in": auth.current_session() is not None,
            "display_name": display_name,
        }

    @app.errorhandler(CSRFError)
    def csrf_error(e):
        # JSON, not Flask's HTML error page: the fetch() clients surface
        # `error` directly, and HTTP/2 has no status text to fall back on.
        return jsonify(error=f"CSRF check failed: {e.description}"), 400

    @app.after_request
    def security_headers(resp):
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'"
        )
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        # same-origin (NOT no-referrer): Flask-WTF's HTTPS CSRF layer requires a
        # same-origin Referer on secure POSTs; with this policy the referrer is
        # sent only to the panel itself, so nothing leaks cross-origin.
        resp.headers["Referrer-Policy"] = "same-origin"
        return resp

    @app.cli.command("enroll-token")
    @click.option("--user", "user_id", default=None,
                  help="User id to enroll a passkey for (default: the bootstrap owner).")
    def enroll_token_cmd(user_id):
        """Print a one-time passkey enrollment URL (valid 15 minutes)."""
        from app.panel_db import BOOTSTRAP_USER_ID
        user_id = user_id or BOOTSTRAP_USER_ID
        if user_id != BOOTSTRAP_USER_ID:
            target = users.get_user(user_id)
            if target is None or target["status"] != "active":
                raise click.ClickException(f"no active user '{user_id}' — run `flask user-create` first")
        token = auth.new_enroll_token(user_id)
        auth.audit("auth.enroll_token_issued", user_id=user_id)
        click.echo(f"{app.config['PANEL_ORIGIN']}/enroll?token={token}")
        click.echo("Open this on the device you want to enroll. Valid 15 minutes, single use.")

    @app.cli.command("user-create")
    @click.argument("user_id")
    @click.option("--name", "display_name", default=None,
                  help="Display name shown in the panel (default: the id).")
    @click.option("--role", type=click.Choice(["admin", "member"]), default="member")
    def user_create_cmd(user_id, display_name, role):
        """Create a user with their own health.db + vault under USERS_ROOT."""
        from pathlib import Path
        from scripts.init_hermes import init_health_database
        try:
            users.validate_user_id(user_id)
        except users.InvalidUserId as exc:
            raise click.ClickException(str(exc))
        if users.get_user(user_id) is not None:
            raise click.ClickException(f"user already exists: {user_id}")
        user_dir = Path(users.users_root(app)) / user_id
        user_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(user_dir, 0o700)
        try:
            init_health_database(user_dir / "health.db", user_dir / "vault")
        except FileExistsError as exc:
            raise click.ClickException(str(exc))
        users.create_user(user_id, display_name, role)
        auth.audit("user.create", user_id)
        click.echo(f"created user '{user_id}' ({role}) at {user_dir}")

    @app.cli.command("user-list")
    def user_list_cmd():
        """List every panel user."""
        for row in users.all_users():
            click.echo(f"{row['id']}\t{row['role']}\t{row['status']}\t{row['display_name']}")

    @app.cli.command("user-disable")
    @click.argument("user_id")
    def user_disable_cmd(user_id):
        """Disable a user and revoke their sessions immediately."""
        if users.get_user(user_id) is None:
            raise click.ClickException(f"no such user: {user_id}")
        users.set_status(user_id, "disabled")
        auth.audit("user.disable", user_id)
        click.echo(f"disabled '{user_id}'")

    @app.cli.command("user-enable")
    @click.argument("user_id")
    def user_enable_cmd(user_id):
        """Re-enable a disabled user."""
        if users.get_user(user_id) is None:
            raise click.ClickException(f"no such user: {user_id}")
        users.set_status(user_id, "active")
        auth.audit("user.enable", user_id)
        click.echo(f"enabled '{user_id}'")

    return app
