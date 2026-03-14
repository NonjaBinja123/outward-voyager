"""
Code validation pipeline for agent self-written code.

Three-stage validation:
  1. Syntax — ast.parse(); any SyntaxError → reject
  2. Static safety — ban dangerous patterns (shell execution, write outside data/)
  3. Smoke test — import the module in a subprocess; catch import-time crashes

A ValidationResult with ok=True means the code is safe to integrate.
All failures include a human-readable reason logged to the research log.
"""
import ast
import logging
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Modules the agent is never allowed to import
_BANNED_MODULES = {
    "os",        # os.system, os.popen
    "subprocess",
    "socket",
    "shutil",
    "ctypes",
    "importlib",
    "builtins",  # prevents __import__ tricks
    "pty",
    "signal",
    "threading",  # keep sandbox single-threaded
    "multiprocessing",
}

# Builtins the agent may not call
_BANNED_BUILTINS = {"eval", "exec", "compile", "__import__", "open"}


@dataclass
class ValidationResult:
    ok: bool
    stage: str       # "syntax" | "safety" | "smoke"
    reason: str = ""
    warnings: list[str] = field(default_factory=list)


class CodeValidator:
    def __init__(self, smoke_timeout: float = 5.0) -> None:
        self._timeout = smoke_timeout

    def validate(self, code: str, skill_name: str = "unnamed") -> ValidationResult:
        """Run all validation stages in order. Returns on first failure."""
        result = self._check_syntax(code)
        if not result.ok:
            logger.warning(f"[Sandbox] {skill_name} failed syntax: {result.reason}")
            return result

        tree = ast.parse(code)
        result = self._check_safety(tree)
        if not result.ok:
            logger.warning(f"[Sandbox] {skill_name} failed safety: {result.reason}")
            return result

        result = self._smoke_test(code)
        if not result.ok:
            logger.warning(f"[Sandbox] {skill_name} failed smoke: {result.reason}")
            return result

        logger.info(f"[Sandbox] {skill_name} passed all validation stages")
        return result

    # ── Stage 1: Syntax ─────────────────────────────────────────────────────

    def _check_syntax(self, code: str) -> ValidationResult:
        try:
            ast.parse(code)
            return ValidationResult(ok=True, stage="syntax")
        except SyntaxError as e:
            return ValidationResult(ok=False, stage="syntax",
                                    reason=f"SyntaxError at line {e.lineno}: {e.msg}")

    # ── Stage 2: Static safety ───────────────────────────────────────────────

    def _check_safety(self, tree: ast.AST) -> ValidationResult:
        checker = _SafetyVisitor()
        checker.visit(tree)
        if checker.violations:
            return ValidationResult(ok=False, stage="safety",
                                    reason=checker.violations[0],
                                    warnings=checker.violations[1:])
        return ValidationResult(ok=True, stage="safety",
                                warnings=checker.warnings)

    # ── Stage 3: Smoke test (subprocess import) ──────────────────────────────

    def _smoke_test(self, code: str) -> ValidationResult:
        """Write code to a temp file and import it in a subprocess."""
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w",
                                         delete=False, encoding="utf-8") as f:
            f.write(code)
            tmp_path = f.name

        try:
            result = subprocess.run(
                [sys.executable, "-c",
                 f"import importlib.util; "
                 f"spec = importlib.util.spec_from_file_location('_sandbox_test', r'{tmp_path}'); "
                 f"mod = importlib.util.module_from_spec(spec); "
                 f"spec.loader.exec_module(mod)"],
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
            if result.returncode != 0:
                return ValidationResult(ok=False, stage="smoke",
                                        reason=result.stderr.strip()[:500])
            return ValidationResult(ok=True, stage="smoke")
        except subprocess.TimeoutExpired:
            return ValidationResult(ok=False, stage="smoke",
                                    reason=f"smoke test timed out after {self._timeout}s")
        finally:
            Path(tmp_path).unlink(missing_ok=True)


class _SafetyVisitor(ast.NodeVisitor):
    """AST visitor that collects safety violations and warnings."""

    def __init__(self) -> None:
        self.violations: list[str] = []
        self.warnings: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root in _BANNED_MODULES:
                self.violations.append(f"Banned import: {alias.name} (line {node.lineno})")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            root = node.module.split(".")[0]
            if root in _BANNED_MODULES:
                self.violations.append(f"Banned import: from {node.module} (line {node.lineno})")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Catch eval/exec/compile/__import__/open called as bare names
        if isinstance(node.func, ast.Name) and node.func.id in _BANNED_BUILTINS:
            self.violations.append(f"Banned builtin call: {node.func.id}() (line {node.lineno})")
        # Catch getattr(x, 'eval') style
        if isinstance(node.func, ast.Attribute) and node.func.attr in _BANNED_BUILTINS:
            self.warnings.append(f"Suspicious attribute call: .{node.func.attr}() (line {node.lineno})")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # Catch os.system, os.popen, subprocess.Popen etc. accessed as attributes
        if isinstance(node.value, ast.Name) and node.value.id in _BANNED_MODULES:
            self.violations.append(
                f"Banned module attribute access: {node.value.id}.{node.attr} (line {node.lineno})"
            )
        self.generic_visit(node)
