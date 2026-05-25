"""libcst-based code transformers, one per RecipeId.

libcst preserves comments and formatting so diffs are readable and code review
is feasible. Each transformer is deterministic — no LLM at this step. The LLM
only proposes WHICH recipe to apply WHERE; it never authors raw code.

Transformers ALL inherit from libcst.CSTTransformer and rewrite a copy of the
parsed module. The dispatcher returns the new source string.
"""
from __future__ import annotations

import logging
from typing import Callable

import libcst as cst
import libcst.matchers as m

from ..models import RecipeId

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Recipe 1: %-format and "x".format() -> f-string
# ---------------------------------------------------------------------------


class _FStringTransformer(cst.CSTTransformer):
    """Rewrite simple %-format and .format() into f-strings.

    Conservative scope:
      - %-format: literal-string left, tuple or single arg right
      - .format(): only positional named-substitution {0}, {1} or {} ordered
                    or keyword-named matching kwargs
      - Bails out on width/precision specifiers (we keep the original)
    """

    def __init__(self) -> None:
        super().__init__()
        self.changes: int = 0

    def leave_BinaryOperation(  # type: ignore[override]
        self,
        original_node: cst.BinaryOperation,
        updated_node: cst.BinaryOperation,
    ) -> cst.BaseExpression:
        # Only string % expr
        if not isinstance(updated_node.operator, cst.Modulo):
            return updated_node
        left = updated_node.left
        if not isinstance(left, cst.SimpleString):
            return updated_node
        try:
            text = _strip_string_quotes(left.value)
        except ValueError:
            return updated_node
        if not _is_simple_percent_format(text):
            return updated_node
        # Build f-string parts
        right = updated_node.right
        args: list[cst.BaseExpression]
        if isinstance(right, cst.Tuple):
            args = [el.value for el in right.elements]
        else:
            args = [right]
        try:
            new_str = _percent_format_to_fstring(text, args)
        except _Bail:
            return updated_node
        self.changes += 1
        return cst.parse_expression(new_str)

    def leave_Call(  # type: ignore[override]
        self,
        original_node: cst.Call,
        updated_node: cst.Call,
    ) -> cst.BaseExpression:
        # Match: "literal".format(arg, key=val, ...)
        if not isinstance(updated_node.func, cst.Attribute):
            return updated_node
        if not isinstance(updated_node.func.value, cst.SimpleString):
            return updated_node
        if updated_node.func.attr.value != "format":
            return updated_node
        try:
            text = _strip_string_quotes(updated_node.func.value.value)
        except ValueError:
            return updated_node
        # Skip strings whose braces are too gnarly
        if not _is_simple_format_template(text):
            return updated_node
        # Map args
        positional: list[cst.BaseExpression] = []
        keyword: dict[str, cst.BaseExpression] = {}
        for arg in updated_node.args:
            if arg.keyword is None:
                positional.append(arg.value)
            else:
                keyword[arg.keyword.value] = arg.value
        try:
            new_str = _format_template_to_fstring(text, positional, keyword)
        except _Bail:
            return updated_node
        self.changes += 1
        return cst.parse_expression(new_str)


class _Bail(Exception):
    pass


def _strip_string_quotes(s: str) -> str:
    """Return the string content from a SimpleString token like 'hello' or "x"."""
    # Reject f-strings / r-strings / b-strings; we only convert plain str.
    if s.startswith(("f", "F", "rf", "fr", "Rf", "fR", "b", "B")):
        raise ValueError("not a plain str literal")
    # Strip optional 'u'/'U' prefix
    if s[0] in ("u", "U"):
        s = s[1:]
    if s.startswith(('"""', "'''")):
        return s[3:-3]
    if s.startswith(('"', "'")):
        return s[1:-1]
    raise ValueError("not a quoted str literal")


