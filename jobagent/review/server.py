"""Local review dashboard.

Runs on your machine only, bound to localhost. Shows today's shortlist with the
fit reasoning, the generated material, and a button per job that opens the form
in your Chrome with everything filled in. You read it, you click submit.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, url_for

from .. import pipeline
from ..config import Config
from ..models import APPLIED, PREFILLED, PREPPED, QUEUED, SKIPPED

log = logging.getLogger(__name__)


def _decode(row) -> dict:
    data = dict(row)
    for key in ("strengths", "gaps", "red_flags"):
        try:
            data[key] = json.loads(data.get(key) or "[]")
        except (ValueError, TypeError):
            data[key] = []
    try:
        data["artifacts"] = json.loads(data.get("artifacts") or "{}")
    except (ValueError, TypeError):
        data["artifacts"] = {}
    report_path = Path(data["artifacts"].get("brief", "")).parent / "fill_report.json" \
        if data["artifacts"].get("brief") else None
    data["fill_report"] = {}
    if report_path and report_path.exists():
        try:
            data["fill_report"] = json.loads(report_path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return data


def create_app(cfg: Config) -> Flask:
    app = Flask(__name__)
    app.config["JOBAGENT"] = cfg

    def store():
        return pipeline.get_store(cfg)

    @app.get("/")
    def index():
        st = store()
        try:
            open_rows = [_decode(r) for r in st.queue([QUEUED, PREPPED, PREFILLED])]
            closed_rows = [_decode(r) for r in st.queue([APPLIED, SKIPPED])][:40]
            counts = st.counts()
        finally:
            st.close()
        return render_template(
            "index.html", jobs=open_rows, closed=closed_rows, counts=counts, cfg=cfg
        )

    @app.get("/artifact")
    def artifact():
        """Serve a generated file, but only from inside the runs directory."""
        raw = request.args.get("path", "")
        target = Path(raw).resolve()
        runs = pipeline.RUNS_DIR.resolve()
        if not target.is_relative_to(runs) or not target.exists():
            return "not found", 404
        return app.response_class(target.read_text(encoding="utf-8"), mimetype="text/plain")

    @app.post("/prefill/<job_id>")
    def prefill(job_id: str):
        st = store()
        try:
            result = pipeline.stage_prefill(cfg, st, job_ids=[job_id])
        finally:
            st.close()
        if request.headers.get("Accept", "").startswith("application/json"):
            return jsonify(result)
        return redirect(url_for("index"))

    @app.post("/status/<job_id>/<status>")
    def set_status(job_id: str, status: str):
        if status not in (APPLIED, SKIPPED, QUEUED):
            return "bad status", 400
        st = store()
        try:
            st.set_status(job_id, status)
        finally:
            st.close()
        return redirect(url_for("index"))

    @app.post("/run")
    def run_now():
        st = store()
        try:
            result = pipeline.run_all(cfg, st, prefill=False)
        finally:
            st.close()
        return jsonify(result)

    return app


def serve(cfg: Config) -> None:
    app = create_app(cfg)
    print(f"\n  review dashboard: http://127.0.0.1:{cfg.review_port}\n")
    app.run(host="127.0.0.1", port=cfg.review_port, debug=False)
