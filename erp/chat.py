"""Chat between the people of a company, and everyone's profile.

Rooms
-----
* **General** (id 1): everyone signed in to the company; no permission needed.
* **Rooms** the super administrator creates (partners, accountants, engineers, sales…) for the people they choose;
  only members read and write. The super administrator can rename a room, change who is in it, archive it (kept,
  read-only) or delete it, and export any room with its files.
* **Direct messages** between two people.

A message can be edited (or taken back) by its author until someone else has seen it; the super administrator can
remove any message. Files of any type travel with the message (base64), pictures with a small preview.

Every change here is an ordinary API call, so with the shared workspace it reaches every device like any other change.
"""
import base64
import html
import io
import json
import re
import zipfile

from . import accounting as acc
from . import looks

GENERAL = 1
MAX_MESSAGE_FILES_MB = 15          # all files of one message (the request limit is 25 MB of base64)
MAX_BODY_CHARS = 20000
MENTION_RX = re.compile(r"(?<![\w@])@([\w.\-]+)", re.UNICODE)


class ChatError(acc.ValidationError):
    pass


# ------------------------------------------------------------------ people
PROFILE_FIELDS = ("display_name", "job_title", "department", "phone", "bio", "status")
DEFAULT_PREFS = {"nav_mode": "click", "phone_menu": "overlay",
                 "notify": {"level": "all", "rooms": [], "mode": "sound", "desktop": True, "sound": "chime", "volume": 70,
                            "vibrate": True, "vibe": "double", "banner": True, "preview": True, "reactions": True,
                            "alerts": True, "quiet": False, "quiet_from": "22:00", "quiet_to": "07:00", "ask": ""}}
SOUNDS = ("chime", "glass", "pop", "marimba", "bell", "soft", "pulse", "none", "custom")
VIBES = ("short", "double", "long", "pulse")
MAX_SOUND = 400_000            # characters of an uploaded sound (data: URL)
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def profile(db, uid):
    p = db.one("SELECT * FROM user_profiles WHERE user_id=?", (uid,)) or {"user_id": uid}
    out = {k: p.get(k) or "" for k in PROFILE_FIELDS + ("avatar",)}
    try:
        prefs = json.loads(p.get("prefs") or "{}")
    except ValueError:
        prefs = {}
    merged = {**DEFAULT_PREFS, **prefs}
    merged["notify"] = {**DEFAULT_PREFS["notify"], **(prefs.get("notify") or {})}
    out["prefs"] = merged
    return out


