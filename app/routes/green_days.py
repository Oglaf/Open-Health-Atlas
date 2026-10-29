"""Dashboard Green-day analysis jobs (fork 2026-10-28): the governed job
machinery moved here verbatim from the removed Insight Explorer module — the
Dashboard's hero card keeps its deterministic, fingerprint-checked analysis
jobs. Only the dashboard endpoint URL changed (see app/routes/dash.py mount,
url_prefix stays /api/insights for the browser contract). The arbitrary
outcome/finding/hypothesis job routes were removed with Insight Explorer.
"""
import hashlib
import json
import re

from deploy.bridge_commands import OUTCOME_ANALYSIS_TIMEOUT

from flask import Blueprint, current_app, jsonify, request

from app import bridge, limiter
from datetime import date

bp = Blueprint("dash_green_days", __name__, url_prefix="/api/insights")

JOB_ID = re.compile(r"^[0-9a-f]{32}$")
SHA256_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
FIXTURE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")
PHASE4_ENGINE_TIMEOUT = float(OUTCOME_ANALYSIS_TIMEOUT + 10)

def _invalid_query(error):
    return jsonify(error=error), 400



def _canonical_result_sha256(result):
    encoded = json.dumps(
        result, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()



def _job_flags(command, args):
    """Compare complete operation scopes without depending on flag order."""
    flags = {"--mode": "all", "--min-n": "30", "--interactions": "none",
             "--top": "30"} if command == "outcome-associations" else {}
    supplied = set()
    index = 0
    while index < len(args):
        flag = args[index]
        if not isinstance(flag, str) or not flag.startswith("--") or flag in supplied:
            raise ValueError("invalid job scope")
        supplied.add(flag)
        if flag == "--all":
            flags[flag] = True
        else:
            index += 1
            if index >= len(args) or not isinstance(args[index], str):
                raise ValueError("invalid job scope")
            flags[flag] = (str(int(args[index])) if flag in {"--min-n", "--top", "--days"}
                           else args[index])
        index += 1
    if not supplied.intersection({"--from", "--to", "--days", "--all"}):
        flags["--days"] = "365"
    return flags



def _dashboard_green_days_scope():
    """Return one trusted display range; reject malformed server config."""
    scope = current_app.config.get("DASHBOARD_GREEN_DAYS_FIXED_SCOPE")
    if scope is None:
        return ("--days", "365"), None
    expected = {
        "data_class", "fixture_id", "range_from", "range_to",
        "seed_sha256", "database_sha256",
    }
    if not isinstance(scope, dict) or set(scope) != expected:
        raise ValueError("invalid dashboard Green-day fixed scope")
    if (
        scope["data_class"] != "fictional"
        or not isinstance(scope["fixture_id"], str)
        or FIXTURE_ID.fullmatch(scope["fixture_id"]) is None
        or SHA256_ID.fullmatch(scope.get("seed_sha256", "")) is None
        or SHA256_ID.fullmatch(scope.get("database_sha256", "")) is None
    ):
        raise ValueError("invalid dashboard Green-day fixed scope")
    try:
        start = date.fromisoformat(scope["range_from"])
        end = date.fromisoformat(scope["range_to"])
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid dashboard Green-day fixed scope") from exc
    if (
        start.isoformat() != scope["range_from"]
        or end.isoformat() != scope["range_to"]
        or start > end
    ):
        raise ValueError("invalid dashboard Green-day fixed scope")
    return ("--from", scope["range_from"], "--to", scope["range_to"]), dict(scope)



def _analysis_job(command, argv, *, job_id=None, socket_path=None, scope=False):
    """Job state stays outside the unchanged deterministic result envelope."""
    if job_id is not None and JOB_ID.fullmatch(job_id) is None:
        return _invalid_query("invalid_job_id")
    if job_id is None and request.get_json(silent=True) != {}:
        return _invalid_query("invalid_job_request")
    kwargs = {"timeout": 30.0}
    if socket_path is not None:
        kwargs["socket_path"] = socket_path
    try:
        if job_id is None:
            job = bridge.run(
                "analysis-job-start",
                stdin=json.dumps({"command": command, "args": list(argv)}),
                **kwargs,
            )
        else:
            job = bridge.run("analysis-job-status", job_id, **kwargs)
    except bridge.BridgeError as exc:
        status = 409 if exc.code in {"stale_finding", "stale_job"} else 502
        return jsonify(ok=False, error={"code": exc.code or "job_unavailable",
                                         "message": str(exc)}), status
    try:
        stored = job["request"]
        matches = (
            stored["command"] == command
            and _job_flags(command, stored["args"]) == _job_flags(command, argv)
        )
        valid = (
            JOB_ID.fullmatch(job["job_id"]) is not None
            and (job_id is None or job["job_id"] == job_id)
            and job["status"] in {"queued", "running", "completed", "failed", "stale"}
        )
    except (KeyError, TypeError, ValueError):
        return jsonify(ok=False, error="Invalid analysis job response."), 502
    if not valid:
        return jsonify(ok=False, error="Invalid analysis job response."), 502
    if not matches:
        # A remembered identifier is never authority to reveal another scope.
        return jsonify(ok=False, error={"code": "job_scope_mismatch",
                                         "message": "This job belongs to another analysis window."}), 409
    public = {"job_id": job["job_id"], "status": job["status"]}
    if job["status"] == "completed":
        if not isinstance(job.get("result"), dict):
            return jsonify(ok=False, error="Analysis job has no result."), 502
        public["result"] = job["result"]
        if scope is not False:
            evidence = {
                "contract": "openhealthatlas-dashboard-green-days-evidence-v1",
                "deterministic_authority": "OpenHealthAtlas",
                "command": command, "args": list(argv),
                "result_sha256": _canonical_result_sha256(job["result"]),
            }
            if scope is not None:
                evidence.update(scope)
            public["evidence"] = evidence
    elif job["status"] in {"failed", "stale"}:
        public["error"] = job.get("error") or {
            "code": "analysis_failed", "message": "Analysis could not finish. Try again.",
        }
    response = jsonify(ok=True, job=public)
    response.headers["Cache-Control"] = "no-store"
    return response, (202 if job["status"] in {"queued", "running"} else 200)




@bp.post("/dashboard-green-days/jobs")
@limiter.limit("12/minute")

def start_dashboard_green_days_job():
    return dashboard_green_days_job()


@bp.get("/dashboard-green-days/jobs/<job_id>")

def dashboard_green_days_job(job_id=None):
    prepared, error = _dashboard_green_days_request()
    if error is not None:
        return error
    argv, scope, fixture_socket = prepared
    return _analysis_job("outcome-associations", argv, job_id=job_id,
                         socket_path=fixture_socket, scope=scope)


def _dashboard_green_days_request():
    if request.args:
        return None, _invalid_query("unexpected_query_parameter")
    try:
        range_argv, scope = _dashboard_green_days_scope()
    except ValueError:
        return None, (jsonify(
            ok=False,
            error="dashboard Green-day display configuration is invalid",
        ), 503)
    fixture_socket = current_app.config.get(
        "DASHBOARD_GREEN_DAYS_BRIDGE_SOCKET"
    )
    if scope is not None and (
        not isinstance(fixture_socket, str)
        or not fixture_socket.startswith("/")
        or "\x00" in fixture_socket
    ):
        return None, (jsonify(
            ok=False,
            error="dashboard Green-day fixture broker is unavailable",
        ), 503)
    if scope is None and fixture_socket is not None:
        return None, (jsonify(
            ok=False,
            error="dashboard Green-day display configuration is invalid",
        ), 503)
    argv = (
        "--outcome", "subjective.day_rating",
        "--mode", "green-vs-non-green",
        *range_argv,
        "--min-n", "30",
        "--interactions", "none",
        "--top", "3",
    )
    return (argv, scope, fixture_socket), None




# Fork 2026-10-29: two small read-only engine reads the Consistency and
# Dashboard pages still call. They lived in the removed insights module; the
# removal accidentally 404'd them ("Engine unavailable: HTTP 404").
ADHERENCE_ALLOWED_DAYS = {"1": 1, "7": 7, "30": 30, "90": 90, "365": 365, "all": 0}


def _engine(subcmd, *args, timeout=75.0):
    """Run a read-only engine command; engine JSON passes through untouched."""
    try:
        return jsonify(ok=True, result=bridge.run(subcmd, *args, timeout=timeout))
    except bridge.BridgeError as exc:
        if exc.code is not None:
            return jsonify(ok=False, error={"code": exc.code, "message": str(exc)}), 502
        return jsonify(ok=False, error=str(exc)), 502


@bp.get("/adherence")
def adherence():
    """Follow-through trend; `days` is optional and whitelisted."""
    rng = request.args.get("days")
    if rng is None:
        days = 90
    elif rng in ADHERENCE_ALLOWED_DAYS:
        days = ADHERENCE_ALLOWED_DAYS[rng]
    else:
        return jsonify(error="days must be one of 1, 7, 30, 90, 365, all"), 400
    return _engine("adherence", "--days", str(days))


@bp.get("/coverage")
def coverage():
    return _engine("data-coverage", "--days", "90")