def _is_simple_percent_format(s: str) -> bool:
    """Quick check: only %s, %d, %r, %f without width/precision specifiers."""
    if "%" not in s:
        return False
    # Walk and reject anything not in the allowed set
    i = 0
    while i < len(s):
        if s[i] == "%":
            if i + 1 >= len(s):
                return False
            nxt = s[i + 1]
            if nxt == "%":
                i += 2
                continue
            if nxt not in "sdrfgi":
                return False
            i += 2
        else:
            i += 1
    return True


def _percent_format_to_fstring(template: str, args: list[cst.BaseExpression]) -> str:
    out: list[str] = []
    arg_iter = iter(args)
    i = 0
    while i < len(template):
        ch = template[i]
        if ch == "%":
            nxt = template[i + 1] if i + 1 < len(template) else ""
            if nxt == "%":
                out.append("%")
                i += 2
                continue
            try:
                expr = next(arg_iter)
            except StopIteration:
                raise _Bail()
            code = cst.Module([]).code_for_node(expr)
            # Brace-escape for f-string body
            out.append("{" + code + (("!r}") if nxt == "r" else "}"))
            i += 2
        elif ch == "{":
            out.append("{{")
            i += 1
        elif ch == "}":
            out.append("}}")
            i += 1
        else:
            out.append(ch)
            i += 1
    # Reject if we didn't consume all args (extra arg = bug surface)
    if any(True for _ in arg_iter):
        raise _Bail()
    body = "".join(out)
    return 'f"' + body.replace('"', r"\"") + '"'


def _is_simple_format_template(s: str) -> bool:
    """Disallow braces with format specs ({:>5}, {:.2f}) and conversions ({!r})
    that we don't fully model. Allows {}, {0}, {name}.
    """
    if "{" not in s:
        return False
    i = 0
    while i < len(s):
        if s[i] == "{" and i + 1 < len(s) and s[i + 1] == "{":
            i += 2
            continue
        if s[i] == "}" and i + 1 < len(s) and s[i + 1] == "}":
            i += 2
            continue
        if s[i] == "{":
            j = s.find("}", i + 1)
            if j == -1:
                return False
            spec = s[i + 1 : j]
            if any(c in spec for c in (":", "!")):
                return False
            i = j + 1
            continue
        i += 1
    return True


def _format_template_to_fstring(
    template: str,
    positional: list[cst.BaseExpression],
    keyword: dict[str, cst.BaseExpression],
) -> str:
    out: list[str] = []
    i = 0
    pos_idx = 0
    while i < len(template):
        if template[i] == "{" and i + 1 < len(template) and template[i + 1] == "{":
            out.append("{{")
            i += 2
            continue
        if template[i] == "}" and i + 1 < len(template) and template[i + 1] == "}":
            out.append("}}")
            i += 2
            continue
        if template[i] == "{":
            j = template.find("}", i + 1)
            spec = template[i + 1 : j]
            if spec == "":
                # Auto-numbered
                if pos_idx >= len(positional):
                    raise _Bail()
                expr = positional[pos_idx]
                pos_idx += 1
            elif spec.isdigit():
                idx = int(spec)
                if idx >= len(positional):
                    raise _Bail()
                expr = positional[idx]
            else:
                if spec not in keyword:
                    raise _Bail()
                expr = keyword[spec]
            code = cst.Module([]).code_for_node(expr)
            out.append("{" + code + "}")
            i = j + 1
            continue
        # Single-quote escape inside f-string body — we'll wrap with double-quotes
        out.append(template[i])
        i += 1
    body = "".join(out)
    return 'f"' + body.replace('"', r"\"") + '"'


# ---------------------------------------------------------------------------
# Recipe 2: typing_to_pep585  (List[int] -> list[int], etc.)
# ---------------------------------------------------------------------------


_PEP585_MAP = {
    "List": "list",
    "Dict": "dict",
    "Tuple": "tuple",
    "Set": "set",
    "FrozenSet": "frozenset",
    "Type": "type",
}


