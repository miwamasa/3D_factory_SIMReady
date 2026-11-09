#!/usr/bin/env python3
"""Loopback-only test bench. Run: python3 server.py --demo"""
import argparse
import hmac
import json
import os
from pathlib import Path
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from engine import Factory
from mcp_protocol import VERSION, dispatch, error
from policy import LabError, require

ROOT = Path(__file__).resolve().parent
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/factory3d/view.css": ("factory3d/view.css", "text/css; charset=utf-8"),
    "/factory3d/line.glb": ("factory3d/line.glb", "model/gltf-binary"),
}
for relative_path in (
    "factory3d/view.js", "factory3d/vendor/three.module.min.js",
    "factory3d/vendor/controls/OrbitControls.js", "factory3d/vendor/loaders/GLTFLoader.js",
    "factory3d/vendor/utils/BufferGeometryUtils.js",
):
    STATIC_FILES["/" + relative_path] = (relative_path, "text/javascript; charset=utf-8")


def load_credentials(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        data = {role: {"id": role + "-01", "role": role, "token": secrets.token_urlsafe(32)}
                for role in ("agent", "supervisor", "planner", "viewer")}
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
    os.chmod(str(path), 0o600)
    data = json.loads(path.read_text())
    for role, item in data.items():
        require(item["role"] == role and len(item["token"]) >= 32, "CONFIG", "資格情報が不正です", 500)
    return data


def make_handler(factory, credentials, demo, port):
    allowed_hosts = {"127.0.0.1:%d" % port, "localhost:%d" % port}
    origins = {"http://" + h for h in allowed_hosts}

    class Handler(BaseHTTPRequestHandler):
        server_version = "MotorFactoryLab/0.1"

        def log_message(self, fmt, *args):
            # Do not log credentials, bodies or query strings.
            pass

        def send(self, status, data=None, content_type="application/json; charset=utf-8"):
            body = b"" if data is None else (data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False, allow_nan=False).encode())
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
            if status == 401:
                self.send_header("WWW-Authenticate", 'Bearer realm="motor-factory-lab"')
            if status == 405:
                self.send_header("Allow", "POST")
            self.end_headers()
            self.wfile.write(body)

        def boundary(self):
            require(self.headers.get("Host") in allowed_hosts, "HOST_DENIED", "Hostが不正です", 403)
            require(not self.headers.get("Origin") or self.headers["Origin"] in origins, "ORIGIN_DENIED", "Originが不正です", 403)

        def authenticate(self):
            authorization = self.headers.get("Authorization", "")
            require(authorization.startswith("Bearer "), "UNAUTHENTICATED", "Bearer認証が必要です", 401)
            token = authorization[7:]
            for identity in credentials.values():
                if hmac.compare_digest(token, identity["token"]):
                    return {"id": identity["id"], "role": identity["role"]}
            raise LabError("UNAUTHENTICATED", "有効なBearerキーが必要です", 401)

        def body(self):
            require(self.headers.get("Content-Type", "").split(";")[0].strip() == "application/json", "CONTENT_TYPE", "application/jsonが必要です", 415)
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise LabError("BODY_SIZE", "Content-Lengthが不正です", 400)
            require(0 < length <= 65536, "BODY_SIZE", "本文のサイズが不正です", 400)
            try:
                data = json.loads(self.rfile.read(length), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            except (ValueError, UnicodeError, RecursionError):
                raise LabError("INVALID_JSON", "JSONを読み取れません", 400)
            require(isinstance(data, dict), "INVALID_JSON", "本文はオブジェクトにしてください", 400)
            return data

        def do_GET(self):
            try:
                self.boundary()
                path = urlsplit(self.path).path
                if path in STATIC_FILES:
                    file_name, mime = STATIC_FILES[path]
                    return self.send(200, (ROOT / "static" / file_name).read_bytes(), mime)
                if path == "/health":
                    return self.send(200, {"ok": True, "version": "0.1.0"})
                if path == "/api/config":
                    return self.send(200, {"demo": demo, "protocol": VERSION})
                if path == "/mcp":
                    self.authenticate()
                    return self.send(405, {"message": "POST only; no protocol session or GET event stream"})
                principal = self.authenticate()
                if path == "/api/whoami":
                    return self.send(200, principal)
                if path == "/api/snapshot":
                    return self.send(200, factory.snapshot())
                if path == "/api/proposals":
                    return self.send(200, {"proposals": factory.proposals(principal)})
                if path == "/api/audit":
                    return self.send(200, {"events": factory.audit()})
                raise LabError("NOT_FOUND", "見つかりません", 404)
            except LabError as err:
                self.send(err.status, err.as_dict())

        def do_POST(self):
            principal = None
            try:
                self.boundary()
                path = urlsplit(self.path).path
                if path == "/api/demo-agent":
                    require(demo, "FORBIDDEN", "デモモードが無効です", 403)
                    self.body()
                    return self.send(200, {"token": credentials["agent"]["token"]})
                principal = self.authenticate()
                data = self.body()
                if path == "/mcp":
                    accept = self.headers.get("Accept", "")
                    require("application/json" in accept and "text/event-stream" in accept, "ACCEPT", "JSONとSSEをAcceptに指定してください", 406)
                    status, response = dispatch(factory, principal, data, self.headers)
                    return self.send(status, response)
                if path == "/api/tool":
                    require(set(data) == {"name", "arguments"} and isinstance(data["name"], str), "INVALID_ARGUMENTS", "name/argumentsのみ指定してください", 400)
                    return self.send(200, factory.call(principal, data["name"], data["arguments"]))
                if path == "/api/approval":
                    require(set(data) == {"proposal_id", "decision", "reason"} and isinstance(data["proposal_id"], str), "INVALID_ARGUMENTS", "承認引数が不正です", 400)
                    return self.send(200, factory.decide(principal, data["proposal_id"], data["decision"], data["reason"]))
                if path == "/api/control":
                    require(set(data) == {"operation", "value"} and isinstance(data["operation"], str), "INVALID_ARGUMENTS", "operation/valueを指定してください", 400)
                    return self.send(200, factory.control(principal, data["operation"], data["value"]))
                if path == "/api/step":
                    require(principal["role"] == "supervisor", "FORBIDDEN", "現場責任者専用です", 403)
                    require(set(data) == {"seconds"}, "INVALID_ARGUMENTS", "secondsだけを指定してください", 400)
                    return self.send(200, factory.advance(data["seconds"]))
                raise LabError("NOT_FOUND", "見つかりません", 404)
            except LabError as err:
                if principal:
                    factory.log_denial(principal, urlsplit(self.path).path, err)
                self.send(err.status, err.as_dict())
            except (TypeError, KeyError, ValueError) as err:
                self.send(400, {"ok": False, "code": "INVALID_ARGUMENTS", "message": "入力の形式が不正です"})

    return Handler


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--demo", action="store_true", help="Allow dashboard to obtain agent-only credential. Human credentials stay separate.")
    parser.add_argument("--data-dir", default=str(ROOT / ".runtime"))
    args = parser.parse_args()
    runtime = Path(args.data_dir)
    runtime.mkdir(parents=True, exist_ok=True)
    credentials = load_credentials(runtime / "credentials.json")
    factory = Factory(str(runtime / "factory.sqlite"))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(factory, credentials, args.demo, args.port))
    server.daemon_threads = True
    stop = threading.Event()

    def ticker():
        while not stop.wait(1):
            s = factory.snapshot()
            if s["clock_running"]:
                factory.advance(s["speed"])

    worker = threading.Thread(target=ticker, daemon=True)
    worker.start()
    print("Motor Factory Lab: http://127.0.0.1:%d" % args.port, flush=True)
    print("開発用キー: " + str(runtime / "credentials.json"), flush=True)
    print("デモ: " + ("ON（エージェントの閲覧・提案キーのみ自動配布）" if args.demo else "OFF"), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        worker.join(timeout=5)
        server.server_close()
        factory.db.close()


if __name__ == "__main__":
    run()