def save_profile(db, uid, d):
    cur = profile(db, uid)
    vals = {k: acc.text(d[k])[:500] if k in d else cur[k] for k in PROFILE_FIELDS}
    avatar = cur["avatar"]
    if "avatar" in d:
        avatar = d["avatar"] or ""
        if avatar and not re.match(r"^data:image/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+$", avatar):
            raise ChatError("The picture must be a PNG, JPEG, WebP or GIF image")
        if len(avatar) > 400_000:
            raise ChatError("The picture is too large — choose a smaller one")
    prefs = cur["prefs"]
    if isinstance(d.get("prefs"), dict):
        new = d["prefs"]
        if new.get("nav_mode") in ("hover", "click"):
            prefs["nav_mode"] = new["nav_mode"]
        if new.get("phone_menu") in ("overlay", "rail"):     # on a phone: the menu over the screen, or a slim strip of icons
            prefs["phone_menu"] = new["phone_menu"]
        if isinstance(new.get("views"), dict):    # saved filter views: {screen: [{name, q}]}
            views = {}
            for route_, lst in list(new["views"].items())[:80]:
                if not isinstance(lst, list):
                    continue
                keep = []
                for v in lst[:30]:
                    if isinstance(v, dict) and acc.text(v.get("name")) and isinstance(v.get("q"), dict):
                        keep.append({"name": acc.text(v["name"])[:60],
                                     "q": {str(k)[:40]: str(x)[:200] for k, x in list(v["q"].items())[:30] if x not in (None, "")}})
                if keep:
                    views[str(route_)[:80]] = keep
            prefs["views"] = views
        if "look" in new:         # this person's own colours, font, density and background pictures
            lk = looks.clean_look(new["look"]) if isinstance(new["look"], dict) else {}
            if lk:
                prefs["look"] = lk
            else:
                prefs.pop("look", None)
        if "notify_sound" in new:   # this person's own alert sound (an uploaded clip)
            snd = new["notify_sound"] or ""
            if snd and not re.match(r"^data:audio/(mpeg|mp3|wav|x-wav|ogg|webm|aac|mp4|x-m4a);base64,[A-Za-z0-9+/=]+$", snd):
                raise ChatError("The sound must be an MP3, WAV, OGG, WebM or M4A file")
            if len(snd) > MAX_SOUND:
                raise ChatError("That sound is too long — pick a clip under about 250 KB (a few seconds)")
            if snd:
                prefs["notify_sound"] = snd
            else:
                prefs.pop("notify_sound", None)
        if "themes" in new:       # themes this person saved for themselves
            prefs["themes"] = looks.clean_themes(new["themes"])
        tr = new.get("tour")      # the guided tour: where this person stopped, or that they never want it
        if isinstance(tr, dict) and tr.get("state") in ("new", "paused", "done", "never"):
            prefs["tour"] = {"state": tr["state"], "step": max(0, min(99, int(tr.get("step") or 0))),
                             "kind": acc.text(tr.get("kind"))[:30]}
        n = new.get("notify")
        if isinstance(n, dict):
            if n.get("level") in ("all", "mentions", "rooms", "none"):
                prefs["notify"]["level"] = n["level"]
            if n.get("mode") in ("sound", "vibrate", "silent"):
                prefs["notify"]["mode"] = n["mode"]
            if "desktop" in n:
                prefs["notify"]["desktop"] = bool(n["desktop"])
            for k in ("vibrate", "banner", "preview", "reactions", "alerts", "quiet", "push", "links",
                      "approvals", "decisions", "fu_new", "fu_due", "desk", "bell"):     # each kind of notification
                if k in n:
                    prefs["notify"][k] = bool(n[k])
            if n.get("sound") in SOUNDS:
                prefs["notify"]["sound"] = n["sound"]
            if n.get("vibe") in VIBES:
                prefs["notify"]["vibe"] = n["vibe"]
            if "volume" in n:
                try:
                    prefs["notify"]["volume"] = max(0, min(100, int(float(n["volume"]))))
                except (TypeError, ValueError):
                    pass
            for k in ("quiet_from", "quiet_to", "fu_time"):
                if isinstance(n.get(k), str) and _HHMM.match(n[k]):
                    prefs["notify"][k] = n[k]
            if n.get("ask") in ("", "never", "done"):      # the "turn on notifications?" question: never ask again / answered
                prefs["notify"]["ask"] = n["ask"]
            for k in ("rooms", "muted"):        # rooms that notify me / rooms muted except when I am named
                if isinstance(n.get(k), list):
                    prefs["notify"][k] = [int(x) for x in n[k] if str(x).lstrip("-").isdigit()][:200]
            if isinstance(n.get("quiet_days"), list):
                prefs["notify"]["quiet_days"] = sorted({int(x) for x in n["quiet_days"] if str(x).isdigit() and 0 <= int(x) <= 6})
            if "snooze_until" in n:              # paused until this moment (ms), 0 = not paused
                try:
                    prefs["notify"]["snooze_until"] = max(0, int(float(n["snooze_until"] or 0)))
                except (TypeError, ValueError):
                    pass
    db.x("""INSERT INTO user_profiles(user_id, display_name, job_title, department, phone, bio, status, avatar, prefs, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET display_name=excluded.display_name, job_title=excluded.job_title,
              department=excluded.department, phone=excluded.phone, bio=excluded.bio, status=excluded.status,
              avatar=excluded.avatar, prefs=excluded.prefs, updated_at=excluded.updated_at""",
         (uid, *[vals[k] for k in PROFILE_FIELDS], avatar, json.dumps(prefs), acc.now_iso()))
    return profile(db, uid)


def people(db):
    """Everyone in the company (for chat, mentions and avatars): never permissions or contact details of others
    beyond what a colleague would see on a business card."""
    rows = db.q("""SELECT u.id, u.username, u.full_name, u.is_admin, u.is_super, u.active,
                          p.display_name, p.job_title, p.department, p.status, p.avatar
                   FROM users u LEFT JOIN user_profiles p ON p.user_id=u.id ORDER BY u.full_name, u.username""")
    return [{"id": r["id"], "username": r["username"], "name": r["display_name"] or r["full_name"] or r["username"],
             "job_title": r["job_title"] or "", "department": r["department"] or "", "status": r["status"] or "",
             "avatar": r["avatar"] or "", "active": bool(r["active"]),
             "role": "super" if r["is_super"] else "admin" if r["is_admin"] else "user"} for r in rows]


# ------------------------------------------------------------------ rooms
def _room(db, rid):
    r = db.one("SELECT * FROM chat_rooms WHERE id=?", (int(rid),))
    if not r:
        raise LookupError("No such chat room")
    return r


def _members(db, rid):
    return [r["user_id"] for r in db.q("SELECT user_id FROM chat_members WHERE room_id=? ORDER BY user_id", (rid,))]


def can_read(db, user, room):
    if room["kind"] == "general":
        return True
    if room["kind"] == "room" and user.get("is_super"):
        return True              # the super administrator manages and exports every room
    return bool(db.one("SELECT 1 FROM chat_members WHERE room_id=? AND user_id=?", (room["id"], user["id"])))


def can_write(db, user, room):
    if room["archived_at"]:
        return False
    if room["kind"] == "general":
        return True
    return bool(db.one("SELECT 1 FROM chat_members WHERE room_id=? AND user_id=?", (room["id"], user["id"])))


def _need_read(db, user, rid):
    room = _room(db, rid)
    if not can_read(db, user, room):
        raise PermissionError()
    return room


def _need_super(user):
    if not user.get("is_super"):
        raise PermissionError("Only a super administrator can manage chat rooms")


