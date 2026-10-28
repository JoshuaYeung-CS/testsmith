"""Run untrusted, model-written tests away from the user's checkout.

Each run works on a throwaway copy of the target in a temp directory, in a fresh
``python`` subprocess with:

* a scrubbed environment (no API keys or other secrets are passed through),
* a wall-clock timeout that kills the whole process group,
* a per-test time limit (SIGALRM, POSIX) so one hanging test is reported, not fatal,
* CPU-time and file-size rlimits where the OS supports them,
* network access disabled at the Python level: a ``sitecustomize`` module makes
  every IPv4/IPv6 ``connect`` and DNS lookup raise, and proxy variables point at a
  closed local port. This is a best-effort guard for honest mistakes, not a
  security boundary against hostile code.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .results import CoverageResult, TestOutcome, parse_coverage_json, parse_junit

SITECUSTOMIZE = '''\
"""testsmith sandbox: block outbound network for model-written code."""
import socket as _socket

_MSG = "network access is disabled inside the testsmith sandbox"
_INET = (_socket.AF_INET, getattr(_socket, "AF_INET6", _socket.AF_INET))
_orig_connect = _socket.socket.connect
_orig_connect_ex = _socket.socket.connect_ex


def _connect(self, address, *a, **k):
    if self.family in _INET:
        raise ConnectionRefusedError(_MSG)
    return _orig_connect(self, address, *a, **k)


def _connect_ex(self, address, *a, **k):
    if self.family in _INET:
        raise ConnectionRefusedError(_MSG)
    return _orig_connect_ex(self, address, *a, **k)


def _deny(*a, **k):
    raise ConnectionRefusedError(_MSG)


_socket.socket.connect = _connect
_socket.socket.connect_ex = _connect_ex
_socket.create_connection = _deny
_socket.getaddrinfo = _deny
'''

CONFTEST = '''\
"""testsmith sandbox: per-test time limit."""
import signal

import pytest

_LIMIT = {limit}


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    if not hasattr(signal, "SIGALRM"):
        yield
        return

    def _expired(signum, frame):
        raise TimeoutError(f"test exceeded the {{_LIMIT}}s per-test limit")

    previous = signal.signal(signal.SIGALRM, _expired)
    signal.alarm(_LIMIT)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
'''

CANDIDATE_DIR = "candidates"
EXISTING_DIR = "existing_tests"


@dataclass
class RunResult:
    outcomes: list[TestOutcome] = field(default_factory=list)
    coverage: CoverageResult | None = None
    returncode: int = 0
    timed_out: bool = False
    duration_s: float = 0.0
    output: str = ""

    def by_name(self) -> dict[str, list[TestOutcome]]:
        grouped: dict[str, list[TestOutcome]] = {}
        for o in self.outcomes:
            grouped.setdefault(o.name, []).append(o)
        return grouped

    @property
    def collection_errors(self) -> list[TestOutcome]:
        return [o for o in self.outcomes if o.collection_error]


def _limits(cpu_s: int):
    def apply() -> None:  # runs in the child before exec
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s + 5))
            resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 2**20, 64 * 2**20))
        except (ImportError, ValueError, OSError):
            pass

    return apply


class Sandbox:
    """A temp-dir copy of the target package plus its existing tests."""

    def __init__(
        self,
        target: Path,
        existing_tests: Path | None = None,
        *,
        timeout_s: float = 120.0,
        per_test_s: int = 10,
    ) -> None:
        self.target = Path(target).resolve()
        if not self.target.exists():
            raise FileNotFoundError(self.target)
        self.is_module = self.target.is_file()
        self.name = self.target.stem if self.is_module else self.target.name
        self.timeout_s = timeout_s
        self.per_test_s = per_test_s
        self.root = Path(tempfile.mkdtemp(prefix="testsmith-")).resolve()  # macOS: /var -> /private/var
        self.site = self.root / "_testsmith_site"
        self.site.mkdir()
        (self.site / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
        (self.root / "conftest.py").write_text(CONFTEST.format(limit=per_test_s), encoding="utf-8")
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".coverage*")
        if self.is_module:
            shutil.copy2(self.target, self.root / self.target.name)
        else:
            shutil.copytree(self.target, self.root / self.name, ignore=ignore)
        self.existing: Path | None = None
        if existing_tests is not None and Path(existing_tests).is_dir():
            self.existing = self.root / EXISTING_DIR
            shutil.copytree(existing_tests, self.existing, ignore=ignore)
        (self.root / CANDIDATE_DIR).mkdir()

    # -- lifecycle -------------------------------------------------------------------
    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def __enter__(self) -> "Sandbox":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- helpers ---------------------------------------------------------------------
    @property
    def source_path(self) -> Path:
        return self.root / (self.target.name if self.is_module else self.name)

    def write_test_file(self, filename: str, code: str) -> Path:
        path = self.root / CANDIDATE_DIR / filename
        path.write_text(code, encoding="utf-8")
        return path

    def env(self) -> dict[str, str]:
        keep = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT", "TMPDIR") if k in os.environ}
        keep.update(
            {
                "HOME": str(self.root),
                "PYTHONPATH": os.pathsep.join([str(self.site), str(self.root)]),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONHASHSEED": "0",
                "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "http_proxy": "http://127.0.0.1:9",
                "https_proxy": "http://127.0.0.1:9",
                "HTTP_PROXY": "http://127.0.0.1:9",
                "HTTPS_PROXY": "http://127.0.0.1:9",
                "NO_PROXY": "",
            }
        )
        return keep

    def _exec(self, cmd: list[str], timeout: float) -> tuple[int, str, bool]:
        proc = subprocess.Popen(
            cmd,
            cwd=self.root,
            env=self.env(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
            preexec_fn=_limits(int(timeout) + 5) if os.name == "posix" else None,
        )
        try:
            out, _ = proc.communicate(timeout=timeout)
            return proc.returncode, out, False
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, AttributeError):
                proc.kill()
            out, _ = proc.communicate()
            return -9, (out or "") + f"\n[testsmith] killed after {timeout:.0f}s timeout\n", True

    # -- runs ------------------------------------------------------------------------
    def run_pytest(self, paths: list[str], *, coverage: bool = True, tag: str = "run") -> RunResult:
        """Run pytest on ``paths`` (relative to the sandbox root)."""
        junit = self.root / f".junit-{tag}.xml"
        cov_data = self.root / f".coverage-{tag}"
        cov_json = self.root / f".coverage-{tag}.json"
        for p in (junit, cov_data, cov_json):
            p.unlink(missing_ok=True)
        pytest_args = [
            "-q",
            "-p",
            "no:cacheprovider",
            "--tb=short",
            "-o",
            "junit_family=xunit2",
            f"--junitxml={junit}",
            f"--rootdir={self.root}",
            *paths,
        ]
        if coverage:
            scope = (
                [f"--include={self.source_path}"] if self.is_module else [f"--source={self.source_path}"]
            )
            cmd = [sys.executable, "-m", "coverage", "run", f"--data-file={cov_data}", *scope, "-m", "pytest", *pytest_args]
        else:
            cmd = [sys.executable, "-m", "pytest", *pytest_args]
        start = time.monotonic()
        rc, out, timed_out = self._exec(cmd, self.timeout_s)
        result = RunResult(
            outcomes=parse_junit(junit),
            returncode=rc,
            timed_out=timed_out,
            duration_s=time.monotonic() - start,
            output=out[-20000:],
        )
        if coverage and cov_data.exists():
            self._exec(
                [sys.executable, "-m", "coverage", "json", f"--data-file={cov_data}", "-o", str(cov_json), "-q"],
                60,
            )
            result.coverage = parse_coverage_json(cov_json, self.root)
        return result

    def existing_test_paths(self) -> list[str]:
        if self.existing is None:
            return []
        if not any(self.existing.rglob("test_*.py")) and not any(self.existing.rglob("*_test.py")):
            return []
        return [EXISTING_DIR]

    def baseline(self) -> RunResult:
        """Coverage of the existing suite, or of a bare import when there is none."""
        paths = self.existing_test_paths()
        if not paths:
            mod = self.name
            self.write_test_file("test__import_baseline.py", f"import {mod}\n\n\ndef test_imports():\n    assert {mod}\n")
            paths = [f"{CANDIDATE_DIR}/test__import_baseline.py"]
        result = self.run_pytest(paths, tag="baseline")
        (self.root / CANDIDATE_DIR / "test__import_baseline.py").unlink(missing_ok=True)
        return result

    def run_snippet(self, code: str, timeout: float = 15.0) -> tuple[int, str]:
        """Run a short Python snippet (a bug repro) inside the sandbox."""
        script = self.root / ".repro.py"
        script.write_text(code, encoding="utf-8")
        rc, out, timed_out = self._exec([sys.executable, str(script)], timeout)
        return rc, out[-4000:]
