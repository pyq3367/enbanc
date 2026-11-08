"""Command line entry point: python -m enbanc <command>."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path

from .arbiter import escalate, review
from .dispatch import Task, dispatch
from .mandate import Mandate
from .providers import CliProvider, MockProvider
from .route import route
from .state import Run
from .verifiers import default_verifiers


def _root(args) -> Path:
    return Path(args.root or os.environ.get("ENBANC_ROOT", "runs"))


def _factory(args):
    if args.provider == "mock":
        quality = 1.0 if args.quality >= 0.5 else 0.3

        def make(role: str):
            return MockProvider(quality=quality, seed=role)

        return make

    if args.provider != "cli":
        raise SystemExit("不支持该 provider；账户登录与模型权限需要用户人工确认，未调用模型")

    template = shlex.split(args.provider_cli or "")
    if not template:
        raise SystemExit("--provider cli requires --provider-cli")

    def make(role: str):
        return CliProvider(template, name=f"cli:{role}")

    return make


def cmd_init(a):
    run = Run.init(a.run_id, Path(a.requirement_file).read_text(encoding="utf-8")
                   if a.requirement_file else a.requirement, _root(a), a.main_model or "")
    print(run.render())
    print(f"frozen at {run.requirement_path}")
    return 0


def cmd_render(a):
    run = Run.load(a.run_id, _root(a))
    print(run.render())
    return 0


def cmd_collect(a):
    run = Run.load(a.run_id, _root(a))
    changes = run.collect()
    for c in changes:
        print("  " + c)
    if not changes:
        print("already consistent")
    print(run.render())
    return 0


def cmd_stage(a):
    run = Run.load(a.run_id, _root(a))
    try:
        run.set_stage(a.set, force=a.force, reason=a.reason or "",
                      accept_residual=bool(getattr(a, "accept_residual", False)))
    except (PermissionError, ValueError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    print(run.render())
    if run.state.get("outcome") == "stopped_by_user":
        print(f"⚠ delivered as stopped_by_user: {run.state.get('outcome_reason')}")
    if run.state.get("outcome") == "accepted_with_residual":
        print("⚠ delivered with %d accepted finding(s) — list them in the delivery note"
              % len(run.state.get("outcome_residual") or []))
    if a.set == "P5" and run.is_passed():
        last = run.last_review() or {}
        open_items = len(last.get("issues") or [])
        if open_items:
            print(f"⚠ pass with {open_items} open finding(s) — list them in the delivery note")
    return 0


def cmd_queue(a):
    run = Run.load(a.run_id, _root(a))
    q = run.queue()
    if not q["tasks"]:
        print("queue is empty")
        return 0
    for tid, t in sorted(q["tasks"].items()):
        print(f"  {tid:<14} {t.get('status','?'):<12} round={t.get('round','?')}")
    return 0


def cmd_dispatch(a):
    run = Run.load(a.run_id, _root(a))
    spec = json.loads(Path(a.tasks).read_text(encoding="utf-8"))
    tasks = [Task(**{k: v for k, v in t.items() if k in Task.__dataclass_fields__}) for t in spec]
    results = dispatch(run, tasks, _factory(a), max_parallel=a.max_parallel,
                       force=a.force, resume=a.resume)
    for r in results:
        print(f"  {r['id']:<14} {r['status']}")
    print(run.render())
    return 0 if all(r["status"] in ("done", "degraded", "skipped") for r in results) else 2


def cmd_review(a):
    run = Run.load(a.run_id, _root(a))
    entries = []
    for tid, t in (run.state.get("tasks") or {}).items():
        art = t.get("artifact")
        if art:
            entries.append((tid, run.dir / art))
    if not entries:
        print("no artifacts to review; run dispatch first", file=sys.stderr)
        return 1
    criteria = run.state.get("criteria") or []
    record = review(run, entries, _factory(a), default_verifiers(), criteria, a.round)
    print(f"round {record['round']}: {record['verdict']} ({len(record['issues'])} findings)")
    for i in record["issues"]:
        print(f"  [{i['severity']}/{i['type']}] {i['where']}: {i['detail'][:90]}")
    print(run.render())
    return 0 if record["verdict"] == "pass" else 1


def cmd_escalate(a):
    run = Run.load(a.run_id, _root(a))
    chain = json.loads(Path(a.chain).read_text(encoding="utf-8"))
    res = escalate(run, chain)
    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("escalated") else 3


def cmd_mandate(a):
    run = Run.load(a.run_id, _root(a))
    m = Mandate(run)
    if a.action == "init":
        data = m.init(allow=a.allow, deny=a.deny, acceptance=a.acceptance, cwd=a.cwd)
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return 0
    if a.action == "check":
        changed = None
        if a.changed_file:
            changed = [x.strip() for x in Path(a.changed_file).read_text(encoding="utf-8").splitlines() if x.strip()]
        res = m.check(changed)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0 if res.get("ok") else 3
    print(m.load() or {"mandate": None})
    return 0


def cmd_route(a):
    run = Run.load(a.run_id, _root(a))
    res = route(run, a.round)
    print(f"routed {res['routed']}/{res['total']}: {res['by_owner']}")
    for u in res["unrouted"]:
        print(f"  unrouted: {u['issue'].get('where')}")
    return 0 if not res["unrouted"] else 2


def cmd_doctor(a):
    from .doctor import run_offline

    return run_offline()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="enbanc", description="long-haul verification for high-value artifacts")
    ap.add_argument("--root", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, **kwargs):
        p = sub.add_parser(name, **kwargs)
        p.set_defaults(fn=fn)
        return p

    p = add("doctor", cmd_doctor, help="离线便携性检查，不验证账户登录或模型权限")
    p.add_argument("--offline", action="store_true", default=True,
                   help="仅离线检查（默认），不读取认证配置或执行账户 CLI")

    p = add("init", cmd_init)
    p.add_argument("--run-id", required=True)
    p.add_argument("--requirement", default="")
    p.add_argument("--requirement-file")
    p.add_argument("--main-model", default="")

    p = add("render", cmd_render)
    p.add_argument("--run-id", required=True)

    p = add("collect", cmd_collect)
    p.add_argument("--run-id", required=True)

    p = add("stage", cmd_stage)
    p.add_argument("--run-id", required=True)
    p.add_argument("--set", required=True)
    p.add_argument("--force", action="store_true")
    p.add_argument("--reason", default="")
    p.add_argument("--accept-residual", action="store_true", dest="accept_residual",
                   help="allow delivery when the last verdict is conditional_pass")

    p = add("queue", cmd_queue)
    p.add_argument("--run-id", required=True)

    for name, fn in (("dispatch", cmd_dispatch), ("review", cmd_review)):
        p = add(name, fn)
        p.add_argument("--run-id", required=True)
        p.add_argument("--provider", choices=["mock", "cli"], default="mock",
                       help="默认 mock 离线；cli 为用户显式命令，未实现 workbuddy 账户适配")
        p.add_argument("--provider-cli", default="")
        p.add_argument("--quality", type=float, default=1.0)

    p = sub.choices["dispatch"]
    p.add_argument("--tasks", required=True)
    p.add_argument("--max-parallel", type=int, default=2)
    p.add_argument("--force", action="store_true")
    p.add_argument("--resume", action="store_true")

    sub.choices["review"].add_argument("--round", type=int)

    p = add("escalate", cmd_escalate)
    p.add_argument("--run-id", required=True)
    p.add_argument("--chain", required=True, help="JSON file: [{name: judge-1}, ...]")

    p = add("mandate", cmd_mandate)
    p.add_argument("--run-id", required=True)
    p.add_argument("action", choices=["init", "check", "show"])
    p.add_argument("--allow", action="append")
    p.add_argument("--deny", action="append")
    p.add_argument("--acceptance", action="append")
    p.add_argument("--cwd")
    p.add_argument("--changed-file")

    p = add("route", cmd_route)
    p.add_argument("--run-id", required=True)
    p.add_argument("--round", type=int)

    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
