"""Create, inspect, and repair a local AI application incident lab."""

from __future__ import annotations

import argparse
import json
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


MARKER = "ai-ops-lab-v1"
MODEL_ID = "demo-qwen"
CASES = ("model_unreachable", "wrong_model_id")
ACTIONS = ("fix_model_url", "fix_model_id")


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def checked_root(raw: str) -> Path:
    root = Path(raw).expanduser().resolve(strict=True)
    if not root.is_dir() or (root / ".ai-ops-lab").read_text(encoding="utf-8").strip() != MARKER:
        raise ValueError("This is not an ai-ops-triage lab directory")
    return root


def available_ports(count: int) -> list[int]:
    sockets = []
    try:
        for _ in range(count):
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            sockets.append(sock)
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def create(raw: str, case: str) -> dict:
    root = Path(raw).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError("Target directory must be empty")
    root.mkdir(parents=True, exist_ok=True)
    app_port, model_port, wrong_port = available_ports(3)
    inventory = {
        "case": case,
        "app_url": f"http://127.0.0.1:{app_port}",
        "model_url": f"http://127.0.0.1:{model_port}",
        "model_id": MODEL_ID,
        "lab_only": True,
    }
    config = {
        "model_url": inventory["model_url"] if case == "wrong_model_id" else f"http://127.0.0.1:{wrong_port}",
        "model_id": "missing-model" if case == "wrong_model_id" else MODEL_ID,
    }
    (root / ".ai-ops-lab").write_text(MARKER + "\n", encoding="utf-8")
    write_json(root / "inventory.json", inventory)
    write_json(root / "config.json", config)
    (root / "app.log").touch()
    return {"root": str(root), "case": case, "app_url": inventory["app_url"]}


def probe(url: str) -> dict:
    try:
        with urlopen(url, timeout=2) as response:
            return {"reachable": True, "status": response.status, "body": json.load(response)}
    except HTTPError as error:
        try:
            body = json.load(error)
        except (ValueError, OSError):
            body = {"error": error.reason}
        return {"reachable": True, "status": error.code, "body": body}
    except (URLError, TimeoutError, OSError, ValueError) as error:
        return {"reachable": False, "status": None, "error": str(error)}


def inspect(raw: str) -> dict:
    root = checked_root(raw)
    config = read_json(root / "config.json")
    inventory = read_json(root / "inventory.json")
    application = probe(inventory["app_url"] + "/health")
    configured_model = probe(config["model_url"] + "/v1/models")
    inventory_model = probe(inventory["model_url"] + "/v1/models")
    lines = (root / "app.log").read_text(encoding="utf-8").splitlines()
    return {
        "config": config,
        "inventory": inventory,
        "application": application,
        "configured_model": configured_model,
        "inventory_model": inventory_model,
        "recent_logs": lines[-5:],
    }


def apply(raw: str, action: str) -> dict:
    root = checked_root(raw)
    config = read_json(root / "config.json")
    inventory = read_json(root / "inventory.json")
    field = "model_url" if action == "fix_model_url" else "model_id"
    before = config[field]
    config[field] = inventory[field]
    write_json(root / "config.json", config)
    record = {
        "time": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "field": field,
        "before": before,
        "after": config[field],
    }
    with (root / "actions.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create_parser = sub.add_parser("create")
    create_parser.add_argument("--root", required=True)
    create_parser.add_argument("--case", choices=CASES, required=True)
    inspect_parser = sub.add_parser("inspect")
    inspect_parser.add_argument("--root", required=True)
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--root", required=True)
    apply_parser.add_argument("--action", choices=ACTIONS, required=True)
    args = parser.parse_args()
    try:
        if args.command == "create":
            result = create(args.root, args.case)
        elif args.command == "inspect":
            result = inspect(args.root)
        else:
            result = apply(args.root, args.action)
    except (ValueError, FileNotFoundError, NotADirectoryError, KeyError, json.JSONDecodeError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
