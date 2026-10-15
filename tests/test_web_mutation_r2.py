"""Mutation markers of the Accounts, Keys, Request logs and Alerts views: each breaks one rule in the TypeScript
source and demands that the named Vitest test turns red (scripts/mutate.py runs these, no rebuild)."""

import pytest
from webunit import vitest

from relay_core.testing.mutation import mutation

SRC = "apps/admin-web/src"
WINDOW = SRC + "/components/logs/log-window.ts"
KEY_MODEL = SRC + "/components/keys/key-model.ts"
ACCOUNT_MODEL = SRC + "/components/accounts/account-model.ts"
ACCOUNTS = SRC + "/views/AccountsView.vue"
KEYS = SRC + "/views/KeysView.vue"
ALERTS = SRC + "/views/AlertsView.vue"
LOGS = SRC + "/views/LogsView.vue"
NEW_KEY = SRC + "/components/keys/NewKeyCallout.vue"
ALERTS_MODEL = SRC + "/components/alerts/alerts-model.ts"
ALERT_DETAIL = SRC + "/components/alerts/AlertDetailPanel.vue"

WINDOW_SPEC = "tests/unit/logs/log-window.spec.ts"
KEY_SPEC = "tests/unit/keys/key-model.spec.ts"
ACCOUNT_SPEC = "tests/unit/accounts/account-model.spec.ts"
ACCOUNTS_VIEW_SPEC = "tests/unit/accounts/accounts-view.spec.ts"
KEYS_VIEW_SPEC = "tests/unit/keys/keys-view.spec.ts"
ALERTS_VIEW_SPEC = "tests/unit/alerts/alerts-view.spec.ts"
ALERTS_MODEL_SPEC = "tests/unit/alerts/alerts-model.spec.ts"
LOGS_VIEW_SPEC = "tests/unit/logs/logs-view.spec.ts"

SAIGON = f"FAILED {WINDOW_SPEC} > the window in Asia/Saigon (UTC+7, no clock change) > "
NEW_YORK = f"FAILED {WINDOW_SPEC} > the window across a clock change (America/New_York) > "
QUERY = f"FAILED {WINDOW_SPEC} > logQuery > "
BODY = f"FAILED {KEY_SPEC} > keyBody (the create and edit payload) > "
CHANGES = f"FAILED {ACCOUNT_SPEC} > accountChanges (the edit payload) > "
AUTO = f"FAILED {ACCOUNTS_VIEW_SPEC} > auto-refresh > "
ONE_TIME = f"FAILED {KEYS_VIEW_SPEC} > the one-time key > "
GUARD = f"FAILED {ALERTS_VIEW_SPEC} > the generation guard > "
ACK = f"FAILED {ALERTS_VIEW_SPEC} > acknowledge > "
CHECK_ALL = f"FAILED {ACCOUNTS_VIEW_SPEC} > Check all and Refresh catalog > "
SWITCH = f"FAILED {ACCOUNTS_VIEW_SPEC} > the enable switch > "
LIST = f"FAILED {ACCOUNTS_VIEW_SPEC} > the list > "
COMMANDS = f"FAILED {ACCOUNTS_VIEW_SPEC} > the row commands and the edit dialog > "
COPY = f"FAILED {KEYS_VIEW_SPEC} > Copy > "
CLEAR = f"FAILED {LOGS_VIEW_SPEC} > clear logs > "
ALERT_LISTS = f"FAILED {ALERTS_VIEW_SPEC} > the lists > "
ALERT_FILTERS = (f"FAILED {ALERTS_VIEW_SPEC} > the filters > send the Status and then the Kind to the server, each "
                 "in its own place")
ALERT_DETAIL_SPEC = f"FAILED {ALERTS_VIEW_SPEC} > the detail > "
IN_FLIGHT = (f"FAILED {ALERTS_VIEW_SPEC} > acknowledge > while an acknowledge is in flight, the selects, Details, "
             "Close and both Acknowledge buttons are off")
AFTER_FAILURE = f"FAILED {ALERTS_VIEW_SPEC} > acknowledge > after a failed acknowledge every control is on again"
ONE_CUSTOMER = f"FAILED {ALERTS_MODEL_SPEC} > oneCustomer > "
EMPTY_WORDING = f"FAILED {ALERTS_MODEL_SPEC} > the empty-wording group > "


# ---- the Request logs date window -------------------------------------------------------------------------------
@mutation(WINDOW, "new Date(y, m, d + 1).getTime()", "new Date(y, m, d).getTime() + 86400000",
          expect=NEW_YORK + "the day the clocks go forward is 23 hours long")
