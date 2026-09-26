"""A tiny sqlite3-compatible adapter over sql.js, used only by the browser build.

The browser has no native sqlite3 module in Pyodide's core, so the web version runs
SQLite compiled to WebAssembly (sql.js) and talks to it through the JavaScript helper
`globalThis.beyondSqlRun(sql, paramsJson) -> resultJson` defined in web/bridge.js.
Only the subset of the DB-API the app uses is implemented.
"""
import json


class Row(tuple):
    """Like sqlite3.Row: index by position or column name, and dict(row) works."""

    def __new__(cls, values, cols):
        r = super().__new__(cls, values)
        r._cols = cols
        return r

    def keys(self):
        return list(self._cols)

    def __getitem__(self, k):
        if isinstance(k, str):
            return tuple.__getitem__(self, self._cols.index(k))
        return tuple.__getitem__(self, k)


class Cursor:
    def __init__(self, rows, cols, lastrowid, rowcount):
        self._rows = [Row(r, cols) for r in rows]
        self.lastrowid = lastrowid
        self.rowcount = rowcount
        self.description = [(c, None, None, None, None, None, None) for c in cols]

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


def _plain(v):
    if isinstance(v, (int, float, str)) or v is None:
        return v
    if isinstance(v, bool):
        return int(v)
    return str(v)


class Connection:
    def __init__(self, runner):
        self._run = runner          # JS function (sql, params_json) -> result_json
        self.row_factory = None
        self.in_transaction = False

    def _track(self, sql):
        head = sql.lstrip()[:8].upper()
        if head.startswith("BEGIN"):
            self.in_transaction = True
        elif head.startswith(("COMMIT", "ROLLBACK", "END")):
            self.in_transaction = False

    def execute(self, sql, params=()):
        if isinstance(params, dict):
            raise NotImplementedError("named parameters are not used by the app")
        res = json.loads(self._run(sql, json.dumps([_plain(p) for p in params])))
        if res.get("error"):
            msg = res["error"]
            raise (IntegrityError if "constraint" in msg.lower() else DatabaseError)(msg)
        self._track(sql)
        return Cursor(res.get("rows", []), res.get("cols", []), res.get("lastId"), res.get("changes", 0))

    def executemany(self, sql, seq):
        cur = None
        for params in seq:
            cur = self.execute(sql, params)
        return cur

    def executescript(self, script):
        res = json.loads(self._run(script, "null"))
        if res.get("error"):
            raise DatabaseError(res["error"])

    def commit(self):
        if self.in_transaction:
            self.execute("COMMIT")

    def close(self):
        pass


class DatabaseError(Exception):
    pass


class IntegrityError(DatabaseError):
    pass


def connect(runner):
    return Connection(runner)
