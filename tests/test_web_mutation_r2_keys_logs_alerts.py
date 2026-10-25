"""Mutation markers of the Keys, Request logs and Alerts views: each breaks one rule in the TypeScript source and
demands that the named Vitest test turns red (scripts/mutate.py runs these, no rebuild)."""

import pytest
from webunit import vitest

from relay_core.testing.mutation import mutation

SRC = "apps/admin-web/src"
WINDOW = SRC + "/components/logs/log-window.ts"
KEY_MODEL = SRC + "/components/keys/key-model.ts"
KEYS = SRC + "/views/KeysView.vue"
ALERTS = SRC + "/views/AlertsView.vue"
LOGS = SRC + "/views/LogsView.vue"
NEW_KEY = SRC + "/components/keys/KeySecretModal.vue"
KEY_LIST = SRC + "/components/keys/KeysList.vue"
KEY_FORM = SRC + "/components/keys/KeyFormDialog.vue"
DRAWER = SRC + "/components/shared/DetailDrawer.vue"
LEAVE_GUARD = SRC + "/components/shared/use-leave-guard.ts"
ALERTS_MODEL = SRC + "/components/alerts/alerts-model.ts"
ALERT_DETAIL = SRC + "/components/alerts/AlertDetailPanel.vue"
LOG_FILTERS = SRC + "/components/logs/LogsFilterForm.vue"
LOG_DATES = SRC + "/components/logs/LogsDatesField.vue"
LOG_TABLE = SRC + "/components/logs/LogsTable.vue"
LOG_JSON = SRC + "/components/logs/log-json.ts"
LOG_DRAWER = SRC + "/components/logs/LogDetailDrawer.vue"

WINDOW_SPEC = "tests/unit/logs/log-window.spec.ts"
KEY_SPEC = "tests/unit/keys/key-model.spec.ts"
KEYS_VIEW_SPEC = "tests/unit/keys/keys-view.spec.ts"
ALERTS_VIEW_SPEC = "tests/unit/alerts/alerts-view.spec.ts"
ALERTS_MODEL_SPEC = "tests/unit/alerts/alerts-model.spec.ts"
LOGS_VIEW_SPEC = "tests/unit/logs/logs-view.spec.ts"

SAIGON = f"FAILED {WINDOW_SPEC} > the window in Asia/Saigon (UTC+7, no clock change) > "
NEW_YORK = f"FAILED {WINDOW_SPEC} > the window across a clock change (America/New_York) > "
QUERY = f"FAILED {WINDOW_SPEC} > logQuery > "
WRITTEN = f"FAILED {WINDOW_SPEC} > how a request is written > "
BODY = f"FAILED {KEY_SPEC} > keyBody (the create and edit payload) > "
BALANCE = f"FAILED {KEY_SPEC} > balanceOf > "
KEY_TODAY = f"FAILED {KEY_SPEC} > todayText > "
PLAN = f"FAILED {KEY_SPEC} > planChange (Adjust wallet) > "
CREATED = f"FAILED {KEY_SPEC} > createdText > "
ONE_TIME = f"FAILED {KEYS_VIEW_SPEC} > the one-time key > "
KEYS_LIST = f"FAILED {KEYS_VIEW_SPEC} > the list > "
DESIGN = f"FAILED {KEYS_VIEW_SPEC} > the page as the design draws it > "
GUARDED = f"FAILED {KEYS_VIEW_SPEC} > the key form as a drawer that guards what was typed > "
OPTIONAL = f"FAILED {KEYS_VIEW_SPEC} > the label of a key is optional > "
SHOW = f"FAILED {KEYS_VIEW_SPEC} > the Show chips and the search > "
ADJUST = f"FAILED {KEYS_VIEW_SPEC} > Adjust wallet > "
KEY_NAME = ("FAILED tests/unit/keys/key-model.spec.ts > keyName > is the label without its edges, else the prefix (a "
            "label of spaces is no label)")
DRAWER_SPEC = "tests/unit/shared/detail-drawer.spec.ts"
GUARD_SPEC = "tests/unit/shared/use-leave-guard.spec.ts"
CLOSE_GUARD = f"FAILED {DRAWER_SPEC} > DetailDrawer close guard > "
ASKED = f"FAILED {GUARD_SPEC} > askDiscard > "
LEAVE = f"FAILED {GUARD_SPEC} > useRouteLeaveGuard > "
FORM = f"FAILED {KEYS_VIEW_SPEC} > the key form as the design draws it > "
KEYS_SMALL = f"FAILED {KEYS_VIEW_SPEC} > the tablet and the phone > "
GUARD = f"FAILED {ALERTS_VIEW_SPEC} > the generation guard > "
ACK = f"FAILED {ALERTS_VIEW_SPEC} > acknowledge > "
COPY = f"FAILED {KEYS_VIEW_SPEC} > Copy > "
CLEAR = f"FAILED {LOGS_VIEW_SPEC} > clear logs > "
LOGS_LIST = f"FAILED {LOGS_VIEW_SPEC} > the list > "
LOGS_PAGING = f"FAILED {LOGS_VIEW_SPEC} > paging > "
LOGS_PAGE = LOGS_PAGING
LOGS_PHONE = f"FAILED {LOGS_VIEW_SPEC} > the phone > "
LOGS_DESKTOP_TYPING = f"FAILED {LOGS_VIEW_SPEC} > typing on a desktop > "
LOGS_DRAWER = f"FAILED {LOGS_VIEW_SPEC} > the request drawer, in detail > "
LOGS_DETAIL = f"FAILED {LOGS_VIEW_SPEC} > the detail > "
ALERT_LISTS = f"FAILED {ALERTS_VIEW_SPEC} > the lists > "
ALERT_FILTERS = (f"FAILED {ALERTS_VIEW_SPEC} > the filters > send the Status and then the Kind to the server, each "
                 "in its own place")
ALERT_DETAIL_SPEC = f"FAILED {ALERTS_VIEW_SPEC} > the detail > "
ALERT_DESIGN = f"FAILED {ALERTS_VIEW_SPEC} > the detail as the design draws it > "
IN_FLIGHT = (f"FAILED {ALERTS_VIEW_SPEC} > acknowledge > while an acknowledge is in flight, the selects, the titles "
             "and both Acknowledge buttons are off, and X and Escape are inert")
UNREADABLE = (ALERT_DETAIL_SPEC + "a detail that cannot be read keeps the drawer open with the error inside, and the "
              "list stays whole")
AFTER_FAILURE = f"FAILED {ALERTS_VIEW_SPEC} > acknowledge > after a failed acknowledge every control is on again"
ONE_CUSTOMER = f"FAILED {ALERTS_MODEL_SPEC} > oneCustomer > "
EMPTY_WORDING = f"FAILED {ALERTS_MODEL_SPEC} > the empty-wording group > "
OUTCOMES = f"FAILED {ALERTS_MODEL_SPEC} > outcomesText > "


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


# ---- how a request is written -----------------------------------------------------------------------------------
CLOCK = WRITTEN + "the clock has two digits for each part, in local time; a missing time is a dash"
DURATION = WRITTEN + "a duration is in ms, and from one second in seconds with two decimals, the slow ones marked"
ROUTE = WRITTEN + "a route splits into its method and its path; one with no method is all path"
STATUS = WRITTEN + "a status is ok below 400, a warning from 400, bad from 500"
ERROR_TONE = WRITTEN + "an error code takes its status's family, and is red on a request that went through"