def rooms(db, user):
    """The rooms this person can open, newest activity first, with unread counts and the last message."""
    uid = user["id"]
    rows = db.q("""SELECT r.*, (SELECT MAX(id) FROM chat_messages m WHERE m.room_id=r.id) AS last_id,
                          COALESCE((SELECT last_read FROM chat_reads x WHERE x.room_id=r.id AND x.user_id=?), 0) AS my_read
                   FROM chat_rooms r ORDER BY r.id""", (uid,))
    out = []
    names = {p["id"]: p for p in people(db)}
    for r in rows:
        if not can_read(db, user, r):
            continue
        members = _members(db, r["id"])
        last = db.one("""SELECT m.id, m.user_id, m.body, m.created_at, m.deleted_at,
                                (SELECT COUNT(*) FROM chat_files f WHERE f.message_id=m.id) AS files,
                               (SELECT MIN(f.mime) FROM chat_files f WHERE f.message_id=m.id) AS file_mime
                         FROM chat_messages m WHERE m.id=?""", (r["last_id"],)) if r["last_id"] else None
        unread = db.one("SELECT COUNT(*) n FROM chat_messages WHERE room_id=? AND id>? AND COALESCE(user_id,0)<>? "
                        "AND deleted_at IS NULL", (r["id"], r["my_read"], uid))["n"]
        mention = sum(1 for m in db.q("SELECT mentions FROM chat_messages WHERE room_id=? AND id>? AND COALESCE(user_id,0)<>? "
                                      "AND deleted_at IS NULL AND mentions<>'[]'", (r["id"], r["my_read"], uid))
                      if {uid, -1} & set(json.loads(m["mentions"] or "[]")))
        name = r["name"]
        other = None
        if r["kind"] == "dm":
            other = next((m for m in members if m != uid), uid)
            name = (names.get(other) or {}).get("name", "?")
        out.append({"id": r["id"], "kind": r["kind"], "name": name, "topic": r["topic"] or "", "icon": r["icon"] or "",
                    "members": members, "other": other, "archived": bool(r["archived_at"]),
                    "member": r["kind"] == "general" or uid in members, "can_write": can_write(db, user, r),
                    "unread": unread, "mentions": mention, "last_id": r["last_id"] or 0, "my_read": r["my_read"],
                    "last": last and {"user_id": last["user_id"], "at": last["created_at"],
                                      "text": "" if last["deleted_at"] else (last["body"] or "")[:140],
                                      "files": last["files"], "file_mime": last["file_mime"] or "", "deleted": bool(last["deleted_at"])}})
    out.sort(key=lambda x: (x["kind"] != "general", -(x["last_id"] or 0), x["name"].lower()))
    return out


def _clean_members(db, ids):
    ids = sorted({int(i) for i in ids or [] if str(i).isdigit()})
    if ids:
        have = {r["id"] for r in db.q(f"SELECT id FROM users WHERE id IN ({','.join('?' * len(ids))})", ids)}
        ids = [i for i in ids if i in have]
    return ids


def create_room(db, user, d):
    _need_super(user)
    name = acc.text(d.get("name"))[:80]
    if not name:
        raise ChatError("Give the room a name")
    if db.one("SELECT 1 FROM chat_rooms WHERE kind='room' AND lower(name)=lower(?) AND archived_at IS NULL", (name,)):
        raise ChatError("A room with this name already exists")
    now = acc.now_iso()
    rid = db.x("INSERT INTO chat_rooms(kind, name, topic, icon, created_by, created_at) VALUES ('room',?,?,?,?,?)",
               (name, acc.text(d.get("topic"))[:300], acc.text(d.get("icon"))[:8], user["id"], now)).lastrowid
    for uid in set(_clean_members(db, d.get("members"))) | {user["id"]}:
        db.x("INSERT OR IGNORE INTO chat_members(room_id, user_id, added_by, added_at) VALUES (?,?,?,?)", (rid, uid, user["id"], now))
    return rid


def update_room(db, user, rid, d):
    _need_super(user)
    room = _room(db, rid)
    if room["kind"] == "dm":
        raise ChatError("Direct messages cannot be changed")
    sets, vals = [], []
    for k, n in (("name", 80), ("topic", 300), ("icon", 8)):
        if k in d:
            v = acc.text(d[k])[:n]
            if k == "name" and not v:
                raise ChatError("Give the room a name")
            sets.append(f"{k}=?")
            vals.append(v)
    if sets and room["kind"] == "room":
        db.x(f"UPDATE chat_rooms SET {', '.join(sets)} WHERE id=?", (*vals, room["id"]))
    elif sets:   # General keeps its name; only the topic and icon change
        keep = [(s, v) for s, v in zip(sets, vals) if not s.startswith("name")]
        if keep:
            db.x(f"UPDATE chat_rooms SET {', '.join(s for s, _ in keep)} WHERE id=?", (*[v for _, v in keep], room["id"]))
    if "members" in d and room["kind"] == "room":
        want = set(_clean_members(db, d["members"]))
        have = set(_members(db, room["id"]))
        now = acc.now_iso()
        for uid in want - have:
            db.x("INSERT OR IGNORE INTO chat_members(room_id, user_id, added_by, added_at) VALUES (?,?,?,?)",
                 (room["id"], uid, user["id"], now))
        for uid in have - want:
            db.x("DELETE FROM chat_members WHERE room_id=? AND user_id=?", (room["id"], uid))
    return room["id"]


