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
NEW_KEY = SRC + "/components/keys/NewKeyCallout.vue"
KEY_FORM = SRC + "/components/keys/KeyFormDialog.vue"
KEY_SWITCH = SRC + "/components/shared/ToggleSwitch.vue"
WALLET_LINES = SRC + "/components/keys/WalletLines.vue"
ALERTS_MODEL = SRC + "/components/alerts/alerts-model.ts"
ALERT_DETAIL = SRC + "/components/alerts/AlertDetailPanel.vue"
LOG_FILTERS = SRC + "/components/logs/LogsFilterForm.vue"
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
HEADERS = f"FAILED {WINDOW_SPEC} > the day headers > "
BODY = f"FAILED {KEY_SPEC} > keyBody (the create and edit payload) > "
WALLET_TEXT = f"FAILED {KEY_SPEC} > walletText > "
PERCENT = f"FAILED {KEY_SPEC} > usagePercent > "
LABEL = f"FAILED {KEY_SPEC} > usageLabel > "
CREATED = f"FAILED {KEY_SPEC} > createdText > "
ONE_TIME = f"FAILED {KEYS_VIEW_SPEC} > the one-time key > "
KEYS_LIST = f"FAILED {KEYS_VIEW_SPEC} > the list > "
DESIGN = f"FAILED {KEYS_VIEW_SPEC} > the page as the design draws it > "
FORM = f"FAILED {KEYS_VIEW_SPEC} > the key form as the design draws it > "
KEYS_SMALL = f"FAILED {KEYS_VIEW_SPEC} > the tablet and the phone > "
GUARD = f"FAILED {ALERTS_VIEW_SPEC} > the generation guard > "
ACK = f"FAILED {ALERTS_VIEW_SPEC} > acknowledge > "
COPY = f"FAILED {KEYS_VIEW_SPEC} > Copy > "
CLEAR = f"FAILED {LOGS_VIEW_SPEC} > clear logs > "
LOGS_LIST = f"FAILED {LOGS_VIEW_SPEC} > the list > "
LOGS_FILTERS = f"FAILED {LOGS_VIEW_SPEC} > filters > "
LOGS_PAGING = f"FAILED {LOGS_VIEW_SPEC} > paging and refresh > "
LOGS_TABLET = f"FAILED {LOGS_VIEW_SPEC} > the tablet > "
LOGS_PHONE = f"FAILED {LOGS_VIEW_SPEC} > the phone > "
LOGS_DESKTOP_TYPING = f"FAILED {LOGS_VIEW_SPEC} > typing on a desktop > "
LOGS_DRAWER = f"FAILED {LOGS_VIEW_SPEC} > the request drawer, in detail > "
LOGS_DETAIL = f"FAILED {LOGS_VIEW_SPEC} > the detail > "
ALERT_LISTS = f"FAILED {ALERTS_VIEW_SPEC} > the lists > "
ALERT_FILTERS = (f"FAILED {ALERTS_VIEW_SPEC} > the filters > send the Status and then the Kind to the server, each "
                 "in its own place")
ALERT_DETAIL_SPEC = f"FAILED {ALERTS_VIEW_SPEC} > the detail > "
ALERT_DESIGN = f"FAILED {ALERTS_VIEW_SPEC} > the detail as the design draws it > "
ALERT_SMALL = f"FAILED {ALERTS_VIEW_SPEC} > the tablet and the phone > "
IN_FLIGHT = (f"FAILED {ALERTS_VIEW_SPEC} > acknowledge > while an acknowledge is in flight, the selects, Details, "
             "Close and both Acknowledge buttons are off")
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
@mutation(WINDOW, "export const PAGE_SIZE = 50;",
          "export const PAGE_SIZE = 10;", expect=WRITTEN + "a page is 50 requests")
@mutation(WINDOW, "code >= 500", "code > 500", expect=WRITTEN + "only a code of 500 or more is a server error")
@mutation(WINDOW, 'n.toLocaleString("en-US")', "String(n)", expect=WRITTEN + "a number has a thousands comma")
@mutation(WINDOW, ":${two(d.getSeconds())}", "",
          expect=WRITTEN + "the clock has two digits for each part, in local time; a missing time is a dash")
