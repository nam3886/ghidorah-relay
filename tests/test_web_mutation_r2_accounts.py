"""Mutation markers of the Accounts view: each breaks one rule in the TypeScript source and demands that the named
Vitest test turns red (scripts/mutate.py runs these, no rebuild)."""

import pytest
from webunit import vitest

from relay_core.testing.mutation import mutation

SRC = "apps/admin-web/src"
ACCOUNT_MODEL = SRC + "/components/accounts/account-model.ts"
ACCOUNTS = SRC + "/views/AccountsView.vue"
DESKTOP = SRC + "/components/accounts/AccountsTableDesktop.vue"
MENU = SRC + "/components/shared/ActionMenu.vue"
IMPORT = SRC + "/components/accounts/ImportDialog.vue"

ACCOUNT_SPEC = "tests/unit/accounts/account-model.spec.ts"
ACCOUNTS_VIEW_SPEC = "tests/unit/accounts/accounts-view.spec.ts"
PARTS_SPEC = "tests/unit/accounts/account-parts.spec.ts"
DIALOGS_SPEC = "tests/unit/accounts/accounts-dialogs.spec.ts"

CHANGES = f"FAILED {ACCOUNT_SPEC} > accountChanges (the edit payload) > "
ASLEEP = f"FAILED {ACCOUNT_SPEC} > asleepInfo > "
ROW_TEXT = f"FAILED {ACCOUNT_SPEC} > the text of a row > "
FIGURES = f"FAILED {ACCOUNT_SPEC} > the pool card figures > "
AUTO = f"FAILED {ACCOUNTS_VIEW_SPEC} > auto-refresh > "
CHECK_ALL = f"FAILED {ACCOUNTS_VIEW_SPEC} > Check all and Refresh catalog > "
SWITCH_VIEW = f"FAILED {ACCOUNTS_VIEW_SPEC} > the enable switch > "
LIST = f"FAILED {ACCOUNTS_VIEW_SPEC} > the list > "
POOL = f"FAILED {ACCOUNTS_VIEW_SPEC} > the pool card > "
LAYOUTS = f"FAILED {ACCOUNTS_VIEW_SPEC} > the layouts > "
COMMANDS = f"FAILED {ACCOUNTS_VIEW_SPEC} > the row commands and the edit dialog > "
MENU_SPEC = f"FAILED {PARTS_SPEC} > ActionMenu > "
IMPORT_SPEC = f"FAILED {DIALOGS_SPEC} > ImportDialog > "
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
          expect=SWITCH_VIEW + "reads the list again when the change fails, so the switch goes back, and says why")
@mutation(ACCOUNTS, "const ticket = latest.next();", "const ticket = 0;",
          expect=LIST + "a slow first answer that arrives after the second does not bring its rows back")
@mutation(ACCOUNTS, 'error.value = loadFailedText(err, errorText(err), "the accounts");',
          "error.value = loadFailedText(err, errorText(err));",
          expect=LIST + "an empty list says so and offers Import; a failed read shows the error with Retry")
@mutation(DESKTOP, '<span class="sub clip" :title="accountSub(a)">', '<span class="sub clip">',
          expect=LIST + "a long account, status or asleep line keeps its whole text as its title, where it ends in "
                        "an ellipsis")
@pytest.mark.web_unit
def test_the_accounts_view_keeps_its_rules():
    vitest(ACCOUNTS_VIEW_SPEC)


# ---- Accounts: the switch, the reason, the commands and the proxy re-check -----------------------------------------
@mutation(DESKTOP, 'v-if="!a.in_use"', 'v-if="true"',
          expect=LIST + "draws every server string as text, and hides the switch of an account in use")
@mutation(DESKTOP, '<template v-if="statusReason(a)"', '<template v-if="false"',
          expect=LIST + "draws every server string as text, and hides the switch of an account in use")
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
COUNTS = (f"FAILED {ACCOUNTS_VIEW_SPEC} > the list > draws every server string as text, and hides the switch of an "
          "account in use")


@mutation(ACCOUNT_MODEL, "if (minutes < 60) return", "if (minutes <= 60) return", expect=AGO)
@mutation(ACCOUNT_MODEL, "if (hours < 48) return", "if (hours < 49) return", expect=AGO)
@pytest.mark.web_unit
def test_the_accounts_checked_stamp_reads_as_a_time_ago():
    vitest(ACCOUNT_SPEC)


@mutation(DESKTOP, '<td class="tnum right mono">{{ a.made.video }}</td>',
          '<td class="tnum right mono">{{ a.made.image }}</td>', expect=COUNTS)
