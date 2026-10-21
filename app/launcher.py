"""Local launcher: fixed origin and instance-safe startup."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
import webbrowser


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Invalid local port")
    app = Path(__file__).resolve().parent
    root = app.parent
    if (root / "BUILDING.json").exists():
        raise RuntimeError("Release build is incomplete")
    base = f"http://127.0.0.1:{args.port}"
    identity = hashlib.sha256(str(app).encode("utf-8")).hexdigest()[:24]

    def status():
        try:
            with urlopen(base + "/api/status", timeout=2) as response:
                return json.load(response)
        except (URLError, OSError):
            return None

    def check(value):
        if value and (value.get("app") != "fkg-reader" or value.get("instanceId") != identity):
            raise RuntimeError("Port is used by another reader/service. Stop that instance before starting this copy.")

    current = status()
    check(current)
    if args.stop:
        if current:
            with urlopen(Request(base + "/api/shutdown", data=b"{}", headers={"Content-Type": "application/json"}), timeout=5) as response:
                json.load(response)
        print("Reader stopped." if current else "Reader is not running.")
        return
    if not current:
        required = ("manifest.json", "catalog/display-titles.json", "catalog/script-compat-profiles.json")
        if any(not (root / "content" / name).is_file() for name in required):
            raise RuntimeError("Local content is missing or incomplete. See README.md and check content/manifest.json. The program does not include story data.")
        logs = root / "userdata/logs"
        logs.mkdir(parents=True, exist_ok=True)
        flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if sys.platform == "win32" else 0
        with (logs / "reader.log").open("ab") as out, (logs / "reader.err.log").open("ab") as err:
            process = subprocess.Popen([sys.executable, "-X", "utf8", "-E", "-s", "-S", "-B", "-u", str(app / "server.py"), "--port", str(args.port)],
                cwd=app, stdin=subprocess.DEVNULL, stdout=out, stderr=err, creationflags=flags)
        print("Starting local reader...", flush=True)
        for attempt in range(120):
            current = status()
            check(current)
            if current:
                break
            if process.poll() is not None:
                raise RuntimeError("Reader failed to start. See userdata/logs/reader.err.log")
            time.sleep(0.5)
        else:
            raise RuntimeError("Startup is taking longer than expected. See userdata/logs before trying again.")
    print(base)
    if not args.no_browser:
        webbrowser.open(base)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
