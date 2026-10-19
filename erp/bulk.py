"""Bulk actions on a list screen: edit, void, reverse or delete many records at once.

Every selected record is handled on its own, with exactly the permission and the rules that apply when it is done by
hand: deletes and status changes go through the record's own route (the same checks, the same refusals), entries
through the accounting engine with their voucher type's permission, and edits change only a short, safe list of
fields per list. A record that cannot be changed is reported with the reason; the others are still done. On the shared
workspace the whole bulk action is one change in the log, so every device replays it identically.
"""
import json

from . import accounting as acc
from . import auth
from .accounting import ValidationError, text

MAX_IDS = 200
BU_STATUSES = ("Tendering", "In Progress", "On Hold", "Completed", "Closed")


def _route(ctx, method, path, body=None):
    """Run one of the app's own routes as the person doing the bulk action (same permission, same rules)."""
    from .server import ACTION_DENIED, ROUTES, Ctx, request_action
    for m, rx, module, level, public, fn in ROUTES:
        match = rx.match(path) if m == method else None
        if not match:
            continue
        if module and not auth.can(ctx.user, module, level):
            raise PermissionError("You do not have permission for this action.")
        act = request_action(method, path, {})
        if module and act and not auth.action_ok(ctx.user, module, act):
            raise PermissionError(ACTION_DENIED[act])
        raw = json.dumps(body or {}).encode()
        sub = Ctx(ctx.app, ctx.db, ctx.user, ctx.token, {}, body or {}, raw, {"content-type": "application/json"})
        sub.cloud_hook = getattr(ctx, "cloud_hook", None)
        r = fn(sub, **match.groupdict())
        if getattr(r, "status", 200) >= 400:
            try:
                msg = json.loads(r.body.decode()).get("error")
            except (ValueError, AttributeError):
                msg = None
            raise ValidationError(msg or f"Refused ({r.status})")
        return r
    raise ValidationError("Not available for this record")


def _need(ctx, module, level="edit"):
    if not any(ctx.can(m, level) for m in ((module,) if isinstance(module, str) else module)):
        raise PermissionError("You do not have permission for this action.")


# ------------------------------------------------------------------ journal entries / vouchers
def _entry(ctx, eid, level="edit"):
    from .server import KIND_MODULE
    e = ctx.db.one("SELECT id, kind, status FROM journal_entries WHERE id=?", (int(eid),))
    if not e:
        raise ValidationError("Entry not found")
    if not (ctx.can("accounting.journal", level) or ctx.can(KIND_MODULE.get(e["kind"], "accounting.journal"), level)):
        raise PermissionError("You do not have permission for this voucher type.")
    if e["kind"] == "Work in Progress":
        raise ValidationError("Work in progress is kept up to date automatically")
    return e


def entry_void(ctx, eid, d):
    _entry(ctx, eid)
    reason = text(d.get("reason"), "reason", required=True)
    acc.void_entry(ctx.db, int(eid), reason, ctx.user["username"])


def entry_reverse(ctx, eid, d):
    _entry(ctx, eid)
    acc.reverse_entry(ctx.db, int(eid), d.get("date") or None, ctx.user["username"])


def entry_delete(ctx, eid, d):
    _entry(ctx, eid)
    acc.delete_entry(ctx.db, int(eid))


ENTRY_FIELDS = ("date", "bu_id", "party", "memo", "ref")


def entry_edit(ctx, eid, d):
    _entry(ctx, eid)
    ch = {k: v for k, v in (d.get("changes") or {}).items() if k in ENTRY_FIELDS}
    if "bu_id" in ch and ch["bu_id"] in ("none", "-"):
        ch["bu_id"] = None
    acc.edit_entry(ctx.db, int(eid), ch, ctx.user["username"])


# ------------------------------------------------------------------ other lists
def _simple_edit(table, module, fields, pre=None, post=None):
    """Edit a few whitelisted columns. fields: {name: cleaner(db, value) -> stored value}."""
    def run(ctx, rid, d):
        _need(ctx, module)
        row = ctx.db.one(f"SELECT * FROM {table} WHERE id=?", (int(rid),))
        if not row:
            raise ValidationError("Record not found")
        ch = {k: fields[k](ctx.db, v) for k, v in (d.get("changes") or {}).items() if k in fields}
        if pre:
            pre(ctx, row, ch)
        if ch:
            ctx.db.x(f"UPDATE {table} SET {', '.join(k + '=?' for k in ch)} WHERE id=?", list(ch.values()) + [row["id"]])
        if post:
            post(ctx, row, ch)
    return run


def _choice(options):
    def clean(db, v):
        if v not in options:
            raise ValidationError(f"Choose one of: {', '.join(options)}")
        return v
    return clean


def _bu(db, v):
    return None if v in ("none", "-", "", None) else acc.bu_id_or_none(db, v)


def _txt(db, v):
    return text(v, maxlen=200)


def _date(db, v):
    return acc.clean_date(v) if v else ""


def _bu_after(ctx, row, ch):
    if "wip_mode" in ch:
        from . import wip
        wip.sync(ctx.db, row["id"])


