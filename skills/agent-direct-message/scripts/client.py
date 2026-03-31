#!/usr/bin/env python3
"""AgentDirectMessage client — stdlib-only.

  # Check: join a token channel and read any pending message
  python3 client.py --token X --check

  # Send: deposit a message and wait for the other agent's reply
  python3 client.py --token X --message "Hello"

  # Exit: close the session
  python3 client.py --token X --exit

Participant ID is managed automatically via /tmp/.agent_dm_{token}.pid
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

DEFAULT_GATEWAY = "http://api.junshanhuang.com:11451"
MAX_WAIT_ITERATIONS = 60  # ~20 min at 20s per poll
HTTP_TIMEOUT = 25


def _normalize_gateway(value: str) -> str:
    if "://" in value:
        return value.rstrip("/")
    return f"http://{value}".rstrip("/")


GATEWAY = _normalize_gateway(os.getenv("AGENT_DM_GATEWAY", DEFAULT_GATEWAY))


def _pid_file(token: str) -> str:
    return f"/tmp/.agent_dm_{token}.pid"


def _save_pid(token: str, pid: str) -> None:
    with open(_pid_file(token), "w") as f:
        f.write(pid)


def _load_pid(token: str) -> str:
    path = _pid_file(token)
    if not os.path.exists(path):
        print(f"No session found for token '{token}'. Run --check first.", file=sys.stderr)
        sys.exit(2)
    with open(path) as f:
        return f.read().strip()


def _clear_pid(token: str) -> None:
    path = _pid_file(token)
    if os.path.exists(path):
        os.remove(path)


def _request(method: str, path: str, data: dict | None = None, headers: dict | None = None) -> dict:
    url = f"{GATEWAY}{path}"
    body = json.dumps(data).encode() if data else None
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read()).get("detail", "")
        except Exception:
            pass
        raise SystemExit(f"HTTP {e.code}: {detail or e.reason}") from None
    except urllib.error.URLError as e:
        raise SystemExit(f"Connection error: {e.reason}") from None


def _pid_header(pid: str) -> dict:
    return {"X-Participant-Id": pid}


def _wait_for_reply(pid: str) -> None:
    for i in range(MAX_WAIT_ITERATIONS):
        try:
            resp = _request("GET", "/wait", headers=_pid_header(pid))
        except SystemExit as e:
            if i < MAX_WAIT_ITERATIONS - 1:
                print(f"[wait] error: {e}, retrying...", file=sys.stderr)
                time.sleep(2)
                continue
            raise

        st = resp["status"]
        if st == "message":
            print(json.dumps({"message": resp["message"], "from": resp["from"]}))
            return
        if st == "closed":
            print("[closed] session ended by partner", file=sys.stderr)
            sys.exit(1)
        if st == "timeout":
            continue

        print(f"[wait] unexpected status: {st}", file=sys.stderr)
        sys.exit(2)

    print("[timeout] gave up waiting after too many iterations", file=sys.stderr)
    sys.exit(2)


def main() -> None:
    parser = argparse.ArgumentParser(description="AgentDirectMessage client")
    parser.add_argument("--token", required=True)
    parser.add_argument("--check", action="store_true", help="Join and check for pending message")
    parser.add_argument("--message", default=None, help="Message to send")
    parser.add_argument("--exit", action="store_true", help="Close session")
    args = parser.parse_args()

    if args.exit:
        pid = _load_pid(args.token)
        _request("POST", "/exit", headers=_pid_header(pid))
        _clear_pid(args.token)
        print("[exit] closed", file=sys.stderr)
        return

    if args.check:
        resp = _request("POST", "/check", {"token": args.token})
        pid = resp["participant_id"]
        role = resp["role"]
        msg = resp.get("message")
        _save_pid(args.token, pid)
        print(json.dumps({"role": role, "message": msg}))
        return

    if args.message is None:
        print("--message is required when not using --check or --exit", file=sys.stderr)
        sys.exit(2)

    pid = _load_pid(args.token)
    _request("POST", "/send", {"message": args.message}, headers=_pid_header(pid))
    print("[sent] waiting for reply...", file=sys.stderr)
    _wait_for_reply(pid)


if __name__ == "__main__":
    main()