@pytest.mark.web_unit
def test_the_accounts_counts_are_two_number_columns():
    vitest(ACCOUNTS_VIEW_SPEC)


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
@mutation(ACCOUNT_MODEL, 'return [name, a.provider, a.composer ? `composer ${a.composer}` : ""].filter(Boolean)',
          'return [name, a.provider, a.composer ? `composer ${a.composer}` : ""]',
          expect=ROW_TEXT + "the second line is name, provider and composer, with no empty piece")
@pytest.mark.web_unit
def test_a_row_says_its_sleep_error_proxy_and_menu_as_drawn():
    vitest(ACCOUNT_SPEC)


# ---- the pool card ----------------------------------------------------------------------------------------------
READY = (FIGURES + "health: ready is usable / available, dead is bad only when there is one, in use and asleep "
                   "are the stats'")
POOL_FIG = (FIGURES + "pool: video points left / budget, unknown, drift as a warning, failed proxies as bad, the "
                      "last full check")
POOL_EDGE = (FIGURES + "pool: one account is singular, zeros carry no colour, no full check reads never, no "
                       "answer shows dashes")


@mutation(ACCOUNT_MODEL, "value: stats ? `${stats.usable} / ${stats.available}` : \"-\"",
          "value: stats ? `${stats.available} / ${stats.usable}` : \"-\"", expect=READY)
@mutation(ACCOUNT_MODEL, "...count(stats?.in_use)", "...count(stats?.asleep)", expect=READY)
@mutation(ACCOUNT_MODEL, "...count(stats?.asleep)", "...count(stats?.in_use)", expect=READY)
@mutation(ACCOUNT_MODEL, "tone: n ? tone : undefined", "tone", expect=READY)
@mutation(ACCOUNT_MODEL, 'p.pool === "video"', 'p.pool === "image"', expect=POOL_FIG)
@mutation(ACCOUNT_MODEL, 'tone: drift ? "warn" : undefined', 'tone: "warn"', expect=POOL_EDGE)
@mutation(ACCOUNT_MODEL, '`${n} account${n === 1 ? "" : "s"}`', '`${n} accounts`', expect=POOL_EDGE)
@mutation(ACCOUNT_MODEL, 'pool.last_full_check_ms ? hhmmss(pool.last_full_check_ms) : "never"',
          'hhmmss(pool.last_full_check_ms ?? 0)', expect=POOL_EDGE)
@pytest.mark.web_unit
def test_the_pool_card_figures():
    vitest(ACCOUNT_SPEC)


@mutation(ACCOUNTS, "getPoolStats(scope.provider)", 'getPoolStats("")',
          expect=POOL + "the right half comes from the pool endpoint of the provider filter and is titled with "
                        "the provider")
@mutation(ACCOUNTS, "(err: unknown) => ({ err })", "(err: unknown) => {\n          throw err;\n        }",
          expect=POOL + "a failed pool read does not hide the list; it shows an alert and keeps the old figures")
@mutation(ACCOUNTS, 'poolError.value = loadFailedText(poolRead.err, errorText(poolRead.err), "the pool figures");',
          "poolError.value = null;",
          expect=POOL + "a failed pool read does not hide the list; it shows an alert and keeps the old figures")
@mutation(ACCOUNTS, "v-else-if=\"isTablet\"", "v-else-if=\"false\"",
          expect=LAYOUTS + "a tablet draws five columns and no Videos made column")
@mutation(ACCOUNTS, 'v-if="isPhone && pageCount <= 1 && items.length"', 'v-if="false"',
          expect=LAYOUTS + "a phone draws a list: no table, no Check button, Check first in the row menu, one "
                           "footer line")
@pytest.mark.web_unit
def test_the_pool_card_reads_the_pool_endpoint_and_the_layouts_follow_the_width():
    vitest(ACCOUNTS_VIEW_SPEC)


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
@mutation(IMPORT, "Imported {{ result.imported.length }}, rejected {{ result.rejected.length }}",
          "Imported {{ result.rejected.length }}, rejected {{ result.imported.length }}",
          expect=IMPORT_SPEC + "a clean import shows one pill and no list; the box is gone on the next opening")
@pytest.mark.web_unit
def test_the_switch_the_menu_and_the_import_result():
    vitest(PARTS_SPEC, DIALOGS_SPEC)


# ---- the pasted secret never outlives its use; polling is polite -----------------------------------------------------
@mutation(IMPORT, 'proxy.value = "";', "",
          expect=WIPE + "after a success both fields are empty, and the request still carried both")