@mutation(WINDOW, "return new Date(y, m, d).getTime();", "return Date.UTC(y, m, d);",
          expect=SAIGON + "a day starts at its own local midnight")
@mutation(WINDOW, "query.until_ms = dayAfter(filters.to);", "query.until_ms = dayStart(filters.to);",
          expect=QUERY + "From alone sets since only; To alone sets until only")
@mutation(WINDOW, "query.since_ms = dayStart(filters.from);", "query.since_ms = dayAfter(filters.from);",
          expect=QUERY + "From alone sets since only; To alone sets until only")
@pytest.mark.web_unit
def test_the_logs_window_is_local_midnights_and_half_open():
    vitest(WINDOW_SPEC)


# ---- the key form payload ---------------------------------------------------------------------------------------
@mutation(KEY_MODEL, 'if (row.mode === "limit") {', 'if (row.mode !== "closed") {',
          expect=BODY + "closed and unlimited wallets carry no limits, even when the boxes still hold numbers")
@mutation(KEY_MODEL, "if (row.day) body.day_limit = Number(row.day);", "body.day_limit = Number(row.day);",
          expect=BODY + "a limit wallet sends only the boxes that are filled, as numbers")
@mutation(KEY_MODEL, 'models: form.any ? ["*"] : [...form.models],', "models: [...form.models],",
          expect=BODY + "any model sends * and ignores the ticked list")
@mutation(KEY_MODEL, 'form.maxUnfinished === "" ? null : Number(form.maxUnfinished)', "Number(form.maxUnfinished)",
          expect=BODY + "max unfinished: empty is the default (null), 0 is unlimited, a number is a number")
@pytest.mark.web_unit
def test_the_key_body_sends_what_the_form_means():
    vitest(KEY_SPEC)


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
          expect=AUTO + "reads the list every 10 seconds while the switch is on, and stops when it is off")
@mutation(ACCOUNTS, "if (!dialogOpen.value) void load();", "void load();",
          expect=AUTO + "does not read the list while a dialog is open, and goes on when it closes")
@mutation(ACCOUNTS, "await queue(start(name), `${label} ${name}`);", "void queue(start(name), `${label} ${name}`);",
          expect=CHECK_ALL + "with the filter on All, goes over the providers one after another")
@mutation(ACCOUNTS, "await patchAccount(a.id, { enabled });\n    } finally {\n      if (!disposed) await load();",
          "await patchAccount(a.id, { enabled });\n    } finally {\n      void 0;",
          expect=SWITCH + "reads the list again when the change fails, so the switch goes back, and says why")
@mutation(ACCOUNTS, "const ticket = latest.next();", "const ticket = 0;",
          expect=LIST + "a slow first answer that arrives after the second does not bring its rows back")
@pytest.mark.web_unit
def test_the_accounts_view_keeps_its_rules():
    vitest(ACCOUNTS_VIEW_SPEC)


# ---- the one-time key -------------------------------------------------------------------------------------------
@mutation(KEYS, "const forgetSignOut = auth.onSignOut(() => {", "const forgetSignOut = (() => () => {})(() => {",
          expect=ONE_TIME + "is gone on sign-out")
@mutation(KEYS, '@dismiss="rawKey = null"', '@dismiss="rawKey = rawKey"',
          expect=ONE_TIME + "is gone from every place after Dismiss")
@mutation(KEYS, "if (!disposed) rawKey.value = created;", "rawKey.value = created;",
          expect=ONE_TIME + "a create that answers after sign-out does not bring it back")
@mutation(KEYS, "  disposed = true;\n  rawKey.value = null;\n  forgetSignOut();",
          "  disposed = true;\n  forgetSignOut();",
          expect=ONE_TIME + "leaving the view clears the key it held in memory")
@pytest.mark.web_unit
def test_the_raw_key_lives_only_until_dismissed_left_or_signed_out():
    vitest(KEYS_VIEW_SPEC)


# ---- a failed create never shows the server's text --------------------------------------------------------------
@mutation(KEY_MODEL, "text: CREATE_ERRORS[err.code] ?? CREATE_ERROR_DEFAULT", "text: err.message",
          expect=f"FAILED {KEY_SPEC} > createError > is fixed text by error code, never the server's message")
@mutation(KEY_MODEL, 'FIELDS.find((f) => f === head) ?? null', "(head as CreateError[\"field\"]) ?? null",
          expect=f"FAILED {KEY_SPEC} > createError > highlights only a field from the fixed list; wallets.N is the "
                 "wallets field")
@pytest.mark.web_unit
def test_a_failed_create_is_shown_from_fixed_text():
    vitest(KEY_SPEC)


