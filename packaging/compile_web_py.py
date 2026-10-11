"""Compile the ERP's Python for the web edition: bytecode only, no source text, no comments, no docstrings.

  python3.12 packaging/compile_web_py.py <out.zip>

The browser runs Pyodide (Python 3.12), so this must run under Python 3.12 — the bytecode format is version-specific.
Server routes keep the first line of their docstring (the assistant lists what each one does); every other docstring
goes. Module files become erp/<name>.pyc inside the zip, which Python imports directly ("sourceless" modules)."""
import ast
import importlib.util
import marshal
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = (3, 12)


def strip_docstrings(tree, keep_first_line=False):
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                and isinstance(body[0].value.value, str):
            if keep_first_line and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                first = (body[0].value.value.strip().splitlines() or [""])[0]
                body[0].value.value = first
            else:
                body.pop(0)
                if not body:
                    body.append(ast.Pass())
    return tree


def compile_to(out_zip):
    if sys.version_info[:2] != PYTHON:
        raise SystemExit(f"Run this with Python {PYTHON[0]}.{PYTHON[1]} (Pyodide's version), not {sys.version.split()[0]}")
    header = importlib.util.MAGIC_NUMBER + (0).to_bytes(4, "little") + (0).to_bytes(8, "little")
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted((ROOT / "erp").glob("*.py")):
            tree = strip_docstrings(ast.parse(p.read_text(encoding="utf-8"), filename=p.name), keep_first_line=p.name == "server.py")
            code = compile(ast.fix_missing_locations(tree), f"erp/{p.name}", "exec", dont_inherit=True, optimize=0)
            z.writestr(f"erp/{p.stem}.pyc", header + marshal.dumps(code))


if __name__ == "__main__":
    compile_to(sys.argv[1])