@mutation(WINDOW, "export const PAGE_SIZE = 50;",
          "export const PAGE_SIZE = 10;", expect=WRITTEN + "a page is 50 requests")
@mutation(WINDOW, '(code >= 500 ? "bad" : code >= 400 ? "warn" : "ok")',
          '(code > 500 ? "bad" : code >= 400 ? "warn" : "ok")', expect=STATUS)
@mutation(WINDOW, 'code >= 400 ? "warn" : "ok")', 'code > 400 ? "warn" : "ok")', expect=STATUS)
@mutation(WINDOW, '(code >= 400 ? (statusTone(code) as "warn" | "bad") : "bad")',
          '(code >= 500 ? (statusTone(code) as "warn" | "bad") : "bad")', expect=ERROR_TONE)
@mutation(WINDOW, 'n.toLocaleString("en-US")', "String(n)", expect=WRITTEN + "a number has a thousands comma")
@mutation(WINDOW, ":${two(d.getSeconds())}", "", expect=CLOCK)
@mutation(WINDOW, "${d.getDate()} ${MONTHS[d.getMonth()]}", "${d.getDate()} ${MONTHS[d.getMonth() + 1]}", expect=CLOCK)
@mutation(WINDOW, 'padStart(3, "0")}` : "";', 'padStart(2, "0")}` : "";', expect=CLOCK)
@mutation(WINDOW, "(ms / 1000).toFixed(2)", "(ms / 1000).toFixed(1)", expect=DURATION)
@mutation(WINDOW, "  ms >= 1000 ? `${(ms / 1000)", "  ms > 1000 ? `${(ms / 1000)", expect=DURATION)
@mutation(WINDOW, "isSlow = (ms: number): boolean => ms >= 1000;", "isSlow = (ms: number): boolean => ms > 1000;",
          expect=DURATION)
@mutation(WINDOW, "path: route.slice(at + 1)", "path: route.slice(at)", expect=ROUTE)
@mutation(WINDOW, '{ value: "4xx", label: "4xx" }', '{ value: "4xx", label: "4XX" }',
          expect=WRITTEN + "the Status chips offer the canvas words for the status families")
@pytest.mark.web_unit
def test_a_request_is_written_as_the_design_writes_it():
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


# ---- the balance of a key, its line of today and the created date --------------------------------------------------
@mutation(KEY_MODEL, 'left === 0 ? "bad" : left < limit * LOW_SHARE ? "warn" : "ok"',
          'left === 0 ? "bad" : left <= limit * LOW_SHARE ? "warn" : "ok"',
          expect=BALANCE + "is the total less what was used in total, red at 0 and amber under a fifth of the total")
@mutation(KEY_MODEL, "const left = Math.max(0, limit - (byTotal ? w.used_total : w.used_day));",
          "const left = limit - (byTotal ? w.used_total : w.used_day);",
          expect=BALANCE + "is the total less what was used in total, red at 0 and amber under a fifth of the total")
@mutation(KEY_MODEL, "const limit = (byTotal ? w.total_limit : w.day_limit) ?? 0;", "const limit = w.total_limit ?? 0;",
          expect=BALANCE + "a day limit alone is what is left of today's")
@mutation(KEY_MODEL, "? `−${n(w.used_day)} of ${n(w.day_limit)} today`", "? `−${n(w.used_day)} today`",
          expect=KEY_TODAY + "says what was spent today, against the day limit when there is one")
@mutation(KEY_MODEL, "d.getFullYear() === new Date(now).getFullYear() ? date :", "true ? date :",
          expect=CREATED + "a key of an earlier year adds the year")
@mutation(KEY_MODEL, "const date = `${d.getDate()} ${MONTHS[d.getMonth()]}`;",
          "const date = `${d.getDate()} ${MONTHS[d.getMonth() + 1]}`;",
          expect=CREATED + "a key of this year shows the day and the month, as KeyForm writes it")
@pytest.mark.web_unit
def test_the_balance_the_line_of_today_and_the_created_date_are_written_right():
    vitest(KEY_SPEC)


LAST_USED = f"FAILED {KEY_SPEC} > lastUsedText > writes the last call as the list does, never for none"
LAST_USED_ROW = KEYS_LIST + "Last used is the key's last call with its address under it, and never for a key never used"


@mutation(KEY_MODEL, 'if (!ms) return "never";', 'if (!ms) return "-";', expect=LAST_USED)
@mutation(KEY_MODEL, 'return days === 1 ? "yesterday" : `${days} days ago`;', "return `${days} days ago`;",
          expect=LAST_USED)
@mutation(KEY_MODEL, "if (minutes < 60) return `${minutes} min ago`;", "", expect=LAST_USED)
@mutation(KEY_LIST, '<div v-if="k.last_used_ip" class="meta mono">{{ k.last_used_ip }}</div>', "",
          expect=LAST_USED_ROW)
@pytest.mark.web_unit
def test_last_used_is_written_as_the_design_does_with_the_address_under_it():
    vitest(KEY_SPEC, KEYS_VIEW_SPEC)


# ---- the order of the wallets -----------------------------------------------------------------------------------
ORDER = f"FAILED {KEY_SPEC} > the order of the wallets > "


@mutation(KEY_MODEL, "if (a.scope === POINTS) return -1;", "if (a.scope === POINTS) return 1;",
          expect=ORDER + "the points wallet is first whichever side of the comparison it is on")
@mutation(KEY_MODEL, "if (b.scope === POINTS) return 1;", "if (b.scope === POINTS) return -1;",
          expect=ORDER + "the points wallet is first whichever side of the comparison it is on")
@mutation(KEY_MODEL, "return a.scope < b.scope ? -1 : 1;", "return a.scope < b.scope ? 1 : -1;",
          expect=ORDER + "the points wallet comes first, then the providers' caps by name")
@mutation(KEY_MODEL, "sortedWallets([POINTS, ...new Set(models.map((m) => m.owned_by))]",
          "sortedWallets([...new Set(models.map((m) => m.owned_by))]",
          expect=f"FAILED {KEY_SPEC} > walletScopes and keyForm > the points wallet, then one cap per distinct "
                 "provider, sorted")
@pytest.mark.web_unit
def test_the_points_wallet_is_listed_first_then_the_caps_by_name():
    vitest(KEY_SPEC)


# ---- a failed create never shows the server's text --------------------------------------------------------------
@mutation(KEY_MODEL, "text: CREATE_ERRORS[err.code] ?? CREATE_ERROR_DEFAULT", "text: err.message",
          expect=f"FAILED {KEY_SPEC} > createError > is fixed text by error code, never the server's message")
@mutation(KEY_MODEL, 'FIELDS.find((f) => f === head) ?? null', "(head as CreateError[\"field\"]) ?? null",
          expect=f"FAILED {KEY_SPEC} > createError > highlights only a field from the fixed list; wallets.N is the "
                 "wallets field")
@pytest.mark.web_unit
def test_a_failed_create_is_shown_from_fixed_text():
    vitest(KEY_SPEC)


# ---- the one-time key -------------------------------------------------------------------------------------------
@mutation(KEYS, "const forgetSignOut = auth.onSignOut(() => {", "const forgetSignOut = (() => () => {})(() => {",
          expect=ONE_TIME + "is gone on sign-out")
@mutation(KEYS, "      if (!disposed) {\n        rawKey.value = created.key;",
          "      {\n        rawKey.value = created.key;",
          expect=ONE_TIME + "a create that answers after sign-out does not bring it back")
