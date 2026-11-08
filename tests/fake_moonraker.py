#!/usr/bin/env python3
"""Local Moonraker protocol fixture for the disposable installer CI host.

Only discovery, status and a configuration reload are supported. Every other
RPC is rejected and recorded, so this fixture can never execute a print action.
"""
import argparse
import configparser
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import signal
import socketserver
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


API_KEY = "ci-moonraker-api-key"
DATA_DIR = Path("/home/printer/printer_data")
STATE_FILE = Path("/tmp/printforge-moonraker-fixture/state.json")


class RPCError(Exception):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class MoonrakerFixture:
    def __init__(self, data_dir=DATA_DIR, state_file=STATE_FILE, port=7125):
        self.data_dir = Path(data_dir)
        self.config_file = self.data_dir / "config/moonraker.conf"
        self.socket_file = self.data_dir / "comms/moonraker.sock"
        self.state_file = Path(state_file)
        self.port = port
        self.lock = threading.RLock()
        self.restart_count = 0
        self.forbidden_actions = []
        self.requests = {}
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.socket_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.config_file.exists():
            self.config_file.write_text(
                "# Preserve this fixture comment during automatic setup.\n"
                "[server]\nhost: 127.0.0.1\n"
                f"port: {port}\nklippy_uds_address: {self.data_dir}/comms/klippy.sock\n\n"
                "[authorization]\nforce_logins: True\nenable_api_key: True\n",
                encoding="utf-8",
            )
        self.reload_config()
        self.save_state()

    def reload_config(self):
        parser = configparser.ConfigParser(interpolation=None)
        with self.config_file.open(encoding="utf-8") as handle:
            parser.read_file(handle)
        self.original_config = {section: dict(parser.items(section)) for section in parser.sections()}
        self.config = copy.deepcopy(self.original_config)
        self.config["server"]["port"] = int(self.config["server"]["port"])
        if "authorization" in self.config:
            self.config["authorization"]["force_logins"] = parser.getboolean("authorization", "force_logins")
            self.config["authorization"]["enable_api_key"] = parser.getboolean("authorization", "enable_api_key")

    def save_state(self):
        temporary = self.state_file.with_suffix(".new")
        temporary.write_text(json.dumps({
            "restart_count": self.restart_count,
            "forbidden_actions": self.forbidden_actions,
            "requests": self.requests,
        }), encoding="utf-8")
        temporary.replace(self.state_file)

    def spoolman_status(self):
        server = self.config.get("spoolman", {}).get("server", "").rstrip("/")
        connected = False
        # Check the real service, not merely the presence of a config section.
        if server:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                with opener.open(server + "/api/v1/health", timeout=2) as response:
                    connected = response.status == 200
            except (OSError, urllib.error.URLError):
                pass
        return {"spoolman_connected": connected, "spool_id": None, "pending_reports": []}

    @staticmethod
    def printer_status():
        configuration = {
            "printer": {"kinematics": "cartesian", "max_velocity": "300", "max_accel": "3000"},
            "extruder": {"nozzle_diameter": "0.4", "filament_diameter": "1.75"},
            "stepper_x": {"position_min": "0", "position_max": "300"},
            "stepper_y": {"position_min": "0", "position_max": "300"},
            "stepper_z": {"position_min": "0", "position_max": "400"},
        }
        return {
            "configfile": {"config": configuration, "settings": copy.deepcopy(configuration),
                           "save_config_pending": False, "warnings": []},
            "webhooks": {"state": "ready", "state_message": "Printer is ready"},
            "print_stats": {"state": "standby", "filename": "", "message": "",
                            "total_duration": 0, "print_duration": 0, "filament_used": 0},
            "toolhead": {"axis_minimum": [0, 0, 0, 0], "axis_maximum": [300, 300, 400, 0],
                         "position": [0, 0, 0, 0], "homed_axes": "xyz", "extruder": "extruder"},
            "virtual_sdcard": {"is_active": False, "progress": 0, "file_position": 0},
            "pause_resume": {"is_paused": False},
            "idle_timeout": {"state": "Ready", "printing_time": 0},
            "extruder": {"temperature": 22, "target": 0},
            "heater_bed": {"temperature": 21, "target": 0},
        }

    def call(self, method, params=None):
        params = params or {}
        with self.lock:
            self.requests[method] = self.requests.get(method, 0) + 1
            self.save_state()
            if method == "server.info":
                return {"klippy_connected": True, "klippy_state": "ready",
                        "components": ["klippy_connection", "authorization", "webcams"] +
                                      (["spoolman"] if "spoolman" in self.config else []),
                        "failed_components": [], "warnings": [],
                        "registered_directories": ["config", "gcodes"],
                        "moonraker_version": "v0.9.3-ci-fixture", "api_version": [1, 5, 0],
                        "api_version_string": "1.5.0"}
            if method == "server.config":
                return {"config": copy.deepcopy(self.config), "orig": copy.deepcopy(self.original_config),
                        "files": [{"filename": str(self.config_file), "sections": list(self.original_config)}]}
            if method == "access.get_api_key":
                return API_KEY
            if method == "printer.info":
                return {"state": "ready", "state_message": "Printer is ready", "hostname": "ci-printer",
                        "software_version": "v0.13.0-ci-fixture", "config_file": str(self.data_dir / "config/printer.cfg"),
                        "log_file": str(self.data_dir / "logs/klippy.log"), "cpu_info": "CI fixture"}
            if method == "printer.objects.query":
                status = self.printer_status()
                requested = params.get("objects")
                if requested:
                    status = {name: ({key: value for key, value in status[name].items() if key in fields}
                                     if fields is not None else status[name])
                              for name, fields in requested.items() if name in status}
                return {"eventtime": time.monotonic(), "status": status}
            if method == "server.webcams.list":
                return {"webcams": [{"name": "CI camera", "location": "printer", "service": "mjpegstreamer",
                                     "stream_url": "/webcam/?action=stream", "snapshot_url": "/webcam/?action=snapshot",
                                     "enabled": True, "icon": "mdiWebcam", "target_fps": 15,
                                     "target_fps_idle": 5, "flip_horizontal": False, "flip_vertical": False,
                                     "rotation": 0, "source": "config", "uid": "ci-camera"}]}
            if method == "server.spoolman.status":
                return self.spoolman_status()
            if method == "server.restart":
                self.reload_config()
                self.restart_count += 1
                self.save_state()
                return "ok"
            self.forbidden_actions.append(method)
            self.save_state()
            raise RPCError(404, f"Fixture rejects unsupported or mutating RPC: {method}")


class HTTPHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def respond(self, status, body):
        encoded = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        self.handle_api()

    def do_POST(self):
        self.handle_api()

    def handle_api(self):
        if self.headers.get("X-Api-Key") != API_KEY:
            self.respond(401, {"error": {"code": 401, "message": "API key required"}})
            return
        target = urllib.parse.urlsplit(self.path)
        method = target.path.strip("/").replace("/", ".")
        try:
            params = {}
            if self.command == "POST":
                length = int(self.headers.get("Content-Length", 0))
                if not 0 <= length <= 65536:
                    raise RPCError(413, "Request too large")
                params = json.loads(self.rfile.read(length)) if length else {}
            elif method == "printer.objects.query":
                query = urllib.parse.parse_qs(target.query, keep_blank_values=True)
                params = {"objects": {name: values[0].split(",") if values[0] else None
                                      for name, values in query.items()}}
            result = self.server.fixture.call(method, params)
            self.respond(200, {"result": result})
        except RPCError as error:
            self.respond(error.code, {"error": {"code": error.code, "message": str(error)}})
        except (ValueError, TypeError) as error:
            self.respond(400, {"error": {"code": 400, "message": str(error)}})


class UnixHandler(socketserver.BaseRequestHandler):
    def handle(self):
        buffer = b""
        while True:
            received = self.request.recv(65536)
            if not received:
                return
            buffer += received
            if len(buffer) > 1024 * 1024:
                return
            while b"\x03" in buffer:
                message, buffer = buffer.split(b"\x03", 1)
                request = json.loads(message)
                response = {"jsonrpc": "2.0", "id": request.get("id")}
                try:
                    response["result"] = self.server.fixture.call(request["method"], request.get("params"))
                except RPCError as error:
                    response["error"] = {"code": error.code, "message": str(error)}
                self.request.sendall(json.dumps(response).encode() + b"\x03")


class UnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--state-file", type=Path, default=STATE_FILE)
    parser.add_argument("--port", type=int, default=7125)
    args = parser.parse_args()
    if not Path("/.dockerenv").exists():
        parser.error("This fixture is only launched inside the disposable CI container")
    fixture = MoonrakerFixture(args.data_dir, args.state_file, args.port)
    http = ThreadingHTTPServer(("127.0.0.1", args.port), HTTPHandler)
    unix = UnixServer(str(fixture.socket_file), UnixHandler)
    http.fixture = unix.fixture = fixture
    fixture.socket_file.chmod(0o600)
    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_args: stopped.set())
    workers = [threading.Thread(target=server.serve_forever, daemon=True) for server in (http, unix)]
    for worker in workers:
        worker.start()
    try:
        stopped.wait()
    finally:
        for server in (http, unix):
            server.shutdown()
            server.server_close()
        fixture.socket_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