@mutation(WINDOW, "${two(d.getDate())}/${two(d.getMonth() + 1)}", "${two(d.getDate())}/${two(d.getMonth())}",
          expect=WRITTEN + "the clock has two digits for each part, in local time; a missing time is a dash")
@mutation(WINDOW, 'label: "OK below 400"', 'label: "OK"',
          expect=WRITTEN + "the Status select offers the canvas words for the four statuses")
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


# ---- the wallet lines of a key ----------------------------------------------------------------------------------
@mutation(KEY_MODEL, 'return [day, total].filter(Boolean).join(" · ");',
          'return [day, total].filter(Boolean).join(", ");',
          expect=WALLET_TEXT + "limit with both, with the day only and with the total only")
@mutation(KEY_MODEL, "Math.round((used / limit) * 100)", "Math.ceil((used / limit) * 100)",
          expect=PERCENT + "is the rounded share of the limit used")
@mutation(KEY_MODEL, "if (!limit) return 0;", "if (limit === null) return 0;",
          expect=PERCENT + "is 0 without a limit and never more than 100")
@mutation(KEY_MODEL, "Math.min(100, Math.round((used / limit) * 100))", "Math.round((used / limit) * 100)",
          expect=PERCENT + "is 0 without a limit and never more than 100")
@mutation(KEY_MODEL, "${w.used_day} of ${w.day_limit} today", "${w.used_day} today",
          expect=LABEL + "speaks both bars")
@mutation(KEY_MODEL, "${w.used_total} of ${w.total_limit} in total", "${w.used_total} in total",
          expect=LABEL + "speaks both bars")
@mutation(KEY_MODEL, "const day = w.day_limit === null ? `", "const day = w.day_limit !== null ? `",
          expect=LABEL + "names a side that has no limit without a limit")
@mutation(KEY_MODEL, 'd.toDateString() === new Date(now).toDateString()',
          'd.getFullYear() === new Date(now).getFullYear()',
          expect=CREATED + "a key of an earlier day shows day/month/year")
@mutation(KEY_MODEL, "return `${date}/${d.getFullYear()}`;", "return date;",
          expect=CREATED + "a key of an earlier day shows day/month/year")
@mutation(KEY_MODEL, 'padStart(2, "0")', 'padStart(1, "0")',
          expect=CREATED + "a key made at midnight today still reads as today, one a minute before as yesterday's date")
@pytest.mark.web_unit
def test_the_wallet_text_the_bars_and_the_created_date_are_written_right():
    vitest(KEY_SPEC)


# ---- the order of the wallets -----------------------------------------------------------------------------------
ORDER = f"FAILED {KEY_SPEC} > the order of the wallets > "


@mutation(KEY_MODEL, "const rank = kindRank(a.kind) - kindRank(b.kind);",
          "const rank = kindRank(b.kind) - kindRank(a.kind);",
          expect=ORDER + "a provider's video wallet comes before its image wallet, then any other kind by name")
@mutation(KEY_MODEL, "return at === -1 ? KIND_ORDER.length : at;", "return at;",
          expect=ORDER + "a provider's video wallet comes before its image wallet, then any other kind by name")
@mutation(KEY_MODEL, "return a.provider < b.provider ? -1 : 1;", "return a.provider < b.provider ? 1 : -1;",
          expect=ORDER + "providers go in name order whatever their kinds")
@pytest.mark.web_unit
def test_the_wallets_are_listed_video_first():
    vitest(KEY_SPEC)


@mutation(WALLET_LINES, "const ordered = computed(() => sortedWallets(props.wallets));",
          "const ordered = computed(() => props.wallets);",
          expect=DESIGN + "lists a provider's video wallet before its image wallet, whatever order the server sent")
@pytest.mark.web_unit
def test_the_keys_view_lists_video_wallets_first():
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


# ---- the one-time key -------------------------------------------------------------------------------------------
@mutation(KEYS, "const forgetSignOut = auth.onSignOut(() => {", "const forgetSignOut = (() => () => {})(() => {",
          expect=ONE_TIME + "is gone on sign-out")
@mutation(KEYS, '@dismiss="rawKey = null"', '@dismiss="rawKey = rawKey"',
          expect=ONE_TIME + "is gone from every place after Dismiss")
@mutation(KEYS, "      if (!disposed) {\n        rawKey.value = created.key;",
          "      {\n        rawKey.value = created.key;",
          expect=ONE_TIME + "a create that answers after sign-out does not bring it back")