@mutation(KEYS, "  disposed = true;\n  rawKey.value = null;\n  forgetSignOut();",
          "  disposed = true;\n  forgetSignOut();",
          expect=ONE_TIME + "leaving the view clears the key it held in memory")
@pytest.mark.web_unit
def test_the_raw_key_lives_only_until_dismissed_left_or_signed_out():
    vitest(KEYS_VIEW_SPEC)


@mutation(KEYS, "const shown = createError(err);",
          "const shown: CreateError = { text: (err as Error).message, field: null };",
          expect=f"FAILED {KEYS_VIEW_SPEC} > the one-time key > a failed create shows fixed text and the field, "
                 "never the server's message or param")
@pytest.mark.web_unit
def test_the_keys_view_shows_a_failed_create_from_fixed_text():
    vitest(KEYS_VIEW_SPEC)


# ---- Keys: the list, the callout, the switch, the bars, the form ------------------------------------------------
@mutation(KEYS, "createdLabel.value = keyName(created);", "createdLabel.value = body.label;",
          expect=OPTIONAL + "the modal of a key made with no label names it by its prefix, never 'Key created for '")
@mutation(KEY_LIST, """<tr
            v-for="k in rows"
            :key="k.id"
            :class="{ 'is-new': k.id === createdId,""", """<tr
            v-for="k in rows"
            :key="k.id"
            :class="{ 'is-new': k.id !== createdId,""",
          expect=DESIGN + "the modal names the new key, and its row is the one tinted")
@mutation(KEYS, "title: `Delete key ${keyName(k)}?`,", 'title: "Delete key",',
          expect=KEYS_LIST + "delete says whether the key went or was only disabled")
@mutation(KEY_MODEL, 'const rule = "A key that has jobs is only disabled.";', 'const rule = "Delete it.";',
          expect=KEYS_LIST + "delete says whether the key went or was only disabled")
@mutation(KEYS, "await setKeyEnabled(k.id, enabled);", "await setKeyEnabled(k.id, !enabled);",
          expect=KEYS_LIST + "a switch that fails reads the list again, so it goes back")
@mutation(KEY_LIST, '(enabled ? "Disable key" : "Enable key")', '(enabled ? "Enable key" : "Disable key")',
          expect=KEYS_LIST + "the row menu says what the switch does, as the server holds it")
@pytest.mark.web_unit
def test_the_keys_view_draws_the_modal_the_row_menu_and_the_delete_confirm():
    vitest(KEYS_VIEW_SPEC)


@mutation(KEYS, '{{ isTablet ? "New key" : "Create key" }}', '{{ "Create key" }}',
          expect=KEYS_SMALL + "a tablet keeps the desktop's columns in a table that scrolls sideways, and New key at "
                              "the head")
@mutation(KEY_LIST, "v-else-if=\"keyState(k) === 'empty' || keyState(k) === 'closed'\"", 'v-else-if="false"',
          expect=KEYS_SMALL + "a phone lists each key as a block with its pill, balance, caps and row buttons")
@pytest.mark.web_unit
def test_the_keys_view_folds_for_a_tablet_and_a_phone():
    vitest(KEYS_VIEW_SPEC)


@mutation(KEY_FORM, "`Edit key · ${keyName(props.editing)}`", '"Edit key"',
          expect=FORM + "an edit names the key in its title and tells the balance and what each cap has used")
@mutation(KEY_MODEL, "`total ${n(w.used_total)} / ${n(w.total_limit)}`", "`total ${n(w.total_limit)}`",
          expect=FORM + "an edit names the key in its title and tells the balance and what each cap has used")
@mutation(KEY_FORM, ':disabled="form.any || (m.disabled === true && !picked(m.id))"',
          ':disabled="form.any || m.disabled === true"',
          expect=FORM + "the models are grouped by provider in the drawn order; a paused one can come off a key "
                        "but not go on")
@mutation(KEY_MODEL, "  count += Number(models(a) !== models(b));\n", "",
          expect=FORM + "the footer counts what changed")
@pytest.mark.web_unit
def test_the_key_form_shows_what_the_design_shows():
    vitest(KEYS_VIEW_SPEC)


# ---- Keys: the Show chips, the search, an empty result and a failed list ------------------------------------------
@mutation(KEYS, "const filtered = computed(() => items.value.length > 0);", "const filtered = computed(() => false);",
          expect=SHOW + "the search finds a label or a prefix, and an empty result offers Clear filters")
@mutation(KEYS, "items.value.filter((k) => inFilter(k, show.value) && inSearch(k, q.value))",
          "items.value.filter((k) => inSearch(k, q.value))",
          expect=SHOW + "each chip counts its keys and shows only them")
@mutation(KEYS, "error.value = loadFailedText(err, errorText(err));", "error.value = errorText(err);",
          expect=DESIGN + "a list that fails to load says why in the canvas's words")
@pytest.mark.web_unit
def test_the_keys_page_names_and_folds_its_parts_as_drawn():
    vitest(KEYS_VIEW_SPEC)


@mutation(KEY_MODEL, "? `the relay answered ${err.status} (${said})`", "? said",
          expect=f"FAILED {KEY_SPEC} > loadFailedText > words a failed list as the canvas does, with the relay's "
                 "status when it answered")
@pytest.mark.web_unit
def test_a_failed_keys_list_is_worded_as_drawn():
    vitest(KEY_SPEC)


# ---- Keys: the label is optional; a key without one is named by its prefix ----------------------------------------
@mutation(KEY_FORM, 'function send(): void {\n  emit("submit", keyBody(form));',
          'function send(): void {\n  if (!props.editing && form.label.trim() === "") return;\n'
          '  emit("submit", keyBody(form));',
          expect=OPTIONAL + "Create key is on with an empty label, says nothing of a missing label and creates a key")
@mutation(KEY_MODEL, "label: form.label.trim(),", "label: form.label,",
          expect=OPTIONAL + "a label of spaces is sent as no label, and a padded one without its edges")
@mutation(KEY_MODEL, '(k: Pick<ApiKey, "label" | "prefix">): string => k.label.trim() || k.prefix;',
          '(k: Pick<ApiKey, "label" | "prefix">): string => k.label || k.prefix;',
          expect=KEY_NAME)
@pytest.mark.web_unit
def test_the_label_of_a_key_is_optional_and_a_key_without_one_is_named_by_its_prefix():
    vitest(KEYS_VIEW_SPEC, KEY_SPEC)


# ---- Keys: the form is a drawer that guards what was typed -------------------------------------------------------
@mutation(KEY_FORM, "    baseline.value = JSON.stringify(form);\n  },", '    baseline.value = "";\n  },',
          expect=GUARDED + "an edit is dirty once a field changes, not before")
@mutation(KEYS, "  () => formOpen.value && formDirty.value,\n", "  () => false,\n",
          expect=GUARDED + "a reload or a closed tab is warned about while the form holds typing, and not after")
@mutation(KEYS, "        else if (!signedOut) notify.error(shown.text);\n", "",
          expect=GUARDED + "a create that fails after the form is gone is a toast, not a lost error")
@mutation(KEYS, "  if (opener?.isConnected && opener !== document.body) return;\n", "  return;\n",
          expect=GUARDED + "focus goes to the page's primary button when the button that opened the form is gone")
@mutation(KEYS, "  () => creating.value,\n", "  () => false,\n",
          expect=GUARDED + "while a create is in flight a reload or a closed tab is warned about, though nothing was "
                           "typed")
@mutation(KEYS, "    secret.value?.reveal();\n", "",
          expect=GUARDED + "a good save puts focus in the modal's field once the drawer is gone")