def archive_room(db, user, rid, archived=True):
    _need_super(user)
    room = _room(db, rid)
    if room["kind"] == "general":
        raise ChatError("The general room cannot be archived")
    db.x("UPDATE chat_rooms SET archived_at=?, archived_by=? WHERE id=?",
         (acc.now_iso() if archived else None, user["id"] if archived else None, room["id"]))


def delete_room(db, user, rid):
    _need_super(user)
    room = _room(db, rid)
    if room["kind"] == "general":
        raise ChatError("The general room cannot be deleted")
    db.x("DELETE FROM chat_rooms WHERE id=?", (room["id"],))
    return room


def direct(db, user, other_id):
    other = db.one("SELECT id FROM users WHERE id=? AND active=1", (int(other_id),))
    if not other:
        raise ChatError("No such person")
    a, b = sorted((user["id"], other["id"]))
    key = f"{a}:{b}"
    r = db.one("SELECT id FROM chat_rooms WHERE dm_key=?", (key,))
    if r:
        return r["id"]
    now = acc.now_iso()
    rid = db.x("INSERT INTO chat_rooms(kind, name, dm_key, created_by, created_at) VALUES ('dm','',?,?,?)",
               (key, user["id"], now)).lastrowid
    for uid in {a, b}:
        db.x("INSERT OR IGNORE INTO chat_members(room_id, user_id, added_by, added_at) VALUES (?,?,?,?)", (rid, uid, user["id"], now))
    return rid


# ------------------------------------------------------------------ messages
def _seen_by_others(db, msg):
    return [r["user_id"] for r in db.q("SELECT user_id FROM chat_reads WHERE room_id=? AND last_read>=? AND user_id<>?",
                                       (msg["room_id"], msg["id"], msg["user_id"] or 0))]


def _mentions(db, room, body):
    names = {m.group(1).lower() for m in MENTION_RX.finditer(body or "")}
    if not names:
        return []
    out = []
    if names & {"all", "everyone", "الكل"}:
        out.append(-1)
    for u in db.q("SELECT id, username FROM users WHERE active=1"):
        if u["username"].lower() in names:
            out.append(u["id"])
    return out


def _files_in(d):
    files, total = [], 0
    for f in d.get("files") or []:
        data = str(f.get("data") or "")
        if data.startswith("data:"):
            data = data.split(",", 1)[1] if "," in data else ""
        try:
            raw = base64.b64decode(data, validate=True)
        except (ValueError, TypeError):
            raise ChatError(f"Could not read the file {f.get('name') or ''}")
        total += len(raw)
        thumb = str(f.get("thumb") or "")
        if thumb and (not thumb.startswith("data:image/") or len(thumb) > 200_000):
            thumb = ""
        files.append({"name": acc.text(f.get("name") or "file")[:200] or "file",
                      "mime": acc.text(f.get("mime") or "application/octet-stream")[:120] or "application/octet-stream",
                      "size": len(raw), "data": data, "thumb": thumb})
    if total > MAX_MESSAGE_FILES_MB * 1024 * 1024:
        raise ChatError(f"Files are limited to {MAX_MESSAGE_FILES_MB} MB per message")
    return files


def send(db, user, rid, d):
    room = _room(db, rid)
    if not can_write(db, user, room):
        raise PermissionError("This room is archived" if room["archived_at"] else "You are not a member of this room")
    body = str(d.get("body") or "").strip()[:MAX_BODY_CHARS]
    files = _files_in(d)
    if not body and not files:
        raise ChatError("Write a message or attach a file")
    reply_to = d.get("reply_to")
    if reply_to and not db.one("SELECT 1 FROM chat_messages WHERE id=? AND room_id=?", (int(reply_to), room["id"])):
        reply_to = None
    mid = db.x("INSERT INTO chat_messages(room_id, user_id, body, mentions, reply_to, created_at) VALUES (?,?,?,?,?,?)",
               (room["id"], user["id"], body, json.dumps(_mentions(db, room, body)), reply_to and int(reply_to),
                acc.now_iso())).lastrowid
    for f in files:
        db.x("INSERT INTO chat_files(message_id, name, mime, size, thumb, data) VALUES (?,?,?,?,?,?)",
             (mid, f["name"], f["mime"], f["size"], f["thumb"], f["data"]))
    mark_read(db, user, room["id"], mid)
    return mid


def _msg(db, mid):
    m = db.one("SELECT * FROM chat_messages WHERE id=?", (int(mid),))
    if not m:
        raise LookupError("No such message")
    return m