@mutation(KEYS, "  disposed = true;\n  rawKey.value = null;\n  forgetSignOut();",
          "  disposed = true;\n  forgetSignOut();",
          expect=ONE_TIME + "leaving the view clears the key it held in memory")
@pytest.mark.web_unit
def test_the_raw_key_lives_only_until_dismissed_left_or_signed_out():
    vitest(KEYS_VIEW_SPEC)


@mutation(KEYS, "if (!disposed) formError.value = createError(err);",
          "if (!disposed) formError.value = { text: (err as Error).message, field: null };",
          expect=f"FAILED {KEYS_VIEW_SPEC} > the one-time key > a failed create shows fixed text and the field, "
                 "never the server's message or param")
@pytest.mark.web_unit
def test_the_keys_view_shows_a_failed_create_from_fixed_text():
    vitest(KEYS_VIEW_SPEC)


# ---- Keys: the list, the callout, the switch, the bars, the form ------------------------------------------------
@mutation(KEYS, "createdLabel.value = created.label || body.label;", "createdLabel.value = body.label;",
          expect=DESIGN + "the callout names the new key, and its row is the one tinted")
@mutation(KEYS, "row.id === createdId.value", "row.id !== createdId.value",
          expect=DESIGN + "the callout names the new key, and its row is the one tinted")
@mutation(NEW_KEY, "<strong>Key created for {{ label }}.</strong>", "<strong>Key created.</strong>",
          expect=DESIGN + "the callout names the new key, and its row is the one tinted")
@mutation(KEYS, "title: `Delete key ${k.label || k.prefix}?`,", 'title: "Delete key",',
          expect=KEYS_LIST + "delete says whether the key went or was only disabled")
@mutation(KEYS, 'message: "A key that has jobs is only disabled.",', 'message: "Delete it.",',
          expect=KEYS_LIST + "delete says whether the key went or was only disabled")
@mutation(KEYS, "await setKeyEnabled(k.id, enabled);", "await setKeyEnabled(k.id, !enabled);",
          expect=KEYS_LIST + "a switch that fails reads the list again, so it goes back")
@mutation(KEY_SWITCH, "emit('change', !props.modelValue)", "emit('change', props.modelValue)",
          expect=KEYS_LIST + "a switch that fails reads the list again, so it goes back")
@mutation(KEYS, '(enabled ? "Disable key" : "Enable key")', '(enabled ? "Enable key" : "Disable key")',
          expect=DESIGN + "the switch is a labelled switch that shows what the server holds")
@mutation(KEYS, '<span v-else class="k-enabled">enabled</span>', '<StatusChip v-else status="enabled" />',
          expect=KEYS_LIST + "an enabled key is plain text, a disabled key is the only one with a chip, and 'any "
                             "model' is neutral")
@mutation(KEYS, "<span v-if=\"row.models.includes('*')\" class=\"k-tag\">any model</span>",
          "<span v-if=\"false\" class=\"k-tag\">any model</span>",
          expect=KEYS_LIST + "an enabled key is plain text, a disabled key is the only one with a chip, and 'any "
                             "model' is neutral")
@mutation(WALLET_LINES, 'v-if="w.mode === \'limit\'"', 'v-if="true"',
          expect=DESIGN + "a limit wallet draws two bars of the rounded share used, with a spoken label; other "
                          "modes draw none")
@mutation(WALLET_LINES, "usagePercent(w.used_day, w.day_limit)", "usagePercent(w.used_total, w.day_limit)",
          expect=DESIGN + "a limit wallet draws two bars of the rounded share used, with a spoken label; other "
                          "modes draw none")
@pytest.mark.web_unit
def test_the_keys_view_draws_the_callout_the_switch_the_bars_and_the_delete_confirm():
    vitest(KEYS_VIEW_SPEC)


@mutation(KEYS, ':tablet-columns="TABLET_COLUMNS"', "",
          expect=KEYS_SMALL + "a tablet keeps five columns and folds the models and the date into the label and "
                              "unfinished cells")
@mutation(KEYS, '"Unfinished · jobs"', '"Unfinished / max"',
          expect=KEYS_SMALL + "a tablet keeps five columns and folds the models and the date into the label and "
                              "unfinished cells")
