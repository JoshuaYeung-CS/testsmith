"""An editable model of a model-written pytest file.

The file is held as a *header* (imports, helpers, fixtures, constants) plus an ordered
set of top-level ``test_*`` functions, so single tests can be replaced, dropped or
marked as known bugs without disturbing the rest.
"""

from __future__ import annotations

import ast
import textwrap
from dataclasses import dataclass, field


@dataclass
class TestFunc:
    __test__ = False  # not a pytest test class

    name: str
    source: str  # including decorators, dedented, no trailing newline
    origin: str = "write"  # write | repair | coverage
    repairs: int = 0
    bug_reason: str | None = None  # set => emitted with a strict xfail marker

    def rendered(self) -> str:
        if not self.bug_reason:
            return self.source
        reason = self.bug_reason.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
        if len(reason) > 160:
            reason = reason[:157] + "..."
        return f'@pytest.mark.xfail(strict=True, reason="testsmith: likely bug: {reason}")\n{self.source}'


def _segment(code: str, node: ast.AST) -> str:
    lines = code.splitlines()
    start = node.lineno
    if getattr(node, "decorator_list", None):
        start = min(d.lineno for d in node.decorator_list)
    return textwrap.dedent("\n".join(lines[start - 1 : node.end_lineno])).rstrip()


def _is_test(node: ast.AST) -> bool:
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test")


@dataclass
class TestFile:
    __test__ = False  # not a pytest test class

    plain_imports: list[str] = field(default_factory=list)  # "import pytest", "import math as m"
    from_imports: dict[str, list[str]] = field(default_factory=dict)  # module -> ["a", "b as c"]
    helpers: list[tuple[str, str]] = field(default_factory=list)  # (name, source)
    tests: dict[str, TestFunc] = field(default_factory=dict)
    dropped_classes: list[str] = field(default_factory=list)

    # -- parsing ---------------------------------------------------------------------
    @classmethod
    def parse(cls, code: str, origin: str = "write") -> "TestFile":
        tf = cls()
        tf.merge(code, origin=origin)
        return tf

    def merge(self, code: str, origin: str = "write", replace: bool = False) -> list[str]:
        """Merge the tests and helpers in ``code``. Returns the test names added/replaced.

        With ``replace=False`` a name clash gets a numeric suffix; with ``replace=True``
        the existing test of that name is overwritten.
        """
        tree = ast.parse(code)
        touched: list[str] = []
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                self._add_import(node)
            elif _is_test(node):
                src = _segment(code, node)
                name = node.name
                if name in self.tests and not replace:
                    n = 2
                    while f"{name}_{n}" in self.tests:
                        n += 1
                    new = f"{name}_{n}"
                    src = src.replace(f"def {name}(", f"def {new}(", 1)
                    name = new
                if name in self.tests and replace:
                    old = self.tests[name]
                    self.tests[name] = TestFunc(name, src, origin="repair", repairs=old.repairs + 1)
                else:
                    self.tests[name] = TestFunc(name, src, origin=origin)
                touched.append(name)
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                # Class-based tests are flattened into top-level functions when they
                # do not use ``self``; otherwise the class is dropped and reported.
                flat = self._flatten_class(code, node)
                if flat is None:
                    self.dropped_classes.append(node.name)
                else:
                    touched += self.merge(flat, origin=origin, replace=replace)
            elif isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant):
                continue  # module docstring
            else:
                name = getattr(node, "name", None) or _segment(code, node).split("\n", 1)[0]
                src = _segment(code, node)
                if all(h[1] != src for h in self.helpers):
                    self.helpers = [h for h in self.helpers if h[0] != name] + [(name, src)]
        return touched

    def _add_import(self, node: ast.Import | ast.ImportFrom) -> None:
        names = [a.name + (f" as {a.asname}" if a.asname else "") for a in node.names]
        if isinstance(node, ast.Import):
            for n in names:
                line = f"import {n}"
                if line not in self.plain_imports:
                    self.plain_imports.append(line)
            return
        module = "." * node.level + (node.module or "")
        bucket = self.from_imports.setdefault(module, [])
        for n in names:
            if n not in bucket:
                bucket.append(n)

    @property
    def imports(self) -> list[str]:
        lines = list(self.plain_imports)
        for module, names in self.from_imports.items():
            lines.append(f"from {module} import {', '.join(names)}")
        return lines

    @staticmethod
    def _flatten_class(code: str, node: ast.ClassDef) -> str | None:
        parts = []
        for item in node.body:
            if not _is_test(item):
                if isinstance(item, ast.Expr):
                    continue
                return None
            uses_self = any(isinstance(n, ast.Name) and n.id == "self" for n in ast.walk(item) if n is not item.args)
            src = _segment(code, item)
            if uses_self:
                return None
            src = src.replace("(self, ", "(", 1).replace("(self)", "()", 1)
            parts.append(src.replace(f"def {item.name}(", f"def {item.name}(", 1))
        return "\n\n\n".join(parts) if parts else None

    # -- editing ---------------------------------------------------------------------
    def drop(self, name: str) -> None:
        self.tests.pop(name, None)

    def mark_bug(self, name: str, reason: str) -> None:
        if name in self.tests:
            self.tests[name].bug_reason = reason
            if "import pytest" not in self.plain_imports:
                self.plain_imports.insert(0, "import pytest")

    def source_of(self, name: str) -> str:
        t = self.tests.get(name)
        return t.source if t else ""

    def render(self, header_comment: str = "") -> str:
        chunks = []
        if header_comment:
            chunks.append(header_comment.rstrip())
        if self.imports:
            chunks.append("\n".join(self.imports))
        for _, src in self.helpers:
            chunks.append(src)
        for t in self.tests.values():
            chunks.append(t.rendered())
        return "\n\n\n".join(chunks) + "\n"
