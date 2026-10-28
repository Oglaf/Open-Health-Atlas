"""CLI ownership for the validated BIA body-composition writer and reader."""
import json

from .. import body_composition as domain, runtime
from ..importers.common import stdin_text


def log_body_composition(context, a, *, stdin):
    if stdin is None:
        raise TypeError("log_body_composition requires an explicit stdin stream")
    text = stdin_text(stdin, "body composition report")
    if len(text.encode("utf-8")) > 200_000:
        raise SystemExit("body composition report too large (>200k)")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise SystemExit(f"body composition report is not valid JSON: {exc}")
    report = domain.validate_report(data)
    c = runtime.connect(context.database)
    try:
        c.execute("BEGIN IMMEDIATE")
        result = domain.log_body_composition(c, report)
        c.commit()
        return result
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def body_composition(context, a):
    c = runtime.connect(context.database)
    try:
        return domain.body_composition(
            c, days=a.days, latest=a.latest, clock=context.clock,
        )
    finally:
        c.close()