@mutation(KEYS, ': row.models.join(", ")', ': row.models.join(" ")',
          expect=KEYS_SMALL + "a tablet keeps five columns and folds the models and the date into the label and "
                              "unfinished cells")
@mutation(KEYS, '<span class="kla-mono k-num"> · {{ row.jobs }} jobs</span>',
          '<span class="kla-mono k-num"> · {{ row.jobs + 1 }} jobs</span>',
          expect=KEYS_SMALL + "a tablet keeps five columns and folds the models and the date into the label and "
                              "unfinished cells")
@mutation(KEYS, 'k.models.join(" · ")', 'k.models.join(", ")',
          expect=KEYS_SMALL + "a phone lists each key as a block with its models, wallets, counts and the two row "
                              "buttons")
@pytest.mark.web_unit
def test_the_keys_view_folds_for_a_tablet_and_a_phone():
    vitest(KEYS_VIEW_SPEC)


@mutation(KEY_FORM, ":value=\"w.mode === 'limit' ? w.day : ''\"", ':value="w.day"',
          expect=FORM + "a wallet that leaves limit mode shows empty, disabled number boxes, and a limit wallet "
                        "shows its numbers")
@mutation(KEY_FORM, ':disabled="form.any"', ':disabled="false"',
          expect=FORM + "the model boxes are off while Any model is ticked and on once it is cleared")
@mutation(KEY_FORM, ':aria-disabled="form.any"', ':aria-disabled="false"',
          expect=FORM + "the model boxes are off while Any model is ticked and on once it is cleared")
@mutation(KEY_FORM, "`Edit API key — ${props.editing.label || props.editing.prefix}`", '"Edit API key"',
          expect=FORM + "an edit names the key in its title and tells what each wallet has used")
@mutation(KEY_FORM, "`${w.used_day} used today · ${w.used_total} total`", "`${w.used_total} total`",
          expect=FORM + "an edit names the key in its title and tells what each wallet has used")
@mutation(KEY_FORM, '`${String(m.kind ?? "")} · ${m.owned_by}`', '`${m.owned_by} · ${String(m.kind ?? "")}`',
          expect=FORM + "each model row says its kind and provider")
@pytest.mark.web_unit
def test_the_key_form_shows_what_the_design_shows():
    vitest(KEYS_VIEW_SPEC)


# ---- Keys: the Copy buttons ------------------------------------------------------------------------------------
@mutation(NEW_KEY, "copyText(props.rawKey, () => field.value, abort.signal)",
          'copyText("", () => field.value, abort.signal)',
          expect=COPY + "the one-time callout has a Copy that copies the raw key")
@mutation(NEW_KEY, "copied.value = true;", "copied.value = false;",
          expect=COPY + "the Copy of the callout says Copied afterwards")
@mutation(KEYS, "await copyText(k.prefix);", "await copyText(k.label);",
          expect=COPY + "the prefix in a row has a Copy that copies the prefix and nothing else")
@pytest.mark.web_unit
def test_the_keys_view_copy_buttons_copy_the_right_text():
    vitest(KEYS_VIEW_SPEC)


# ---- Request logs: Clear logs, the More menu --------------------------------------------------------------------
@mutation(LOGS, "  if (!ok) return;\n  await run(async () => {\n    const out = await clearLogs();",
          "  await run(async () => {\n    const out = await clearLogs();", expect=CLEAR + "a cancel deletes nothing")
@mutation(LOGS, "    offset.value = 0;\n    await load();", "    await load();",
          expect=CLEAR + "asks first, then says how many rows went and goes to the first page")
@mutation(LOGS, "async function clear() {\n  menuOpen.value = false;", "async function clear() {",
          expect=LOGS_TABLET + "the menu item asks first, like the desktop button")
@mutation(LOGS, 'if (event.key === "Escape") menuOpen.value = false;',
          'if (event.key === "Escape") menuOpen.value = true;',
          expect=LOGS_TABLET + "puts Clear logs in a More menu: closed at first, one item, closed again by Escape "
                               "and by a click outside")
@mutation(LOGS, "!menuWrap.value?.contains(event.target as Node)", "menuWrap.value?.contains(event.target as Node)",
          expect=LOGS_TABLET + "puts Clear logs in a More menu: closed at first, one item, closed again by Escape "
                               "and by a click outside")