@mutation(IMPORT, "  wipe();\n  await run(", "  await run(",
          expect=WIPE + "the fields are empty while the request is still running")
@mutation(IMPORT, "watch(open, (isOpen) => {\n  wipe();", "watch(open, (isOpen) => {",
          expect=WIPE + "a cancelled paste is gone when the dialog is opened again")
@pytest.mark.web_unit
def test_the_import_dialog_wipes_what_was_pasted():
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
@mutation(DESKTOP, 'v-if="chips[`check:${a.id}`]"', 'v-if="false"',
          expect=COMMANDS + "a Check's chip runs under its button, says how it ended, stays through the next automatic "
                            "read and goes with the one after")
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
@mutation(DESKTOP, '<tbody v-if="skeleton" aria-hidden="true">', '<tbody v-if="false" aria-hidden="true">',
          expect=LIST + "the first read draws the list's own header over five rows of bars, and no footer count")
@mutation(ACCOUNTS, 'v-else-if="items.length === 0 && !skeleton"', 'v-else-if="items.length === 0"',
          expect=LIST + "the first read draws the list's own header over five rows of bars, and no footer count")
@pytest.mark.web_unit
def test_the_command_chips_follow_their_commands():
    vitest(ACCOUNT_SPEC, ACCOUNTS_VIEW_SPEC, DIALOGS_SPEC)


@mutation(SRC + "/composables/use-command-poll.ts", "`${label} failed${", "`${label}: failed${",
          expect="FAILED tests/unit/use-command-poll.spec.ts > useCommandPoll > says just failed when the worker left "
                 "no error text")
@pytest.mark.web_unit
def test_a_failed_command_reads_as_the_canvas_writes_it():
    vitest("tests/unit/use-command-poll.spec.ts")


@mutation(SRC + "/components/accounts/PoolCard.vue", 'v-if="count !== null"', 'v-if="true"',
          expect=f"FAILED {PARTS_SPEC} > PoolCard > names the number of accounts once the list has been read, and "
                 "nothing before")
@pytest.mark.web_unit
def test_the_pool_card_names_no_count_before_the_list_is_read():
    vitest(PARTS_SPEC)


# ---- page-accounts round, review fixes: one action per account at a time, Save after a running Check proxy, no
# server text in the import result, the toasts' words ----------------------------------------------------------------
DUPLICATE = COMMANDS + "a second click while an action runs sends nothing: Wake, Delete, the switch, the phone's Check"


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
INK = (f"FAILED {FIELD_SPEC} > the field variant of FilterSelect > the chevron is drawn in the page's ink, as the Jobs "
       "chip's is, and only a disabled field turns it faint")
FORM_FIELD = (f"FAILED {FIELD_SPEC} > the field variant of FilterSelect > a form's field (block) is no chip on a "
              "phone, and the list hangs under the box")
IN_DIALOG = (f"FAILED {FIELD_SPEC} > the field variant of FilterSelect > in a dialog: Escape closes the open list "
             "only > ")
PROVIDER = (IMPORT_SPEC + "the Provider field is the drawn select: another provider picked from its list is the one "
            "sent")


@mutation(IMPORT, "@update:model-value=\"(v) => (provider = String(v))\"", "", expect=PROVIDER)
@mutation(FIELD, '        @pointerdown="onListPress"\n', "",
          expect=IN_DIALOG + "a press on the list's padding keeps the focus on the select, and Escape still leaves the "
                             "dialog open")
@mutation(FIELD, "  if (x < el.clientWidth && y < el.clientHeight) event.preventDefault();",
          "  event.preventDefault();",
          expect=IN_DIALOG + "a press in the list's scrollbar gutter is left alone, so the scrollbar can be dragged")
@mutation(FIELD, "  right: 11px;\n  color: var(--rl-fg);", "  right: 11px;\n  color: var(--rl-muted);", expect=INK)
@mutation(FIELD, "chevron {\n  color: var(--rl-faint);", "chevron {\n  color: var(--rl-muted);", expect=INK)
@mutation(FIELD, '<IconChevronDown class="filter-field__chevron"',
          '<IconChevronDown v-if="false" class="filter-field__chevron"', expect=DRAWN)
@mutation(FIELD, "field.value && !props.block && isPhone.value", "field.value && isPhone.value", expect=FORM_FIELD)
@mutation(FIELD, '"is-tall": isTablet.value,', '"is-tall": false,', expect=DRAWN)
@pytest.mark.web_unit
def test_the_provider_field_is_the_drawn_select():
    vitest(DIALOGS_SPEC, FIELD_SPEC)