@mutation(KEYS, "if (!disposed) formError.value = createError(err);",
          "if (!disposed) formError.value = { text: (err as Error).message, field: null };",
          expect=f"FAILED {KEYS_VIEW_SPEC} > the one-time key > a failed create shows fixed text and the field, "
                 "never the server's message or param")
@pytest.mark.web_unit
def test_the_keys_view_shows_a_failed_create_from_fixed_text():
    vitest(KEYS_VIEW_SPEC)


# ---- the Alerts generation guard and the acknowledge ------------------------------------------------------------
@mutation(ALERTS, "const ticket = latest.next();", "const ticket = 0;",
          expect=GUARD + "a slow first answer that arrives after the second is dropped")
@mutation(ALERTS, "    if (!latest.isCurrent(ticket)) return;\n    draw(view);", "    draw(view);",
          expect=GUARD + "a slow first answer that arrives after the second is dropped")
@mutation(ALERTS, "      if (!latest.isCurrent(ticket)) return;\n      error.value = errorText(err);",
          "      error.value = errorText(err);",
          expect=GUARD + "a slow first failure that arrives after the second answer shows nothing")
@mutation(ALERTS, "await acknowledgeGroup(g, g.newest_id)", "await acknowledgeGroup(g, 0)",
          expect=ACK + "from the list sends the newest row that was shown, then toasts, reloads and refreshes the "
                       "banner")
@mutation(ALERTS, "await stats.refresh();", "await Promise.resolve();",
          expect=ACK + "from the list sends the newest row that was shown, then toasts, reloads and refreshes the "
                       "banner")
@pytest.mark.web_unit
def test_the_alerts_view_drops_stale_answers_and_acknowledges_what_was_shown():
    vitest(ALERTS_VIEW_SPEC)


# ---- the Alerts filters, the live conditions, the footers and the detail --------------------------------------------
@mutation(ALERTS, "at.status, at.kind", '"open", at.kind', expect=ALERT_FILTERS)
@mutation(ALERTS, "at.status, at.kind", 'at.status, ""', expect=ALERT_FILTERS)
@mutation(ALERTS, "live: stat.alerts", "live: []",
          expect=ALERT_LISTS + "asks for the open groups, 200 of them, with the live conditions beside")
@mutation(ALERTS, "groups.length >= GROUP_LIMIT", "groups.length > GROUP_LIMIT",
          expect=ALERT_LISTS + "says the list is cut at 200 groups, and only then")
@mutation(ALERTS, "groups.length >= GROUP_LIMIT", "groups.length >= 1",
          expect=ALERT_LISTS + "says the list is cut at 200 groups, and only then")
@mutation(ALERT_DETAIL, "detail.items.length >= ROW_LIMIT", "detail.items.length > ROW_LIMIT",
          expect=ALERT_DETAIL_SPEC + "says the detail is cut at 100 rows, and only then")
@mutation(ALERT_DETAIL, "detail.items.length >= ROW_LIMIT", "detail.items.length >= 1",
          expect=ALERT_DETAIL_SPEC + "says the detail is cut at 100 rows, and only then")
@mutation(ALERTS, "const locked = computed(() => writing.value || loading.value > 0);",
          "const locked = computed(() => writing.value);",
          expect=GUARD + "while a view loads, Acknowledge is locked but the filters are not")
@mutation(ALERTS, '<el-button v-if="row.open_count > 0" size="small" :disabled="locked"',
          '<el-button v-if="row.open_count > 0" size="small" :disabled="writing"',
          expect=GUARD + "while a view loads, Acknowledge is locked but the filters are not")
@mutation(ALERTS, 'aria-label="Status" :disabled="writing"', 'aria-label="Status" :disabled="false"',
          expect=IN_FLIGHT)
@mutation(ALERTS, 'aria-label="Kind" :disabled="writing"', 'aria-label="Kind" :disabled="false"', expect=IN_FLIGHT)
@mutation(ALERTS, '<el-button size="small" :disabled="writing" @click="openGroup(row)">',
          '<el-button size="small" :disabled="false" @click="openGroup(row)">', expect=IN_FLIGHT)
@mutation(ALERT_DETAIL, '<el-button :disabled="writing" @click="$emit(\'close\')">',
          '<el-button :disabled="false" @click="$emit(\'close\')">', expect=IN_FLIGHT)
@mutation(ALERT_DETAIL, '<el-button v-if="detail.open_count > 0" :disabled="locked"',
          '<el-button v-if="detail.open_count > 0" :disabled="false"', expect=IN_FLIGHT)
