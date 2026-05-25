"""AST-based scanner. Finds modernization candidates in a Python tree.

Uses the standard library `ast` module (parses the file once, walks it).
We don't try to fix anything here — only detect. Fixes happen in
src.transforms with libcst, which preserves comments and formatting.

The scanner is deliberately conservative: it errs on the side of NOT flagging
ambiguous cases. Better to miss a candidate than to propose a bad rewrite.
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path

from .models import Finding, RecipeId, Risk

logger = logging.getLogger(__name__)


# Files / directories we never touch.
_DEFAULT_EXCLUDES = {
    ".venv", "venv", "env", "__pycache__",
    ".git", ".pytest_cache", ".mypy_cache", ".tox",
    "build", "dist", "site-packages",
}


def iter_python_files(root: Path, excludes: set[str] | None = None) -> list[Path]:
    excludes = excludes or _DEFAULT_EXCLUDES
    out: list[Path] = []
    for p in root.rglob("*.py"):
        # Skip if any path part is in excludes
        if any(part in excludes for part in p.parts):
            continue
        out.append(p)
    return sorted(out)


# ---------------------------------------------------------------------------
# Finding visitors
# ---------------------------------------------------------------------------


class _FindingsVisitor(ast.NodeVisitor):
    def __init__(self, file_str: str, source: str):
        self.file = file_str
        self.source_lines = source.splitlines()
        self.findings: list[Finding] = []

    # --- format_to_fstring (% formatting and .format()) ---------------------

    def visit_BinOp(self, node: ast.BinOp) -> None:
        # Detect "fmt" % args  where left is a string constant
        if isinstance(node.op, ast.Mod) and isinstance(node.left, ast.Constant) \
                and isinstance(node.left.value, str):
            self._add(
                node,
                "format_to_fstring",
                rationale="%-formatting; safer to migrate to f-string",
                risk="low",
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Detect "...".format(...) on a string literal
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"
            and isinstance(node.func.value, ast.Constant)
            and isinstance(node.func.value.value, str)
        ):
            self._add(
                node,
                "format_to_fstring",
                rationale=".format() on a string literal; convert to f-string",
                risk="low",
            )
        # Detect raw open() that isn't inside a `with` — flagged by visit_Expr
        # below (we need parent context that ast.NodeVisitor doesn't carry).
        self.generic_visit(node)

    # --- typing_to_pep585 (List[X] -> list[X], Dict[k,v] -> dict[k,v]) -------
    # We rely on subscript form: List[int], Dict[str, int], Tuple[int, ...]
    # Set[int], FrozenSet[int], Type[X]

    _PEP585_NAMES = {"List", "Dict", "Tuple", "Set", "FrozenSet", "Type"}

    def visit_Subscript(self, node: ast.Subscript) -> None:
        val = node.value
        name = None
        if isinstance(val, ast.Name):
            name = val.id
        elif isinstance(val, ast.Attribute):
            name = val.attr
        if name in self._PEP585_NAMES:
            self._add(
                node,
                "typing_to_pep585",
                rationale=f"`typing.{name}` is deprecated for subscripted types since 3.9; "
                          f"use built-in lower-case form",
                risk="low",
            )
        # Optional[X] -> X | None
        if name == "Optional":
            self._add(
                node,
                "typing_optional_to_pep604",
                rationale="Use PEP 604 union syntax: X | None",
                risk="low",
            )
        self.generic_visit(node)

    # --- helpers -----------------------------------------------------------

    def _add(self, node: ast.AST, recipe: RecipeId, *, rationale: str, risk: Risk) -> None:
        line = getattr(node, "lineno", 1)
        end = getattr(node, "end_lineno", line)
        try:
            snippet = "\n".join(self.source_lines[line - 1 : end])[:200]
        except Exception:
            snippet = ""
        self.findings.append(
            Finding(
                file=self.file,
                line=line,
                end_line=end,
                recipe=recipe,
                snippet=snippet,
                rationale=rationale,
                estimated_risk=risk,
            )
        )


def _scan_open_without_with(tree: ast.AST, file_str: str, source: str) -> list[Finding]:
    """Detect open() calls whose result is not the context manager of a `with`.

    Walks the tree once, tracking which Call nodes are the immediate context
    expression of a `with` statement, then flags every other open() Call.

    This is high-risk because the fix changes control flow.
    """
    open_calls: list[ast.Call] = []
    in_with: set[int] = set()  # id(node)

    class _CallCollector(ast.NodeVisitor):
        def visit_Call(self, n: ast.Call) -> None:
            if isinstance(n.func, ast.Name) and n.func.id == "open":
                open_calls.append(n)
            self.generic_visit(n)

    class _WithCollector(ast.NodeVisitor):
        def visit_With(self, n: ast.With) -> None:
            for item in n.items:
                ctx = item.context_expr
                if isinstance(ctx, ast.Call):
                    in_with.add(id(ctx))
            self.generic_visit(n)

    _CallCollector().visit(tree)
    _WithCollector().visit(tree)

    out: list[Finding] = []
    lines = source.splitlines()
    for c in open_calls:
        if id(c) in in_with:
            continue
        # Skip open() inside expressions that already use try/finally close
        # patterns — that's still fixable, but it requires deeper analysis.
        line = getattr(c, "lineno", 1)
        end = getattr(c, "end_lineno", line)
        snippet = "\n".join(lines[line - 1 : end])[:200]
        out.append(
            Finding(
                file=file_str,
                line=line,
                end_line=end,
                recipe="open_to_with_open",
                snippet=snippet,
                rationale="open() not inside a `with` statement; risk of leaked file handle",
                estimated_risk="high",
            )
        )
    return out


def scan_file(path: Path, root: Path) -> list[Finding]:
    rel = str(path.relative_to(root)).replace("\\", "/")
    try:
        source = path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("Skip %s: %s", rel, e)
        return []
    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError as e:
        logger.warning("SyntaxError in %s: %s", rel, e)
        return []

    visitor = _FindingsVisitor(rel, source)
    visitor.visit(tree)
    findings = visitor.findings + _scan_open_without_with(tree, rel, source)
    return findings


def scan_repo(root: Path, *, excludes: set[str] | None = None) -> list[Finding]:
    files = iter_python_files(root, excludes=excludes)
    logger.info("Scanning %d Python files under %s", len(files), root)
    out: list[Finding] = []
    for f in files:
        out.extend(scan_file(f, root))
    return out
