"""The shell around every tab: the account menu and the theme's multiple-select tick (the Provider list is gone:
every tab filters by provider in its strip)."""

from patchright.sync_api import expect

ROUTE = "shell"
STATES = [("shell", "account-menu"), ("shell", "multiple-select-tick")]


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
    sw.step("shell", "account-menu", lambda: popover(page, page.get_by_test_id("account-menu"),
                                                     page.get_by_role("menu", name="Account")))
    sw.step("shell", "multiple-select-tick", lambda: check_multiple_select_tick(page))