def _task_save(ctx, rid, d):
    from . import projects
    _need(ctx, "projects.tasks", "add")
    t = ctx.db.one("SELECT * FROM project_tasks WHERE id=?", (int(rid),))
    if not t:
        raise ValidationError("Task not found")
    ch = {k: v for k, v in (d.get("changes") or {}).items() if k in ("status", "assignee", "due", "priority", "bu_id")}
    projects.save_task(ctx.db, {**t, **ch}, t["id"], ctx.user["username"])


def _go(method, path_fmt, body=None):
    """An action done through the record's own route."""
    def run(ctx, rid, d):
        _route(ctx, method, path_fmt.format(id=rid), body(d) if callable(body) else body)
    return run


TARGETS = {
    "entries": {"void": entry_void, "reverse": entry_reverse, "delete": entry_delete, "edit": entry_edit},
    "bus": {"edit": _simple_edit("business_units", "business_units.units",
                                 {"status": lambda db, v: _choice(BU_STATUSES)(db, v), "wip_mode": lambda db, v: _choice(("", "cost_plus", "drafts"))(db, v),
                                  "customer": _txt}, post=_bu_after),
            "delete": _go("DELETE", "/api/bus/{id}")},
    "employees": {"edit": _simple_edit("employees", "payroll.employees",
                                       {"status": lambda db, v: _choice(("active", "left"))(db, v), "department": _txt, "job_title": _txt,
                                        "bu_id": _bu, "end_date": _date}),
                  "delete": _go("DELETE", "/api/employees/{id}")},
    "tasks": {"edit": _task_save, "delete": _go("DELETE", "/api/project-tasks/{id}")},
    "contracts": {"edit": _simple_edit("contracts", "contracts.contracts",
                                       {"status": lambda db, v: _choice(("draft", "active", "completed", "terminated"))(db, v)}),
                  "delete": _go("DELETE", "/api/contracts/{id}")},
    "purchase_requests": {**{a: _go("POST", "/api/purchase-requests/{id}/status", {"status": s})
                             for a, s in (("submit", "submitted"), ("approve", "approved"), ("reject", "rejected"), ("cancel", "cancelled"))},
                          "delete": _go("DELETE", "/api/purchase-requests/{id}")},
    "purchase_orders": {"approve": _go("POST", "/api/purchase-orders/{id}/approve"),
                        "close": _go("POST", "/api/purchase-orders/{id}/status", {"status": "closed"}),
                        "cancel": _go("POST", "/api/purchase-orders/{id}/status", {"status": "cancelled"}),
                        "delete": _go("DELETE", "/api/purchase-orders/{id}")},
    "equipment": {"edit": _simple_edit("equipment", "equipment.register",
                                       {"status": lambda db, v: _choice(("available", "in_use", "maintenance", "retired"))(db, v), "bu_id": _bu,
                                        "category": _txt}),
                  "delete": _go("DELETE", "/api/equipment/{id}")},
    "parties": {"edit": _simple_edit("parties", ("sales.parties", "purchasing.contacts"), {"category": _txt, "governate": _txt}),
                "delete": _go("DELETE", "/api/parties-master/{id}")},
    "invoices": {"post": _go("POST", "/api/extracts/{id}/post", {}), "delete": _go("DELETE", "/api/extracts/{id}")},
    "ereceipts": {"issue": _go("POST", "/api/ereceipts/{id}/issue"), "delete": _go("DELETE", "/api/ereceipts/{id}")},
    "stock_counts": {"cancel": _go("POST", "/api/stock-counts/{id}/cancel"), "delete": _go("DELETE", "/api/stock-counts/{id}")},
}


def run(ctx, d):
    target, action = d.get("target"), d.get("action")
    fn = (TARGETS.get(target) or {}).get(action)
    if not fn:
        raise ValidationError("This action is not available here")
    ids = []
    for i in d.get("ids") or []:
        s = str(i).strip()
        if s and s not in ids:
            ids.append(s)
    if not ids:
        raise ValidationError("Select at least one record")
    if len(ids) > MAX_IDS:
        raise ValidationError(f"Select at most {MAX_IDS} records at a time")
    if action == "void" and not text(d.get("reason")):
        raise ValidationError("Give a reason for voiding")
    if action == "edit" and not any(v not in (None, "") for v in (d.get("changes") or {}).values()):
        raise ValidationError("Choose at least one change")
    done, failed = [], []
    for rid in ids:
        try:
            fn(ctx, rid, d)
            done.append(rid)
        except (ValidationError, LookupError, PermissionError, ValueError) as e:
            failed.append({"id": rid, "error": str(e) or "You do not have permission for this action."})
        except Exception as e:  # noqa: BLE001 — one record's failure must not stop or undo the others
            failed.append({"id": rid, "error": f"Could not change it ({e.__class__.__name__})"})
    ctx.log(f"bulk.{target}.{action}", f"{len(done)} done, {len(failed)} not: {', '.join(done)[:300]}")
    return {"done": len(done), "done_ids": done, "failed": failed}