@pytest.mark.web_unit
def test_the_key_form_drawer_guards_what_was_typed_and_says_so_when_a_create_fails():
    vitest(KEYS_VIEW_SPEC)


@mutation(DRAWER, "  if (props.busy || asking) return;\n  if (props.dirty) {",
          "  if (asking) return;\n  if (props.dirty) {",
          expect=CLOSE_GUARD + "a busy drawer ignores X, Escape, the footer's close and the scrim, clean or dirty")
@mutation(DRAWER, "  if (props.busy || asking) return;", "  if (props.busy) return;",
          expect=CLOSE_GUARD + "Escape, the X and the footer's close all ask, and three of them while the box is up "
                               "make one box")
@mutation(DRAWER, "  if (props.dirty) {\n    asking = true;", "  if (false) {\n    asking = true;",
          expect=CLOSE_GUARD + "a dirty drawer asks once with its own wording, and closes only on Discard")
@mutation(DRAWER, "      if (!(await askDiscard(props.discardText))) return;",
          "      await askDiscard(props.discardText);",
          expect=CLOSE_GUARD + "a dirty drawer asks once with its own wording, and closes only on Discard")
@mutation(DRAWER, "    if (!open) cancelPending();", "    if (!open) return;",
          expect=CLOSE_GUARD + "when the parent closes the drawer under a pending box, the box goes as keep and "
                               "nothing is emitted")
@mutation(DRAWER, "    closeOnScrim: true,\n", "    closeOnScrim: false,\n",
          expect=CLOSE_GUARD + "a scrim click is Escape: a clean drawer closes, a dirty one asks, Keep editing keeps "
                               "and Discard closes")
@pytest.mark.web_unit
def test_the_drawer_asks_before_it_loses_typing_and_does_nothing_while_a_write_runs():
    vitest(DRAWER_SPEC)


@mutation(LEAVE_GUARD, '    if (to.name === "login") {', '    if (false) {',
          expect=LEAVE + "a leave to the login page is never refused, blocked or dirty, and asks nothing")
@mutation(LEAVE_GUARD, "      cancelPending();\n      return true;", "      return true;",
          expect=LEAVE + "a leave to the login page closes a discard question that is still up, as keep")
@mutation(LEAVE_GUARD, "    if (isBlocked()) return false;\n", "",
          expect=LEAVE + "while a write is in flight the page is not left, with no question, clean or dirty")
@mutation(LEAVE_GUARD, "    () => isDirty() || isBlocked(),", "    () => isDirty(),",
          expect=LEAVE + "a reload is warned about while a write is in flight, though nothing is dirty")
@mutation(LEAVE_GUARD, "  if (pending) return pending;\n", "",
          expect=ASKED + "while a box is up every further ask is the same promise and no second box opens")
@mutation(LEAVE_GUARD, "  keep?.();\n", "",
          expect=ASKED + "cancelPending closes the box as keep even though the box's own promise never settles")
@mutation(LEAVE_GUARD, "  ElMessageBox.close();\n", "",
          expect=ASKED + "cancelPending closes the box as keep even though the box's own promise never settles")
@mutation(LEAVE_GUARD, "    return !isDirty() || (await askDiscard());", "    return true;",
          expect=LEAVE + "a dirty form asks; keep stays, discard leaves")
@mutation(LEAVE_GUARD, "      if (dirty) window.addEventListener", "      if (false) window.addEventListener",
          expect=LEAVE + "a reload or a closed tab is warned about while dirty, and only then")
@mutation(LEAVE_GUARD, "    window.removeEventListener(\"beforeunload\", warn);\n  });",
          "  });", expect=LEAVE + "a reload or a closed tab is warned about while dirty, and only then")
@pytest.mark.web_unit
def test_one_discard_question_at_a_time_and_a_leave_guard_while_dirty():
    vitest(GUARD_SPEC)


# ---- Keys: Adjust wallet changes the points wallet's total, never past its balance ---------------------------------
@mutation(KEY_MODEL, 'if (change === "deduct" && amount > start)', "if (false)",
          expect=PLAN + "never deducts more than the balance, and wants a whole amount of 1 or more")
@mutation(KEY_MODEL, 'const total = change === "top" ? base + amount : base - amount;',
          "const total = base + amount;",
          expect=PLAN + "a top-up raises the total, a deduction lowers it, the day limit kept")
@mutation(KEY_MODEL, "  if (day !== null) body.day_limit = day;\n", "",
          expect=PLAN + "a top-up raises the total, a deduction lowers it, the day limit kept")
@pytest.mark.web_unit
def test_a_wallet_change_moves_the_total_and_never_deducts_past_the_balance():
    vitest(KEY_SPEC)


@mutation(KEYS, "await patchKey(k.id, { wallets: [body] });", "await patchKey(k.id, { wallets: [] });",
          expect=ADJUST + "the wallet figure opens it from its row; a top-up sends the points wallet with a higher "
                          "total")
@mutation(KEYS, "      walletKey.value = null;\n      notify.success", "      notify.success",
          expect=ADJUST + "the wallet figure opens it from its row; a top-up sends the points wallet with a higher "
                          "total")
@pytest.mark.web_unit
def test_adjust_wallet_sends_the_points_wallet_and_closes():
    vitest(KEYS_VIEW_SPEC)


# ---- Keys, page-keys round: a wallet's Mode is the design's select (FilterSelect's field) in the wallet's group ----


SHARED_SELECT = SRC + "/components/shared/FilterSelect.vue"
MODE_NAMED = FORM + "each wallet's Mode is named after its wallet and keeps the visible word"


@mutation(SHARED_SELECT, '"aria-label": props.ariaLabel,', '"aria-label": props.label ? undefined : props.ariaLabel,',
          expect=MODE_NAMED)
@pytest.mark.web_unit
def test_each_wallet_mode_is_named_after_its_wallet():
    vitest(KEYS_VIEW_SPEC)


# ---- Keys: the Copy buttons ------------------------------------------------------------------------------------
@mutation(NEW_KEY, "copyText(props.rawKey, () => field.value, abort.signal)",
          'copyText("", () => field.value, abort.signal)',
          expect=COPY + "the one-time modal has a Copy that copies the raw key")
@mutation(NEW_KEY, "copied.value = true;", "copied.value = false;",
          expect=COPY + "the Copy of the modal says Copied afterwards")
@mutation(NEW_KEY, '  unlocked.value = true;\n  if (outcome === "select")', '  if (outcome === "select")',
          expect=ONE_TIME + "Done is locked until the key is copied, then removes it from every place")
@mutation(NEW_KEY, ':locked="!unlocked"', ':locked="false"',
          expect=ONE_TIME + "Escape and a click beside it do nothing until the key is copied, and are ways out after")
@pytest.mark.web_unit
def test_the_keys_view_copy_buttons_copy_the_right_text():
    vitest(KEYS_VIEW_SPEC)


# ---- Request logs: Clear logs ----------------------------------------------------------------------------------
@mutation(LOGS, "  if (!ok) return;\n  await run(async () => {\n    const out = await clearLogs();",
          "  await run(async () => {\n    const out = await clearLogs();", expect=CLEAR + "a cancel deletes nothing")
@mutation(LOGS, "    offset.value = 0;\n    await load();", "    await load();",
          expect=CLEAR + "is an icon button that asks first, then says how many rows went and goes to the first page")
@mutation(LOGS, "    danger: true,\n", "    danger: false,\n",
          expect=CLEAR + "is an icon button that asks first, then says how many rows went and goes to the first page")
