"""A tiny in-memory stand-in for the parts of Supabase the app uses (auth, the workspace RPCs and tables, and the
login / manage-user edge functions).

It mirrors supabase/migrations/*_companies.sql closely enough to test syncing end to end without a network:
several companies, each with its own members, change log and snapshots; usernames unique across the workspace.
Run on its own for browser tests:  python tests/mock_supabase.py 54321
"""
import json
import re
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.accounts = {}      # email -> password
        self.companies = {}     # id -> {id, name, invite_code}
        self.members = {}       # (company id, email) -> {email, is_admin, gateway, active, pending}
        self.people = {}        # email -> username
        self.ops = {}           # company id -> [op]
        self.snapshots = {}     # company id -> {seq: snapshot}
        self.confirm_required = False   # Supabase's "Confirm email" setting
        self.unconfirmed = set()
        self.banned = set()             # accounts switched off (manage-user with active = false)

    # helpers for tests
    def company_named(self, name):
        return next(c["id"] for c in self.companies.values() if c["name"] == name)

    def all_ops(self):
        return [o for ops in self.ops.values() for o in ops]

    def member(self, cid, email):
        m = self.members.get((cid, email))
        return m if m and m["active"] and not m["pending"] else None

    def username_of(self, email):
        return self.people.get(email)


def _err(code, msg, status=400):
    return status, {"code": code, "message": msg}


