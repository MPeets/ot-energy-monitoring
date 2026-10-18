"""Minimal Alertmanager webhook receiver for the lab, reused from
airgapped-observability-lab.

Prints one line per alert, so routing, grouping and inhibition can be watched
with `docker compose logs -f webhook-logger`. The path says which receiver it
came through: /oncall, /team, /log or /watchdog. Standard library only, so it
runs on the stock Python image with nothing to download.
"""
import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

# Labels that are on every alert, or don't help tell alerts apart in a log line
QUIET = {"alertname", "severity", "job", "instance", "input", "device_type", "environment"}


def describe(receiver: str, alert: dict) -> str:
    labels = alert.get("labels", {})
    details = " ".join(f"{key}={value}" for key, value in sorted(labels.items()) if key not in QUIET)
    summary = alert.get("annotations", {}).get("summary", "")
    line = (
        f"[{receiver}] {alert.get('status', '?').upper():8} {labels.get('alertname', '?')} "
        f"severity={labels.get('severity', '-')} {details}"
    ).rstrip()
    return f"{line}: {summary}" if summary else line


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return

        now = datetime.now(timezone.utc).strftime("%H:%M:%S")
        receiver = self.path.strip("/") or "unknown"
        for alert in payload.get("alerts", []):
            print(f"{now} {describe(receiver, alert)}", flush=True)

        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass  # keep output to one line per alert


if __name__ == "__main__":
    print("webhook-logger listening on :8080", flush=True)
    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()