"""Loopback decision screen for a protected, pre-enrolled WebAuthn installation."""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.dont_write_bytecode = True

from flamin_status_mcp import continue_workspace, review_decision
from flaminlib.decision_desk import DecisionDesk, decode
from flaminlib.product import Product
from flaminlib.util import FlaminError

MAX_REQUEST = 65536

PAGE = b"""<!doctype html>
<meta charset="utf-8">
<title>FLAMIN decisions</title>
<h1>FLAMIN decisions</h1>
<p id="workspace"></p>
<select id="requests" aria-label="Pending decision"></select>
<button id="review">Review</button>
<pre id="proposal"></pre>
<button id="approve" disabled>Approve with passkey</button>
<button id="reject" disabled>Reject with passkey</button>
<p id="status" role="status"></p>
<script src="/desk.js"></script>
"""

SCRIPT = rb"""
const get = id => document.getElementById(id);
const status = message => get("status").textContent = message;
let proposal;
const toBytes = value => Uint8Array.from(atob(value.replaceAll("-", "+").replaceAll("_", "/")), c => c.charCodeAt(0));
const fromBytes = value => btoa(String.fromCharCode(...new Uint8Array(value)))
  .replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
async function request(path, data) {
  const response = await fetch(path, data === undefined ? {} :
    {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)});
  const value = await response.json();
  if (!response.ok) throw Error(value.error);
  return value;
}
function buttons(enabled) {
  get("approve").disabled = !enabled;
  get("reject").disabled = !enabled;
}
async function load() {
  const value = await request("/requests");
  get("workspace").textContent = value.workspace;
  get("requests").replaceChildren(...value.requests.map(item => new Option(item.id + " " + item.title, item.id)));
  if (!value.requests.length) status("No reviewable decisions.");
}
get("review").onclick = async () => {
  buttons(false);
  proposal = undefined;
  get("proposal").textContent = "";
  if (!get("requests").value) return;
  try {
    proposal = await request("/review", {id: get("requests").value});
    get("proposal").textContent = [proposal.id, proposal.workspace, proposal.title,
      proposal.payload, "Payload digest " + proposal.payload_hash,
      "Workspace revision " + proposal.state_revision].join("\n\n");
    buttons(true);
    status("Review the exact proposal before deciding.");
  } catch (error) { status(error.message); }
};
get("requests").onchange = () => {
  proposal = undefined;
  get("proposal").textContent = "";
  buttons(false);
};
async function decide(decision) {
  buttons(false);
  get("review").disabled = true;
  get("requests").disabled = true;
  try {
    const prepared = await request("/prepare", {id: proposal.id, decision, reviewed: proposal});
    status("Confirm " + decision + " using your enrolled passkey.");
    const assertion = await navigator.credentials.get({publicKey: {
      challenge: toBytes(prepared.challenge), rpId: "localhost", userVerification: "required",
      allowCredentials: [{type: "public-key", id: toBytes(prepared.credential_id)}], timeout: 110000,
    }});
    const result = await request("/decide", {challenge: prepared.challenge, assertion: {
      id: fromBytes(assertion.rawId), client_data: fromBytes(assertion.response.clientDataJSON),
      authenticator_data: fromBytes(assertion.response.authenticatorData),
      signature: fromBytes(assertion.response.signature),
    }});
    await load();
    status(result.id + " " + result.outcome + ".");
  } catch (error) {
    status(error.message);
  } finally {
    get("review").disabled = false;
    get("requests").disabled = false;
  }
}
get("approve").onclick = () => decide("approve");
get("reject").onclick = () => decide("reject");
load().catch(error => status(error.message));
"""


def handler_for(desk, product, port):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def _send(self, status, content, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(content)

        def _json(self, status, body):
            self._send(status, json.dumps(body).encode("utf-8"), "application/json")

        def _host(self):
            return self.headers.get("Host") == f"localhost:{port}"

        def do_GET(self):
            if not self._host():
                return self._json(403, {"error": "Use the local decision screen."})
            if self.path == "/":
                return self._send(200, PAGE, "text/html; charset=utf-8")
            if self.path == "/desk.js":
                return self._send(200, SCRIPT, "application/javascript")
            if self.path == "/requests":
                try:
                    requests = []
                    for item in continue_workspace(product, desk.database)["formal_decisions"]:
                        if item["kind"] in ("assumption", "stack") and not item.get("requires_refresh"):
                            requests.append(item)
                    return self._json(200, {"workspace": desk.workspace, "requests": requests})
                except (FlaminError, ValueError, OSError, KeyError, TypeError):
                    return self._json(409, {"error": "The workspace proposals could not be read."})
            self._json(404, {"error": "Unknown decision screen operation."})

        def do_POST(self):
            if not self._host() or self.headers.get("Origin") != desk.origin:
                return self._json(403, {"error": "Use the local decision screen."})
            if self.headers.get("Content-Type") != "application/json":
                return self._json(415, {"error": "Expected a JSON request."})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_REQUEST:
                    return self._json(413, {"error": "Invalid decision request size."})
                data = json.loads(self.rfile.read(length))
                if self.path == "/review":
                    result = desk.inspect(data["id"])
                elif self.path == "/prepare":
                    result = desk.prepare(data["id"], data["decision"], data["reviewed"])
                elif self.path == "/decide":
                    result = desk.decide(data["challenge"], data["assertion"])
                else:
                    return self._json(404, {"error": "Unknown decision screen operation."})
                self._json(200, result)
            except FlaminError as exc:
                self._json(409, {"error": str(exc)})
            except (ValueError, OSError, AttributeError, KeyError, TypeError):
                self._json(400, {"error": "Invalid decision request."})
    return Handler


def main() -> int:
    if len(sys.argv) != 2:
        print("Supply the installed decision service's protected pairing file.", file=sys.stderr)
        return 2
    try:
        pairing = Path(sys.argv[1]).resolve(strict=True)
        config = json.loads(pairing.read_text(encoding="utf-8"))
        product = Product(Path(config["workspace"]).resolve(strict=True))
        if pairing.is_relative_to(product.root):
            raise FlaminError("The authority pairing and database must be outside the workspace.")
        if not product.store.exists():
            raise FlaminError("The paired workspace has no initialized product.")
        port = config["port"]
        if type(port) is not int or not 1024 <= port <= 65535:
            raise FlaminError("Use a fixed unprivileged port.")
        origin = f"http://localhost:{port}"
        credential_id = config["credential_id"]
        decode(credential_id)
        public_key = decode(config["public_key_spki"])
        database = pairing.with_suffix(".sqlite3").resolve()
        if database.is_relative_to(product.root):
            raise FlaminError("The decision database must be outside the workspace.")
        desk = DecisionDesk(database, product.root, origin, credential_id, public_key,
                            lambda rid: review_decision(product, rid, protected=True))
        with HTTPServer(("127.0.0.1", port), handler_for(desk, product, port)) as server:
            print(f"Decision screen at {origin}", flush=True)
            server.serve_forever()
    except (OSError, KeyError, ValueError, TypeError, FlaminError) as exc:
        print(f"Decision screen unavailable: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