def edit(db, user, mid, d):
    m = _msg(db, mid)
    room = _room(db, m["room_id"])
    if m["user_id"] != user["id"]:
        raise PermissionError("Only the author can edit a message")
    if m["deleted_at"]:
        raise ChatError("This message was removed")
    if room["archived_at"]:
        raise ChatError("This room is archived")
    if _seen_by_others(db, m):
        raise ChatError("Someone has already seen this message — it can no longer be edited")
    body = str(d.get("body") or "").strip()[:MAX_BODY_CHARS]
    if not body and not db.one("SELECT 1 FROM chat_files WHERE message_id=?", (m["id"],)):
        raise ChatError("A message cannot be empty")
    db.x("UPDATE chat_messages SET body=?, mentions=?, edited_at=? WHERE id=?",
         (body, json.dumps(_mentions(db, room, body)), acc.now_iso(), m["id"]))


def remove(db, user, mid):
    m = _msg(db, mid)
    if not user.get("is_super"):
        if m["user_id"] != user["id"]:
            raise PermissionError("Only the author or a super administrator can remove a message")
        if _seen_by_others(db, m):
            raise ChatError("Someone has already seen this message — ask a super administrator to remove it")
    db.x("UPDATE chat_messages SET body='', mentions='[]', deleted_at=? WHERE id=?", (acc.now_iso(), m["id"]))
    db.x("DELETE FROM chat_files WHERE message_id=?", (m["id"],))
    db.x("DELETE FROM chat_reactions WHERE message_id=?", (m["id"],))


def mark_read(db, user, rid, last):
    last = int(last or 0)
    db.x("""INSERT INTO chat_reads(room_id, user_id, last_read, at) VALUES (?,?,?,?)
            ON CONFLICT(room_id, user_id) DO UPDATE SET last_read=MAX(last_read, excluded.last_read), at=excluded.at""",
         (int(rid), user["id"], last, acc.now_iso()))


def messages(db, user, rid, after=0, before=0, limit=60):
    room = _need_read(db, user, rid)
    limit = max(1, min(int(limit or 60), 200))
    w, p = ["room_id=?"], [room["id"]]
    if after:
        w.append("id>?")
        p.append(int(after))
    if before:
        w.append("id<?")
        p.append(int(before))
    order = "ASC" if after else "DESC"
    rows = db.q(f"SELECT * FROM chat_messages WHERE {' AND '.join(w)} ORDER BY id {order} LIMIT ?", p + [limit])
    if order == "DESC":
        rows.reverse()
    ids = [r["id"] for r in rows]
    files = {}
    if ids:
        for f in db.q(f"SELECT id, message_id, name, mime, size, thumb FROM chat_files WHERE message_id IN "
                      f"({','.join('?' * len(ids))}) ORDER BY id", ids):
            files.setdefault(f["message_id"], []).append({k: f[k] for k in ("id", "name", "mime", "size", "thumb")})
    reads = {r["user_id"]: r["last_read"] for r in db.q("SELECT user_id, last_read FROM chat_reads WHERE room_id=?", (room["id"],))}
    reacts = _reactions(db, ids, user["id"])
    out = []
    for r in rows:
        seen = [u for u, last in reads.items() if last >= r["id"] and u != r["user_id"]]
        out.append({"id": r["id"], "user_id": r["user_id"], "body": r["body"], "reply_to": r["reply_to"],
                    "mentions": json.loads(r["mentions"] or "[]"), "at": r["created_at"], "edited": r["edited_at"],
                    "deleted": bool(r["deleted_at"]), "files": files.get(r["id"], []), "seen_by": seen,
                    "reactions": reacts.get(r["id"], []),
                    "editable": r["user_id"] == user["id"] and not seen and not r["deleted_at"] and not room["archived_at"]})
    return {"room": {"id": room["id"], "kind": room["kind"], "archived": bool(room["archived_at"]),
                     "can_write": can_write(db, user, room)},
            "messages": out, "more": bool(rows) and not after and len(rows) == limit}


def _reactions(db, ids, me):
    """Reactions per message: each emoji with how many, who, and whether one of them is mine (first used first)."""
    out = {}
    if not ids:
        return out
    for r in db.q(f"SELECT message_id, user_id, emoji FROM chat_reactions WHERE message_id IN ({','.join('?' * len(ids))}) ORDER BY id",
                  list(ids)):
        lst = out.setdefault(r["message_id"], [])
        e = next((x for x in lst if x["emoji"] == r["emoji"]), None)
        if not e:
            e = {"emoji": r["emoji"], "count": 0, "users": [], "mine": False}
            lst.append(e)
        e["count"] += 1
        e["users"].append(r["user_id"])
        e["mine"] = e["mine"] or r["user_id"] == me
    return out


def _emoji_ok(e):
    """A short emoji (with skin tone / joiner sequences), never text or markup."""
    return 0 < len(e) <= 16 and not re.search(r"[A-Za-z0-9<>&\"'\s]", e) and any(ord(c) > 0x2000 for c in e)


def react(db, user, mid, emoji):
    """React to a message. The same emoji again takes it back; another one replaces it (one per person)."""
    m = _msg(db, mid)
    room = _room(db, m["room_id"])
    _need_read(db, user, room["id"])
    if m["deleted_at"]:
        raise ChatError("This message was removed")
    if room["archived_at"]:
        raise ChatError("This room is archived")
    emoji = str(emoji or "").strip()
    cur = db.one("SELECT emoji FROM chat_reactions WHERE message_id=? AND user_id=?", (m["id"], user["id"]))
    db.x("DELETE FROM chat_reactions WHERE message_id=? AND user_id=?", (m["id"], user["id"]))
    if not emoji or (cur and cur["emoji"] == emoji):
        return None
    if not _emoji_ok(emoji):
        raise ChatError("Pick an emoji to react with")
    db.x("INSERT INTO chat_reactions(message_id, user_id, emoji, at) VALUES (?,?,?,?)", (m["id"], user["id"], emoji, acc.now_iso()))
    return emoji