@mutation(ALERTS, "writing.value = true;", "writing.value = false;", expect=IN_FLIGHT)
@mutation(ALERTS, "writing.value = false;", "writing.value = writing.value;", expect=AFTER_FAILURE)
@mutation(ALERTS, "  opened.value = null;\n  void reload();", "  opened.value = null;",
          expect=ALERT_DETAIL_SPEC + "Close reloads the list")
@mutation(ALERTS, "  asked.value = { ...asked.value, open: null };\n  opened.value = null;", "  opened.value = null;",
          expect=ALERT_DETAIL_SPEC + "Close reloads the list")
@mutation(ALERTS, "    asked.value = { ...asked.value, open: null };\n    notify.error(errorText(view.detailError));",
          "    notify.error(errorText(view.detailError));",
          expect=ALERT_DETAIL_SPEC + "a detail that cannot be read is closed with a toast and the list stays whole")
@mutation(ALERTS, "if (failure) notify.error(", "if (false) notify.error(",
          expect=ACK + "an acknowledge whose reload fails says both")
@mutation(ALERTS, "failure = err;", "failure = null;", expect=ACK + "an acknowledge whose reload fails says both")
@pytest.mark.web_unit
def test_the_alerts_view_keeps_its_filters_footers_and_locks():
    vitest(ALERTS_VIEW_SPEC)


# ---- the Alerts group rules ------------------------------------------------------------------------------------
@mutation(ALERTS_MODEL, "g.keys === 1 && !isEmptyWording(g)", "g.keys >= 1 && !isEmptyWording(g)",
          expect=ONE_CUSTOMER + "is for an unclear wording seen under exactly one key")
@mutation(ALERTS_MODEL, "g.keys === 1 && !isEmptyWording(g)", "g.keys !== 2 && !isEmptyWording(g)",
          expect=ONE_CUSTOMER + "is for an unclear wording seen under exactly one key")
@mutation(ALERTS_MODEL, 'return g.kind === "unclear_reply" && g.keys === 1', "return g.keys === 1",
          expect=ONE_CUSTOMER + "is not for a billing group, nor for the read failure")
@mutation(ALERTS_MODEL, "g.keys === 1 && !isEmptyWording(g);", "g.keys === 1;",
          expect=ONE_CUSTOMER + "is not for a billing group, nor for the read failure")
@mutation(ALERTS_MODEL, 'g.kind === "unclear_reply" && g.title === EMPTY_WORDING_TITLE',
          "g.title === EMPTY_WORDING_TITLE",
          expect=EMPTY_WORDING + "is rewritten as an API read failure, only for an unclear reply")
@mutation(ALERTS_MODEL, 'isEmptyWording(g) ? "API read failure: no reply text was read" : g.title', "g.title",
          expect=EMPTY_WORDING + "is rewritten as an API read failure, only for an unclear reply")
@pytest.mark.web_unit
def test_the_alert_group_rules_hold():
    vitest(ALERTS_MODEL_SPEC)


# ---- Accounts: the switch, the reason, the commands and the proxy re-check -----------------------------------------
@mutation(ACCOUNTS, 'v-if="!row.in_use"', 'v-if="true"',
          expect=LIST + "draws every server string as text, and hides the switch of an account in use")
@mutation(ACCOUNTS, '<div v-if="row.state_reason" class="sub">', '<div v-if="false" class="sub">',
          expect=LIST + "draws every server string as text, and hides the switch of an account in use")
@mutation(ACCOUNT_MODEL, 'WAKE_LABEL = "Wake: clear cooldown and strikes"', 'WAKE_LABEL = "Clear cooldown"',
          expect=COMMANDS + "Wake queues the wake command under the label that names the cooldown and the strikes")
@mutation(ACCOUNTS, 'title="Clear the cooldown and the strike count of this account"', 'title="Wake"',
          expect=COMMANDS + "the Wake item says what it clears")
@mutation(ACCOUNTS, 'queue(recheckProxy(a.id), "Proxy check")', 'queue(recheckProxy(a.id), "Proxy")',
          expect=COMMANDS + "Re-check proxy closes the dialog, queues the re-check, polls Proxy check and reads "
                            "the list again")
@mutation(ACCOUNTS, 'if (out.command_id) await poll(out.command_id, "Proxy check");', "",
          expect=COMMANDS + "Save of a changed proxy polls the Proxy check the server queued, then reads the list")
@pytest.mark.web_unit
def test_the_accounts_view_commands_and_proxy_recheck():
    vitest(ACCOUNTS_VIEW_SPEC)


# ---- Keys: the Copy buttons ------------------------------------------------------------------------------------
@mutation(NEW_KEY, '<CopyButton :text="() => rawKey" size="default" :select-from="() => field" />', "",
          expect=COPY + "the one-time callout has a Copy that copies the raw key")
