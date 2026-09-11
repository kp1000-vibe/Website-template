"""Command line entry point."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import pipeline
from .config import CONFIG_DIR, ConfigError, DATA_DIR, RUNS_DIR, load


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)


def _print(result) -> None:
    print(json.dumps(result, indent=2, default=str))


# --------------------------------------------------------------------- commands

def cmd_source(cfg, args):
    store = pipeline.get_store(cfg)
    try:
        _print(pipeline.stage_source(cfg, store))
    finally:
        store.close()


def cmd_score(cfg, args):
    store = pipeline.get_store(cfg)
    try:
        _print(pipeline.stage_score(cfg, store, offline=args.offline))
    finally:
        store.close()


def cmd_queue(cfg, args):
    store = pipeline.get_store(cfg)
    try:
        _print(pipeline.stage_queue(cfg, store))
    finally:
        store.close()


def cmd_prep(cfg, args):
    store = pipeline.get_store(cfg)
    try:
        _print(pipeline.stage_prep(cfg, store))
    finally:
        store.close()


def cmd_prefill(cfg, args):
    store = pipeline.get_store(cfg)
    try:
        _print(pipeline.stage_prefill(cfg, store, job_ids=args.job_id or None))
    finally:
        store.close()


def cmd_run(cfg, args):
    store = pipeline.get_store(cfg)
    try:
        result = pipeline.run_all(cfg, store, offline=args.offline, prefill=not args.no_prefill)
    finally:
        store.close()
    _print(result)
    print("\nReview and submit:  python -m jobagent review")


def cmd_review(cfg, args):
    from .review.server import serve

    serve(cfg)


def cmd_status(cfg, args):
    store = pipeline.get_store(cfg)
    try:
        counts = store.counts()
        rows = store.queue()
    finally:
        store.close()
    print(json.dumps(counts, indent=2))
    print()
    for row in rows[:30]:
        print(f"  {str(row['score'] or '--'):>3}  {row['status']:<9}  "
              f"{row['title'][:48]:<48}  {row['company'][:28]}")


def cmd_chrome(cfg, args):
    from .fill.browser import launch

    launch(cfg.cdp_port, cfg.chrome_profile_dir, cfg.chrome_binary)
    print(f"Chrome is up on port {cfg.cdp_port} using {cfg.chrome_profile_dir}.")
    print("Log into LinkedIn and anything else you need. The session persists.")


def cmd_verify_boards(cfg, args):
    """Ping every board token and write the ones that answer to boards.verified.yaml.

    The shipped boards.yaml is a starting list, not a verified one. Company board
    tokens change and companies switch ATS. Run this first, and again monthly.
    """
    import yaml

    from .sources.base import get_json

    endpoints = {
        "greenhouse": ("https://boards-api.greenhouse.io/v1/boards/{t}/jobs", {"content": "false"}),
        "lever": ("https://api.lever.co/v0/postings/{t}", {"mode": "json"}),
        "ashby": ("https://api.ashbyhq.com/posting-api/job-board/{t}", {}),
        "smartrecruiters": ("https://api.smartrecruiters.com/v1/companies/{t}/postings", {"limit": 1}),
    }
    verified: dict[str, list[str]] = {}
    for ats, tokens in (cfg.boards or {}).items():
        url_tpl, params = endpoints.get(ats, (None, None))
        if not url_tpl:
            verified[ats] = tokens
            continue
        alive = []
        for token in tokens:
            payload = get_json(url_tpl.format(t=token), params=params, retries=0)
            ok = bool(payload) and (
                isinstance(payload, list) or payload.get("jobs") is not None
                or payload.get("content") is not None
            )
            print(f"  {'ok  ' if ok else 'dead'}  {ats}/{token}")
            if ok:
                alive.append(token)
        verified[ats] = alive
        print(f"{ats}: {len(alive)}/{len(tokens)} live")
    checked = sum(len(v) for v in (cfg.boards or {}).values())
    alive_total = sum(len(v) for v in verified.values())
    # A network problem makes every board look dead. Writing that result would
    # leave the agent finding nothing, day after day, with no visible error.
    if checked and alive_total < max(1, checked // 5):
        print(f"\nonly {alive_total} of {checked} boards answered. That looks like a "
              f"network problem, not {checked - alive_total} dead companies.")
        print("nothing written. Check your connection and run this again.")
        return 1

    out = CONFIG_DIR / "boards.verified.yaml"
    out.write_text(yaml.safe_dump(verified, sort_keys=True), encoding="utf-8")
    print(f"\nwrote {out} with {alive_total} live boards. "
          f"It takes precedence over boards.yaml from now on.")


def cmd_doctor(cfg_or_error, args):
    """Check every dependency before you rely on this at 7am."""
    import os
    import shutil

    ok = True

    def check(label, passed, hint="", fatal=True):
        """fatal=False means it is worth knowing but will not stop a run."""
        nonlocal ok
        if not passed and fatal:
            ok = False
        print(f"  {'PASS' if passed else ('FAIL' if fatal else 'NOTE')}  {label}")
        if not passed and hint:
            print(f"        {hint}")

    print("\nconfig")
    if isinstance(cfg_or_error, str):
        check("config files", False, cfg_or_error)
        return
    cfg = cfg_or_error
    check("config/config.yaml", True)
    missing = cfg.profile.missing_required()
    check("profile.yaml required fields", not missing,
          f"still to fill: {', '.join(missing)}" if missing else "")
    try:
        path = cfg.profile.resume_path
        check(f"resume file ({path.name})", True)
        from . import resume as resume_mod
        text = resume_mod.load(path, DATA_DIR)
        check("resume text extraction", len(text) > 300,
              f"only got {len(text)} characters, is the pdf a scan?")
    except Exception as exc:
        check("resume file", False, str(exc))

    print("\npython packages")
    for module, hint in [("anthropic", "pip install anthropic"),
                         ("playwright", "pip install playwright"),
                         ("flask", "pip install flask"),
                         ("yaml", "pip install PyYAML"),
                         ("requests", "pip install requests")]:
        try:
            __import__(module)
            check(module, True)
        except ImportError:
            check(module, False, hint)

    print("\nbrowser")
    from .fill.browser import find_chrome, port_open
    chrome = find_chrome(cfg.chrome_binary)
    check("chrome binary", bool(chrome), "install Chrome or set browser.chrome_binary")
    check(f"debugging port {cfg.cdp_port}", port_open(cfg.cdp_port),
          "not running yet. `python -m jobagent chrome` starts it", fatal=False)

    print("\nclaude api")
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
    profile_ok = False
    if not has_key and shutil.which("ant"):
        import subprocess
        try:
            res = subprocess.run(["ant", "auth", "status"], capture_output=True, text=True, timeout=10)
            profile_ok = res.returncode == 0 and "active" in res.stdout.lower()
        except Exception:
            profile_ok = False
    check("credentials", has_key or profile_ok,
          "export ANTHROPIC_API_KEY, or run `ant auth login`")
    check(f"model set to {cfg.model}", bool(cfg.model))

    print("\nurl readers")
    check(f"configured: {', '.join(cfg.readers)}", bool(cfg.readers),
          "set sources.readers in config.yaml")
    if "jina" not in cfg.readers:
        check("jina reader enabled", False,
              "LinkedIn and Workday links need it. Add 'jina' to sources.readers",
              fatal=False)
    if cfg.reader_command:
        check("reader_command set", "{url}" in cfg.reader_command,
              "the template must contain {url}")

    print("\nboards")
    total = sum(len(v) for v in (cfg.boards or {}).values())
    check(f"{total} board tokens configured", total > 0, "config/boards.yaml is empty")
    verified = CONFIG_DIR / "boards.verified.yaml"
    check("boards verified", verified.exists(),
          "run `python -m jobagent verify-boards` to drop dead tokens", fatal=False)

    print(f"\n{'everything looks ready' if ok else 'fix the FAIL lines above'}\n")


def cmd_read(cfg, args):
    """Try every configured reader against one url and show what each returns.

    Reading a posting is the part most likely to break, and it breaks per site,
    so this exists to tell you which reader works for a url before you rely on it.
    """
    from .sources import manual, readers

    chain = readers.build_chain(cfg.readers, cfg.reader_command)
    if not chain:
        print("no readers configured. Set sources.readers in config.yaml")
        return 1

    print(f"\n{args.url}\n")
    winner = None
    for reader in chain:
        result = reader.read(args.url)
        if result is None:
            print(f"  {reader.name:<8} no result")
            continue
        mark = "ok  " if result.useful else "thin"
        print(f"  {reader.name:<8} {mark} {len(result.text):>6} chars   {result.title[:60]}")
        if result.useful and winner is None:
            winner = result

    if winner is None:
        print("\nNothing usable. If this is LinkedIn or Workday, make sure 'jina' is in\n"
              "sources.readers. For anything Agent Reach handles, set sources.reader_command\n"
              'to a shell template such as: "curl -s https://r.jina.ai/{url}"')
        return 1

    job = manual.fetch_url(args.url, chain)
    if job:
        print(f"\n  parsed title   {job.title}")
        print(f"  parsed company {job.company}")
        print(f"  detected ats   {job.ats}")
    if args.show:
        print("\n" + "-" * 70)
        print(winner.text[:args.show])
    return 0


def cmd_export(cfg, args):
    import csv

    store = pipeline.get_store(cfg)
    try:
        rows = store.queue()
    finally:
        store.close()
    out = Path(args.out)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["date", "company", "title", "location", "score",
                         "status", "url", "notes"])
        for row in rows:
            writer.writerow([
                (row["queued_at"] or "")[:10], row["company"], row["title"],
                row["location"], row["score"], row["status"], row["url"],
                row["notes"] or "",
            ])
    print(f"wrote {out} ({len(rows)} rows)")


# --------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobagent",
        description="Find product management jobs posted in the last few days, "
                    "score them against your resume, and prefill the applications.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--config", type=Path, help="path to config.yaml")
    parser.add_argument("--profile", type=Path, help="path to profile.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check that everything is set up").set_defaults(fn=cmd_doctor)
    sub.add_parser("chrome", help="start the agent's Chrome so you can log in").set_defaults(fn=cmd_chrome)
    sub.add_parser("verify-boards", help="drop dead board tokens").set_defaults(fn=cmd_verify_boards)
    sub.add_parser("source", help="pull postings from every board").set_defaults(fn=cmd_source)
    sub.add_parser("queue", help="pick today's shortlist").set_defaults(fn=cmd_queue)
    sub.add_parser("prep", help="write cover letters and answers").set_defaults(fn=cmd_prep)
    sub.add_parser("review", help="open the review dashboard").set_defaults(fn=cmd_review)
    sub.add_parser("status", help="show what is in the pipeline").set_defaults(fn=cmd_status)

    p_score = sub.add_parser("score", help="score unscored postings")
    p_score.add_argument("--offline", action="store_true",
                         help="keyword fallback, no api calls")
    p_score.set_defaults(fn=cmd_score)

    p_prefill = sub.add_parser("prefill", help="fill the forms in Chrome, never submit")
    p_prefill.add_argument("job_id", nargs="*", help="specific job ids, default is the whole queue")
    p_prefill.set_defaults(fn=cmd_prefill)

    p_run = sub.add_parser("run", help="source, score, queue, prep and prefill")
    p_run.add_argument("--offline", action="store_true")
    p_run.add_argument("--no-prefill", action="store_true",
                       help="stop after prep, prefill later from the dashboard")
    p_run.set_defaults(fn=cmd_run)

    p_read = sub.add_parser("read", help="test the url readers against one posting")
    p_read.add_argument("url")
    p_read.add_argument("--show", type=int, metavar="N", default=0,
                        help="also print the first N characters of the text")
    p_read.set_defaults(fn=cmd_read)

    p_export = sub.add_parser("export", help="write the application log to csv")
    p_export.add_argument("--out", default="applications.csv")
    p_export.set_defaults(fn=cmd_export)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)
    try:
        cfg = load(args.config, args.profile)
    except ConfigError as exc:
        if args.command == "doctor":
            cmd_doctor(str(exc), args)
            return 1
        print(f"config problem: {exc}", file=sys.stderr)
        return 1
    # A blank work authorization or sponsorship answer is worse than no
    # application at all, so the stages that produce one refuse to start.
    if args.command in {"score", "prep", "prefill", "run"}:
        missing = cfg.profile.missing_required()
        if missing:
            print("profile.yaml is not finished. Still to fill:", file=sys.stderr)
            for item in missing:
                print(f"  {item}", file=sys.stderr)
            print("\nRun `python -m jobagent doctor` for the full check.", file=sys.stderr)
            return 1

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        return args.fn(cfg, args) or 0
    except KeyboardInterrupt:
        print("\nstopped")
        return 130
    except RuntimeError as exc:
        # Chrome missing, debugging port refused, that class of thing. The
        # message is already written for a human, a traceback is not.
        print(f"\n{exc}", file=sys.stderr)
        return 1
