"""A tiny in-memory stand-in for the parts of Supabase the app uses (auth + the workspace RPCs and tables).

It mirrors supabase/migrations/*_beyond_workspace.sql closely enough to test syncing end to end without a network.
Run on its own for browser tests:  python tests/mock_supabase.py 54321
"""
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.accounts = {}      # email -> password
        self.members = {}       # email -> dict
        self.ops = []
        self.snapshots = {}


def _err(code, msg, status=400):
    return status, {"code": code, "message": msg}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, status, obj):
        body = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, None)

    def _email(self):
        tok = (self.headers.get("Authorization") or "").replace("Bearer ", "")
        return tok[4:] if tok.startswith("tok:") else ""

    def do_GET(self):
        st, email, url = self.server.state, self._email(), urlparse(self.path)
        q = parse_qs(url.query)
        member = email in st.members and st.members[email]["active"]
        with st.lock:
            if url.path == "/rest/v1/ops":
                after = int(re.sub(r"^gt\.", "", q.get("seq", ["gt.0"])[0]))
                limit = int(q.get("limit", ["1000"])[0])
                rows = [o for o in st.ops if o["seq"] > after][:limit] if member else []
                return self._send(200, rows)
            if url.path == "/rest/v1/snapshots":
                rows = sorted(st.snapshots.values(), key=lambda s: -s["seq"])[:1] if member else []
                return self._send(200, rows)
            if url.path == "/rest/v1/members":
                return self._send(200, sorted(st.members.values(), key=lambda m: m["email"]) if member else [])
        self._send(404, {"message": "not found"})

    def do_POST(self):
        st, url = self.server.state, urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        with st.lock:
            if url.path == "/auth/v1/signup":
                st.accounts[body["email"].lower()] = body["password"]
                return self._send(200, {"user": {"email": body["email"].lower()}})
            if url.path == "/auth/v1/token":
                gt = parse_qs(url.query).get("grant_type", [""])[0]
                if gt == "password":
                    e = body["email"].lower()
                    if st.accounts.get(e) != body["password"]:
                        return self._send(400, {"error_description": "Invalid login credentials"})
                else:
                    e = body["refresh_token"][4:]
                return self._send(200, {"access_token": "tok:" + e, "refresh_token": "ref:" + e, "expires_in": 3600,
                                        "user": {"email": e}})
            if url.path.startswith("/rest/v1/rpc/"):
                email = self._email()
                if not email:
                    return self._send(401, {"message": "JWT required"})
                status, out = self.rpc(url.path.rsplit("/", 1)[1], body, email)
                return self._send(status, out)
        self._send(404, {"message": "not found"})

    def rpc(self, fn, p, email):
        st = self.server.state
        m = st.members.get(email)
        m = m if m and m["active"] else None

        def info():
            snap = max(st.snapshots.values(), key=lambda s: s["seq"]) if st.snapshots else None
            return {"email": email, "has_members": bool(st.members), "is_member": bool(m),
                    "is_admin": bool(m and m["is_admin"]), "gateway": bool(m and m["gateway"]),
                    "last_seq": (st.ops[-1]["seq"] if st.ops else 0) if m else None,
                    "snapshot_seq": snap["seq"] if m and snap else None,
                    "snapshot_version": snap["version"] if m and snap else None}
        if fn == "workspace_info":
            return 200, info()
        if fn == "claim_workspace":
            if st.members:
                return _err("42501", "this workspace already has members", 403)
            st.members[email] = {"email": email, "is_admin": True, "gateway": False, "active": True}
            m = st.members[email]
            return 200, info()
        if fn == "append_op":
            if not m:
                return _err("42501", "not a member of this workspace", 403)
            last = st.ops[-1]["seq"] if st.ops else 0
            if p["p_seq"] != last + 1:
                return _err("40001", f"conflict: next change is {last + 1}")
            who = p["p_app_user"] if m["gateway"] and p["p_app_user"] else email
            st.ops.append({"seq": p["p_seq"], "version": p["p_version"], "actor": email, "app_user": who,
                           "device": p["p_device"], "method": p["p_method"], "path": p["p_path"], "body": p["p_body"],
                           "content_type": p["p_content_type"], "clock": p["p_clock"]})
            return 200, {"seq": p["p_seq"], "app_user": who}
        if fn == "put_snapshot":
            if not m or not (m["is_admin"] or m["gateway"]):
                return _err("42501", "only administrators can save workspace copies", 403)
            st.snapshots[p["p_seq"]] = {"seq": p["p_seq"], "version": p["p_version"], "data": p["p_data"]}
            return 200, p["p_seq"]
        if fn == "set_member":
            if not m or not m["is_admin"]:
                return _err("42501", "only administrators can manage members", 403)
            e = p["p_email"].strip().lower()
            st.members[e] = {"email": e, "is_admin": p["p_is_admin"], "gateway": p["p_gateway"], "active": p["p_active"]}
            return 200, None
        return 404, {"message": "unknown function"}


def start(port=0):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.state = State()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    s = start(int(sys.argv[1]) if len(sys.argv) > 1 else 54321)
    print(f"mock Supabase on http://127.0.0.1:{s.server_address[1]}")
    threading.Event().wait()
