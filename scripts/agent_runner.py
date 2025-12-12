#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from factory_common.agent_mode import (
    get_queue_dir,
    pending_path,
    results_path,
    write_result,
)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def cmd_list(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    pending_dir = q / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in sorted(pending_dir.glob("*.json")):
        try:
            obj = _load_json(p)
        except Exception:
            obj = {}
        task_id = str(obj.get("id") or p.stem)
        task = str(obj.get("task") or "-")
        created = str(obj.get("created_at") or "-")
        runbook = str(obj.get("runbook_path") or "-")
        status = "ready" if results_path(task_id, queue_dir=q).exists() else "pending"
        rows.append((status, task_id, task, created, runbook))

    if not rows:
        print("(no pending tasks)")
        return 0

    # TSV for easy copy/paste
    print("status\ttask_id\ttask\tcreated_at\trunbook")
    for r in rows:
        print("\t".join(r))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    p = pending_path(args.task_id, queue_dir=q)
    if not p.exists():
        print(f"pending not found: {p}", file=sys.stderr)
        return 2
    obj = _load_json(p)
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    return 0


def cmd_complete(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    p = pending_path(args.task_id, queue_dir=q)
    if not p.exists():
        print(f"pending not found: {p}", file=sys.stderr)
        return 2
    pending_obj = _load_json(p)
    task = str(pending_obj.get("task") or "")
    if not task:
        print("pending missing task name", file=sys.stderr)
        return 2

    if args.content_file:
        content = Path(args.content_file).read_text(encoding="utf-8")
    else:
        content = sys.stdin.read()
    if content is None:
        content = ""

    if not args.no_validate:
        fmt = pending_obj.get("response_format")
        if fmt == "json_object":
            try:
                parsed = json.loads(content)
            except Exception as e:
                print(f"content is not valid JSON (response_format=json_object): {e}", file=sys.stderr)
                return 2
            if not isinstance(parsed, dict):
                print("content JSON must be an object (top-level dict) for response_format=json_object", file=sys.stderr)
                return 2

    result_path = write_result(
        task_id=args.task_id,
        task=task,
        content=content,
        notes=args.notes,
        queue_dir=q,
        move_pending=not args.keep_pending,
    )
    print(str(result_path))
    return 0


def cmd_prompt(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    p = pending_path(args.task_id, queue_dir=q)
    if not p.exists():
        print(f"pending not found: {p}", file=sys.stderr)
        return 2
    obj = _load_json(p)

    task_id = str(obj.get("id") or args.task_id)
    task = str(obj.get("task") or "-")
    runbook = str(obj.get("runbook_path") or "-")
    response_format = obj.get("response_format")
    caller = obj.get("caller") or {}
    result_path = str(obj.get("result_path") or results_path(task_id, queue_dir=q))

    print("AGENT_TASK")
    print(f"- id: {task_id}")
    print(f"- task: {task}")
    print(f"- response_format: {response_format}")
    print(f"- runbook: {runbook}")
    if isinstance(caller, dict) and caller:
        print(f"- caller: {caller.get('file')}:{caller.get('line')} ({caller.get('function')})")
    print(f"- expected_result: {result_path}")
    print("")
    print("INSTRUCTIONS")
    print("- Produce ONLY the required output content.")
    if response_format == "json_object":
        print("- IMPORTANT: Output MUST be a single JSON object (no extra text).")
    print("- Save content to a file, then run:")
    print(f"  python scripts/agent_runner.py complete {task_id} --content-file /path/to/content.txt")
    print("- Then rerun the original pipeline command to continue.")
    print("")
    print("MESSAGES")
    for m in obj.get("messages") or []:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "")
        content = str(m.get("content") or "")
        print(f"----- role: {role} -----")
        print(content)
        print("")
    return 0


def cmd_chat(args: argparse.Namespace) -> int:
    """
    Print a prompt optimized for a chat-only LLM (no terminal/file access).
    Intended to be copy/pasted into an external chat UI.
    """
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    p = pending_path(args.task_id, queue_dir=q)
    if not p.exists():
        print(f"pending not found: {p}", file=sys.stderr)
        return 2
    obj = _load_json(p)

    task_id = str(obj.get("id") or args.task_id)
    task = str(obj.get("task") or "-")
    runbook = str(obj.get("runbook_path") or "-")
    response_format = obj.get("response_format")
    instructions = obj.get("instructions") or {}

    print("あなたはチャットAIです。端末操作やファイル参照はできません。")
    print("以下の TASK の「必要な出力コンテンツだけ」を生成してください。")
    print("")
    print("出力ルール:")
    print("- 余計な前置き/謝罪/提案/質問/メタ説明は禁止（要求がある場合を除く）。")
    if response_format == "json_object":
        print('- response_format=json_object のため、出力は「単一のJSONオブジェクト」だけ（コードフェンス禁止）。')
    print("- 指定されたMarkdown/JSON構造・終端トークン（例: [[END]]）があれば厳守。")
    print("")
    print("TASK:")
    print(f"- id: {task_id}")
    print(f"- task: {task}")
    print(f"- response_format: {response_format}")
    print(f"- runbook_path: {runbook}")
    if isinstance(instructions, dict) and instructions:
        print("")
        print("補足（運用）:")
        for k in ("what_to_do", "notes"):
            v = instructions.get(k)
            if v:
                print(f"- {k}: {v}")
    print("")
    print("messages（これが全コンテキストです。ここだけを根拠に生成してください）:")
    for m in obj.get("messages") or []:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "")
        content = str(m.get("content") or "")
        print("")
        print(f"[{role}]")
        print(content)
    print("")
    print("あなたの返答は「生成した出力コンテンツ」だけにしてください。")
    return 0


def cmd_bundle(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    p = pending_path(args.task_id, queue_dir=q)
    if not p.exists():
        print(f"pending not found: {p}", file=sys.stderr)
        return 2
    obj = _load_json(p)

    task_id = str(obj.get("id") or args.task_id)
    task = str(obj.get("task") or "-")
    runbook = str(obj.get("runbook_path") or "-")
    response_format = obj.get("response_format")
    caller = obj.get("caller") or {}
    invocation = obj.get("invocation") or {}
    expected_result = str(obj.get("result_path") or results_path(task_id, queue_dir=q))
    instructions = obj.get("instructions") or {}

    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = q / out_path
    else:
        out_path = q / "bundles" / f"{task_id}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append("# Agent Task Bundle")
    lines.append("")
    lines.append(f"- id: `{task_id}`")
    lines.append(f"- task: `{task}`")
    lines.append(f"- response_format: `{response_format}`")
    lines.append(f"- pending: `{p}`")
    lines.append(f"- expected_result: `{expected_result}`")
    lines.append(f"- runbook: `{runbook}`")
    if isinstance(caller, dict) and caller:
        lines.append(f"- caller: `{caller.get('file')}:{caller.get('line')} ({caller.get('function')})`")
    if isinstance(invocation, dict) and invocation:
        cwd = invocation.get("cwd")
        argv = invocation.get("argv")
        if cwd:
            lines.append(f"- invocation.cwd: `{cwd}`")
        if argv:
            try:
                joined = " ".join(str(x) for x in argv)
            except Exception:
                joined = str(argv)
            lines.append(f"- invocation.argv: `{joined}`")
    lines.append("")

    if args.include_runbook and runbook and runbook != "-":
        rb_path = Path(runbook)
        if not rb_path.is_absolute():
            rb_path = (Path(__file__).resolve().parents[1] / rb_path).resolve()
        if rb_path.exists():
            try:
                lines.append("## Runbook")
                lines.append("")
                lines.append(f"source: `{runbook}`")
                lines.append("")
                lines.append("```")
                lines.append(rb_path.read_text(encoding="utf-8").rstrip())
                lines.append("```")
                lines.append("")
            except Exception:
                pass

    lines.append("## Messages")
    lines.append("")
    for m in obj.get("messages") or []:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "")
        content = str(m.get("content") or "")
        lines.append(f"### role: {role}")
        lines.append("")
        lines.append("```")
        lines.append(content.rstrip())
        lines.append("```")
        lines.append("")

    if isinstance(instructions, dict) and instructions:
        lines.append("## Instructions (for operators/agents)")
        lines.append("")
        for k, v in instructions.items():
            if v is None or v == "":
                continue
            lines.append(f"- {k}: {v}")
        lines.append("")

    lines.append("## Complete")
    lines.append("")
    lines.append("1) Write ONLY the required output content to a file.")
    lines.append(f"2) Run: `python scripts/agent_runner.py complete {task_id} --content-file /path/to/content.txt`")
    lines.append("3) Rerun the original pipeline command to continue.")
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(str(out_path))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Agent-mode task queue helper")
    p.add_argument("--queue-dir", default=None, help="override queue dir (default: env/ logs/agent_tasks)")

    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("list", help="list pending tasks")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("show", help="show pending task json")
    sp.add_argument("task_id")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("prompt", help="print a copy/paste-friendly prompt for an AI agent")
    sp.add_argument("task_id")
    sp.set_defaults(func=cmd_prompt)

    sp = sub.add_parser("chat", help="print a prompt for an external chat AI (no terminal access)")
    sp.add_argument("task_id")
    sp.set_defaults(func=cmd_chat)

    sp = sub.add_parser("bundle", help="write a markdown bundle file for an AI agent")
    sp.add_argument("task_id")
    sp.add_argument("--out", default=None, help="output path (default: logs/agent_tasks/bundles/<id>.md)")
    sp.add_argument("--include-runbook", action="store_true", help="embed runbook content into the bundle")
    sp.set_defaults(func=cmd_bundle)

    sp = sub.add_parser("complete", help="write results json and (optionally) move pending to completed/")
    sp.add_argument("task_id")
    sp.add_argument("--content-file", default=None, help="file containing response content (utf-8). If omitted, read stdin.")
    sp.add_argument("--notes", default=None, help="optional notes to store in results")
    sp.add_argument("--keep-pending", action="store_true", help="do not move pending to completed/")
    sp.add_argument("--no-validate", action="store_true", help="skip validation (default validates json_object tasks)")
    sp.set_defaults(func=cmd_complete)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