@mutation(LOGS, 'v-if="!isPhone && !isTablet"', 'v-if="!isPhone"',
          expect=LOGS_TABLET + "puts Clear logs in a More menu: closed at first, one item, closed again by Escape "
                               "and by a click outside")
@pytest.mark.web_unit
def test_clear_logs_asks_first_and_the_more_menu_opens_and_closes():
    vitest(LOGS_VIEW_SPEC)


# ---- Request logs: the date fields, the page size and the filters -----------------------------------------------
RANGE = (f"FAILED {LOGS_VIEW_SPEC} > filters > From and To send the half-open window of the viewer's own "
         "midnights, and search at once")
ALONE = f"FAILED {LOGS_VIEW_SPEC} > filters > From alone sets since only; To alone sets until only"
SIZE = (f"FAILED {LOGS_VIEW_SPEC} > paging and refresh > a page size from the pager is asked for and starts "
        "again at the first page")


@mutation(LOG_FILTERS, 'v-model="filters.from"', 'v-model="filters.to"', expect=RANGE)
@mutation(LOG_FILTERS, 'v-model="filters.to"', 'v-model="filters.from"', expect=ALONE)
@mutation(LOGS, "scope.provider, offset.value, limit.value)", "scope.provider, offset.value)", expect=SIZE)
@mutation(LOGS, "            limit = s;\n            search();", "            limit = s;\n            void load();",
          expect=SIZE)
@mutation(LOG_FILTERS, "if (!isPhone.value) return;", "",
          expect=LOGS_DESKTOP_TYPING + "does not search by itself: the Search button or Enter does")
@mutation(LOG_FILTERS, 'v-if="!isPhone" type="submit"', 'v-if="true" type="submit"',
          expect=LOGS_PHONE + "has no Search button: a choice in Status searches at once, and typing searches after "
                              "a pause")
@mutation(LOG_FILTERS, "const TYPING_PAUSE_MS = 350;", "const TYPING_PAUSE_MS = 3500;",
          expect=LOGS_PHONE + "has no Search button: a choice in Status searches at once, and typing searches after "
                              "a pause")
@pytest.mark.web_unit
def test_the_logs_range_fields_and_page_size_reach_the_query():
    vitest(LOGS_VIEW_SPEC)


# ---- Request logs: colour is for exceptions, the day header, the drawer --------------------------------------------
@mutation(LOGS, 'isServerError(row.status_code) ? "bad" : undefined', 'row.status_code >= 400 ? "bad" : undefined',
          expect=LOGS_LIST + "only a 5xx row is filled red; a 4xx row and a fine row are not tinted")
@mutation(LOGS, 'isServerError(row.status_code) ? "is-bad" : "is-muted"', 'true ? "is-bad" : "is-muted"',
          expect=LOGS_LIST + "the error code is plain mono text, red on a 5xx row and muted otherwise, never a chip")
@mutation(LOGS, 'v-if="isTablet && row.error_code"', 'v-if="row.error_code"',
          expect=LOGS_LIST + "the error code is plain mono text, red on a 5xx row and muted otherwise, never a chip")
@mutation(LOGS, '{{ isTablet ? " ms" : "" }}', '{{ "" }}',
          expect=LOGS_TABLET + "keeps five columns, folds the error code under the pill and the job under the route, "
                               "and writes the unit")
@mutation(LOGS, "  detailOpen.value = false;\n  ui.openJob(id);", "  ui.openJob(id);",
          expect=LOGS_DETAIL + "the job link closes the drawer and opens the job drawer")
@pytest.mark.web_unit
def test_the_logs_view_marks_exceptions_only_and_hands_a_job_to_its_drawer():
    vitest(LOGS_VIEW_SPEC)


@mutation(LOG_DRAWER, 'if (typeof value === "string") return value;', "",
          expect=LOGS_DRAWER + "a body that is already text is shown as it is, not quoted")
@mutation(LOG_DRAWER, 'which === "request" ? requestText.value : (answerText.value ?? "")',
          'which === "request" ? (answerText.value ?? "") : requestText.value',
          expect=LOGS_DRAWER + "each Copy copies its own body and says Copied")
@mutation(LOG_DRAWER, "copied.value = which;", "copied.value = null;",
          expect=LOGS_DRAWER + "each Copy copies its own body and says Copied")
