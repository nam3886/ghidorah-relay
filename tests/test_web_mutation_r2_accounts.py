"""Mutation markers of the Accounts view: each breaks one rule in the TypeScript source and demands that the named
Vitest test turns red (scripts/mutate.py runs these, no rebuild)."""

import pytest
from webunit import vitest

from relay_core.testing.mutation import mutation

SRC = "apps/admin-web/src"
ACCOUNT_MODEL = SRC + "/components/accounts/account-model.ts"
ACCOUNTS = SRC + "/views/AccountsView.vue"
MENU = SRC + "/components/shared/ActionMenu.vue"
IMPORT = SRC + "/components/accounts/ImportDialog.vue"

ACCOUNT_SPEC = "tests/unit/accounts/account-model.spec.ts"
ACCOUNTS_VIEW_SPEC = "tests/unit/accounts/accounts-view.spec.ts"
PARTS_SPEC = "tests/unit/accounts/account-parts.spec.ts"
DIALOGS_SPEC = "tests/unit/accounts/accounts-dialogs.spec.ts"

CHANGES = f"FAILED {ACCOUNT_SPEC} > accountChanges (the edit payload) > "
ASLEEP = f"FAILED {ACCOUNT_SPEC} > asleepInfo > "
ROW_TEXT = f"FAILED {ACCOUNT_SPEC} > the text of a row > "
AUTO = f"FAILED {ACCOUNTS_VIEW_SPEC} > auto-refresh > "
CHECK_ALL = f"FAILED {ACCOUNTS_VIEW_SPEC} > Check all and Refresh catalog > "
SWITCH_VIEW = f"FAILED {ACCOUNTS_VIEW_SPEC} > the bulk Disable > "
LIST = f"FAILED {ACCOUNTS_VIEW_SPEC} > the list > "
COMMANDS = f"FAILED {ACCOUNTS_VIEW_SPEC} > the row commands and the edit dialog > "
MENU_SPEC = f"FAILED {PARTS_SPEC} > ActionMenu > "
WIPE = f"FAILED {DIALOGS_SPEC} > what was pasted never outlives its use > "


# ---- the account edit payload and the auto-refresh period -------------------------------------------------------
@mutation(ACCOUNT_MODEL, "if (label !== a.label) body.label = label;", "body.label = label;",
          expect=CHANGES + "sends only the proxy when only the proxy changed, trimmed")
@mutation(ACCOUNT_MODEL, "if (next !== proxyText(a)) body.proxy = next || null;", "body.proxy = next || null;",
          expect=CHANGES + "sends only the label when only the label changed")
@mutation(ACCOUNT_MODEL, "body.proxy = next || null", "body.proxy = next",
          expect=CHANGES + "sends proxy null when the proxy was emptied")
@pytest.mark.web_unit
def test_the_account_edit_sends_changed_fields_only():
    vitest(ACCOUNT_SPEC)


@mutation(ACCOUNT_MODEL, "AUTO_REFRESH_MS = 10000", "AUTO_REFRESH_MS = 5000",
          expect=AUTO + "reads the list every 10 seconds while the page is open")
@mutation(ACCOUNTS, "if (!dialogOpen.value && ", "if (",
          expect=AUTO + "does not read the list while a dialog is open, and goes on when it closes")
@mutation(ACCOUNTS, "await queue(start(name), `${label} ${name}`, false);",
          "void queue(start(name), `${label} ${name}`, false);",
          expect=CHECK_ALL + "with the filter on All, goes over the providers one after another")
@mutation(ACCOUNTS, "await patchAccount(a.id, { enabled });\n    } finally {\n      if (!disposed) await load();",
          "await patchAccount(a.id, { enabled });\n    } finally {\n      void 0;",
          expect=SWITCH_VIEW + "reads the list again when the change fails, so the row goes back, and says why")
@mutation(ACCOUNTS, "const ticket = latest.next();", "const ticket = 0;",
          expect=LIST + "a slow first answer that arrives after the second does not bring its rows back")