def file(db, user, fid):
    f = db.one("SELECT f.*, m.room_id FROM chat_files f JOIN chat_messages m ON m.id=f.message_id WHERE f.id=?", (int(fid),))
    if not f:
        raise LookupError("No such file")
    _need_read(db, user, f["room_id"])
    return f["name"], f["mime"], base64.b64decode(f["data"])


def updates(db, user, since=0, rsince=None):
    """New messages for this person since a message id (for notifications), the unread total, and — when asked —
    reactions others put on this person's messages since a reaction id."""
    since = int(since or 0)
    mine = rooms(db, user)
    visible = {r["id"]: r for r in mine if r["member"] or r["kind"] == "general"}
    rows = []
    if visible:
        ids = list(visible)
        rows = db.q(f"""SELECT id, room_id, user_id, body, mentions, created_at,
                               (SELECT COUNT(*) FROM chat_files f WHERE f.message_id=m.id) AS files,
                               (SELECT MIN(f.mime) FROM chat_files f WHERE f.message_id=m.id) AS file_mime
                        FROM chat_messages m WHERE id>? AND room_id IN ({','.join('?' * len(ids))})
                          AND COALESCE(user_id,0)<>? AND deleted_at IS NULL ORDER BY id LIMIT 50""",
                    [since, *ids, user["id"]])
    last = db.one("SELECT MAX(id) m FROM chat_messages")["m"] or 0
    out = []
    for r in rows:
        men = json.loads(r["mentions"] or "[]")
        room = visible[r["room_id"]]
        out.append({"id": r["id"], "room_id": r["room_id"], "room": room["name"], "kind": room["kind"],
                    "user_id": r["user_id"], "text": (r["body"] or "")[:200], "files": r["files"], "file_mime": r["file_mime"] or "",
                    "mention": user["id"] in men or (-1 in men), "at": r["created_at"]})
    res = {}
    if rsince is not None:
        rows = db.q("""SELECT r.id, r.user_id, r.emoji, m.id AS message_id, m.room_id, m.body FROM chat_reactions r
                       JOIN chat_messages m ON m.id=r.message_id
                       WHERE r.id>? AND m.user_id=? AND r.user_id<>? AND m.deleted_at IS NULL ORDER BY r.id LIMIT 30""",
                    (int(rsince or 0), user["id"], user["id"]))
        res = {"reactions_last": (db.one("SELECT MAX(id) m FROM chat_reactions")["m"] or 0),
               "reactions": [{**{k: r[k] for k in ("id", "user_id", "emoji", "message_id", "room_id")},
                              "room": (visible.get(r["room_id"]) or {}).get("name", ""), "text": (r["body"] or "")[:120]}
                             for r in rows if r["room_id"] in visible]}
    return {**res, "last": last, "messages": out,
            "unread": sum(r["unread"] for r in mine if r["member"] or r["kind"] == "general"),
            "mentions": sum(r["mentions"] for r in mine)}


