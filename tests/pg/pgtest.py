"""A throwaway PostgreSQL with the Supabase stand-ins and every migration applied, for testing the workspace's SQL.

    with Workspace() as ws:
        ws.as_user("a@x.com").rpc("create_company", p_name="A")

Needs PostgreSQL's server programs (initdb, pg_ctl) and psql; tests skip when they are missing.
"""
import glob
import json
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STUB = Path(__file__).with_name("supabase_stub.sql")


def _bin(name):
    found = shutil.which(name)
    if found:
        return found
    for d in sorted(glob.glob("/usr/lib/postgresql/*/bin"), reverse=True):
        if os.path.exists(os.path.join(d, name)):
            return os.path.join(d, name)
    return None


def available():
    return all(_bin(n) for n in ("initdb", "pg_ctl", "psql"))


class SQLError(Exception):
    pass


class Workspace:
    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="berp-pg-")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.user = "postgres"
        self.env = {**os.environ}
        self.run_as = []
        if os.geteuid() == 0:            # PostgreSQL will not run as root: use its own account
            self.run_as = ["runuser", "-u", "postgres", "--"]
            subprocess.run(["chown", "-R", "postgres", self.dir], check=True)

    def __enter__(self):
        data = os.path.join(self.dir, "data")
        subprocess.run(self.run_as + [_bin("initdb"), "-D", data, "-U", self.user, "-A", "trust", "--no-sync", "-E", "UTF8"],
                       check=True, capture_output=True, env=self.env)
        subprocess.run(self.run_as + [_bin("pg_ctl"), "-D", data, "-l", os.path.join(self.dir, "log"), "-w", "-o",
                                      f"-p {self.port} -k {self.dir} -c listen_addresses='' -c fsync=off", "start"],
                       check=True, capture_output=True, env=self.env)
        self.psql_file(STUB)
        for f in sorted(glob.glob(str(ROOT / "supabase" / "migrations" / "*.sql"))):
            self.psql_file(f)
        return self

    def __exit__(self, *exc):
        subprocess.run(self.run_as + [_bin("pg_ctl"), "-D", os.path.join(self.dir, "data"), "-m", "immediate", "stop"],
                       capture_output=True, env=self.env)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _psql(self, args, stdin=None):
        r = subprocess.run(self.run_as + [_bin("psql"), "-h", self.dir, "-p", str(self.port), "-U", self.user, "-d", "postgres",
                                          "-v", "ON_ERROR_STOP=1", "-X", "-q", "-A", "-t"] + args,
                           input=stdin, capture_output=True, text=True, env=self.env)
        if r.returncode:
            raise SQLError((r.stderr or r.stdout).strip())
        return r.stdout

    def psql_file(self, path):
        return self._psql(["-f", str(path)])

    def sql(self, text):
        """Run SQL as the database owner (like the SQL editor); the last statement's rows as text."""
        return self._psql([], stdin=text)

    def as_user(self, email, role="authenticated", aal="aal1"):
        return Caller(self, email, role, aal)


class Caller:
    """Calls as a signed-in person (or the service role), the way PostgREST does."""

    def __init__(self, ws, email, role, aal):
        self.ws, self.email, self.role, self.aal = ws, email, role, aal

    def _wrap(self, body):
        claims = json.dumps({"email": self.email or "", "role": self.role, "aal": self.aal})
        return (f"begin; select set_config('request.jwt.claims', {_lit(claims)}, true); set local role {self.role};\n"
                f"{body}\ncommit;")

    def rpc(self, fn, **params):
        args = ", ".join(f"{k} => {_lit(v)}" for k, v in params.items())
        out = self.ws._psql([], stdin=self._wrap(f"select to_jsonb(x) from (select public.{fn}({args}) as r) x;"))
        line = out.strip().splitlines()[-1] if out.strip() else "null"
        return json.loads(line).get("r") if line != "null" else None

    def select(self, query):
        out = self.ws._psql([], stdin=self._wrap(f"select coalesce(jsonb_agg(to_jsonb(q)), '[]') from ({query}) q;"))
        return json.loads(out.strip().splitlines()[-1])

    def execute(self, statement):
        return self.ws._psql([], stdin=self._wrap(statement))


def _lit(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (dict, list)):
        return "'" + json.dumps(v).replace("'", "''") + "'::jsonb"
    return "'" + str(v).replace("'", "''") + "'"
