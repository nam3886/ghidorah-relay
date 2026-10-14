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

WINDOW_SPEC = "tests/unit/logs/log-window.spec.ts"
KEY_SPEC = "tests/unit/keys/key-model.spec.ts"
ACCOUNT_SPEC = "tests/unit/accounts/account-model.spec.ts"
ACCOUNTS_VIEW_SPEC = "tests/unit/accounts/accounts-view.spec.ts"
KEYS_VIEW_SPEC = "tests/unit/keys/keys-view.spec.ts"
ALERTS_VIEW_SPEC = "tests/unit/alerts/alerts-view.spec.ts"

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