@mutation(ACCOUNTS, 'error.value = loadFailedText(err, errorText(err), "the accounts");',
          "error.value = loadFailedText(err, errorText(err));",
          expect=LIST + "an empty list says so and offers Import; a failed read shows the error with Retry")
@pytest.mark.web_unit
def test_the_accounts_view_keeps_its_rules():
    vitest(ACCOUNTS_VIEW_SPEC)


# ---- Accounts: the switch, the reason, the commands and the proxy re-check -----------------------------------------
@mutation(ACCOUNT_MODEL, 'WAKE_LABEL = "Wake: clear cooldown and strikes"', 'WAKE_LABEL = "Clear cooldown"',
          expect=COMMANDS + "Wake queues the wake command under the label that names the cooldown and the strikes")
@mutation(ACCOUNTS, 'else if (name === "wake") void wake(a);', 'else if (name === "wake") void check(a);',
          expect=COMMANDS + "Wake queues the wake command under the label that names the cooldown and the strikes")
@mutation(ACCOUNTS, "runProxyCheck(() =>", "run(() =>",
          expect=COMMANDS + "Check proxy keeps the dialog open with its chip, queues the re-check, polls Proxy check "
                            "and reads the list again")
@mutation(ACCOUNTS, 'queue(recheckProxy(a.id), "Proxy check", false)', 'queue(recheckProxy(a.id), "Proxy", false)',
          expect=COMMANDS + "Check proxy keeps the dialog open with its chip, queues the re-check, polls Proxy check "
                            "and reads the list again")
@mutation(ACCOUNTS, 'if (out.command_id) await poll(out.command_id, "Proxy check");', "",
          expect=COMMANDS + "Save of a changed proxy polls the Proxy check the server queued, then reads the list")
@mutation(ACCOUNTS, "  if (checking.value.has(a.id)) return;\n  checking.value", "  checking.value",
          expect=COMMANDS + "Check queues a check, locks that button while it runs, and says what it found")
@pytest.mark.web_unit
def test_the_accounts_view_commands_and_proxy_recheck():
    vitest(ACCOUNTS_VIEW_SPEC)


# ---- colour is for exceptions: the checked stamp and the counts -------------------------------------------------
AGO = (f"FAILED {ACCOUNT_SPEC} > pointsFigure, ago, proxyCheck, checkSummary > ago: never, just now, minutes, hours, "
       "days; a future time is just now")


@mutation(ACCOUNT_MODEL, "if (minutes < 60) return", "if (minutes <= 60) return", expect=AGO)
@mutation(ACCOUNT_MODEL, "if (hours < 48) return", "if (hours < 49) return", expect=AGO)
@pytest.mark.web_unit
def test_the_accounts_checked_stamp_reads_as_a_time_ago():
    vitest(ACCOUNT_SPEC)


# ---- what a row says: sleep, errors, proxy, the menu ----------------------------------------------------------------
@mutation(ACCOUNT_MODEL, 'kind: a.strikes > 0 ? "strike" : null', 'kind: "strike"',
          expect=ASLEEP + "a cooldown still ahead: the time it ends, a strike when the account has strikes, and "
                          "the reason")
@mutation(ACCOUNT_MODEL, "if (a.cooldown_until_ms && a.cooldown_until_ms > now)",
          "if (a.cooldown_until_ms && a.cooldown_until_ms > 0)",
          expect=ASLEEP + "a time in the past, or none, is awake")
@mutation(ACCOUNT_MODEL, "find((c) => c.rest_until_ms && c.rest_until_ms > now)",
          "find((c) => c.rest_until_ms)", expect=ASLEEP + "a time in the past, or none, is awake")
@mutation(ACCOUNT_MODEL, "return a.last_error && a.last_error !== a.state_reason ? a.last_error : null;",
          "return a.last_error || null;",
          expect=ROW_TEXT + "a narrow row shows the last error only when it adds to the reason")
@mutation(ACCOUNT_MODEL, '.slice("proxy check: ".length)', ".slice(0)",
          expect=ROW_TEXT + "the proxy status of the Edit dialog carries the stored reason of a failed check")
