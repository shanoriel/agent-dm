from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request

DEFAULT_GATEWAY = "http://api.junshanhuang.com:11451"
MAX_WAIT_ITERATIONS = 60  # ~20 min at 20s per poll
HTTP_TIMEOUT = 25
ACTIVE_PID_FILE: str | None = None


def _normalize_gateway(value: str) -> str:
    if "://" in value:
        return value.rstrip("/")
    return f"http://{value}".rstrip("/")


def _gateway() -> str:
    return _normalize_gateway(os.getenv("AGENT_DM_GATEWAY", DEFAULT_GATEWAY))


def _state_dir() -> str:
    configured = os.getenv("AGENT_DM_STATE_DIR")
    if configured:
        return os.path.expanduser(configured)
    return os.path.abspath(".agent_dm")


def _pid_file(token: str) -> str:
    directory = _state_dir()
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, f"{token}.pid")


def _save_pid(token: str, pid: str) -> str:
    path = _pid_file(token)
    with open(path, "w", encoding="utf-8") as file_obj:
        file_obj.write(pid)
    return path


def _load_pid(token: str) -> tuple[str, str]:
    path = _pid_file(token)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as file_obj:
            return file_obj.read().strip(), path
    print(f"No session found for token '{token}'. Run check first.", file=sys.stderr)
    sys.exit(2)


def _clear_pid_file(path: str | None) -> None:
    if path and os.path.exists(path):
        os.remove(path)


def _install_signal_cleanup(pid_path: str) -> None:
    global ACTIVE_PID_FILE
    ACTIVE_PID_FILE = pid_path

    def _handle_signal(signum: int, _frame: object) -> None:
        _clear_pid_file(ACTIVE_PID_FILE)
        sys.exit(128 + signum)

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)


def _disable_signal_cleanup() -> None:
    global ACTIVE_PID_FILE
    ACTIVE_PID_FILE = None


def _request(method: str, path: str, data: dict | None = None, headers: dict | None = None) -> dict:
    url = f"{_gateway()}{path}"
    body = json.dumps(data).encode() if data else None
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read()).get("detail", "")
        except Exception:
            pass
        raise SystemExit(f"HTTP {exc.code}: {detail or exc.reason}") from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"Connection error: {exc.reason}") from None


def _pid_header(pid: str) -> dict:
    return {"X-Participant-Id": pid}


def _print_json(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False))


def _wait_for_reply(pid: str, pid_path: str) -> None:
    for attempt in range(MAX_WAIT_ITERATIONS):
        try:
            response = _request("GET", "/wait", headers=_pid_header(pid))
        except SystemExit as exc:
            if attempt < MAX_WAIT_ITERATIONS - 1:
                print(f"[wait] error: {exc}, retrying...", file=sys.stderr)
                time.sleep(2)
                continue
            raise

        status = response["status"]
        if status == "message":
            _print_json({"message": response["message"], "from": response["from"]})
            _disable_signal_cleanup()
            return
        if status == "closed":
            _clear_pid_file(pid_path)
            _disable_signal_cleanup()
            print("[closed] session ended by partner", file=sys.stderr)
            sys.exit(1)
        if status == "timeout":
            continue

        _disable_signal_cleanup()
        print(f"[wait] unexpected status: {status}", file=sys.stderr)
        sys.exit(2)

    _disable_signal_cleanup()
    print("[timeout] gave up waiting after too many iterations", file=sys.stderr)
    sys.exit(2)


def _command_check(token: str) -> None:
    response = _request("POST", "/check", {"token": token})
    _save_pid(token, response["participant_id"])
    _print_json({"role": response["role"], "message": response.get("message")})


def _command_send(token: str, message: str) -> None:
    pid, pid_path = _load_pid(token)
    _install_signal_cleanup(pid_path)
    response = _request("POST", "/send", {"message": message}, headers=_pid_header(pid))
    if response.get("status") == "blocked":
        _disable_signal_cleanup()
        _print_json(
            {
                "status": "blocked",
                "message": response.get("message"),
                "from": response.get("from"),
                "reason": response.get("reason"),
                "next_action": "Read the pending message first, then send a new reply if needed.",
            }
        )
        return
    print("[sent] waiting for reply...", file=sys.stderr)
    _wait_for_reply(pid, pid_path)


def _command_close(token: str) -> None:
    pid, pid_path = _load_pid(token)
    _request("POST", "/exit", headers=_pid_header(pid))
    _clear_pid_file(pid_path)
    print("[exit] closed", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agent Direct Message CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="Join a token channel and read any pending message")
    check_parser.add_argument("--token", required=True)

    send_parser = subparsers.add_parser("send", help="Send a message and wait for the reply")
    send_parser.add_argument("--token", required=True)
    send_parser.add_argument("--message", required=True)

    close_parser = subparsers.add_parser("close", help="Close the current session")
    close_parser.add_argument("--token", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "check":
        _command_check(args.token)
        return 0
    if args.command == "send":
        _command_send(args.token, args.message)
        return 0
    if args.command == "close":
        _command_close(args.token)
        return 0

    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
