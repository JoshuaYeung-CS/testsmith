"""Parse pytest JUnit XML and coverage.py JSON into plain data."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TestOutcome:
    __test__ = False  # not a pytest test class

    name: str  # function name, parametrisation stripped: "test_foo"
    case: str  # full case name as pytest reports it: "test_foo[3-4]"
    module: str  # dotted module path: "candidates.test_pkg_testsmith"
    status: str  # passed | failed | error | skipped | xfailed
    message: str = ""
    details: str = ""
    collection_error: bool = False

    @property
    def failed(self) -> bool:
        return self.status in ("failed", "error")

    @property
    def signature(self) -> str:
        """A short fingerprint of the failure, used to check reruns are identical."""
        lines = [ln.strip() for ln in self.details.splitlines() if ln.strip().startswith("E ")]
        core = lines[0] if lines else self.message
        return re.sub(r"0x[0-9a-f]+", "0x?", core)[:300]


@dataclass
class CoverageResult:
    percent: float
    covered: int
    statements: int
    missing: dict[str, list[int]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "percent": round(self.percent, 2),
            "covered": self.covered,
            "statements": self.statements,
            "missing": self.missing,
        }


def parse_junit(path: Path) -> list[TestOutcome]:
    if not path.is_file():
        return []
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return []
    outcomes: list[TestOutcome] = []
    for case in root.iter("testcase"):
        classname = case.get("classname", "")
        case_name = case.get("name", "")
        base = case_name.split("[", 1)[0]
        status, message, details, coll = "passed", "", "", False
        for child in case:
            tag = child.tag
            if tag in ("failure", "error"):
                status = "failed" if tag == "failure" else "error"
                message = child.get("message", "") or ""
                details = (child.text or "").strip()
                if "collection failure" in message or (not classname and tag == "error"):
                    coll = True
                break
            if tag == "skipped":
                kind = child.get("type", "")
                status = "xfailed" if "xfail" in kind else "skipped"
                message = child.get("message", "") or ""
                details = (child.text or "").strip()
                break
        outcomes.append(
            TestOutcome(
                name=base,
                case=case_name,
                module=classname,
                status=status,
                message=message,
                details=details,
                collection_error=coll,
            )
        )
    return outcomes


def parse_coverage_json(path: Path, root: Path) -> CoverageResult | None:
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    totals = data.get("totals", {})
    missing: dict[str, list[int]] = {}
    for fname, info in data.get("files", {}).items():
        lines = info.get("missing_lines") or []
        if lines:
            try:
                rel = str(Path(fname).resolve().relative_to(root.resolve()))
            except ValueError:
                rel = fname
            missing[rel] = sorted(lines)
    return CoverageResult(
        percent=float(totals.get("percent_covered", 0.0)),
        covered=int(totals.get("covered_lines", 0)),
        statements=int(totals.get("num_statements", 0)),
        missing=missing,
    )