# ------------------------------------------------------------------ export
def export_zip(db, user, rid, company=""):
    """A room's whole history: transcript (HTML and text), the raw messages (JSON) and every file."""
    _need_super(user)
    room = _room(db, rid)
    names = {p["id"]: p["name"] for p in people(db)}
    msgs = db.q("SELECT * FROM chat_messages WHERE room_id=? ORDER BY id", (room["id"],))
    files = {}
    for f in db.q("SELECT f.* FROM chat_files f JOIN chat_messages m ON m.id=f.message_id WHERE m.room_id=? ORDER BY f.id",
                  (room["id"],)):
        files.setdefault(f["message_id"], []).append(f)
    title = room["name"] if room["kind"] != "dm" else "Direct messages: " + " & ".join(names.get(u, "?") for u in _members(db, room["id"]))
    buf = io.BytesIO()
    txt, rows, data = [], [], []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for m in msgs:
            who = names.get(m["user_id"], "?")
            fl = []
            for f in files.get(m["id"], []):
                safe = re.sub(r"[^\w.\- ()؀-ۿ]+", "_", f["name"]) or "file"
                path = f"files/{f['id']}-{safe}"
                z.writestr(path, base64.b64decode(f["data"]))
                fl.append({"name": f["name"], "path": path, "mime": f["mime"], "size": f["size"]})
            body = "(removed)" if m["deleted_at"] else m["body"]
            txt.append(f"[{m['created_at']}] {who}: {body}" + (" (edited)" if m["edited_at"] else "")
                       + "".join(f"\n    📎 {f['path']}" for f in fl))
            rows.append(f"<div class='m'><div class='h'><b>{html.escape(who)}</b> <span>{html.escape(m['created_at'])}"
                        f"{' · edited' if m['edited_at'] else ''}</span></div><div class='b'>{html.escape(body).replace(chr(10), '<br>')}</div>"
                        + "".join(f"<div class='f'>📎 <a href='{html.escape(f['path'])}'>{html.escape(f['name'])}</a>"
                                  + (f"<br><img src='{html.escape(f['path'])}'>" if f["mime"].startswith("image/") else
                                     f"<br><audio controls src='{html.escape(f['path'])}'></audio>" if f["mime"].startswith("audio/") else
                                     f"<br><video controls src='{html.escape(f['path'])}'></video>" if f["mime"].startswith("video/") else "")
                                  + "</div>" for f in fl) + "</div>")
            data.append({"id": m["id"], "author": who, "author_id": m["user_id"], "at": m["created_at"], "body": body,
                         "edited_at": m["edited_at"], "removed": bool(m["deleted_at"]), "reply_to": m["reply_to"], "files": fl})
        head = f"{company} — {title}" if company else title
        z.writestr("transcript.txt", f"{head}\nExported {acc.now_iso()}\n\n" + "\n".join(txt))
        z.writestr("transcript.html", "<!doctype html><meta charset='utf-8'><title>" + html.escape(head) + "</title>"
                   "<style>body{font-family:system-ui,'Segoe UI',Tahoma,sans-serif;max-width:860px;margin:24px auto;padding:0 16px;color:#1f2937}"
                   ".m{border-bottom:1px solid #e5e7eb;padding:10px 0}.h span{color:#6b7280;font-size:.85em}.b{white-space:normal;margin-top:4px}"
                   ".f{margin-top:6px}img{max-width:420px;border-radius:8px;margin-top:6px}</style>"
                   f"<h1>{html.escape(head)}</h1><p>Exported {html.escape(acc.now_iso())} · {len(msgs)} messages</p>" + "".join(rows))
        z.writestr("messages.json", json.dumps({"room": {"id": room["id"], "kind": room["kind"], "name": title,
                                                         "topic": room["topic"], "archived_at": room["archived_at"]},
                                                "members": [names.get(u, "?") for u in _members(db, room["id"])],
                                                "messages": data}, ensure_ascii=False, indent=1))
    safe = re.sub(r"[^\w\-]+", "_", title).strip("_")[:60] or "chat"
    return buf.getvalue(), f"Chat_{safe}_{acc.today()}.zip"


# ------------------------------------------------------------- backups (JSON export / the workbook's hidden data)
def export_rows(db):
    """Profiles and every conversation, with people by username (user numbers differ between installations)."""
    names = {u["id"]: u["username"] for u in db.q("SELECT id, username FROM users")}
    nm = lambda uid: names.get(uid, "") if uid else ""   # noqa: E731
    profiles = [{"user": nm(p["user_id"]), **{k: p[k] for k in ("display_name", "job_title", "department", "phone", "bio",
                                                                  "status", "avatar", "prefs", "updated_at")}}
                for p in db.q("SELECT * FROM user_profiles") if nm(p["user_id"])]
    rooms_out = []
    for r in db.q("SELECT * FROM chat_rooms ORDER BY id"):
        msgs = db.q("SELECT * FROM chat_messages WHERE room_id=? ORDER BY id", (r["id"],))
        pos = {m["id"]: i for i, m in enumerate(msgs)}
        files = {}
        for f in db.q("SELECT f.* FROM chat_files f JOIN chat_messages m ON m.id=f.message_id WHERE m.room_id=? ORDER BY f.id", (r["id"],)):
            files.setdefault(f["message_id"], []).append({k: f[k] for k in ("name", "mime", "size", "thumb", "data")})
        rooms_out.append({
            "kind": r["kind"], "name": r["name"], "topic": r["topic"], "icon": r["icon"],
            "dm": [nm(int(x)) for x in (r["dm_key"] or "").split(":") if x.isdigit()],
            "created_by": nm(r["created_by"]), "created_at": r["created_at"], "archived_at": r["archived_at"],
            "archived_by": nm(r["archived_by"]),
            "members": [nm(m["user_id"]) for m in db.q("SELECT user_id FROM chat_members WHERE room_id=?", (r["id"],)) if nm(m["user_id"])],
            "joined": {nm(m["user_id"]): [nm(m["added_by"]), m["added_at"]] for m in db.q("SELECT * FROM chat_members WHERE room_id=?", (r["id"],))
                       if nm(m["user_id"])},   # who added each member, and when
            "messages": [{"user": nm(m["user_id"]), "body": m["body"],
                          "mentions": [x if x == -1 else nm(x) for x in json.loads(m["mentions"] or "[]")],
                          "reply_to": pos.get(m["reply_to"]), "created_at": m["created_at"], "edited_at": m["edited_at"],
                          "deleted_at": m["deleted_at"], "files": files.get(m["id"], []),
                          "reactions": [{"user": nm(x["user_id"]), "emoji": x["emoji"], "at": x["at"]}
                                        for x in db.q("SELECT * FROM chat_reactions WHERE message_id=? ORDER BY id", (m["id"],)) if nm(x["user_id"])]}
                         for m in msgs],
            "reads": {nm(x["user_id"]): pos[x["last_read"]] for x in db.q("SELECT * FROM chat_reads WHERE room_id=?", (r["id"],))
                      if nm(x["user_id"]) and x["last_read"] in pos},
            "read_at": {nm(x["user_id"]): x["at"] for x in db.q("SELECT * FROM chat_reads WHERE room_id=?", (r["id"],)) if nm(x["user_id"])}})
    return {"profiles": profiles, "rooms": rooms_out}


