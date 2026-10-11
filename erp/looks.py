"""How the app looks: colours, light / dark, font, density and background pictures.

Each person keeps their own look on their profile (it follows them to every device). A super administrator can
also set one look for the whole company; anyone who has not chosen their own sees that one."""
import re

from . import accounting as acc

COLOUR_KEYS = ("primary", "nav", "header", "sidebar", "menu", "button", "button2", "page", "card", "text")
IMAGE_SLOTS = ("page", "header", "sidebar", "logo")
FITS = ("cover", "contain", "tile")
MAX_IMAGE = 900_000            # characters of a data: URL (the browser shrinks pictures before sending them)
MAX_IMAGES = 2_400_000
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_IMG = re.compile(r"^data:image/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+$")


class LookError(acc.ValidationError):
    pass


def clean_theme(t):
    if not isinstance(t, dict):
        return None
    out = {k: t[k] for k in COLOUR_KEYS if isinstance(t.get(k), str) and _HEX.match(t[k])}
    if t.get("mode") in ("auto", "light", "dark"):
        out["mode"] = t["mode"]
    for k in ("id", "name", "en", "ar"):
        if t.get(k):
            out[k] = acc.text(t[k])[:60]
    if t.get("custom"):
        out["custom"] = True
    return out


def clean_look(d):
    """A look as saved on a profile or for the company: only known keys, sane values, pictures checked and capped.
    A key left out means "not chosen" (the company's look, then the built-in one, applies)."""
    if not isinstance(d, dict):
        return {}
    out = {}
    if d.get("mode") in ("auto", "light", "dark"):
        out["mode"] = d["mode"]
    th = clean_theme(d.get("theme"))
    if th:
        out["theme"] = th
    if isinstance(d.get("font"), str) and d["font"].strip():
        out["font"] = acc.text(d["font"])[:60]
    try:
        if d.get("fontSize") not in (None, ""):
            out["fontSize"] = str(max(11, min(22, int(float(d["fontSize"])))))
    except (TypeError, ValueError):
        pass
    if d.get("density") in ("comfortable", "compact"):
        out["density"] = d["density"]
    if d.get("corners") in ("rounded", "square"):
        out["corners"] = d["corners"]
    imgs, total = {}, 0
    for slot, im in (d.get("images") or {}).items() if isinstance(d.get("images"), dict) else []:
        if slot not in IMAGE_SLOTS or not isinstance(im, dict) or not im.get("src"):
            continue
        src = im["src"]
        if not isinstance(src, str) or not _IMG.match(src):
            raise LookError("A background must be a PNG, JPEG, WebP or GIF picture")
        if len(src) > MAX_IMAGE:
            raise LookError("That picture is too large — choose a smaller one")
        total += len(src)
        try:
            op = max(0.0, min(1.0, float(im.get("op", 0.15))))
        except (TypeError, ValueError):
            op = 0.15
        imgs[slot] = {"src": src, "op": round(op, 2), "fit": im.get("fit") if im.get("fit") in FITS else "cover"}
    if total > MAX_IMAGES:
        raise LookError("Too many large pictures — use smaller ones")
    if imgs:
        out["images"] = imgs
    return out


def clean_themes(lst, limit=30):
    """Saved themes (a person's own, or the company's shared ones)."""
    out = []
    for t in (lst if isinstance(lst, list) else [])[:limit]:
        c = clean_theme(t)
        if c and c.get("id"):
            out.append({**c, "custom": True})
    return out