@mutation(ACCOUNT_MODEL, "disabled: !a.has_screenshot", "disabled: false",
          expect=ROW_TEXT + "the row menu: Shot cannot be picked without a screenshot, Delete is last and apart, "
                            "a phone adds Check")
@mutation(ACCOUNT_MODEL, 'return phone ? [{ key: "check", label: "Check", icon: IconCheck }, ...items] : items;',
          "return items;",
          expect=ROW_TEXT + "the row menu: Shot cannot be picked without a screenshot, Delete is last and apart, "
                            "a phone adds Check")
@mutation(ACCOUNT_MODEL, 'return [name, a.composer ? `composer ${a.composer}` : ""].filter(Boolean)',
          'return [name, a.composer ? `composer ${a.composer}` : ""]',
          expect=ROW_TEXT + "the second line is name and composer, with no empty piece; the lane names the provider")
@pytest.mark.web_unit
def test_a_row_says_its_sleep_error_proxy_and_menu_as_drawn():
    vitest(ACCOUNT_SPEC)


# ---- the switch, the menu and the import result ---------------------------------------------------------------
@mutation(MENU, 'if (event.key === "Escape") {', 'if (event.key === "Esc") {',
          expect=MENU_SPEC + "Escape closes it and gives the focus back to the trigger")
@mutation(MENU, "if (target && (menu.value?.contains(target) || trigger.value?.contains(target))) return;",
          "if (target) return;", expect=MENU_SPEC + "a press elsewhere closes it; a press inside does not")
@mutation(MENU, "const onWindow = (): void => measure();", "const onWindow = (): void => close();",
          expect=MENU_SPEC + "a scroll or a resize keeps the menu open and moves it under the trigger")
@mutation(MENU, "let top = rect.bottom + GAP;", "let top = rect.top + GAP;",
          expect=MENU_SPEC + "a scroll or a resize keeps the menu open and moves it under the trigger")
@mutation(MENU, "if (height && top + height > window.innerHeight && rect.top - GAP - height >= 0)",
          "if (false && height)",
          expect=MENU_SPEC + "opens above the trigger when it would run past the bottom of the window, and there "
                             "is room above")
@mutation(MENU, "if (item.disabled) return;", "",
          expect=MENU_SPEC + "a pick emits its key and closes the menu; a disabled item emits nothing and stays "
                             "open")
@pytest.mark.web_unit
def test_the_switch_the_menu_and_the_import_result():
    vitest(PARTS_SPEC, DIALOGS_SPEC)


# ---- the pasted secret never outlives its use; polling is polite -----------------------------------------------------
@mutation(IMPORT, 'proxy.value = "";', "",
          expect=WIPE + "after a success both fields are empty, and the request still carried both")
@mutation(IMPORT, "  wipe();\n  await run(", "  await run(",
          expect=WIPE + "the fields are empty while the request is still running")
@mutation(IMPORT, "watch(open, (isOpen) => {\n  wipe();", "watch(open, (isOpen) => {",
          expect=WIPE + "the X: asks, and on Discard the paste is gone when the drawer is opened again")
@pytest.mark.web_unit
def test_the_import_dialog_wipes_what_was_pasted():
    vitest(DIALOGS_SPEC)


# ---- the two forms are guarded drawers: what was typed is never lost to a stray close, and nothing lingers ----------
EDIT = SRC + "/components/accounts/EditDialog.vue"
GUARD = f"FAILED {DIALOGS_SPEC} > EditDialog > the guard > "


@mutation(IMPORT, 'credentials.value.trim() !== "" || proxy.value.trim() !== ""', 'credentials.value.trim() !== ""',
          expect=WIPE + "a proxy typed alone asks")
@mutation(IMPORT, 'credentials.value.trim() !== "" || proxy.value.trim() !== ""',
          'credentials.value !== "" || proxy.value !== ""',
          expect=WIPE + "spaces alone are not worth asking about")