@mutation(LOG_DRAWER, '<dd class="kla-mono rd__bad">', '<dd class="kla-mono">',
          expect=LOGS_DRAWER + "an error code is red on a server error and on a client error alike")
@mutation(LOG_DRAWER, 'v-if="log.error_code" class="rd__row"', 'v-if="true" class="rd__row"',
          expect=LOGS_DRAWER + "a request with no error has no Error line")
@mutation(LOG_DRAWER, "<dd v-else>no job</dd>", "<dd v-else>none</dd>",
          expect=LOGS_DETAIL + "a request with no job shows no job link")
@pytest.mark.web_unit
def test_the_request_drawer_shows_its_bodies_and_facts_as_text():
    vitest(LOGS_VIEW_SPEC)


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
@mutation(ALERTS, 'status: "open", kind: "", open: null', 'status: "all", kind: "", open: null',
          expect=ALERT_LISTS + "asks for the open groups, 200 of them, with the live conditions beside")
@mutation(ALERTS, "live: stat.alerts", "live: []",
          expect=ALERT_LISTS + "asks for the open groups, 200 of them, with the live conditions beside")
@mutation(ALERTS, "groups.length >= GROUP_LIMIT", "groups.length > GROUP_LIMIT",
          expect=ALERT_LISTS + "says the list is cut at 200 groups, and only then")
@mutation(ALERTS, "groups.length >= GROUP_LIMIT", "groups.length >= 1",
          expect=ALERT_LISTS + "says the list is cut at 200 groups, and only then")
@mutation(ALERT_DETAIL, 'v-if="detail.items.length >= ROW_LIMIT" class="ad__foot"',
          'v-if="detail.items.length > ROW_LIMIT" class="ad__foot"',
          expect=ALERT_DETAIL_SPEC + "says the detail is cut at 100 rows, and only then")
@mutation(ALERT_DETAIL, 'v-if="detail.items.length >= ROW_LIMIT" class="ad__foot"',
          'v-if="detail.items.length >= 1" class="ad__foot"',
          expect=ALERT_DETAIL_SPEC + "says the detail is cut at 100 rows, and only then")
@mutation(ALERT_DETAIL, 'v-if="detail.items.length >= ROW_LIMIT" class="ad-foot"',
          'v-if="detail.items.length > ROW_LIMIT" class="ad-foot"',
          expect=ALERT_SMALL + "a phone says the detail is cut at 100 rows, and only then")
@mutation(ALERT_DETAIL, 'v-if="detail.items.length >= ROW_LIMIT" class="ad-foot"',
          'v-if="detail.items.length >= 1" class="ad-foot"',
          expect=ALERT_SMALL + "a phone says the detail is cut at 100 rows, and only then")
@mutation(ALERTS, "const locked = computed(() => writing.value || loading.value > 0);",
          "const locked = computed(() => writing.value);",
          expect=GUARD + "while a view loads, Acknowledge is locked but the filters are not")
@mutation(ALERTS, 'class="kla-btn kla-btn--small g-ack"\n              :disabled="locked"',
          'class="kla-btn kla-btn--small g-ack"\n              :disabled="writing"',
          expect=GUARD + "while a view loads, Acknowledge is locked but the filters are not")
@mutation(ALERTS, 'aria-label="Status"\n            :disabled="writing"',
          'aria-label="Status"\n            :disabled="false"',
          expect=IN_FLIGHT)
@mutation(ALERTS, 'aria-label="Kind"\n            :disabled="writing"',
          'aria-label="Kind"\n            :disabled="false"',
          expect=IN_FLIGHT)
@mutation(ALERTS, '              :disabled="writing"\n              :aria-label="',
          '              :disabled="false"\n              :aria-label="', expect=IN_FLIGHT)
@mutation(ALERT_DETAIL, '<button type="button" class="kla-btn" :disabled="writing" @click="emit(\'close\')">',
          '<button type="button" class="kla-btn" :disabled="false" @click="emit(\'close\')">', expect=IN_FLIGHT)
@mutation(ALERT_DETAIL, 'class="kla-btn kla-btn--primary"\n          :disabled="locked"',
          'class="kla-btn kla-btn--primary"\n          :disabled="false"', expect=IN_FLIGHT)
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


