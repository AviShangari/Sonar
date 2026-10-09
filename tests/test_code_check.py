import pytest

from sonar.navigation.code_check import CodeRejected, check_code

ALLOWED = {
    "open and read": 'new_tab("https://example.com")\nwait_for_load()\nprint(page_info())',
    "find a link in the accessibility tree": (
        'nodes = cdp("Accessibility.getFullAXTree")["nodes"]\n'
        'links = [n for n in nodes if n.get("role", {}).get("value") == "link"]\n'
        'print(len(links))\n'
        'for n in links[:5]:\n'
        '    print(n.get("name", {}).get("value", "").strip())'
    ),
    "click a point": 'q = cdp("DOM.getBoxModel", backendNodeId=7)["model"]["content"]\nclick_at_xy(sum(q[0::2]) / 4, sum(q[1::2]) / 4)',
    "f-string": 'x = 3\nprint(f"clicks: {x}")',
    "tabs": 'tabs = list_tabs()\nswitch_tab(tabs[0]["targetId"])',
}

REJECTED = {
    "import": "import os",
    "from import": "from os import system",
    "dunder escape": "print(().__class__.__bases__)",
    "dunder name": "print(__builtins__)",
    "exec": 'exec("print(1)")',
    "eval": 'eval("1")',
    "open file": 'open("C:/secret.txt").read()',
    "getattr": 'getattr(page_info, "__globals__")',
    "js helper": 'js("document.forms[0].submit()")',
    "typing": 'type_text("hunter2")',
    "fill input": 'fill_input("#password", "x")',
    "press key": 'press_key("Enter")',
    "http_get": 'http_get("https://example.com")',
    "screenshot to path": 'capture_screenshot("C:/x.png")',
    "cdp runtime evaluate": 'cdp("Runtime.evaluate", expression="1")',
    "cdp network": 'cdp("Network.getCookies")',
    "cdp fetch": 'cdp("Fetch.enable")',
    "cdp input": 'cdp("Input.dispatchMouseEvent", type="mousePressed")',
    "cdp navigate": 'cdp("Page.navigate", url="https://example.com")',
    "cdp dynamic method": 'm = "Runtime.evaluate"\ncdp(m, expression="1")',
    "alias helper": 'f = cdp\nf("Runtime.evaluate", expression="1")',
    "helper in list": 'fs = [cdp]\nfs[0]("Runtime.evaluate", expression="1")',
    "assign to helper": 'cdp = print',
    "dynamic url": 'u = "https://example.com"\nnew_tab(u)',
    "javascript url": 'goto_url("javascript:alert(1)")',
    "file url": 'new_tab("file:///C:/Windows/win.ini")',
    "no url": "new_tab()",
    "format string escape": 'print("{0.__class__}".format(1))',
    "lambda": "f = lambda: 1",
    "function def": "def f():\n    pass",
    "class def": "class A:\n    pass",
    "while loop": "while True:\n    wait(1)",
    "try": "try:\n    page_info()\nexcept Exception:\n    pass",
    "with": 'with open("x") as f:\n    pass',
    "subprocess via call chain": "print(page_info)()",
    "syntax error": "print(",
}


@pytest.mark.parametrize("code", ALLOWED.values(), ids=ALLOWED.keys())
def test_allowed_code_passes(code: str) -> None:
    check_code(code)


@pytest.mark.parametrize("code", REJECTED.values(), ids=REJECTED.keys())
def test_dangerous_code_is_rejected(code: str) -> None:
    with pytest.raises(CodeRejected):
        check_code(code)