@mutation(EDIT, "label.value !== snap.value.label || proxy.value !== snap.value.proxy",
          "label.value !== (props.account?.label ?? '') || "
          "proxy.value !== (props.account ? proxyText(props.account) : '')",
          expect=GUARD + "a new copy of the account (the list read, the end of a Check proxy) changes nothing: not "
                         "clean, not dirty")
@mutation(EDIT, "label.value !== snap.value.label || proxy.value !== snap.value.proxy",
          "label.value !== snap.value.label",
          expect=GUARD + "a new password alone, typed as host:port:user:pass, is a change")
@mutation(EDIT, 'label.value = proxy.value = "";', "",
          expect=GUARD + "every close leaves nothing typed behind: a drawer closed from outside is clean and empty")
@pytest.mark.web_unit
def test_the_account_forms_are_guarded_drawers():
    vitest(DIALOGS_SPEC)


# ---- the drawers on the page: a command elsewhere never traps the Edit drawer, a leave of the page is guarded, focus -
DRAWERS = f"FAILED {ACCOUNTS_VIEW_SPEC} > the Edit and Import drawers on the page > "
FOCUS = DRAWERS + "focus after the drawer is gone > "
LEAVE_DIRTY = DRAWERS + "a paste in Import and a change in Edit are what a leave of the page would lose"
LEAVE_BLOCKED = DRAWERS + "an import under way blocks a leave of the page, and so does a save in flight"


@mutation(ACCOUNTS, "(importOpen.value && importDirty.value) || (editOpen.value && editDirty.value),",
          "(editOpen.value && editDirty.value),", expect=LEAVE_DIRTY)
@mutation(ACCOUNTS, "(importOpen.value && importDirty.value) || (editOpen.value && editDirty.value),",
          "(importOpen.value && importDirty.value),", expect=LEAVE_DIRTY)
@mutation(ACCOUNTS, "() => importBusy.value || saving.value,", "() => saving.value,", expect=LEAVE_BLOCKED)
@mutation(ACCOUNTS, "() => importBusy.value || saving.value,", "() => importBusy.value,", expect=LEAVE_BLOCKED)
@mutation(IMPORT, 'watch(busy, (b) => emit("update:busy", b));', "", expect=LEAVE_BLOCKED)
@mutation(ACCOUNTS, " && opener.getClientRects().length) return;", ") return;",
          expect=FOCUS + "goes to the page's Import button when the opener can no longer be seen")
@mutation(ACCOUNTS, "if (opener?.isConnected &&", "if (false && opener?.isConnected &&",
          expect=FOCUS + "is left to Element Plus, which gives it back to the row button, while that button "
                         "can be seen")
@mutation(ACCOUNTS, "work\n  await run(async () => {\n    if (out.command_id)",
          "work\n  await runSave(async () => {\n    if (out.command_id)",
          expect=DRAWERS + "after the save the proxy check it queued does not lock the drawer: Edit opens again and "
                           "closes")
@pytest.mark.web_unit
def test_the_account_drawers_on_the_page():
    vitest(ACCOUNTS_VIEW_SPEC)


@mutation(IMPORT, "  const body = importBody(provider.value, credentials.value, proxy.value);\n",
          "  wipe();\n  const body = importBody(provider.value, credentials.value, proxy.value);\n",
          expect=WIPE + "after a success both fields are empty, and the request still carried both")
@mutation(IMPORT, "watch(open, (isOpen) => {\n  wipe();\n  if (!isOpen) return;",
          "watch(open, (isOpen) => {\n  if (!isOpen) return;\n  wipe();",
          expect=WIPE + "the drawer closed from outside empties both fields and the view hears that it is clean")
@pytest.mark.web_unit
def test_the_import_wipes_in_the_right_order():
    vitest(DIALOGS_SPEC)


@mutation(ACCOUNTS, "!document.hidden && ", "",
          expect=AUTO + "a hidden tab does not read the list, and the tab that comes back reads at once")
@mutation(ACCOUNTS, "if (!document.hidden) tick();", "if (document.hidden) tick();",
          expect=AUTO + "a hidden tab does not read the list, and the tab that comes back reads at once")