@pytest.mark.web_unit
def test_clear_logs_asks_first():
    vitest(LOGS_VIEW_SPEC)


# ---- Request logs: the date range, the page size and the filters ------------------------------------------------
FILTERS = f"FAILED {LOGS_VIEW_SPEC} > filters > "
RANGE = FILTERS + "a range sends the half-open window of the viewer's own midnights, and searches at once"
CLEARED = (FILTERS + "emptying both inputs and leaving them clears the range: no window, and the store holds empty "
           "texts")
QUICK_ORDER = FILTERS + "the quick options are listed in the order Today, Yesterday, Last 7 days, Last 30 days"
OLDER = LOGS_PAGE + "Older goes on by a page, Newer goes back, Newer is off on the first page and Older on the last"


ONE_ENDED_FROM = FILTERS + "a saved range with only From is cleared at both ends before the first search"
ONE_ENDED_TO = FILTERS + "a saved range with only To is cleared at both ends before the first search"
LOGS_FILTERS_STORE = SRC + "/components/logs/logs-filters.ts"
QUICK = FILTERS + "the quick option "
TODAY = QUICK + "Today searches from 2026-03-01 to 2026-03-01"
YESTERDAY = QUICK + "Yesterday searches from 2026-02-28 to 2026-02-28"
LAST_7 = QUICK + "Last 7 days searches from 2026-02-23 to 2026-03-01"
LAST_30 = QUICK + "Last 30 days searches from 2026-01-31 to 2026-03-01"


@mutation(LOGS, "filters.keepRangeWhole();\n", "", expect=ONE_ENDED_FROM)
@mutation(LOGS_FILTERS_STORE, '      to.value = "";\n', "", expect=ONE_ENDED_TO)
@mutation(LOGS_FILTERS_STORE, "if (!from.value || !to.value) {", "if (!from.value && !to.value) {",
          expect=ONE_ENDED_FROM)
@mutation(LOG_DATES, 'filters.from = v?.[0] ?? "";', 'filters.from = v?.[1] ?? "";', expect=RANGE)
@mutation(LOG_DATES, 'filters.to = v?.[1] ?? "";', "filters.to = v?.[1] as string;", expect=CLEARED)
@mutation(LOG_DATES, 'setAttribute("aria-label", "From");', 'setAttribute("aria-label", "X");', expect=RANGE)
@mutation(WINDOW, "[daysAgo(0), daysAgo(0)] },", "[daysAgo(0), daysAgo(1)] },",
          expect=TODAY)
@mutation(WINDOW, "[daysAgo(1), daysAgo(1)] },", "[daysAgo(2), daysAgo(1)] },",
          expect=YESTERDAY)
@mutation(WINDOW, "[daysAgo(6), daysAgo(0)] },", "[daysAgo(7), daysAgo(0)] },",
          expect=LAST_7)
@mutation(WINDOW, "[daysAgo(29), daysAgo(0)] },", "[daysAgo(30), daysAgo(0)] },",
          expect=LAST_30)
@mutation(WINDOW, "now.getDate() - n)", "now.getDate() + n)",
          expect=YESTERDAY)
@mutation(WINDOW, '{ text: "Today",', '{ text: "Todai",', expect=QUICK_ORDER)
@mutation(LOGS, "scope.provider, offset.value, limit.value)", "scope.provider, 0, limit.value)", expect=OLDER)
@mutation(LOG_FILTERS, "if (!compact.value) return;", "",
          expect=LOGS_DESKTOP_TYPING + "does not search by itself: Enter does")
@mutation(LOG_FILTERS, "const TYPING_PAUSE_MS = 350;", "const TYPING_PAUSE_MS = 3500;",
          expect=LOGS_PHONE + "wraps its five status chips on one set and has no Search button: a choice searches at "
                              "once, typing after a pause")
@pytest.mark.web_unit
def test_the_logs_range_fields_reach_the_query():
    vitest(LOGS_VIEW_SPEC, "tests/unit/no-native-date.spec.ts")


# ---- Request logs: the paging and the job link ---------------------------------------------------------------------
PHONE_MORE = f"FAILED {LOGS_VIEW_SPEC} > the phone > "


@mutation(LOGS, "  detailOpen.value = false;\n  ui.openJob(id);", "  ui.openJob(id);",
          expect=LOGS_DETAIL + "the job link closes the drawer and opens the job drawer")
@mutation(LOGS, "items.value = more ? [...items.value, ...out.items] : out.items;", "items.value = out.items;",
          expect=PHONE_MORE + "Load older adds the next page under the rows it has, and goes once every row is there")
@mutation(LOGS, "  offset.value += limit.value;\n  void load(true);", "  offset.value += limit.value;\n  void load();",
          expect=PHONE_MORE + "Load older adds the next page under the rows it has, and goes once every row is there")
@mutation(LOGS, "offset.value = Math.max(0, offset.value - limit.value);", "offset.value = 0;", expect=OLDER)
@mutation(LOGS, ':disabled="last >= total || loading"', ':disabled="loading"', expect=OLDER)
@mutation(LOGS, ':disabled="offset === 0 || loading"', ':disabled="loading"', expect=OLDER)
@pytest.mark.web_unit
def test_the_logs_view_pages_and_hands_a_job_to_its_drawer():
    vitest(LOGS_VIEW_SPEC)


@mutation(LOG_DRAWER, 'if (typeof value === "string") return value;', "",
          expect=LOGS_DRAWER + "a body that is already text is shown as it is, not quoted")
@mutation(LOG_DRAWER, 'which === "request" ? requestText.value : (answerText.value ?? "")',
          'which === "request" ? (answerText.value ?? "") : requestText.value',
          expect=LOGS_DRAWER + "each Copy copies its own body and says so")
@mutation(LOG_DRAWER, "copied.value = which;", "copied.value = null;",
          expect=LOGS_DRAWER + "each Copy copies its own body and says so")
@mutation(LOG_DRAWER, 'notify.success(`Copied the ${which === "request" ? "request" : "answer"} body`);',
          'notify.success("Copied");', expect=LOGS_DRAWER + "each Copy copies its own body and says so")
@mutation(LOG_DRAWER, "prompt.value || props.log?.job_title ||", "props.log?.job_title || prompt.value ||",
          expect=LOGS_DETAIL + "shows the request and the answer as text, titled with the prompt")
@mutation(LOG_JSON, 'else if (m[4]) parts.push({ text: m[4], kind: "lit" });',
          'else if (m[4]) parts.push({ text: m[4], kind: "str" });',
          expect=LOGS_DETAIL + "colours the JSON of a body: keys, strings, numbers and literals each in "
          "their own class")
@mutation(LOG_JSON, 'if (m[1] && m[2]) parts.push({ text: m[1], kind: "key" }, { text: m[2], kind: "pun" });',
          'if (m[1] && m[2]) parts.push({ text: m[1], kind: "str" }, { text: m[2], kind: "pun" });',
          expect=LOGS_DETAIL + "colours the JSON of a body: keys, strings, numbers and literals each in "
          "their own class")
@pytest.mark.web_unit
def test_the_request_drawer_shows_its_bodies_and_facts_as_text():
    vitest(LOGS_VIEW_SPEC)


# ---- the Alerts generation guard and the acknowledge ------------------------------------------------------------
@mutation(ALERTS, "const ticket = latest.next();", "const ticket = 0;",
          expect=GUARD + "a slow first answer that arrives after the second is dropped")
