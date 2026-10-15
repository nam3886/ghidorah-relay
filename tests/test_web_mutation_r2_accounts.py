"""Mutation markers of the Accounts view: each breaks one rule in the TypeScript source and demands that the named
Vitest test turns red (scripts/mutate.py runs these, no rebuild)."""

import pytest
from webunit import vitest

from relay_core.testing.mutation import mutation

SRC = "apps/admin-web/src"
ACCOUNT_MODEL = SRC + "/components/accounts/account-model.ts"
ACCOUNTS = SRC + "/views/AccountsView.vue"

ACCOUNT_SPEC = "tests/unit/accounts/account-model.spec.ts"
ACCOUNTS_VIEW_SPEC = "tests/unit/accounts/accounts-view.spec.ts"

CHANGES = f"FAILED {ACCOUNT_SPEC} > accountChanges (the edit payload) > "
AUTO = f"FAILED {ACCOUNTS_VIEW_SPEC} > auto-refresh > "
CHECK_ALL = f"FAILED {ACCOUNTS_VIEW_SPEC} > Check all and Refresh catalog > "
SWITCH = f"FAILED {ACCOUNTS_VIEW_SPEC} > the enable switch > "
LIST = f"FAILED {ACCOUNTS_VIEW_SPEC} > the list > "
COMMANDS = f"FAILED {ACCOUNTS_VIEW_SPEC} > the row commands and the edit dialog > "


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


# ---- Accounts: the switch, the reason, the commands and the proxy re-check -----------------------------------------
@mutation(ACCOUNTS, 'v-if="!row.in_use"', 'v-if="true"',
          expect=LIST + "draws every server string as text, and hides the switch of an account in use")
@mutation(ACCOUNTS, '<div v-if="row.state_reason" class="sub">', '<div v-if="false" class="sub">',
          expect=LIST + "draws every server string as text, and hides the switch of an account in use")
@mutation(ACCOUNT_MODEL, 'WAKE_LABEL = "Wake: clear cooldown and strikes"', 'WAKE_LABEL = "Clear cooldown"',
          expect=COMMANDS + "Wake queues the wake command under the label that names the cooldown and the strikes")
@mutation(ACCOUNTS, 'label="Wake" @click="wake(row)"', 'label="Awake" @click="wake(row)"',
          expect=COMMANDS + "every row action is an icon button with its name, and Shot shows only when there is "
                            "a screenshot")
@mutation(ACCOUNTS, 'queue(recheckProxy(a.id), "Proxy check")', 'queue(recheckProxy(a.id), "Proxy")',
          expect=COMMANDS + "Re-check proxy closes the dialog, queues the re-check, polls Proxy check and reads "
                            "the list again")
@mutation(ACCOUNTS, 'if (out.command_id) await poll(out.command_id, "Proxy check");', "",
          expect=COMMANDS + "Save of a changed proxy polls the Proxy check the server queued, then reads the list")
@pytest.mark.web_unit
def test_the_accounts_view_commands_and_proxy_recheck():
    vitest(ACCOUNTS_VIEW_SPEC)


# ---- colour is for exceptions: the checked stamp and the counts -------------------------------------------------
AGO = (f"FAILED {ACCOUNT_SPEC} > pointsText, proxyCheck, checkSummary > ago: never, just now, minutes, hours, days; "
       "a future time is just now")
COUNTS = (f"FAILED {ACCOUNTS_VIEW_SPEC} > the list > draws every server string as text, and hides the switch of an "
          "account in use")


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
