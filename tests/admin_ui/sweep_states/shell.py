"""The shell around every tab: the Provider listbox, the account menu and the theme's multiple-select tick."""

from patchright.sync_api import expect

from . import tab

ROUTE = "shell"
STATES = [("shell", "scope-select"), ("shell", "account-menu"), ("shell", "multiple-select-tick")]


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


def popover(page, trigger, popup):
    """Open a top bar popover from its trigger, then close it with Escape."""
    trigger.click()
    expect(popup).to_be_visible()
    page.keyboard.press("Escape")
    expect(popup).to_be_hidden()


def run(page, app, live, sw, data):
    # the top bar's Provider list lives on the tabs without the provider strip (Settings is one)
    sw.step("shell", "scope-select", lambda: (tab(page, "settings"),
                                              popover(page, page.get_by_test_id("provider-filter"),
                                                      page.get_by_role("listbox", name="Provider"))))
    sw.step("shell", "account-menu", lambda: popover(page, page.get_by_test_id("account-menu"),
                                                     page.get_by_role("menu", name="Account")))
    sw.step("shell", "multiple-select-tick", lambda: check_multiple_select_tick(page))