@mutation(ALERTS, "    if (!latest.isCurrent(ticket)) return;\n    draw(view, at);", "    draw(view, at);",
          expect=GUARD + "a slow first answer that arrives after the second is dropped")
@mutation(ALERTS, "      if (!latest.isCurrent(ticket)) return;\n"
                  "      error.value = loadFailedText(err, errorText(err));",
          "      error.value = loadFailedText(err, errorText(err));",
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
@mutation(ALERTS, 'status: "open", kind: "", open: null', 'status: "all", kind: "", open: null',
          expect=ALERT_LISTS + "asks for the open groups, 200 of them, with the live conditions beside")
@mutation(ALERTS, "live: stat.alerts", "live: []",
          expect=ALERT_LISTS + "asks for the open groups, 200 of them, with the live conditions beside")
@mutation(ALERTS, "const locked = computed(() => writing.value || loading.value > 0);",
          "const locked = computed(() => writing.value);",
          expect=GUARD + "while a view loads, Acknowledge is locked but the filters are not")
@mutation(ALERTS, "writing.value = true;", "writing.value = false;", expect=IN_FLIGHT)
@mutation(ALERTS, "writing.value = false;", "writing.value = writing.value;", expect=AFTER_FAILURE)
@mutation(ALERTS, "  opened.value = null;\n  void reload();", "  opened.value = null;",
          expect=ALERT_DETAIL_SPEC + "Close reloads the list")
@mutation(ALERTS, "  opened.value = null;\n  void reload();", "  opened.value = null;\n  detailError.value = null;\n"
                                                                 "  void reload();",
          expect=ALERT_DETAIL_SPEC + "a failed read stays in the drawer while it leaves, and a reopened group starts "
                                     "from a clean drawer")
@mutation(ALERTS, "      if (at.open && !shownDetail.value) detailError.value = err;\n", "",
          expect=ALERT_DETAIL_SPEC + "a list read that fails while the group is asked for says so in the drawer, not "
                                     "Loading for ever")
@mutation(ALERTS, "  if (at.open) detailError.value = view.detailError;", "  detailError.value = view.detailError;",
          expect=ALERT_DETAIL_SPEC + "a failed read stays in the drawer while it leaves, and a reopened group starts "
                                     "from a clean drawer")
@mutation(ALERTS, "  if (writing.value) return;\n  pendingJob = jobId;", "  pendingJob = jobId;",
          expect=ALERT_DETAIL_SPEC + "a job link does nothing while an acknowledge runs: the drawer stays")
@mutation(ALERTS, "  shownDetail.value = null;\n  detailError.value = null;\n  pendingJob = null;",
          "  detailError.value = null;\n  pendingJob = null;",
          expect=ALERT_DETAIL_SPEC + "opening another group at once does not show the last group's title while it "
                                     "reads")
@mutation(ALERTS, "  pendingJob = null;\n  asked.value", "  asked.value",
          expect=ALERT_DETAIL_SPEC + "a job asked for and then a group opened again before the drawer left opens "
                                     "no job")
@mutation(ALERTS, '  document.querySelector<HTMLElement>(".app-scroll__wrap")?.focus({ preventScroll: true });', "",
          expect=ALERT_DETAIL_SPEC + "when the group leaves the list and the drawer is closed, focus goes to the "
                                     "page's scroll area")
@mutation(ALERTS, "  asked.value = { ...asked.value, open: null };\n  opened.value = null;", "  opened.value = null;",
          expect=ALERT_DETAIL_SPEC + "Close reloads the list")
@mutation(ALERTS, "if (at.open) detailError.value = view.detailError;", "if (at.open) detailError.value = null;",
          expect=UNREADABLE)
@mutation(ALERTS, "if (d) shownDetail.value = d;", "shownDetail.value = d;",
          expect=ALERT_DETAIL_SPEC + "closing keeps the last detail until the drawer is gone")
@mutation(ALERTS, "  pendingJob = jobId;\n  closeDetail();", "  ui.openJob(jobId);\n  closeDetail();",
          expect=ALERT_DETAIL_SPEC + "opening a job closes this drawer first and opens the job once it is gone")
@mutation(ALERTS, "    ui.openJob(id);\n    return;", "    return;",
          expect=ALERT_DETAIL_SPEC + "opening a job closes this drawer first and opens the job once it is gone")
@mutation(ALERTS, '?.closest("button, a")) return;', '?.closest("button, a") && false) return;',
          expect=ALERT_DETAIL_SPEC + "a click on the title button opens the detail once: the row does not open it a "
                                     "second time")
@mutation(ALERTS, "  if (writing.value) return;\n  opener =", "  opener =",
          expect=IN_FLIGHT)
@mutation(ALERTS, "if (failure) notify.error(", "if (false) notify.error(",
          expect=ACK + "an acknowledge whose reload fails says both")
@mutation(ALERTS, "failure = err;", "failure = null;", expect=ACK + "an acknowledge whose reload fails says both")
@pytest.mark.web_unit
def test_the_alerts_view_keeps_its_filters_footers_and_locks():
    vitest(ALERTS_VIEW_SPEC)


# ---- Alerts: the group rows, the chips, the live card, the tablet and the phone -------------------------------------
DIMS = ALERT_LISTS + "dims a group with nothing open, marks the opened one, and counts the groups"


@mutation(ALERTS, 'row.open_count === 0 ? "a-dim" : ""', 'row.open_count !== 0 ? "a-dim" : ""', expect=DIMS)
@mutation(ALERTS, 'isOpened(row) ? "a-selected" : ""', '!isOpened(row) ? "a-selected" : ""', expect=DIMS)
@mutation(ALERTS, 'groups.value.length === 1 ? "group" : "groups"',
          'groups.value.length === 2 ? "group" : "groups"',
          expect=DIMS)
@pytest.mark.web_unit
def test_the_alerts_view_marks_and_counts_its_groups():
    vitest(ALERTS_VIEW_SPEC)


@mutation(ALERT_DETAIL, "`${i.acknowledged_by}, ${alertTime(i.acknowledged_ms)}`", "`${i.acknowledged_by}`",
          expect=ALERT_DESIGN + "an acknowledged row shows who and when instead of not yet")
@pytest.mark.web_unit
def test_the_alert_detail_is_drawn_as_the_design_draws_it():
    vitest(ALERTS_VIEW_SPEC)


# ---- the Alerts group rules ------------------------------------------------------------------------------------
@mutation(ALERTS_MODEL, '(reply ?? "").replace(/\\s+/g, " ").trim() || g.title', 'g.title',
          expect=f"FAILED {ALERTS_MODEL_SPEC} > groupSentence (the Jobs banner's muted sentence) > quotes the stored")
@mutation(ALERTS_MODEL, "said.length > QUOTE_LIMIT ?", "false ?",
          expect=f"FAILED {ALERTS_MODEL_SPEC} > groupSentence (the Jobs banner's muted sentence) > quotes the stored")
@mutation(ALERTS_MODEL, "${count(g.open_count, \"job\")}", "${count(g.count, \"job\")}",
          expect=f"FAILED {ALERTS_MODEL_SPEC} > groupSentence (the Jobs banner's muted sentence) > says who replied")
@mutation(ALERTS_MODEL, '`${n} ${one}${n === 1 ? "" : "s"}`', '`${n} ${one}s`',
          expect=f"FAILED {ALERTS_MODEL_SPEC} > groupSentence (the Jobs banner's muted sentence) > says who replied")
@mutation(ALERTS_MODEL, 'if (g.kind !== "unclear_reply" || isEmptyWording(g)) return', "if (false) return",
          expect=f"FAILED {ALERTS_MODEL_SPEC} > groupSentence (the Jobs banner's muted sentence) > a reply with no "
                 "text, or a group of another kind, says its title")
@mutation(ALERTS_MODEL, "g.keys === 1 && !isEmptyWording(g)", "g.keys >= 1 && !isEmptyWording(g)",
          expect=ONE_CUSTOMER + "is for an unclear wording seen under exactly one key")
@mutation(ALERTS_MODEL, "g.keys === 1 && !isEmptyWording(g)", "g.keys !== 2 && !isEmptyWording(g)",
          expect=ONE_CUSTOMER + "is for an unclear wording seen under exactly one key")
@mutation(ALERTS_MODEL, 'return g.kind === "unclear_reply" && g.keys === 1', "return g.keys === 1",
          expect=ONE_CUSTOMER + "is not for a billing group, nor for the read failure (it has no wording)")
@mutation(ALERTS_MODEL, "g.keys === 1 && !isEmptyWording(g);", "g.keys === 1;",
          expect=ONE_CUSTOMER + "is not for a billing group, nor for the read failure (it has no wording)")
@mutation(ALERTS_MODEL, 'g.kind === "unclear_reply" && g.title === EMPTY_WORDING_TITLE',
          "g.title === EMPTY_WORDING_TITLE",
          expect=EMPTY_WORDING + "is rewritten as an API read failure, only for an unclear reply")
@mutation(ALERTS_MODEL, 'isEmptyWording(g) ? "API read failure: no reply text was read" : g.title', "g.title",
          expect=EMPTY_WORDING + "is rewritten as an API read failure, only for an unclear reply")
@mutation(ALERTS_MODEL, 'const open = o.open ? `, ${o.open} still open` : "";', 'const open = "";',
          expect=OUTCOMES + "adds the jobs still open, only when there are some")
@mutation(ALERTS_MODEL, 'jobStatus === "failed" ? "refunded" : jobStatus', "jobStatus",
          expect=f"FAILED {ALERTS_MODEL_SPEC} > outcomeStatus > a failed job was refunded; the other statuses are "
                 "shown as they are")
@mutation(ALERTS_MODEL, "MONTHS[d.getMonth()]", "MONTHS[d.getMonth() + 1]",
          expect=f"FAILED {ALERTS_MODEL_SPEC} > alertTime > is day, month name and the hour and minute, local time")
@mutation(ALERTS_MODEL, "d.open_count > 0 ? {", "d.open_count >= 0 ? {",
          expect=f"FAILED {ALERTS_MODEL_SPEC} > groupFacts > Open has no tone at 0")
@mutation(ALERTS_MODEL, 'export const ONE_CUSTOMER_NOTE = "One customer only: may be the customer’s own words.";',
          'export const ONE_CUSTOMER_NOTE = "One customer only.";',
          expect=f"FAILED {ALERTS_MODEL_SPEC} > the fixed sentences > are the ones the detail shows")
@pytest.mark.web_unit
def test_the_alert_group_rules_hold():
    vitest(ALERTS_MODEL_SPEC)


# ---- the names in the alert detail ------------------------------------------------------------------------------
NAMES = f"FAILED {ALERTS_VIEW_SPEC} > job titles and key labels in the detail > "


@mutation(ALERT_DETAIL, "r.job_title || r.job_id.slice(0, 8)", "r.job_id.slice(0, 8)",
          expect=NAMES + "the job is its title with the short id under it, the key is its label")
@mutation(ALERT_DETAIL, "r.key_label || r.key_id?.slice(0, 8)", "r.key_id?.slice(0, 8)",
          expect=NAMES + "the job is its title with the short id under it, the key is its label")
@mutation(ALERT_DETAIL, "r.key_label || r.key_id?.slice(0, 8) || ", "r.key_label || ",
          expect=NAMES + "without a title or a label the short ids stand in, and a title is text")
@pytest.mark.web_unit
def test_the_alert_detail_names_the_job_and_the_key():
    vitest(ALERTS_VIEW_SPEC)


# ---- Request logs: the drawn error wording and the empty list under a filter ---------------------------------------
EMPTY_FILTERED = ("an empty list under a filter says so; Clear filters clears every filter and searches from the "
                  "first page")


@mutation(LOGS, 'error.value = loadFailedText(err, errorText(err), "the request logs");',
          "error.value = errorText(err);",
          expect=LOGS_LIST + "a failed load says what failed and what the relay answered")
@mutation(LOGS, 'loadFailedText(err, errorText(err), "the request logs")', "loadFailedText(err, errorText(err))",
          expect=LOGS_LIST + "a failed load says what failed and what the relay answered")
@mutation(LOGS, 'filters.q.trim() || filters.status !== "all" || filters.from', "filters.q.trim() || filters.from",
          expect=LOGS_LIST + EMPTY_FILTERED)
@mutation(LOGS, 'filters.q.trim() || filters.status !== "all"', 'filters.q.trim() || filters.status !== "x"',
          expect=LOGS_LIST + "an empty list with no filter says there are no requests yet and offers a test job")
@mutation(LOG_TABLE, '<td><span class="bar bar--pill" /></td>', '<td><span class="bar bar--time" /></td>',
          expect=LOGS_LIST + "the loading skeleton draws four columns of flat bars: the time, the status pill, "
          "the request, the duration")
@mutation(LOG_TABLE, '<div v-for="i in SKELETON" :key="i" class="p-row p-row--skel">',
          '<div v-for="i in 3" :key="i" class="p-row p-row--skel">',
          expect=LOGS_LIST + "a phone's loading skeleton is a list of bars")
@mutation(LOG_TABLE, "const SKELETON = [0, 1, 2, 3, 4];", "const SKELETON = [0, 1, 2];",
          expect=LOGS_LIST + "the loading skeleton draws four columns of flat bars: the time, the status pill, "
          "the request, the duration")
@mutation(LOG_TABLE, "row.error_code && row.status_code < 400 ?", "row.error_code && row.status_code < 600 ?",
          expect=f"FAILED {LOGS_VIEW_SPEC} > the tablet > keeps five columns, the status first, folds the error code "
                 "or the job title, and the key, under the route")
@mutation(LOG_FILTERS, '  filters.status = value as LogStatus;\n  emit("search");',
          "  filters.status = value as LogStatus;",
          expect=LOGS_PAGING + "a status choice searches at once from the first page")
@mutation(LOGS, "  filters.reset();\n  search();", "  search();", expect=LOGS_LIST + EMPTY_FILTERED)
@mutation(LOGS, "  filters.reset();\n  search();", "  filters.reset();", expect=LOGS_LIST + EMPTY_FILTERED)
@pytest.mark.web_unit
def test_the_logs_list_words_its_failure_and_its_empty_list_as_drawn():
    vitest(LOGS_VIEW_SPEC)


# ---- Request logs: a key with no label is named by its prefix, as Jobs names it ------------------------------------
@mutation(SRC + "/components/logs/log-window.ts", "prefix: row.key_prefix", "prefix: null",
          expect=LOGS_LIST + "the Key cell names a key by its label, else by its prefix")
@pytest.mark.web_unit
def test_a_log_row_names_a_key_with_no_label_by_its_prefix():
    vitest(LOGS_VIEW_SPEC)


# ---- Alerts (page-alerts round): the group names, the live words and the phone order, as the canvas draws them ----
TITLE = f"FAILED {ALERTS_MODEL_SPEC} > alertTitle (a group's name on the Alerts page) > "
WORD = f"FAILED {ALERTS_MODEL_SPEC} > liveWord (the word before a live condition) > "
NAMED = f"FAILED {ALERTS_VIEW_SPEC} > the names of the groups, as the canvas writes them > "


@mutation(ALERTS_MODEL, "isEmptyWording(g) ? groupTitle(g) : replied(g, g.reply)",
          "isEmptyWording(g) ? groupTitle(g) : replied(g, null)",
          expect=TITLE + "a reply group is who replied and the newest reply as stored, else its folded wording")
@mutation(ALERTS_MODEL, "isEmptyWording(g) ? groupTitle(g) : replied(g, g.reply)", "replied(g, g.reply)",
          expect=TITLE + "the read failure keeps its own words")
@mutation(ALERTS_MODEL, 'const scope = g.group.split(".")[1];', 'const scope = g.group.split(".")[0];',
          expect=TITLE + "a billing group is its wallet and the key's name, else the server's title")
@mutation(ALERTS_MODEL, "return g.key_label && scope", "return scope",
          expect=TITLE + "a billing group is its wallet and the key's name, else the server's title")
@mutation(ALERTS_MODEL, '${scope === POINTS ? "points wallet" : scopeName(scope)}', "${scopeName(scope)}",
          expect=TITLE + "a billing group is its wallet and the key's name, else the server's title")
@mutation(ALERTS_MODEL, '["accounts_low", "pool"],', '["accounts_low", "accounts_low"],',
          expect=WORD + "names what the condition is about, as the canvas writes `pool:`")
@mutation(ALERTS_MODEL, '["account_dead", "account"],', '["account_dead", "account_dead"],',
          expect=WORD + "names what the condition is about, as the canvas writes `pool:`")
@mutation(ALERTS_MODEL, 'media === "video" ? "video’s"', 'media === "image" ? "video’s"',
          expect=f"FAILED {ALERTS_MODEL_SPEC} > the fixed sentences > the reply is the video's or the image's answer "
                 "when every job is one, else the job's")
@mutation(ALERTS_MODEL, 'media === "image" ? "image’s"', 'media === "video" ? "image’s"',
          expect=f"FAILED {ALERTS_MODEL_SPEC} > the fixed sentences > the reply is the video's or the image's answer "
                 "when every job is one, else the job's")
@mutation(ALERTS_MODEL, "LIVE_WORDS.get(kind) ?? kind", "kind",
          expect=WORD + "names what the condition is about, as the canvas writes `pool:`")
@pytest.mark.web_unit
def test_the_alert_names_and_live_words_are_the_canvas_words():
    vitest(ALERTS_MODEL_SPEC)


@mutation(ALERTS, "? alertTitle(shownDetail.value) :", "? shownDetail.value.title :",
          expect=NAMED + "the list and the detail name a reply group by its newest reply, a billing group by its "
                         "wallet and key")
@mutation(ALERTS_MODEL, "return `Could not load the alerts: ${why}.`;", "return said;",
          expect=GUARD + "the failed list is worded as the canvas words one: who answered what")
@mutation(ALERTS_MODEL, "? `the relay answered ${err.status} (${said})`", "? said",
          expect=GUARD + "the failed list is worded as the canvas words one: who answered what")
@mutation(ALERTS_MODEL, ": said.charAt(0).toLowerCase() + said.slice(1);", ": said;",
          expect=GUARD + "the failed list is worded as the canvas words one: who answered what")
@pytest.mark.web_unit
def test_the_alerts_view_writes_the_canvas_names_and_opens_the_detail_in_a_drawer():
    vitest(ALERTS_VIEW_SPEC)


MUTED = f"FAILED {ALERTS_VIEW_SPEC} > mute, rules, Open Jobs filtered and Resolved today > "
STATS_STORE = SRC + "/stores/stats.ts"


@mutation(ALERTS, "if (g.open_count > 0 && !g.muted_until_ms) n[", "if (g.open_count > 0) n[",
          expect=MUTED + "Mute 1 h mutes the row's kind for 60 minutes; a muted row shows its end and is not counted")
@mutation(ALERTS, "if (!a.muted_until_ms) n[", "if (a) n[",
          expect=MUTED + "a live condition can be muted, and a muted one from the stats is listed with its end")
@mutation(ALERTS, "stat.alerts.concat(stat.muted_alerts ?? [])", "stat.alerts.concat([])",
          expect=MUTED + "a live condition can be muted, and a muted one from the stats is listed with its end")
@mutation(ALERTS_MODEL, 'return { provider: scope && scope !== POINTS ? scope : "", query };',
          'return { provider: scope ?? "", query };',
          expect=MUTED + "Open Jobs filtered opens Jobs on the group's provider and key")
@mutation(ALERTS_MODEL, 'if (d.kind === "clamp") query.status = "failed";', "",
          expect=MUTED + "Open Jobs filtered opens Jobs on the group's provider and key")
@pytest.mark.web_unit
def test_the_alerts_view_mutes_a_kind_counts_only_what_is_raised_and_opens_jobs_filtered():
    vitest(ALERTS_VIEW_SPEC)


@mutation(STATS_STORE, "const raised = groups.items.filter((g) => !g.muted_until_ms);",
          "const raised = groups.items;",
          expect="FAILED tests/unit/stores.spec.ts > stats store > a group of a muted kind is never counted or worded")
@pytest.mark.web_unit
def test_the_shell_never_counts_a_muted_group():
    vitest("tests/unit/stores.spec.ts")


BANNER_MUTED = ("FAILED tests/unit/app-shell.spec.ts > the shell > a muted alert never vanishes: with nothing open the "
                "banner still says quietly how many are muted")


@mutation(STATS_STORE, "muted.value = groups.items.length - raised.length + (stats.muted_alerts?.length ?? 0);",
          "muted.value = groups.items.length - raised.length;", expect=BANNER_MUTED)
@mutation(SRC + "/components/layout/AttentionBanner.vue", "|| stats.openGroups > 0 || stats.muted > 0);",
          "|| stats.openGroups > 0);", expect=BANNER_MUTED)
@pytest.mark.web_unit
def test_the_banner_still_counts_the_muted_alerts():
    vitest("tests/unit/app-shell.spec.ts")


JOBS_LINK = f"FAILED {ALERTS_MODEL_SPEC} > jobsLink (Open Jobs filtered) > "


@mutation(ALERTS_MODEL, "return firstMs >= start - 6 * DAY_MS ? \"7d\" : \"30d\";", "return \"30d\";",
          expect=JOBS_LINK + "the range holds the group's first row: today, 7 days, else 30")
@mutation(ALERTS_MODEL, "if (!firstMs || firstMs >= start) return \"today\";", "if (!firstMs) return \"today\";",
          expect=JOBS_LINK + "the range holds the group's first row: today, 7 days, else 30")
@mutation(ALERTS_MODEL, "return set.size === 1 ? [...set][0]! : \"\";", "return [...set][0] ?? \"\";",
          expect=JOBS_LINK + "an unclear group searches the one account its rows share, else the one key, else nothing")
@mutation(ALERTS_MODEL, "if (o && o.failed > 0 && o.done === 0 && o.open === 0) query.status = \"failed\";",
          "if (o && o.failed > 0) query.status = \"failed\";",
          expect=JOBS_LINK + "an unclear group asks for failed jobs only when every one of them was refunded")
@pytest.mark.web_unit
def test_open_jobs_filtered_carries_the_range_the_search_and_the_statuses():
    vitest(ALERTS_MODEL_SPEC)
