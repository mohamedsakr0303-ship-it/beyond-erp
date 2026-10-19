"""Knowledge base: the company's own how-tos, policies and answers (how we price a job, the return policy, onboarding
steps…). Everyone signed in reads it — and so does the AI assistant, which can answer from it. Permissions (Workplace → Knowledge Base):
view = read, add = also write and change your own, full = change or delete anyone's."""
from .accounting import ValidationError, now_iso, text


def save(db, d, user, kid=None, any_article=False):
    title = text(d.get("title"), "title", required=True, maxlen=200)
    vals = (title, text(d.get("body"), maxlen=200000), text(d.get("category"), maxlen=80), 1 if d.get("pinned") in (True, 1, "1") else 0)
    if kid:
        r = db.one("SELECT * FROM kb_pages WHERE id=?", (int(kid),))
        if not r:
            raise ValidationError("Not found")
        if r["created_by"] != user["username"] and not (any_article or user.get("is_admin")):
            raise PermissionError("Only the author, or a user with full rights on the knowledge base, can edit this article.")
        db.x("UPDATE kb_pages SET title=?, body=?, category=?, pinned=?, updated_by=?, updated_at=? WHERE id=?", vals + (user["username"], now_iso(), int(kid)))
        return int(kid)
    return db.x("INSERT INTO kb_pages(title, body, category, pinned, created_by, created_at, updated_by, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                vals + (user["username"], now_iso(), user["username"], now_iso())).lastrowid


def delete(db, kid, user, any_article=False):
    r = db.one("SELECT * FROM kb_pages WHERE id=?", (int(kid),))
    if not r:
        raise ValidationError("Not found")
    if r["created_by"] != user["username"] and not (any_article or user.get("is_admin")):
        raise PermissionError("Only the author, or a user with full rights on the knowledge base, can delete this article.")
    db.x("DELETE FROM kb_pages WHERE id=?", (int(kid),))


def listing(db, q=None, category=None):
    w, p = ["1=1"], []
    if q:
        for word in str(q).split()[:6]:
            w.append("(title LIKE ? OR body LIKE ? OR category LIKE ?)")
            p += [f"%{word}%"] * 3
    if category:
        w.append("category=?")
        p.append(category)
    rows = db.q(f"SELECT id, title, category, pinned, substr(body, 1, 240) excerpt, updated_by, updated_at, created_by FROM kb_pages "
                f"WHERE {' AND '.join(w)} ORDER BY pinned DESC, updated_at DESC LIMIT 300", p)
    cats = [r["category"] for r in db.q("SELECT DISTINCT category FROM kb_pages WHERE category<>'' ORDER BY category")]
    return {"rows": rows, "categories": cats}


def get(db, kid):
    r = db.one("SELECT * FROM kb_pages WHERE id=?", (int(kid),))
    if not r:
        raise LookupError("Article not found")
    return r


TABLES = ("kb_pages",)
