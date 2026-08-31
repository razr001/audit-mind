import ast
import subprocess
import sys
from pathlib import Path

APP_ROOT = Path(__file__).parents[1] / "app"
PROJECT_ROOT = APP_ROOT.parent


def test_production_modules_stay_within_maintainability_budget() -> None:
    """Prevent route and service responsibilities from growing without a split."""
    violations: list[str] = []
    for path in APP_ROOT.rglob("*.py"):
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        limit = 350 if path.parent.name == "api" else 400
        if line_count > limit:
            violations.append(f"{path.relative_to(APP_ROOT)}: {line_count} > {limit}")

    assert not violations, "Oversized production modules:\n" + "\n".join(violations)


def test_classes_and_functions_stay_within_maintainability_budget() -> None:
    """Catch concentrated responsibilities even when a module is still small."""
    violations: list[str] = []
    for path in APP_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(
                node,
                (ast.AsyncFunctionDef, ast.ClassDef, ast.FunctionDef),
            ):
                continue
            assert node.end_lineno is not None
            line_count = node.end_lineno - node.lineno + 1
            limit = 350 if isinstance(node, ast.ClassDef) else 180
            if line_count > limit:
                violations.append(
                    f"{path.relative_to(APP_ROOT)}:{node.lineno} "
                    f"{node.name}: {line_count} > {limit}",
                )

    assert not violations, "Oversized classes or functions:\n" + "\n".join(violations)


def test_python_indentation_contains_no_tabs() -> None:
    """Repository code style requires deterministic four-space indentation."""
    violations: list[str] = []
    for root in (APP_ROOT, Path(__file__).parent):
        for path in root.rglob("*.py"):
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(),
                start=1,
            ):
                if line.startswith("\t"):
                    violations.append(f"{path}:{line_number}")

    assert not violations, "Tab-indented Python lines:\n" + "\n".join(violations)


def test_system_agent_type_contracts() -> None:
    """Agent 和对话入口必须满足 Pyright 协议，避免 IDE 才发现类型不兼容。"""

    result = subprocess.run(
        [sys.executable, "-m", "pyright", "--project", "pyrightconfig.json"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