@mutation(ACCOUNTS, " && !loading.value) void load(true);", ") void load(true);",
          expect=AUTO + "a read still in flight is never joined by a second one")
@pytest.mark.web_unit
def test_the_accounts_polling_is_polite():
    vitest(ACCOUNTS_VIEW_SPEC)


# ---- page-accounts round: the delete confirm and the row menu as the canvas draws them ------------------------------
@mutation(ACCOUNTS, "title: `Delete account ${a.label}?`", "title: \"Delete account\"",
          expect=COMMANDS + "Delete asks first and queues the delete only after a yes")
@mutation(MENU, "const GAP = props.variant === \"row\" ? 8 : 6;", "const GAP = 6;",
          expect=MENU_SPEC + "a row menu sits 8 px under its trigger and a page menu 6 px, as drawn")
@pytest.mark.web_unit
def test_the_delete_confirm_and_the_row_menu_are_placed_as_drawn():
    vitest(ACCOUNTS_VIEW_SPEC, PARTS_SPEC)


@mutation(ACCOUNT_MODEL, 'a.state === "disabled" ? null : a.state_reason', "a.state_reason",
          expect=ROW_TEXT + "a switched-off account shows no reason under its pill; a dead one shows its reason")
@mutation(ACCOUNT_MODEL, 'Number(b.pool === "video") - Number(a.pool === "video")', "0",
          expect=ROW_TEXT + "the video pool comes first, the others keep their order; the tablet counts are singular "
                            "for one")
@mutation(ACCOUNT_MODEL, '`${n} ${word}${n === 1 ? "" : "s"}`', "`${n} ${word}s`",
          expect=ROW_TEXT + "the video pool comes first, the others keep their order; the tablet counts are singular "
                            "for one")
@pytest.mark.web_unit
def test_a_switched_off_row_and_the_pool_order_read_as_drawn():
    vitest(ACCOUNT_SPEC)


@mutation(ACCOUNT_MODEL, "!c.settled || c.read >= read", "true",
          expect=f"FAILED {ACCOUNT_SPEC} > command chips > a finished chip stays through the next automatic read and "
                 "goes with the one after; a running one stays")
@mutation(ACCOUNT_MODEL, 'if (command.status === "failed") return', "if (false) return",
          expect=f"FAILED {ACCOUNT_SPEC} > command chips > the end of a poll: ok with the time it finished, error with "
                 "the worker's reason, running when it gave up")
@mutation(ACCOUNTS, "if (auto) chips.value = chipsAfterRead(chips.value, ++autoReads);", "",
          expect=COMMANDS + "a Check's chip runs under its button, says how it ended, stays through the next automatic "
                            "read and goes with the one after")
@mutation(ACCOUNTS, "if (auto) chips.value =", "if (true) chips.value =",
          expect=COMMANDS + "two quick Checks keep both results: the reads the commands make do not clear a chip")
@mutation(ACCOUNTS, "if (ended?.status !== \"failed\") ended = command;", "ended = command;",
          expect=CHECK_ALL + "each page command has its chip under its button: running, then how the round ended (the "
                             "first failure)")
@mutation(SRC + "/components/accounts/EditDialog.vue", 'if (!props.account || props.proxyChip?.state === "running")',
          "if (!props.account)",
          expect=f"FAILED {DIALOGS_SPEC} > EditDialog > Check proxy asks the view to queue it and stays open; its chip "
                 "takes the status line, locked while it runs")
@mutation(ACCOUNT_MODEL, '["credential_unparseable", "The line is not a cookie jar."]',
          '["credential_unparseable", ""]',
          expect=f"FAILED {ACCOUNT_SPEC} > rejectedText > says why a line was rejected in a sentence; any other code "
                 "gets one fixed sentence")
@pytest.mark.web_unit
def test_the_command_chips_follow_their_commands():
    vitest(ACCOUNT_SPEC, ACCOUNTS_VIEW_SPEC, DIALOGS_SPEC)


@mutation(SRC + "/composables/use-command-poll.ts", "`${label} failed${", "`${label}: failed${",
          expect="FAILED tests/unit/use-command-poll.spec.ts > useCommandPoll > says just failed when the worker left "
                 "no error text")