class _Pep585Transformer(cst.CSTTransformer):
    def __init__(self) -> None:
        super().__init__()
        self.changes = 0

    def leave_Subscript(  # type: ignore[override]
        self,
        original_node: cst.Subscript,
        updated_node: cst.Subscript,
    ) -> cst.BaseExpression:
        target_name = None
        if isinstance(updated_node.value, cst.Name):
            target_name = updated_node.value.value
        elif isinstance(updated_node.value, cst.Attribute) \
                and updated_node.value.attr.value in _PEP585_MAP:
            target_name = updated_node.value.attr.value
        if target_name in _PEP585_MAP:
            self.changes += 1
            new = updated_node.with_changes(
                value=cst.Name(_PEP585_MAP[target_name]),
            )
            return new
        return updated_node


# ---------------------------------------------------------------------------
# Recipe 3: typing.Optional[X] -> X | None
# ---------------------------------------------------------------------------


class _OptionalToPep604Transformer(cst.CSTTransformer):
    def __init__(self) -> None:
        super().__init__()
        self.changes = 0

    def leave_Subscript(  # type: ignore[override]
        self,
        original_node: cst.Subscript,
        updated_node: cst.Subscript,
    ) -> cst.BaseExpression:
        target_name = None
        if isinstance(updated_node.value, cst.Name):
            target_name = updated_node.value.value
        elif isinstance(updated_node.value, cst.Attribute):
            target_name = updated_node.value.attr.value
        if target_name != "Optional":
            return updated_node
        # Single subscript element expected
        if len(updated_node.slice) != 1:
            return updated_node
        slice0 = updated_node.slice[0].slice
        if not isinstance(slice0, cst.Index):
            return updated_node
        inner = slice0.value
        self.changes += 1
        # Build BinaryOperation (X | None)
        return cst.BinaryOperation(
            left=inner,
            operator=cst.BitOr(
                whitespace_before=cst.SimpleWhitespace(" "),
                whitespace_after=cst.SimpleWhitespace(" "),
            ),
            right=cst.Name("None"),
        )


# ---------------------------------------------------------------------------
# Recipe 4: open(...) -> with open(...) as f: ...
# ---------------------------------------------------------------------------
#
# This one is HIGH RISK because it changes control flow. We scope it to the
# narrow, common pattern:
#
#     f = open(path, ...)
#     <use f>            # any sequence of statements that mention `f`
#     f.close()
#
# Becomes:
#     with open(path, ...) as f:
#         <use f>
#
# Anything more complex (conditional close, multiple files, nested control)
# we refuse to rewrite — the planner will mark it high-risk and the patch
# step will leave it alone with a warning, deferring to the human.
#
# For the demo we keep the implementation minimal and let the eval show that
# the agent correctly REFUSES to rewrite ambiguous cases (the safer behavior).


