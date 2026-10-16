"""The shell around every tab: the Provider filter and the theme's multiple-select tick."""

from . import close_select, open_select

ROUTE = "shell"
STATES = [("shell", "scope-select"), ("shell", "multiple-select-tick")]


def check_multiple_select_tick(page):
    """No view has a multiple select, but the theme overrides the tick Element Plus draws with a data: image there
    (blocked by the CSP). Build the markup it styles and read the computed mask: it must be none."""
    got = page.evaluate("""() => {
        const box = document.createElement('div');
        box.className = 'el-select-dropdown is-multiple';
        const list = document.createElement('ul');
        const item = document.createElement('li');
        item.className = 'el-select-dropdown__item is-selected';
        item.textContent = 'x';
        list.appendChild(item);
        box.appendChild(list);
        document.body.appendChild(box);
        const after = getComputedStyle(item, '::after');
        const out = [after.maskImage, after.webkitMaskImage];
        box.remove();
        return out;
    }""", isolated_context=False)
    assert all(v in ("none", "", None) for v in got), "the tick still has a mask image: %r" % (got,)


def run(page, app, live, sw, data):
    sw.step("shell", "scope-select", lambda: (open_select(page, "Provider filter"),
                                              close_select(page, "Provider filter")))
    sw.step("shell", "multiple-select-tick", lambda: check_multiple_select_tick(page))