# ---- Alerts: the group rows, the chips, the live card, the tablet and the phone -------------------------------------
DIMS = ALERT_LISTS + "dims a group with nothing open, marks the opened one, and counts the groups"


@mutation(ALERTS, 'row.open_count === 0 ? "a-dim" : ""', 'row.open_count !== 0 ? "a-dim" : ""', expect=DIMS)
@mutation(ALERTS, 'isOpened(row) ? "a-selected" : ""', '!isOpened(row) ? "a-selected" : ""', expect=DIMS)
@mutation(ALERTS, 'groups.value.length === 1 ? "group" : "groups"', 'groups.value.length === 2 ? "group" : "groups"',
          expect=DIMS)
@mutation(ALERTS, '<p v-else class="live__none">No live condition right now.</p>',
          '<p v-else class="live__none">Nothing.</p>',
          expect=ALERT_LISTS + "names the live conditions as the app says them and says so when there are none")
@mutation(ALERTS, '<span v-if="isPhone && updatedText" class="a-updated kla-mono">',
          '<span v-if="updatedText" class="a-updated kla-mono">',
          expect=ALERT_LISTS + "asks for the open groups, 200 of them, with the live conditions beside")
@mutation(ALERTS, '<button v-if="!isPhone" type="button" class="kla-btn" :disabled="loading > 0" @click="reload">',
          '<button v-if="true" type="button" class="kla-btn" :disabled="loading > 0" @click="reload">',
          expect=ALERT_SMALL + "a phone has an icon Refresh and the Updated line under the header, and one block per "
                               "group")
@pytest.mark.web_unit
def test_the_alerts_view_marks_and_counts_its_groups():
    vitest(ALERTS_VIEW_SPEC)


@mutation(ALERT_DETAIL, 'unclear ? "Group summary and reply rows" : "Group summary and rows"',
          'unclear ? "Group summary and rows" : "Group summary and reply rows"',
          expect=ALERT_SMALL + "a phone opens the detail right under its group and closes it from the "
                               "Acknowledge-less region")
@mutation(ALERT_DETAIL, ':status="outcomeStatus(row.job_status)"', ':status="row.job_status"',
          expect=ALERT_DESIGN + "a failed job reads refunded, a done one done, and its code sits under the chip")
@mutation(ALERT_DETAIL, "`${i.acknowledged_by}, ${fmtTime(i.acknowledged_ms)}`", "`${i.acknowledged_by}`",
          expect=ALERT_DESIGN + "an acknowledged row shows who and when instead of not yet")
@mutation(ALERT_DETAIL, '<p v-if="unclear" class="ad__sentence">', '<p v-if="true" class="ad__sentence">',
          expect=ALERT_DESIGN + "a billing group has no reply sentence and no delivered line")
@mutation(ALERT_DETAIL, 'v-if="detail.open_count > 0"\n          type="button"',
          'v-if="true"\n          type="button"',
          expect=ALERT_DESIGN + "an opened group with nothing open has no Acknowledge in its detail")
@mutation(ALERTS, '"Open / Rows"', '"Open"',
          expect=ALERT_SMALL + "a tablet shows Severity, Alert and Open / Rows, with the kind, provider and span "
                               "under the title")
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


# ---- the day totals and the names in the alert detail ------------------------------------------------------------
TOTALS = f"FAILED {LOGS_VIEW_SPEC} > day totals while another answer is on its way > "
NAMES = f"FAILED {ALERTS_VIEW_SPEC} > job titles and key labels in the detail > "


@mutation(WINDOW, "status: filters.status, tz: viewerZone() }", "status: filters.status }",
          expect=QUERY + "no filters: the page and the status only")
@mutation(WINDOW, "${two(d.getMonth() + 1)}-${two(d.getDate())}", "${two(d.getMonth())}-${two(d.getDate())}",
          expect=HEADERS + "a day's key is its local date with two digits for month and day")
@mutation(WINDOW, "const n = total ?? shown;", "const n = shown;",
          expect=HEADERS + "the note is the day's total from the server, else the rows on the page, in the same words")
@mutation(WINDOW, "${thousands(n)} ${n === 1", "${n} ${n === 1",
          expect=HEADERS + "the note is the day's total from the server, else the rows on the page, in the same words")