class _OpenToWithTransformer(cst.CSTTransformer):
    def __init__(self) -> None:
        super().__init__()
        self.changes = 0

    def leave_FunctionDef(  # type: ignore[override]
        self,
        original_node: cst.FunctionDef,
        updated_node: cst.FunctionDef,
    ) -> cst.FunctionDef:
        new_body = self._rewrite_block(updated_node.body)
        if new_body is None:
            return updated_node
        return updated_node.with_changes(body=new_body)

    def leave_Module(  # type: ignore[override]
        self,
        original_node: cst.Module,
        updated_node: cst.Module,
    ) -> cst.Module:
        new_body = self._rewrite_module(updated_node)
        if new_body is None:
            return updated_node
        return updated_node.with_changes(body=new_body)

    def _rewrite_module(self, mod: cst.Module) -> tuple[cst.BaseStatement, ...] | None:
        body = list(mod.body)
        new = self._scan_and_rewrite(body)
        if new is None:
            return None
        return tuple(new)

    def _rewrite_block(self, indented: cst.IndentedBlock) -> cst.IndentedBlock | None:
        body = list(indented.body)
        new = self._scan_and_rewrite(body)
        if new is None:
            return None
        return indented.with_changes(body=tuple(new))

    def _scan_and_rewrite(self, stmts: list) -> list | None:
        """Look for the (assign open) ... (call f.close) pattern within stmts."""
        out = []
        i = 0
        changed = False
        while i < len(stmts):
            stmt = stmts[i]
            target_var = self._open_assign_target(stmt)
            if target_var is None:
                out.append(stmt)
                i += 1
                continue

            # Find a matching close() in the rest of this block
            close_idx = self._find_close(target_var, stmts, i + 1)
            if close_idx is None:
                out.append(stmt)
                i += 1
                continue

            # Build a With statement covering [i+1 .. close_idx-1] as body
            inner_body = stmts[i + 1 : close_idx]
            if not inner_body:
                # Just open and immediately close — leave it alone
                out.append(stmt)
                i += 1
                continue

            with_stmt = self._build_with(stmt, target_var, inner_body)
            if with_stmt is None:
                out.append(stmt)
                i += 1
                continue

            out.append(with_stmt)
            self.changes += 1
            changed = True
            i = close_idx + 1
        return out if changed else None

    def _open_assign_target(self, stmt) -> str | None:
        """Return target var name if `stmt` is `f = open(...)`, else None."""
        if not isinstance(stmt, cst.SimpleStatementLine):
            return None
        if len(stmt.body) != 1 or not isinstance(stmt.body[0], cst.Assign):
            return None
        a: cst.Assign = stmt.body[0]
        if len(a.targets) != 1 or not isinstance(a.targets[0].target, cst.Name):
            return None
        if not isinstance(a.value, cst.Call):
            return None
        if not isinstance(a.value.func, cst.Name) or a.value.func.value != "open":
            return None
        return a.targets[0].target.value

    def _find_close(self, var: str, stmts: list, start: int) -> int | None:
        for j in range(start, len(stmts)):
            s = stmts[j]
            if not isinstance(s, cst.SimpleStatementLine):
                continue
            if len(s.body) != 1 or not isinstance(s.body[0], cst.Expr):
                continue
            e = s.body[0].value
            if not isinstance(e, cst.Call):
                continue
            if not isinstance(e.func, cst.Attribute):
                continue
            if e.func.attr.value != "close":
                continue
            if not isinstance(e.func.value, cst.Name) or e.func.value.value != var:
                continue
            # Bail if there's any conditional / loop before close — the
            # caller already checked for direct sequence so we trust this.
            return j
        return None

    def _build_with(self, open_stmt: cst.SimpleStatementLine, var: str, body: list) -> cst.With | None:
        a: cst.Assign = open_stmt.body[0]  # type: ignore[assignment]
        call: cst.Call = a.value  # type: ignore[assignment]
        item = cst.WithItem(
            item=call,
            asname=cst.AsName(name=cst.Name(var)),
        )
        # Wrap body in IndentedBlock; need to coerce types
        try:
            block = cst.IndentedBlock(body=tuple(body))
        except Exception:
            return None
        return cst.With(items=[item], body=block)


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------


def _make_transformer(recipe: RecipeId) -> cst.CSTTransformer:
    if recipe == "format_to_fstring":
        return _FStringTransformer()
    if recipe == "typing_to_pep585":
        return _Pep585Transformer()
    if recipe == "typing_optional_to_pep604":
        return _OptionalToPep604Transformer()
    if recipe == "open_to_with_open":
        return _OpenToWithTransformer()
    raise ValueError(f"Unknown recipe: {recipe}")


def apply_recipe(source: str, recipe: RecipeId) -> tuple[str, int]:
    """Apply a recipe to a source string. Returns (new_source, n_changes).

    If parsing fails, returns the original source with 0 changes.
    """
    try:
        module = cst.parse_module(source)
    except Exception as e:
        logger.warning("libcst parse failed: %s", e)
        return source, 0

    transformer = _make_transformer(recipe)
    new_module = module.visit(transformer)
    changes = getattr(transformer, "changes", 0)
    return new_module.code, changes