def import_rows(db, data, mode, counts):
    """Put back what export_rows wrote. People are matched by username; someone not in these books keeps their
    messages without a name."""
    ids = {u["username"]: u["id"] for u in db.q("SELECT id, username FROM users")}
    uid = lambda n: ids.get(n) if n else None   # noqa: E731
    for p in data.get("profiles") or []:
        if isinstance(p, dict) and uid(p.get("user")):
            db.conn.execute("INSERT OR REPLACE INTO user_profiles(user_id,display_name,job_title,department,phone,bio,status,avatar,prefs,updated_at) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?)", (uid(p["user"]), *[p.get(k) or "" for k in ("display_name", "job_title", "department",
                                                                                                      "phone", "bio", "status", "avatar")],
                                                             p.get("prefs") or "{}", p.get("updated_at")))
    rooms_in = [r for r in data.get("rooms") or [] if isinstance(r, dict)]
    if not rooms_in:
        return
    if mode != "replace" and db.one("SELECT 1 FROM chat_messages"):
        return   # adding to books that already have conversations: keep theirs
    for t in ("chat_reactions", "chat_reads", "chat_files", "chat_messages", "chat_members"):
        db.conn.execute(f"DELETE FROM {t}")
    db.conn.execute("DELETE FROM chat_rooms WHERE id<>1")
    n = 0
    for r in rooms_in:
        if r.get("kind") == "general":
            rid = 1
            db.conn.execute("UPDATE chat_rooms SET name=?, topic=?, icon=? WHERE id=1", (r.get("name") or "General", r.get("topic") or "", r.get("icon") or ""))
        else:
            dm = [uid(x) for x in r.get("dm") or []]
            if r.get("kind") == "dm" and (len(dm) != 2 or None in dm):
                continue   # a direct conversation with someone who is not in these books
            rid = db.conn.execute("INSERT INTO chat_rooms(kind,name,topic,icon,dm_key,created_by,created_at,archived_at,archived_by) VALUES (?,?,?,?,?,?,?,?,?)",
                                  (r.get("kind") or "room", r.get("name") or "", r.get("topic") or "", r.get("icon") or "",
                                   ":".join(str(x) for x in sorted(dm)) if r.get("kind") == "dm" else None, uid(r.get("created_by")),
                                   r.get("created_at"), r.get("archived_at"), uid(r.get("archived_by")))).lastrowid
        for m in r.get("members") or []:
            if uid(m):
                by, at = ((r.get("joined") or {}).get(m) or [None, None])[:2]
                db.conn.execute("INSERT OR IGNORE INTO chat_members(room_id,user_id,added_by,added_at) VALUES (?,?,?,?)", (rid, uid(m), uid(by), at))
        new_ids = []
        for m in r.get("messages") or []:
            mentions = [x if x == -1 else uid(x) for x in m.get("mentions") or []]
            reply = m.get("reply_to")
            mid = db.conn.execute("INSERT INTO chat_messages(room_id,user_id,body,mentions,reply_to,created_at,edited_at,deleted_at) VALUES (?,?,?,?,?,?,?,?)",
                                  (rid, uid(m.get("user")), m.get("body") or "", json.dumps([x for x in mentions if x is not None]),
                                   new_ids[reply] if isinstance(reply, int) and 0 <= reply < len(new_ids) else None,
                                   m.get("created_at") or acc.now_iso(), m.get("edited_at"), m.get("deleted_at"))).lastrowid
            new_ids.append(mid)
            for x in m.get("reactions") or []:
                if isinstance(x, dict) and uid(x.get("user")) and _emoji_ok(str(x.get("emoji") or "")):
                    db.conn.execute("INSERT OR IGNORE INTO chat_reactions(message_id,user_id,emoji,at) VALUES (?,?,?,?)",
                                    (mid, uid(x["user"]), x["emoji"], x.get("at")))
            for f in m.get("files") or []:
                db.conn.execute("INSERT INTO chat_files(message_id,name,mime,size,thumb,data) VALUES (?,?,?,?,?,?)",
                                (mid, f.get("name") or "file", f.get("mime") or "application/octet-stream", int(f.get("size") or 0),
                                 f.get("thumb") or "", f.get("data") or ""))
            n += 1
        for who, i in (r.get("reads") or {}).items():
            if uid(who) and isinstance(i, int) and 0 <= i < len(new_ids):
                db.conn.execute("INSERT OR REPLACE INTO chat_reads(room_id,user_id,last_read,at) VALUES (?,?,?,?)",
                                (rid, uid(who), new_ids[i], (r.get("read_at") or {}).get(who)))
    counts["Chat messages"] = n
