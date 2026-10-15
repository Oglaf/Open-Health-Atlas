"""WebAuthn login + enrollment endpoints.

Ceremony state (the challenge) lives in the signed Flask session cookie —
integrity is what matters for a challenge, not secrecy. The *auth* session
that results from a successful ceremony is server-side (app/auth.py).
"""
import json

from flask import (Blueprint, current_app, jsonify, redirect, render_template,
                   request, session, url_for)
from webauthn import (generate_authentication_options,
                      generate_registration_options, options_to_json,
                      verify_authentication_response,
                      verify_registration_response)
from webauthn.helpers.exceptions import (InvalidAuthenticationResponse,
                                         InvalidRegistrationResponse)
from webauthn.helpers.structs import (AuthenticatorSelectionCriteria,
                                      PublicKeyCredentialDescriptor,
                                      ResidentKeyRequirement,
                                      UserVerificationRequirement)

from app import auth, limiter, users
from app.panel_db import BOOTSTRAP_USER_ID

bp = Blueprint("auth", __name__)


def _rp_id() -> str:
    return current_app.config["PANEL_RP_ID"]


def _origin() -> str:
    return current_app.config["PANEL_ORIGIN"]


def _user_name() -> str:
    return current_app.config["PANEL_USER_NAME"]


def _webauthn_identity(user_id: str):
    """(user handle, display name, resident-key requirement) for enrollment.
    The bootstrap owner keeps PANEL_USER_ID so existing passkeys keep
    matching; every other user gets a stable per-user handle and must use a
    resident/discoverable credential (login never types their id by default)."""
    target = users.get_user(user_id)
    name = _user_name() if user_id == BOOTSTRAP_USER_ID else (
        target["display_name"] if target else user_id
    )
    handle = (
        current_app.config["PANEL_USER_ID"].encode("utf-8") if user_id == BOOTSTRAP_USER_ID
        else user_id.encode("utf-8")
    )
    resident = (
        ResidentKeyRequirement.PREFERRED if user_id == BOOTSTRAP_USER_ID
        else ResidentKeyRequirement.REQUIRED
    )
    return handle, name, resident


def _safe_next() -> str:
    nxt = request.args.get("next", "/")
    return nxt if nxt.startswith("/") and not nxt.startswith("//") else "/"


# ------------------------------------------------------------------- pages
@bp.get("/login")
def login_page():
    if auth.current_session():
        return redirect("/")
    return render_template("login.html", next=_safe_next())


@bp.get("/enroll")
def enroll_page():
    token = request.args.get("token", "")
    token_user_id = auth.enroll_token_user(token)
    token_user = users.get_user(token_user_id) if token_user_id else None
    display = (
        _user_name() if token_user_id == BOOTSTRAP_USER_ID
        else (token_user["display_name"] if token_user else None)
    )
    return render_template("enroll.html",
                           token=token,
                           token_ok=token_user_id is not None,
                           token_user_display=display)


@bp.post("/logout")
def logout():
    auth.audit("auth.logout")
    auth.revoke_current_session()
    return auth.clear_session_cookie(redirect(url_for("auth.login_page")))