@mutation(WINDOW, 'n === 1 ? "request"', 'n === 2 ? "request"',
          expect=HEADERS + "the note is the day's total from the server, else the rows on the page, in the same words")
@pytest.mark.web_unit
def test_the_day_headers_are_asked_for_in_the_viewers_zone_and_worded_right():
    vitest(WINDOW_SPEC)


@mutation(LOGS, "dayTotals.value = out.day_totals ?? {};", "dayTotals.value = {};",
          expect=LOGS_LIST + "a header shows the day's total from the server, not the rows of the page")
@mutation(LOGS, "if (!key && item.at_ms) key = dayKey(item.at_ms);",
          "if (false && item.at_ms) key = dayKey(item.at_ms);",
          expect=LOGS_LIST + "a header shows the day's total from the server, not the rows of the page")
@mutation(LOGS, "  dayTotals.value = {};\n  void load();", "  void load();",
          expect=LOGS_LIST + "a new search drops the old day total at once: the header counts the page's rows until "
                             "the answer lands")
@mutation(LOGS, "    dayTotals.value = {};\n    offset.value = 0;", "    offset.value = 0;",
          expect=TOTALS + "Clear logs drops the old total at once: the data is gone, so are its totals")
@mutation(LOGS, "  const ticket = latest.next();\n  loading.value = true;",
          "  const ticket = latest.next();\n  dayTotals.value = {};\n  loading.value = true;",
          expect=TOTALS + "a page move keeps the total of the same filters while the next page loads")
@pytest.mark.web_unit
def test_a_day_header_uses_the_servers_day_total():
    vitest(LOGS_VIEW_SPEC)


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
FAILED_LOAD = f"FAILED {WINDOW_SPEC} > a failed load > "
EMPTY_FILTERED = ("an empty list under a filter says so; Clear filters clears every filter and searches from the "
                  "first page")


@mutation(WINDOW, "`Could not load the request logs: ${why}.`", "why",
          expect=FAILED_LOAD + "is worded as the drawn error: what failed, then what the relay answered")
@mutation(WINDOW, "err instanceof ApiError\n      ?", "false\n      ?",
          expect=FAILED_LOAD + "is worded as the drawn error: what failed, then what the relay answered")
@mutation(WINDOW, "said.charAt(0).toLowerCase() + said.slice(1)", "said",
          expect=FAILED_LOAD + "with no answer from the relay, the app's own words follow, lower-cased")
@pytest.mark.web_unit
def test_a_failed_logs_load_is_worded_as_drawn():
    vitest(WINDOW_SPEC)


@mutation(LOGS, "error.value = loadFailedText(err, errorText(err));", "error.value = errorText(err);",
          expect=LOGS_LIST + "a failed load says what failed and what the relay answered")
@mutation(LOGS, 'filters.q.trim() || filters.status !== "all" || filters.from', "filters.q.trim() || filters.from",
          expect=LOGS_LIST + EMPTY_FILTERED)
@mutation(LOGS, 'filters.q.trim() || filters.status !== "all"', 'filters.q.trim() || filters.status !== "x"',
          expect=LOGS_LIST + "an empty list with no filter says there are no requests yet, and offers nothing")
@mutation(LOGS, 'const SKELETON_COLUMNS = ["time", "status", "route", "duration"];',
          'const SKELETON_COLUMNS = ["time", "status", "route", "key", "duration"];',
          expect=LOGS_LIST + "the loading skeleton draws four columns")
@mutation(LOGS, 'skeleton: "pill" as const', 'skeleton: "text" as const',
          expect=LOGS_LIST + "the loading skeleton draws four columns")
@mutation(LOGS, 'const PHONE_SKELETON_COLUMNS = ["time", "status", "route"];',
          'const PHONE_SKELETON_COLUMNS = ["time", "status", "route", "duration"];',
          expect=LOGS_LIST + "a phone's loading skeleton leaves the duration out")
@mutation(LOGS, "  filters.reset();\n  search();", "  search();", expect=LOGS_LIST + EMPTY_FILTERED)
@mutation(LOGS, "  filters.reset();\n  search();", "  filters.reset();", expect=LOGS_LIST + EMPTY_FILTERED)
@pytest.mark.web_unit
def test_the_logs_list_words_its_failure_and_its_empty_list_as_drawn():
    vitest(LOGS_VIEW_SPEC)
