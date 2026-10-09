"""Validates the Python an agent wants to run through the browser-use CLI.

The CLI runs whatever Python it is given, as the current Windows user. So before anything
runs, we parse the code and allow only a small, read-only subset: approved browser helpers,
a few harmless builtins and string methods, and read-only Chrome DevTools (CDP) queries.
Everything else (imports, file access, js(), typing, dunder tricks) is rejected.

This is a best-effort filter, not a sandbox. The browser-level guards (blocked non-GET
requests, URL allowlist) and OS-level isolation are the firm boundaries.
"""

import ast

# Browser-use helpers the agent may call. Deliberately absent: js, type_text, fill_input,
# press_key, dispatch_key, upload_file, http_get, capture_screenshot (writes to a path),
# activate_tab, start/stop_remote_daemon.
ALLOWED_HELPERS = {
    "new_tab", "goto_url", "page_info", "click_at_xy", "scroll", "wait", "wait_for_load",
    "wait_for_element", "list_tabs", "current_tab", "switch_tab", "close_tab", "ensure_real_tab",
    "cdp",
}
URL_HELPERS = {"new_tab", "goto_url"}  # first argument must be a literal http(s) URL

ALLOWED_BUILTINS = {
    "print", "len", "range", "sorted", "min", "max", "str", "int", "float", "enumerate",
    "list", "dict", "zip", "sum", "any", "all", "round", "abs", "bool", "set", "tuple",
}
ALLOWED_METHODS = {
    "get", "lower", "upper", "strip", "startswith", "endswith", "items", "keys", "values",
    "append", "split", "join",
}
# Read-only DevTools queries (finding elements and where to click). No Runtime.*, no Fetch.*,
# no Network.*, no Input.* (clicks go through click_at_xy), no Page.navigate.
ALLOWED_CDP = {
    "Accessibility.enable", "Accessibility.getFullAXTree", "Accessibility.queryAXTree",
    "DOM.enable", "DOM.getDocument", "DOM.getBoxModel", "Page.getLayoutMetrics",
}

_ALLOWED_NODES = (
    ast.Module, ast.Expr, ast.Assign, ast.AugAssign, ast.If, ast.For, ast.Break, ast.Continue, ast.Pass,
    ast.Call, ast.Name, ast.Load, ast.Store, ast.Constant, ast.Attribute, ast.Subscript, ast.Slice,
    ast.List, ast.Tuple, ast.Dict, ast.Set, ast.JoinedStr, ast.FormattedValue,
    ast.Compare, ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.IfExp, ast.keyword,
    ast.ListComp, ast.GeneratorExp, ast.comprehension,
    ast.And, ast.Or, ast.Not, ast.USub, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Is, ast.IsNot,
)


class CodeRejected(ValueError):
    """The code uses something outside the allowed subset."""


def check_code(code: str) -> None:
    """Raise CodeRejected, with a message the agent can act on, if the code is not allowed."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise CodeRejected(f"syntax error: {exc.msg} (line {exc.lineno})") from exc

    # Names the code binds itself (assignments, loop variables, comprehension variables).
    assigned = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    # Helpers may only be CALLED. Using one as a value (f = cdp; f(...)) would dodge the call checks.
    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise CodeRejected(f"{type(node).__name__} is not allowed")
        if isinstance(node, ast.Name):
            _check_name(node, assigned, as_call=id(node) in called)
        elif isinstance(node, ast.Attribute):
            if node.attr not in ALLOWED_METHODS:
                raise CodeRejected(f"attribute '.{node.attr}' is not allowed")
        elif isinstance(node, ast.Call):
            _check_call(node)
        elif isinstance(node, ast.Constant) and isinstance(node.value, (bytes, complex)):
            raise CodeRejected("bytes/complex literals are not allowed")


def _check_name(node: ast.Name, assigned: set[str], as_call: bool) -> None:
    name = node.id
    if name.startswith("_"):
        raise CodeRejected(f"name '{name}' is not allowed")
    if isinstance(node.ctx, ast.Store):
        if name in ALLOWED_HELPERS | ALLOWED_BUILTINS:
            raise CodeRejected(f"cannot assign to '{name}'")
        return
    if name in assigned or name in ALLOWED_BUILTINS:
        return
    if name in ALLOWED_HELPERS:
        if not as_call:
            raise CodeRejected(f"'{name}' may only be called, not used as a value")
        return
    raise CodeRejected(f"name '{name}' is not allowed (allowed helpers: {sorted(ALLOWED_HELPERS)})")


def _check_call(node: ast.Call) -> None:
    func = node.func
    if isinstance(func, ast.Attribute):
        return  # method name already checked against ALLOWED_METHODS
    if not isinstance(func, ast.Name) or func.id not in ALLOWED_HELPERS | ALLOWED_BUILTINS:
        raise CodeRejected("only direct calls to approved helpers are allowed")
    if func.id in URL_HELPERS:
        first = node.args[0] if node.args else None
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value.startswith(("http://", "https://"))):
            raise CodeRejected(f"{func.id}() needs a literal http(s) URL as its first argument")
    if func.id == "cdp":
        first = node.args[0] if node.args else None
        if not (isinstance(first, ast.Constant) and first.value in ALLOWED_CDP):
            raise CodeRejected(f"cdp() method not allowed; allowed: {sorted(ALLOWED_CDP)}")
