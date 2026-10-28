"""Read the target package: sources, public API, contracts of individual functions."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

MAX_PROMPT_SOURCE_CHARS = 60_000


@dataclass
class Contract:
    qualname: str  # "Inventory.remove" or "needs_reorder"
    path: str  # relative to the sandbox root, e.g. "buggy_inventory/stock.py"
    lineno: int
    signature: str
    docstring: str
    source: str


@dataclass
class SourceFile:
    path: str  # relative path, e.g. "buggy_inventory/stock.py"
    module: str  # dotted, e.g. "buggy_inventory.stock"
    text: str


def collect_sources(root: Path, name: str, is_module: bool) -> list[SourceFile]:
    files: list[SourceFile] = []
    if is_module:
        p = root / f"{name}.py"
        return [SourceFile(p.name, name, p.read_text(encoding="utf-8"))]
    for p in sorted((root / name).rglob("*.py")):
        rel = p.relative_to(root)
        if any(part.startswith("test") for part in rel.parts[1:]) or "__pycache__" in rel.parts:
            continue
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        files.append(SourceFile(str(rel), ".".join(parts), p.read_text(encoding="utf-8")))
    return files


def select_files(files: list[SourceFile], module: str | None) -> list[SourceFile]:
    if not module:
        return files
    wanted = module.removesuffix(".py").replace("/", ".")
    chosen = [f for f in files if f.module == wanted or f.module.endswith("." + wanted)]
    if not chosen:
        raise ValueError(f"module {module!r} not found; have: {', '.join(f.module for f in files)}")
    return chosen


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = ast.unparse(node.args)
    ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"def {node.name}({args}){ret}"


def contracts(files: list[SourceFile]) -> dict[str, Contract]:
    """Map every function and method name (qualified and bare) to its contract."""
    found: dict[str, Contract] = {}
    for f in files:
        try:
            tree = ast.parse(f.text)
        except SyntaxError:
            continue
        lines = f.text.splitlines()

        def add(node, qual):
            src = "\n".join(lines[node.lineno - 1 : node.end_lineno])
            c = Contract(qual, f.path, node.lineno, _signature(node), ast.get_docstring(node) or "", src)
            found.setdefault(qual, c)
            found.setdefault(qual.rsplit(".", 1)[-1], c)

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                add(node, node.name)
            elif isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        add(item, f"{node.name}.{item.name}")
                cls_src = "\n".join(lines[node.lineno - 1 : node.end_lineno])
                found.setdefault(
                    node.name,
                    Contract(node.name, f.path, node.lineno, f"class {node.name}", ast.get_docstring(node) or "", cls_src),
                )
    return found


def called_names(test_source: str) -> list[str]:
    """Names a test calls, in order of first appearance (``foo`` and ``obj.method``)."""
    try:
        tree = ast.parse(test_source)
    except SyntaxError:
        return []
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else None
            if name and name not in names:
                names.append(name)
    return names


def contracts_for_test(test_source: str, table: dict[str, Contract]) -> list[Contract]:
    out: list[Contract] = []
    for name in called_names(test_source):
        c = table.get(name)
        if c and c not in out and c.signature.startswith("def "):
            out.append(c)
    return out


def numbered(text: str) -> str:
    return "\n".join(f"{i:4d} | {line}" for i, line in enumerate(text.splitlines(), 1))


def render_sources(files: list[SourceFile], function: str | None = None) -> str:
    """Source listing for prompts, with line numbers, trimmed to a size budget."""
    chunks = []
    budget = MAX_PROMPT_SOURCE_CHARS
    for f in files:
        body = numbered(f.text)
        if len(body) > budget:
            body = body[:budget] + "\n   ... (truncated)"
        budget -= len(body)
        chunks.append(f"### {f.path}  (import as `{f.module}`)\n```python\n{body}\n```")
        if budget <= 0:
            break
    listing = "\n\n".join(chunks)
    if function:
        listing += f"\n\nFocus: write tests for `{function}` only (other code is context)."
    return listing