@mutation(KEYS, '<CopyButton :text="row.prefix"', '<CopyButton :text="row.label"',
          expect=COPY + "the prefix in a row has a Copy that copies the prefix and nothing else")
@pytest.mark.web_unit
def test_the_keys_view_copy_buttons_copy_the_right_text():
    vitest(KEYS_VIEW_SPEC)


# ---- Request logs: Clear logs ----------------------------------------------------------------------------------
@mutation(LOGS, "  if (!ok) return;\n  await run(async () => {\n    const out = await clearLogs();",
          "  await run(async () => {\n    const out = await clearLogs();", expect=CLEAR + "a cancel deletes nothing")
@mutation(LOGS, "    offset.value = 0;\n    await load();", "    await load();",
          expect=CLEAR + "asks first, then says how many rows went and goes to the first page")
@pytest.mark.web_unit
def test_clear_logs_asks_first_and_goes_to_the_first_page():
    vitest(LOGS_VIEW_SPEC)


# ---- colour is for exceptions: the left bar and the chips ------------------------------------------------------
BAR = f"FAILED {LOGS_VIEW_SPEC} > the list > a 5xx row has the red left bar, a 4xx row the amber one, a fine row none"
ERROR_CODE = f"FAILED {LOGS_VIEW_SPEC} > the list > the error code is plain muted text: no chip, no status colour"
ENABLED = (f"FAILED {KEYS_VIEW_SPEC} > the list > an enabled key is plain text, a disabled key is the only one with a "
           "chip, and 'any model' is neutral")
GROUP_BAR = (f"FAILED {ALERTS_VIEW_SPEC} > the lists > marks an error group with the red left bar and a warning with "
             "the amber one, an info group with none")
ALL_KINDS = (f"FAILED {ALERTS_VIEW_SPEC} > the filters > the Kind select shows All kinds, not the placeholder, and "
             "choosing All again sends no kind")
AGO = (f"FAILED {ACCOUNT_SPEC} > pointsText, proxyCheck, checkSummary > ago: never, just now, minutes, hours, days; "
       "a future time is just now")
COUNTS = (f"FAILED {ACCOUNTS_VIEW_SPEC} > the list > draws every server string as text, and hides the switch of an "
          "account in use")


@mutation(LOGS, 'return tone === "ok" ? undefined : tone;', "return tone;", expect=BAR)
@mutation(LOGS, 'return tone === "ok" ? undefined : tone;', "return undefined;", expect=BAR)
@mutation(LOGS, '<span v-if="row.error_code" class="mono sub">', '<span v-if="row.error_code" class="mono bad">',
          expect=ERROR_CODE)
@pytest.mark.web_unit
def test_the_logs_view_marks_exceptions_only():
    vitest(LOGS_VIEW_SPEC)


@mutation(KEYS, '<StatusChip v-if="!row.enabled" status="disabled" />', '<StatusChip v-if="true" status="disabled" />',
          expect=ENABLED)
@mutation(KEYS, 'status="any model" tone="neutral"', 'status="any model"', expect=ENABLED)
@pytest.mark.web_unit
def test_the_keys_view_colours_only_the_exception():
    vitest(KEYS_VIEW_SPEC)


@mutation(ALERTS, 'g.severity === "error" ? "bad" : g.severity === "warning" ? "warn" : undefined',
          'g.severity === "error" ? "bad" : undefined', expect=GROUP_BAR)
@mutation(ALERTS, "kind: fromSelect(value)", "kind: value", expect=ALL_KINDS)
@mutation(ALERTS, '<el-option label="All kinds" :value="ALL" />', '<el-option label="All kinds" value="" />',
          expect=ALL_KINDS)
@pytest.mark.web_unit
def test_the_alerts_view_marks_groups_and_shows_all_kinds():
    vitest(ALERTS_VIEW_SPEC)


@mutation(ACCOUNT_MODEL, "if (minutes < 60) return", "if (minutes <= 60) return", expect=AGO)
@mutation(ACCOUNT_MODEL, "if (hours < 48) return", "if (hours < 49) return", expect=AGO)
@pytest.mark.web_unit
def test_the_accounts_checked_stamp_reads_as_a_time_ago():
    vitest(ACCOUNT_SPEC)


@mutation(ACCOUNTS, '<template #cell-videos="{ row }">{{ row.made.video }}</template>',
          '<template #cell-videos="{ row }">{{ row.made.image }}</template>', expect=COUNTS)
@pytest.mark.web_unit
def test_the_accounts_counts_are_two_number_columns():
    vitest(ACCOUNTS_VIEW_SPEC)