# ------------------------------------------------------------------- login
@bp.post("/api/auth/login/options")
@limiter.limit("10/minute")
def login_options():
    body = request.get_json(force=True, silent=True) or {}
    username = body.get("username") if isinstance(body, dict) else None
    if username is not None and (
        not isinstance(username, str) or not users.USER_ID_RE.fullmatch(username)
    ):
        # A malformed username is handled exactly like no username at all —
        # it must not behave differently from "that account doesn't exist".
        username = None
    if username is None:
        if not auth.all_credentials():
            return jsonify(error="No passkeys enrolled yet — use an enrollment link "
                                 "(see README: passkey enrollment)."), 400
        # Discoverable/resident-credential login: the platform picks among
        # whatever passkey(s) it holds for this origin.
        allow_credentials = []
    else:
        # An unknown username gets the exact same options shape as a known
        # one with no credentials yet — never a different response.
        allow_credentials = [
            PublicKeyCredentialDescriptor(id=c["credential_id"])
            for c in auth.credentials_for_user(username)
        ]
    options = generate_authentication_options(
        rp_id=_rp_id(),
        allow_credentials=allow_credentials,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    session["auth_challenge"] = auth.b64u(options.challenge)
    return current_app.response_class(options_to_json(options),
                                      mimetype="application/json")


@bp.post("/api/auth/login/verify")
@limiter.limit("10/minute")
def login_verify():
    challenge_b64 = session.pop("auth_challenge", None)
    if not challenge_b64:
        return jsonify(error="no pending login challenge"), 400
    body = request.get_json(force=True, silent=True) or {}
    cred_json = body.get("credential")
    if not cred_json:
        return jsonify(error="missing credential"), 400

    try:
        credential_id = auth.from_b64u(json.loads(cred_json)["rawId"])
    except (ValueError, KeyError):
        return jsonify(error="malformed credential"), 400
    stored = auth.find_credential(credential_id)
    if stored is None:
        auth.audit("auth.login_failed", "unknown credential")
        return jsonify(error="unknown credential"), 403

    try:
        verification = verify_authentication_response(
            credential=cred_json,
            expected_challenge=auth.from_b64u(challenge_b64),
            expected_rp_id=_rp_id(),
            expected_origin=_origin(),
            credential_public_key=stored["public_key"],
            credential_current_sign_count=stored["sign_count"],
            require_user_verification=True,
        )
        auth.validate_sign_count(stored["sign_count"], verification.new_sign_count)
    except (InvalidAuthenticationResponse, auth.SignCountRegression) as exc:
        auth.audit("auth.login_failed", str(exc))
        return jsonify(error="verification failed"), 403

    user_row = users.get_user(stored["user_id"])
    if user_row is None or user_row["status"] != "active":
        auth.audit("auth.login_failed", "disabled or missing user",
                  user_id=stored["user_id"])
        return jsonify(error="this account is disabled"), 403

    auth.update_sign_count(credential_id, verification.new_sign_count)
    auth.audit("auth.login", stored["label"] or "", user_id=user_row["id"])
    token = auth.create_session(user_row["id"])
    resp = jsonify(ok=True)
    return auth.set_session_cookie(resp, token)


# -------------------------------------------------------------- enrollment
@bp.post("/api/auth/register/options")
@limiter.limit("10/minute")
def register_options():
    body = request.get_json(force=True, silent=True) or {}
    token = body.get("token", "")
    user_id = auth.enroll_token_user(token)
    if user_id is None:
        return jsonify(error="invalid or expired enrollment token"), 403
    target = users.get_user(user_id)
    if target is None or target["status"] != "active":
        return jsonify(error="invalid or expired enrollment token"), 403
    handle, name, resident = _webauthn_identity(user_id)
    options = generate_registration_options(
        rp_id=_rp_id(),
        rp_name="OpenHealthAtlas",
        user_id=handle,
        user_name=name,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=resident,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=c["credential_id"])
            for c in auth.credentials_for_user(user_id)
        ],
    )
    session["reg_challenge"] = auth.b64u(options.challenge)
    return current_app.response_class(options_to_json(options),
                                      mimetype="application/json")


@bp.post("/api/auth/register/verify")
@limiter.limit("10/minute")
def register_verify():
    body = request.get_json(force=True, silent=True) or {}
    token = body.get("token", "")
    user_id = auth.enroll_token_user(token)
    if user_id is None:
        return jsonify(error="invalid or expired enrollment token"), 403
    target = users.get_user(user_id)
    if target is None or target["status"] != "active":
        return jsonify(error="invalid or expired enrollment token"), 403
    challenge_b64 = session.pop("reg_challenge", None)
    if not challenge_b64:
        return jsonify(error="no pending registration challenge"), 400
    cred_json = body.get("credential")
    if not cred_json:
        return jsonify(error="missing credential"), 400

    try:
        verification = verify_registration_response(
            credential=cred_json,
            expected_challenge=auth.from_b64u(challenge_b64),
            expected_rp_id=_rp_id(),
            expected_origin=_origin(),
            require_user_verification=True,
        )
    except InvalidRegistrationResponse as exc:
        auth.audit("auth.enroll_failed", str(exc), user_id=user_id)
        return jsonify(error="registration verification failed"), 403

    label = (body.get("label") or "").strip()[:60] or "passkey"
    transports = ",".join(json.loads(cred_json).get("response", {})
                          .get("transports", []) or [])
    auth.store_credential(
        credential_id=verification.credential_id,
        public_key=verification.credential_public_key,
        sign_count=verification.sign_count,
        transports=transports,
        label=label,
        user_id=user_id,
    )
    auth.consume_enroll_token(token)
    auth.audit("auth.enroll", label, user_id=user_id)
    # The device just proved presence + verification — log it straight in.
    session_token = auth.create_session(user_id)
    resp = jsonify(ok=True)
    return auth.set_session_cookie(resp, session_token)