@pytest.mark.web_unit
def test_a_failed_command_reads_as_the_canvas_writes_it():
    vitest("tests/unit/use-command-poll.spec.ts")


# ---- page-accounts round, review fixes: one action per account at a time, Save after a running Check proxy, no
# server text in the import result, the toasts' words ----------------------------------------------------------------
DUPLICATE = (COMMANDS + "a second click while an action runs sends nothing: Wake, Delete, the bulk Disable, the "
             "phone's Check")


@mutation(ACCOUNTS, "  if (inFlight.has(key)) return;\n", "", expect=DUPLICATE)
@mutation(ACCOUNTS, "  if (checking.value.has(a.id)) return;\n", "", expect=DUPLICATE)
@mutation(ACCOUNTS, "    await proxyRuns.get(a.id);\n", "",
          expect=COMMANDS + "Save during a running Check proxy waits for it, then sends what was typed")
@mutation(SRC + "/components/accounts/EditDialog.vue", "[() => open.value, () => props.account?.id]",
          "[() => open.value, () => props.account]",
          expect=f"FAILED {DIALOGS_SPEC} > EditDialog > a new copy of the same account keeps what was typed; another "
                 "account fills the fields again")
@mutation(ACCOUNTS, "  if (toast) notify.info(`${label}: queued`);", "  notify.info(`${label}: queued`);",
          expect=COMMANDS + "Check queues a check, locks that button while it runs, and says what it found")
@mutation(ACCOUNT_MODEL, "REJECTED.get(reason) ?? REJECTED_OTHER", "REJECTED.get(reason) ?? reason",
          expect=f"FAILED {ACCOUNT_SPEC} > rejectedText > says why a line was rejected in a sentence; any other code "
                 "gets one fixed sentence")
@mutation(ACCOUNT_MODEL, "`Check done: ${", "`Check: ${",
          expect=f"FAILED {ACCOUNT_SPEC} > pointsFigure, ago, proxyCheck, checkSummary > check summary names the "
                 "outcome, the state and the reason")
@mutation(SRC + "/composables/use-command-poll.ts", "notify.success(`${label} done`);",
          "notify.success(`${label}: done`);",
          expect="FAILED tests/unit/use-command-poll.spec.ts > useCommandPoll > stops at done with a success toast, "
                 "one second between asks")
@pytest.mark.web_unit
def test_review_fixes_hold():
    vitest(ACCOUNT_SPEC, ACCOUNTS_VIEW_SPEC, DIALOGS_SPEC, "tests/unit/use-command-poll.spec.ts")


# ---- the Provider field: FilterSelect draws the page's chevron on every field -------------------------------------
FIELD = SRC + "/components/shared/FilterSelect.vue"
FIELD_SPEC = "tests/unit/shared/filter-select-field.spec.ts"
DRAWN = (f"FAILED {FIELD_SPEC} > the field variant of FilterSelect > the field draws the page's chevron, 14 px, on a "
         "desktop, a tablet and a phone chip")
FORM_FIELD = (f"FAILED {FIELD_SPEC} > the field variant of FilterSelect > a form's field (block) is no chip on a "
              "phone, and the list hangs under the box")
IN_DIALOG = (f"FAILED {FIELD_SPEC} > the field variant of FilterSelect > in a drawer: Escape closes the open list "
             "only > ")


@mutation(FIELD, "  if (x < el.clientWidth && y < el.clientHeight) event.preventDefault();",
          "  event.preventDefault();",
          expect=IN_DIALOG + "a press in the list's scrollbar gutter is left alone, so the scrollbar can be dragged")
@mutation(FIELD, "field.value && !props.block && isPhone.value", "field.value && isPhone.value", expect=FORM_FIELD)
@mutation(FIELD, '"is-tall": isTablet.value,', '"is-tall": false,', expect=DRAWN)
@pytest.mark.web_unit
def test_the_provider_field_is_the_drawn_select():
    vitest(DIALOGS_SPEC, FIELD_SPEC)