def _q(q, key):
    v = q.get(key, [""])[0]
    return re.sub(r"^(eq|gt|ilike|neq)\.", "", v)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, status, obj):
        body = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, None)

    def do_PUT(self):
        st, email = self.server.state, self._email()
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if urlparse(self.path).path == "/auth/v1/user" and email:
            with st.lock:
                st.accounts[email] = body.get("password", st.accounts.get(email))
            return self._send(200, {"email": email})
        self._send(404, {"message": "not found"})

    def _email(self):
        tok = (self.headers.get("Authorization") or "").replace("Bearer ", "")
        return tok[4:] if tok.startswith("tok:") else ""

    def do_GET(self):
        st, email, url = self.server.state, self._email(), urlparse(self.path)
        q = parse_qs(url.query)
        with st.lock:
            if url.path == "/auth/v1/settings":
                return self._send(200, {"external": {"email": True, "google": True}})
            if url.path == "/auth/v1/authorize":     # stand-in for "Sign in with Google": straight back, signed in
                e = "google.user@gmail.com"
                st.accounts.setdefault(e, None)
                self.send_response(302)
                self.send_header("Location", q["redirect_to"][0] + f"#access_token=tok:{e}&refresh_token=ref:{e}"
                                 "&expires_in=3600&token_type=bearer&provider_token=x")
                self.end_headers()
                return
            if url.path == "/auth/v1/user":
                return self._send(200, {"email": email} if email else {"msg": "invalid JWT"})
            cid = _q(q, "company_id")
            if url.path == "/rest/v1/ops":           # row level security: members of that company only
                after = int(_q(q, "seq") or 0)
                limit = int(q.get("limit", ["1000"])[0])
                rows = [o for o in st.ops.get(cid, []) if o["seq"] > after][:limit] if st.member(cid, email) else []
                return self._send(200, rows)
            if url.path == "/rest/v1/snapshots":
                snaps = st.snapshots.get(cid, {}) if st.member(cid, email) else {}
                return self._send(200, sorted(snaps.values(), key=lambda s: -s["seq"])[:1])
        self._send(404, {"message": "not found"})

    def do_POST(self):
        st, url = self.server.state, urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        with st.lock:
            if url.path == "/auth/v1/signup":
                e = body["email"].lower()
                if e in st.accounts:   # Supabase hides whether an address is registered: empty identities
                    return self._send(200, {"email": e, "identities": []})
                st.accounts[e] = body["password"]
                if st.confirm_required:
                    st.unconfirmed.add(e)
                    return self._send(200, {"email": e, "identities": [{"provider": "email"}]})
                return self._send(200, {"access_token": "tok:" + e, "refresh_token": "ref:" + e, "expires_in": 3600,
                                        "user": {"email": e}})
            if url.path == "/auth/v1/token":
                gt = parse_qs(url.query).get("grant_type", [""])[0]
                if gt == "password":
                    e = body["email"].lower()
                    if st.accounts.get(e) != body["password"] or e in st.banned:
                        return self._send(400, {"code": 400, "error_code": "invalid_credentials",
                                                "msg": "Invalid login credentials"})
                    if e in st.unconfirmed:
                        return self._send(400, {"code": 400, "error_code": "email_not_confirmed", "msg": "Email not confirmed"})
                else:
                    e = body["refresh_token"][4:]
                return self._send(200, {"access_token": "tok:" + e, "refresh_token": "ref:" + e, "expires_in": 3600,
                                        "user": {"email": e}})
            if url.path == "/functions/v1/manage-user":
                return self._send(*self.manage_user(body))
            if url.path == "/functions/v1/login":    # supabase/functions/login: username -> email -> session
                u = str(body.get("username", "")).strip().lower()
                e = next((em for em, un in st.people.items() if un == u), None)
                if not e or st.accounts.get(e) != body.get("password") or e in st.unconfirmed or e in st.banned:
                    return self._send(400, {"code": 400, "error_code": "invalid_credentials", "msg": "Invalid login credentials"})
                return self._send(200, {"access_token": "tok:" + e, "refresh_token": "ref:" + e, "expires_in": 3600,
                                        "user": {"email": e}})
            if url.path in ("/auth/v1/resend", "/auth/v1/recover"):
                return self._send(200, {})
            if url.path == "/test/config":          # test helper: switch "Confirm email" on/off
                st.confirm_required = bool(body.get("confirm_required"))
                return self._send(200, {})
            if url.path == "/test/confirm":         # test helper: the person clicked the emailed link
                st.unconfirmed.discard(body["email"].lower())
                return self._send(200, {})
            if url.path.startswith("/rest/v1/rpc/"):
                email = self._email()
                if not email:
                    return self._send(401, {"message": "JWT required"})
                status, out = self.rpc(url.path.rsplit("/", 1)[1], body, email)
                return self._send(status, out)
        self._send(404, {"message": "not found"})

    # ---------------------------------------------------------------- supabase/functions/manage-user
    def manage_user(self, body):
        st, caller = self.server.state, self._email()
        cid = body.get("company") or ""
        cm = st.member(cid, caller)
        if not cm or not cm["is_admin"]:
            return 403, {"msg": "Only administrators of this company can manage sign-in accounts"}
        u = str(body.get("username", "")).strip().lower()
        e = (body.get("email") or "").strip().lower()
        others = lambda em: [k for k in st.members if k[1] == em and k[0] != cid]  # noqa: E731
        if body.get("action") == "delete":
            tgt = e or next((em for em, un in st.people.items() if un == u), f"{u}@users.beyond-erp.invalid")
            if tgt == caller:
                return 400, {"msg": "You cannot delete your own sign-in account"}
            st.members.pop((cid, tgt), None)
            if not others(tgt):
                st.accounts.pop(tgt, None)
                st.people.pop(tgt, None)
            return 200, {"deleted": tgt}
        wanted = e or f"{u}@users.beyond-erp.invalid"
        old = next((em for em, un in st.people.items() if un == u), None)
        if old and (cid, old) not in st.members and not (e and e == old and not body.get("password")):
            tag = re.sub(r"[^a-z0-9]+", " ", st.companies[cid]["name"].lower()).strip().split(" ")[0] or "co"
            return 409, {"msg": f"The username “{u}” is already taken by someone in another company (usernames are shared "
                                f"by all companies). Choose another one, for example “{u}.{tag}”."}
        if e and st.people.get(e) and st.people[e] != u and (cid, e) not in st.members:
            return 409, {"msg": f"This email already has a sign-in account with the username “{st.people[e]}” — use that username to add them."}
        target = old or wanted
        shared = bool(others(target)) and target in st.accounts
        if shared:
            if body.get("password"):
                return 400, {"msg": "This person already has a sign-in account (used by another company too). They keep "
                                    "their own password — leave the password empty to add them."}
            final = target
        else:
            if old and old != wanted:
                st.accounts[wanted] = st.accounts.pop(old, None)
                st.people[wanted] = st.people.pop(old)
                for k in [k for k in st.members if k[1] == old]:
                    st.members[(k[0], wanted)] = {**st.members.pop(k), "email": wanted}
            if st.accounts.get(wanted) is None and not body.get("password"):
                return 400, {"msg": "Set a password for this person (they have no sign-in account yet)"}
            if body.get("password"):
                st.accounts[wanted] = body["password"]
            (st.banned.discard if body.get("active", True) is not False else st.banned.add)(wanted)
            st.people[wanted] = u
            final = wanted
        cur = st.members.get((cid, final), {"email": final, "is_admin": False, "gateway": False})
        st.members[(cid, final)] = {**cur, "active": body.get("active", True) is not False, "pending": False}
        return 200, {"email": final, "username": u, "placeholder": not e and not shared, "shared": shared}

    # ---------------------------------------------------------------- RPCs
    def rpc(self, fn, p, email):
        st = self.server.state
        cid = p.get("p_company")
        if cid is None and fn in ("workspace_info", "append_op", "put_snapshot", "set_member", "link_member"):
            live = [k[0] for k, m in st.members.items() if k[1] == email and m["active"] and not m["pending"]]
            cid = live[0] if len(live) == 1 else ""      # app versions before companies
        m = st.member(cid, email) if cid else None

        def admin():
            return m and m["is_admin"]

        def still_admin():
            return any(k[0] == cid and x["is_admin"] and x["active"] and not x["pending"] for k, x in st.members.items())

        def info():
            snaps = st.snapshots.get(cid, {})
            snap = max(snaps.values(), key=lambda s: s["seq"]) if snaps and m else None
            req = st.members.get((cid, email))
            ops = st.ops.get(cid, [])
            return {"email": email, "company": cid if m else None,
                    "company_name": st.companies[cid]["name"] if cid in st.companies and (m or (req and req["pending"])) else None,
                    "invite_code": st.companies[cid]["invite_code"] if admin() else None,
                    "has_members": bool(m), "is_member": bool(m), "pending": bool(req and req["pending"]),
                    "is_admin": bool(admin()), "gateway": bool(m and m["gateway"]),
                    "last_seq": (ops[-1]["seq"] if ops else 0) if m else None,
                    "snapshot_seq": snap["seq"] if snap else None, "snapshot_version": snap["version"] if snap else None}
        if fn == "my_companies":
            return 200, sorted([{"id": k[0], "name": st.companies[k[0]]["name"], "is_admin": x["is_admin"],
                                 "pending": x["pending"], "active": x["active"]}
                                for k, x in st.members.items() if k[1] == email and (x["active"] or x["pending"])],
                               key=lambda c: c["name"])
        if fn == "create_company":
            name = (p.get("p_name") or "").strip()
            if not name:
                return _err("P0001", "enter the company name")
            new = str(uuid.uuid4())
            st.companies[new] = {"id": new, "name": name, "invite_code": uuid.uuid4().hex[:8].upper()}
            st.members[(new, email)] = {"email": email, "is_admin": True, "gateway": False, "active": True, "pending": False}
            st.people.setdefault(email, None)
            return 200, {"id": new, "name": name, "is_admin": True, "pending": False}
        if fn == "claim_workspace":
            if st.companies:
                return _err("42501", "update the app to create or join a company", 403)
            return self.rpc("create_company", {"p_name": "My company"}, email)
        if fn == "join_company":
            c = next((c for c in st.companies.values() if c["invite_code"] == (p.get("p_code") or "").strip().upper()), None)
            if not c:
                return _err("P0001", "no company has this invite code — check it with the company's administrator")
            cur = st.members.get((c["id"], email))
            if not cur:
                st.members[(c["id"], email)] = {"email": email, "is_admin": False, "gateway": False, "active": False,
                                                "pending": True}
                st.people.setdefault(email, None)
            return 200, {"id": c["id"], "name": c["name"], "pending": not cur or cur["pending"],
                         "is_member": bool(cur and cur["active"] and not cur["pending"])}
        if fn == "workspace_info":
            return 200, info()
        if fn == "append_op":
            if not m:
                return _err("42501", "not a member of this company", 403)
            ops = st.ops.setdefault(cid, [])
            last = ops[-1]["seq"] if ops else 0
            if p["p_seq"] != last + 1:
                return _err("40001", f"conflict: next change is {last + 1}")
            who = p["p_app_user"] if m["gateway"] and p["p_app_user"] else email
            ops.append({"company_id": cid, "seq": p["p_seq"], "version": p["p_version"], "actor": email, "app_user": who,
                        "device": p["p_device"], "method": p["p_method"], "path": p["p_path"], "body": p["p_body"],
                        "content_type": p["p_content_type"], "clock": p["p_clock"]})
            return 200, {"seq": p["p_seq"], "app_user": who}
        if fn == "put_snapshot":
            if not m or not (m["is_admin"] or m["gateway"]):
                return _err("42501", "only administrators can save company copies", 403)
            st.snapshots.setdefault(cid, {})[p["p_seq"]] = {"company_id": cid, "seq": p["p_seq"], "version": p["p_version"],
                                                           "data": p["p_data"]}
            return 200, p["p_seq"]
        if fn in ("company_members", "set_member", "remove_member", "link_member", "rename_company", "new_invite_code") \
                and not admin():
            return _err("42501", "only administrators of this company can do this", 403)
        if fn == "company_members":
            return 200, sorted([{**x, "username": st.people.get(k[1])} for k, x in st.members.items() if k[0] == cid],
                               key=lambda x: (not x["pending"], x["email"]))
        if fn == "set_member":
            e = p["p_email"].strip().lower()
            st.members[(cid, e)] = {"email": e, "is_admin": p["p_is_admin"], "gateway": p["p_gateway"],
                                    "active": p["p_active"], "pending": False}
            st.people.setdefault(e, None)
            return (200, None) if still_admin() else _err("P0001", "at least one active administrator must remain")
        if fn == "remove_member":
            e = p["p_email"].strip().lower()
            if e == email:
                return _err("P0001", "you cannot remove yourself")
            st.members.pop((cid, e), None)
            return 200, None
        if fn == "link_member":
            e, u = p["p_email"].strip().lower(), (p.get("p_username") or "").strip().lower() or None
            if u and any(un == u and em != e for em, un in st.people.items()):
                return _err("P0001", f"the username {u} is already used by another account — choose another")
            st.people[e] = u or st.people.get(e)
            cur = st.members.get((cid, e), {"email": e, "is_admin": False, "gateway": False})
            st.members[(cid, e)] = {**cur, "active": p.get("p_active", True), "pending": False}
            return 200, None
        if fn == "rename_company":
            st.companies[cid]["name"] = p["p_name"].strip()
            return 200, None
        if fn == "new_invite_code":
            st.companies[cid]["invite_code"] = uuid.uuid4().hex[:8].upper()
            return 200, st.companies[cid]["invite_code"]
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
