"""Mutation markers for the admin web core: the money-adjacent regenerate key, command polling, the 401 handling,
the /admin header boundary, the media-URL rule, sign-out and the stale-answer counter. Each mutant must turn its Vitest
spec red; `webunit.vitest` runs the spec and the runner (scripts/mutate.py) does the mutating. A plain `check.sh`
deselects these tests."""

import pytest
from webunit import vitest

from relay_core.testing.mutation import mutation

SRC = "apps/admin-web/src"
KEY = f"{SRC}/lib/operation-key.ts"
CLIENT = f"{SRC}/api/client.ts"
POLL = f"{SRC}/composables/use-command-poll.ts"
AUTH = f"{SRC}/stores/auth.ts"


# Each operation-key marker expects the exact failed test, as tests/webunit.py lists it after a red run.
KEY_SPEC = "operation-key.spec.ts"
KEY_FAILED = "FAILED tests/unit/operation-key.spec.ts > "
THE_KEY = KEY_FAILED + "the key > "
ACTIONS = KEY_FAILED + "actions > "
SAVED = KEY_FAILED + "saved before it is sent > "
ANSWER = KEY_FAILED + "keep or drop after the answer > "
FLIGHT = KEY_FAILED + "single flight per action > "
SIGN_OUT = KEY_FAILED + "reload and sign-out > "
READ_ONLY = KEY_FAILED + "storage that can be read but not written > "
ABANDON = KEY_FAILED + "abandoning a kept key > "
SURFACE = KEY_FAILED + "module surface > "
NOT_AN_OBJECT = SAVED + ("replaces a stored record that is JSON but not an object, "
                         "and saves the new key before sending it")


# ---- regenerate operation key: the key itself and the action names -----------------------------------------------
@mutation(KEY, "new Uint8Array(16)", "new Uint8Array(8)",
          expect=THE_KEY + "is 32 hex characters built from 16 random bytes")
@mutation(KEY, 'padStart(2, "0")', 'padStart(1, "0")', expect=THE_KEY + "pads small bytes to two characters")
@mutation(KEY, '.join("")', '.join("-")', expect=THE_KEY + "is 32 hex characters built from 16 random bytes")
@mutation(KEY, "crypto.getRandomValues(new Uint8Array(16))", "new Uint8Array(16).fill(7)",
          expect=THE_KEY + "comes from the browser's random source: two actions get two different keys")
@mutation(KEY, "[...new Set(jobIds)].sort()", "[...new Set(jobIds)]",
          expect=ACTIONS + "names a bulk action by its sorted distinct ids and a job action by its id")
@mutation(KEY, "new Set(jobIds)", "jobIds",
          expect=ACTIONS + "names a bulk action by its sorted distinct ids and a job action by its id")
@mutation(KEY, '.join(",")', '.join(";")',
          expect=ACTIONS + "names a bulk action by its sorted distinct ids and a job action by its id")
@mutation(KEY, "`bulk:${", "`bulks:${", expect=ACTIONS + "takes over a key the old admin stored under the same names")
@mutation(KEY, "`job:${jobId}`", "`jb:${jobId}`",
          expect=ACTIONS + "takes over a key the old admin stored under the same names")
@mutation(KEY, "return { key: stored, made: false };", "",
          expect=ANSWER + "a 503 then a success: the retry sends the same key and the success drops it")
@mutation(KEY, 'typeof stored === "string" && SERVER_KEY.test(stored)', "stored",
          expect=SAVED + "replaces a stored key the server would refuse and reuses one it accepts")
@mutation(KEY, 'typeof stored === "string" && ', "",
          expect=SAVED + "replaces a stored key the server would refuse and reuses one it accepts")
@mutation(KEY, "{16,64}", "{32,64}",
          expect=SAVED + "replaces a stored key the server would refuse and reuses one it accepts")
@pytest.mark.web_unit
def test_operation_key_value_and_names():
    vitest("tests/unit/" + KEY_SPEC)


# ---- regenerate operation key: saved (and read back) before it is sent -------------------------------------------
@mutation(f"{SRC}/lib/session.ts", '"regenerateOperations"', '"regenOps"',
          expect=SAVED + "saves a new key under regenerateOperations before the request goes out")
@mutation(KEY, "save({ ...operations, [action]: key });", "",
          expect=SAVED + "saves a new key under regenerateOperations before the request goes out")
@mutation(KEY, "save({ ...operations, [action]: key });\n"
               "  if (load()[action] !== key) throw new OperationKeyStorageError();",
          "try {\n    save({ ...operations, [action]: key });\n  } catch {\n    /* memory only */\n  }",
          expect=SAVED + "after a failed save and a reload nothing was sent, and the retry sends one new key")
@mutation(KEY, "JSON.stringify(operations));\n  } catch (err) {\n    throw new OperationKeyStorageError(err);",
          "JSON.stringify(operations));\n  } catch {\n    /* swallowed */",
          expect=SAVED + "sends nothing and says so when the key cannot be saved")
@mutation(KEY, "if (load()[action] !== key) throw new OperationKeyStorageError();", "",
          expect=SAVED + "sends nothing when the browser drops the write without an error")
@mutation(KEY, "text = sessionStorage.getItem(OPERATIONS_KEY);\n"
               "  } catch (err) {\n    throw new OperationKeyStorageError(err);",
          "text = sessionStorage.getItem(OPERATIONS_KEY);\n  } catch {\n    text = null;",
          expect=SAVED + "sends nothing and rewrites nothing when storage cannot be read")
@mutation(KEY, "} catch {\n    /* not JSON: counts as empty */", "} catch (err) {\n    throw err;",
          expect=SAVED + "treats a stored record that is not JSON as empty, and saves the new key before sending it")
@mutation(KEY, "parsed !== null", "true", expect=NOT_AN_OBJECT)
@mutation(KEY, 'typeof parsed === "object" && ', "", expect=NOT_AN_OBJECT)
@pytest.mark.web_unit
def test_operation_key_saved_before_send():
    vitest("tests/unit/" + KEY_SPEC)


# ---- regenerate operation key: keep, drop, flag ------------------------------------------------------------------
@mutation(KEY, "err.status < 500", "err.status <= 500",
          expect=ANSWER + "keeps the key on a 5xx and flags the error retry-safe")
@mutation(KEY, "err.status < 500", "err.status < 400", expect=ANSWER + "drops a new key on any answer below 500")
@mutation(KEY, "err instanceof NetworkError ||", "false ||",
          expect=ANSWER + "keeps the key on a network error and flags it retry-safe")
@mutation(KEY, "err.status >= 500", "err.status > 500",
          expect=ANSWER + "keeps the key on a 5xx and flags the error retry-safe")
@mutation(KEY, "err.retrySafe = true;", "err.retrySafe = false;",
          expect=ANSWER + "keeps the key on a 5xx and flags the error retry-safe")
@mutation(KEY, "else if (err instanceof NetworkError ||", "else if (true ||",
          expect=ANSWER + "does not call any other failure retry-safe, and rethrows it as is")
@mutation(KEY, "if (err instanceof ApiError && err.status < 500) {",
          "if (!(err instanceof ApiError) || err.status < 500) {",
          expect=ANSWER + "keeps the key for any other failure")
@mutation(KEY, "drop(action);\n    return result;", "return result;",
          expect=ANSWER + "drops the key after a success, so the next click is a new action")
@mutation(KEY, "if (made) drop(action);", "drop(action);",
          expect=ANSWER + "a 4xx on a retry after a 5xx keeps the key, so jobs made under it are not made again")
@mutation(KEY, "return { key: stored, made: false };", "return { key: stored, made: true };",
          expect=ANSWER + "a 4xx on a retry after a 5xx keeps the key, so jobs made under it are not made again")
@mutation(KEY, "return { key, made: true };", "return { key, made: false };",
          expect=ANSWER + "drops a new key on any answer below 500")
@mutation(KEY, "delete operations[action];", "", expect=ANSWER + "removes only the dropped action from storage")
@mutation(KEY, "dropped.add(action);", 'dropped.add(action);\n    throw new Error("drop failed");',
          expect=ANSWER + "reports a success even when the key cannot be removed afterwards")
@pytest.mark.web_unit
def test_operation_key_keep_drop_flag():
    vitest("tests/unit/" + KEY_SPEC)


# ---- regenerate operation key: single flight, sign-out, storage that refuses writes ------------------------------
@mutation(KEY, "if (running) return running as Promise<T>;", "",
          expect=FLIGHT + "a second call during flight sends nothing and gets the same success")
@mutation(KEY, "inFlight.set(action, run);", "",
          expect=FLIGHT + "a second call during flight sends nothing and gets the same success")
@mutation(KEY, ".finally(() => inFlight.delete(action))", ".finally(() => undefined)",
          expect=FLIGHT + "a click after the flight ended sends a new request")
@mutation(f"{SRC}/lib/session.ts", "sessionStorage.removeItem(TEST_KEY);",
          "sessionStorage.removeItem(TEST_KEY);\n    sessionStorage.removeItem(OPERATIONS_KEY);",
          expect=SIGN_OUT + "a kept key survives a 401 sign-out and re-login: keys are not cleared with the session")
@mutation(KEY, "dropped.add(action);", "",
          expect=READ_ONLY + "a key dropped after a success is not sent again from the stale stored copy")
@mutation(KEY, "!dropped.has(action) && ", "",
          expect=READ_ONLY + "a key dropped after a success is not sent again from the stale stored copy")
@mutation(KEY, "dropped.delete(action);", "",
          expect=READ_ONLY + "once writes work again, a new key is saved and kept as usual")
@pytest.mark.web_unit
def test_operation_key_flight_sign_out_and_read_only_storage():
    vitest("tests/unit/" + KEY_SPEC)


# ---- regenerate operation key: explicit abandon, the public surface, the storage error ---------------------------
@mutation(KEY, "if (inFlight.has(action)) return false;", "",
          expect=ABANDON + "is refused while the action is in flight, and the key stays")
@mutation(KEY, "  drop(action);\n  return true;", "  return true;",
          expect=ABANDON + "drops the kept key only when asked, so the next click sends a new key")
@mutation(KEY, "function keyFor(", "export function keyFor(",
          expect=SURFACE + "exports only the regenerate call, the action names, abandon and the storage error")
@mutation(KEY, "could not save the retry key, so the regenerate was not sent.", "could not save the retry key.",
          expect=SURFACE + "the storage error has its exact message, name and cause")
@mutation(KEY, 'this.name = "OperationKeyStorageError";', "",
          expect=SURFACE + "the storage error has its exact message, name and cause")
@mutation(KEY, 'was not sent.", { cause });', 'was not sent.");',
          expect=SURFACE + "the storage error has its exact message, name and cause")
@pytest.mark.web_unit
def test_operation_key_abandon_and_surface():
    vitest("tests/unit/" + KEY_SPEC)


# ---- command polling -------------------------------------------------------------------------------------------
POLL_FAILED = "FAILED tests/unit/use-command-poll.spec.ts > useCommandPoll > "


@mutation(POLL, "POLL_MAX_TRIES = 90", "POLL_MAX_TRIES = 89", expect=POLL_FAILED + "polls every second up to 90 times")
@mutation(POLL, "POLL_INTERVAL_MS = 1000", "POLL_INTERVAL_MS = 500",
          expect=POLL_FAILED + "polls every second up to 90 times")
@mutation(POLL, 'command.status === "done"', 'command.status === "dne"',
          expect=POLL_FAILED + "stops at done with a success toast, one second between asks")
@mutation(POLL, " - ${command.result.error}", " ${command.result.error}",
          expect=POLL_FAILED + "stops at failed with the worker error in the toast")
@mutation(POLL, ": still running", ": running",
          expect=POLL_FAILED + "gives up after 90 asks with an info toast and returns null")
@pytest.mark.web_unit
def test_command_polling_rules():
    vitest("tests/unit/use-command-poll.spec.ts")


# ---- client: header, boundary, errors, 401, raw ------------------------------------------------------------------
REQUEST = "FAILED tests/unit/client.spec.ts > request > "
BOUNDARY = "FAILED tests/unit/client.spec.ts > the /admin boundary > "
ON_401 = "FAILED tests/unit/client.spec.ts > 401 > "
RAW = "FAILED tests/unit/client.spec.ts > requestRaw > "
ERROR_TEXT = "FAILED tests/unit/client.spec.ts > errorText > "


@mutation(CLIENT, "clearSession();", "void 0;",
          expect=ON_401 + "clears the token, the user and the Test key, runs the handler once and throws")
@mutation(CLIENT, "onUnauthorized?.();", "void 0;",
          expect=ON_401 + "clears the token, the user and the Test key, runs the handler once and throws")
@mutation(CLIENT, "response.status === 401 && !options.noRedirect", "response.status === 401",
          expect=ON_401 + "does nothing but throw for a call marked no-redirect (the login call)")
@mutation(CLIENT, "getToken() === sentToken", "true",
          expect=ON_401 + "does not end a newer session when a slow answer for the old one arrives")
@mutation(CLIENT, '"X-Admin-Session": sentToken', '"X-Admin-Session": ""',
          expect=REQUEST + "sends the session header read from sessionStorage on every call")
@mutation(CLIENT, "?.error ? body : SESSION_ENDED", "?.error ? SESSION_ENDED : SESSION_ENDED",
          expect=ON_401 + "clears the token, the user and the Test key, runs the handler once and throws")
@mutation(CLIENT, 'if (!path.startsWith("/") ||', "if (false ||",
          expect=BOUNDARY + 'refuses the non-path "" before anything is sent')
@mutation(CLIENT, 'path.startsWith("//")', "false",
          expect=BOUNDARY + "refuses the protocol-relative //evil.example/x with no fetch")
@mutation(CLIENT, "this.status = status;", "this.status = 0;", expect=REQUEST + "shows the server message of a 429")
@mutation(CLIENT, "!url.pathname.startsWith(`${BASE}/`)", "false",
          expect=BOUNDARY + "refuses /../x before anything is sent")
@mutation(CLIENT, "throw new NetworkError(err);", "throw err;",
          expect=REQUEST + "turns a failed fetch into a NetworkError that keeps the cause")
@mutation(CLIENT, 'this.param = envelope.param || "";', 'this.param = "";',
          expect=REQUEST + "turns the error envelope into an ApiError")
@mutation(CLIENT, 'this.code = envelope.code || "";', 'this.code = "";',
          expect=REQUEST + "turns the error envelope into an ApiError")
@mutation(CLIENT, "super(envelope.message || `HTTP ${status}`);", "super(`HTTP ${status}`);",
          expect=REQUEST + "shows the server message of a 429")
@mutation(CLIENT, "if (!response.ok) throw new ApiError(response.status, await readBody(response));", "",
          expect=RAW + "throws an ApiError for a non-ok answer")
@mutation(f"{SRC}/lib/errors.ts", "if (err instanceof OperationKeyStorageError) return err.message;", "",
          expect=ERROR_TEXT + "shows the storage error's own message")
@mutation(f"{SRC}/lib/errors.ts", "Retry the same selection in this tab;", "Retry the same selection;",
          expect=ERROR_TEXT + "says retrying is safe for an error flagged retrySafe")
@pytest.mark.web_unit
def test_client_rules():
    vitest("tests/unit/client.spec.ts")


# ---- sign-out and the route to login ----------------------------------------------------------------------------
AUTH_FAILED = "FAILED tests/unit/stores.spec.ts > auth store > "


@mutation(AUTH, "setSession(session.token, username);", "void 0;",
          expect=AUTH_FAILED + "login stores the session under the old key names")
@mutation(AUTH, "for (const hook of [...hooks]) {", "for (const hook of [...hooks].slice(0, 1)) {",
          expect=AUTH_FAILED + "signOut clears token, user and Test key and runs every hook")
@mutation(AUTH, 'console.error("sign-out hook failed", err);', "throw err;",
          expect=AUTH_FAILED + "a hook that throws does not stop the others")
@mutation(AUTH, 'token.value = "";', "",
          expect=AUTH_FAILED + "signOut clears token, user and Test key and runs every hook")
@pytest.mark.web_unit
def test_sign_out_rules():
    vitest("tests/unit/stores.spec.ts")


ROUTE_TO_LOGIN = ("FAILED tests/unit/unauthorized.spec.ts > a 401 on any call > "
                  "clears the session, runs the sign-out hooks once and routes to login")


@mutation(f"{SRC}/api/unauthorized.ts", 'void router.replace({ name: "login" });', "void 0;", expect=ROUTE_TO_LOGIN)
@mutation(f"{SRC}/api/unauthorized.ts", "useAuthStore().signOut();", "void 0;", expect=ROUTE_TO_LOGIN)
@pytest.mark.web_unit
def test_unauthorized_routes_to_login():
    vitest("tests/unit/unauthorized.spec.ts")


# ---- media URL safety -----------------------------------------------------------------------------------------
MEDIA_FAILED = "FAILED tests/unit/safe-media-url.spec.ts > safeMediaUrl > "


@mutation(f"{SRC}/lib/safe-media-url.ts", 'startsWith("/m/")', 'startsWith("/")',
          expect=MEDIA_FAILED + "refuses a same-origin path outside /m/, including one that climbs out of it")
@mutation(f"{SRC}/lib/safe-media-url.ts", "parsed.origin !== location.origin", "false",
          expect=MEDIA_FAILED + "refuses another host, a protocol-relative URL and a look-alike host")
@pytest.mark.web_unit
def test_safe_media_url_rules():
    vitest("tests/unit/safe-media-url.spec.ts")


# ---- stale-answer counter and the sign-in guard -----------------------------------------------------------------
@mutation(f"{SRC}/composables/use-latest.ts", "ticket === generation", "true",
          expect="FAILED tests/unit/use-latest.spec.ts > useLatest > only the newest ticket is current")
@pytest.mark.web_unit
def test_use_latest_rule():
    vitest("tests/unit/use-latest.spec.ts")


@mutation(f"{SRC}/router/index.ts", "!getToken()", "false",
          expect="FAILED tests/unit/router.spec.ts > routes > "
                 "sends a signed-out visitor to login from every tab, and lets login through")
@pytest.mark.web_unit
def test_sign_in_guard():
    vitest("tests/unit/router.spec.ts")


# ---- the attention banner -----------------------------------------------------------------------------------------
BANNER = f"{SRC}/components/layout/AttentionBanner.vue"
SHELL_FAILED = "FAILED tests/unit/app-shell.spec.ts > the shell > "


@mutation(BANNER, "alerts.value.length > 0 || stats.openGroups > 0", "true",
          expect=SHELL_FAILED + "shows no banner when nothing needs attention")
@mutation(BANNER, "stats.openGroups === 1 ?", "stats.openGroups === 2 ?",
          expect=SHELL_FAILED + "says group, not groups, for one open group")
@mutation(BANNER, 'stats.openGroups === 200 ? "200+"', 'stats.openGroups === 200 ? "200"',
          expect=SHELL_FAILED + "writes 200+ for a full page of open groups and opens Alerts from the banner")
@mutation(BANNER, ': "Needs attention."', ': "Attention."',
          expect=SHELL_FAILED + "with live conditions only, says Needs attention and still offers the link to Alerts")
@mutation(BANNER, '<router-link class="banner-link"', '<router-link v-if="stats.openGroups > 0" class="banner-link"',
          expect=SHELL_FAILED + "with live conditions only, says Needs attention and still offers the link to Alerts")
@mutation(BANNER, "Review in Alerts", "Review in alerts",
          expect=SHELL_FAILED + "shows the live alerts and the open group count, 200 as 200+")
@mutation(BANNER, "return rest.length ? `${first.message} (+${rest.length} more)` : first.message;",
          "return first.message;", expect=SHELL_FAILED + "with several live conditions shows the first and the count "
                                                           "of the rest")
@mutation(BANNER, "if (!first) return \"\";", "if (!first) return \"x\";",
          expect=SHELL_FAILED + "draws no detail when only open groups exist")
@mutation(BANNER, "background: var(--rl-warn-bg);", "background: var(--rl-bad-bg);",
          expect=SHELL_FAILED + "draws the banner with a warn fill and no side border")
@mutation(f"{SRC}/components/layout/AppShell.vue", "() => notify.clear(),", "() => undefined,",
          expect=SHELL_FAILED + "closes every toast when the tab changes, and not before")
@mutation(f"{SRC}/App.vue", "<JobDetailDrawer />", "<!-- no drawer -->",
          expect=SHELL_FAILED + "mounts the job detail through JobDetailDrawer, once, while signed in only")
@pytest.mark.web_unit
def test_the_banner_rules():
    vitest("tests/unit/app-shell.spec.ts")


# ---- the top bar: the behaviours the old header and tab bar guarded, now in TopBar --------------------------------
TOP_BAR = f"{SRC}/components/layout/TopBar.vue"
ALL_FILTER = SHELL_FAILED + ("shows All in the provider trigger, lists All then the catalog's providers "
                             "and keeps the store's empty string for All")
POPOVERS = SHELL_FAILED + "closes an open popover on Escape and on a click outside, and keeps one open at a time"
SIGN_OUT_ITEM = SHELL_FAILED + "signs out through the menu item: server revoke first, then every sign-out hook"
BAR_FAILED = "FAILED tests/unit/app-shell.spec.ts > the top bar > "
PROVIDER_ORDER_TEST = SHELL_FAILED + "lists the providers in the drawn order, dola, vibes, meta, then any other sorted"
SIGN_OUT_ITEM_ESC = SHELL_FAILED + (
    "opens a popup with ArrowDown on its trigger, puts the focus on the first menu item and returns it on Escape")
WALK = SHELL_FAILED + ("moves the focus into the listbox on open, onto the chosen option, and walks it with "
                       "Arrow, Home and End")
LAYOUT = BAR_FAILED + ("is one row on a desktop, and two rows on a tablet and on a phone, "
                       "with the user's name only on the desktop")
CURRENT_COUNT = BAR_FAILED + "marks the count of the current tab as primary and the Alerts count as warn"


@mutation(TOP_BAR, "stats.data.jobs.in_progress + stats.data.jobs.queued", "stats.data.jobs.in_progress",
          expect=SHELL_FAILED + "counts the unfinished jobs on the Jobs tab and the open groups on Alerts, "
                 "only when above zero")
@mutation(TOP_BAR, "computed(() => scope.provider || ALL)", "computed(() => scope.provider)", expect=ALL_FILTER)
@mutation(TOP_BAR, 'scope.provider = name === ALL ? "" : name;', "scope.provider = name;", expect=ALL_FILTER)
@mutation(TOP_BAR, ':aria-selected="name === providerLabel"', ':aria-selected="name !== providerLabel"',
          expect=ALL_FILTER)
@mutation(TOP_BAR, 'if (event.key !== "Escape" || !open.value) return;\n  event.preventDefault();',
          'if (event.key !== "Escape" || !open.value) return;', expect=SIGN_OUT_ITEM_ESC)
@mutation(TOP_BAR, 'if (event.key !== "Escape" || !open.value) return;', 'if (event.key !== "Escape") return;',
          expect=SIGN_OUT_ITEM_ESC)
@mutation(TOP_BAR, 'if (event.key !== "Escape" || !open.value) return;',
          'if (event.key !== "Enter" || !open.value) return;', expect=POPOVERS)
@mutation(TOP_BAR, "wrap && !wrap.contains(target)", "wrap && wrap.contains(target)", expect=POPOVERS)
@mutation(TOP_BAR, "const opening = open.value !== which;", "const opening = true;", expect=POPOVERS)
@mutation(TOP_BAR, 'const PROVIDER_ORDER = ["dola", "vibes", "meta"];',
          'const PROVIDER_ORDER = ["dola", "meta", "vibes"];',
          expect=PROVIDER_ORDER_TEST)
@mutation(TOP_BAR, "(items.find((el) => el.getAttribute(\"aria-selected\") === \"true\") ?? items[0])?.focus();",
          "items[0]?.focus();", expect=SHELL_FAILED + "opens the listbox on the chosen option when one is set, and "
                                                      "gives the focus back to the trigger on Escape and on a pick")
@mutation(TOP_BAR, "  Home: 0,", "  Home: 1,", expect=WALK)
@mutation(TOP_BAR, "  End: items.length - 1,", "  End: 0,", expect=WALK)
@mutation(TOP_BAR, "ArrowDown: at + 1,", "ArrowDown: at + 2,", expect=WALK)
@mutation(TOP_BAR, "ArrowUp: at - 1,", "ArrowUp: at - 2,", expect=WALK)
@mutation(TOP_BAR, "  if (target === undefined) return;\n  event.preventDefault();",
          "  if (target === undefined) return;", expect=WALK)
@mutation(TOP_BAR, 'event.key !== "ArrowDown" || open.value === which',
          'event.key !== "ArrowUp" || open.value === which',
          expect=SHELL_FAILED + "opens a popup with ArrowDown on its trigger, puts the focus on the first menu item "
                                "and returns it on Escape")
@mutation(TOP_BAR, 'wrapOf(which)?.querySelector<HTMLElement>("button")?.focus();', "void which;",
          expect=SHELL_FAILED + "opens the listbox on the chosen option when one is set, and gives the focus back "
                                "to the trigger on Escape and on a pick")
@pytest.mark.web_unit
def test_the_top_bar_keeps_the_count_the_provider_and_the_popover_rules():
    vitest("tests/unit/app-shell.spec.ts")


@mutation(TOP_BAR, "'tab-count--current': route.name === tab.name && tab.name !== 'alerts',",
          "'tab-count--current': route.name !== tab.name && tab.name !== 'alerts',", expect=CURRENT_COUNT)
@mutation(TOP_BAR, "'tab-count--warn': tab.name === 'alerts',", "'tab-count--warn': tab.name === 'jobs',",
          expect=CURRENT_COUNT)
@mutation(TOP_BAR, "tab.name !== 'alerts',", "tab.name === 'alerts',", expect=CURRENT_COUNT)
@mutation(TOP_BAR, 'value > 999 ? "999+"', 'value > 1000 ? "999+"',
          expect=BAR_FAILED + "shows 999+ for a count above 999 and the number itself at 999")
@mutation(TOP_BAR, "a.available > 0", "a.available > 1",
          expect=BAR_FAILED + "shows ready over total on the Accounts tab, and nothing without accounts")
@mutation(TOP_BAR, "`${a.usable}/${a.available}`", "`${a.available}/${a.usable}`",
          expect=BAR_FAILED + "shows ready over total on the Accounts tab, and nothing without accounts")
@mutation(TOP_BAR, "await auth.logout();", "void 0;", expect=SIGN_OUT_ITEM)
@mutation(TOP_BAR, 'await router.replace({ name: "login" });', "void 0;",
          expect=SHELL_FAILED + "signs out: calls the server, drops the session and shows the login page")
@mutation(TOP_BAR, 'window.open("/reference", "_blank", "noopener")', 'window.open("/reference", "_self", "noopener")',
          expect=SHELL_FAILED + "opens the API reference in a new tab from the account menu, and nowhere else")
@mutation(TOP_BAR, "computed(() => isPhone.value || isTablet.value)", "computed(() => isPhone.value)", expect=LAYOUT)
@mutation(TOP_BAR, '<span v-if="!stacked" class="account-name"', '<span v-if="true" class="account-name"',
          expect=LAYOUT)
@mutation(f"{SRC}/components/layout/AppShell.vue", "'app-main--tablet': isTablet", "'app-main--tablet': isPhone",
          expect=LAYOUT)
@pytest.mark.web_unit
def test_the_top_bar_behaviour():
    vitest("tests/unit/app-shell.spec.ts")


# ---- the page header ---------------------------------------------------------------------------------------------
HEADER = f"{SRC}/components/layout/PageHeader.vue"
HEADER_FAILED = "FAILED tests/unit/app-shell.spec.ts > the page header > "


@mutation(HEADER, '<span v-if="$slots.meta && !isPhone" class="page-header__meta">',
          '<span v-if="$slots.meta" class="page-header__meta">',
          expect=HEADER_FAILED + "leaves the meta line out on a phone but keeps the actions beside the title")
@mutation(HEADER, '<div v-if="($slots.meta && !isPhone) || $slots.actions"', '<div v-if="true"',
          expect=HEADER_FAILED + "draws no sentence, no meta and no side when it has none")
@mutation(HEADER, '<p v-if="subtitle"', '<p v-if="true"',
          expect=HEADER_FAILED + "draws no sentence, no meta and no side when it has none")
@pytest.mark.web_unit
def test_the_page_header_rules():
    vitest("tests/unit/app-shell.spec.ts")


# ---- breakpoints, tokens, fonts and the brand ----------------------------------------------------------------------
BREAKPOINT = f"{SRC}/composables/use-breakpoint.ts"
THEME_CSS = f"{SRC}/styles/theme.css"
USE_BP = "FAILED tests/unit/theme.spec.ts > useBreakpoint > "
TOKENS = "FAILED tests/unit/theme.spec.ts > the C2 tokens > "
FONTS = "FAILED tests/unit/theme.spec.ts > the fonts > "
LOGO = "FAILED tests/unit/brand.spec.ts > the brand logo > "
BRAND_COLOURS = "FAILED tests/unit/brand.spec.ts > the colours > "
LADDER = TOKENS + "every Element Plus ladder step is set explicitly in each theme and differs between them"
LADDER_FILL = TOKENS + "the two light steps of each Element Plus ladder are the fill of its token"
QUERIES = USE_BP +"asks for phone below 768 px and tablet from 768 to 1279 px, and nothing else"


@mutation(BREAKPOINT, 'PHONE_QUERY = "(max-width: 767px)"', 'PHONE_QUERY = "(max-width: 768px)"', expect=QUERIES)
@mutation(BREAKPOINT, "(min-width: 768px) and (max-width: 1279px)", "(min-width: 768px) and (max-width: 1280px)",
          expect=QUERIES)
@mutation(BREAKPOINT, "target.value = list.matches;", "target.value = false;",
          expect=USE_BP + "is phone only for a phone width and tablet only for a tablet width")
@mutation(BREAKPOINT, "target.value = event.matches;", "target.value = false;",
          expect=USE_BP + "follows a change of the window width")
@mutation(BREAKPOINT, 'onScopeDispose(() => list.removeEventListener("change", onChange));',
          "onScopeDispose(() => undefined);",
          expect=USE_BP + "stops listening when the component is gone, and not before")
@mutation(BREAKPOINT, 'typeof window.matchMedia !== "function"', "false",
          expect=USE_BP + "is the desktop where matchMedia does not exist")
@pytest.mark.web_unit
def test_the_breakpoint_composable():
    vitest("tests/unit/theme.spec.ts")


@mutation(THEME_CSS, "--rl-control: #85758f;", "--rl-control: #85758e;",
          expect=TOKENS + "light has the value of DESIGN section 3 for every token")
@mutation(THEME_CSS, "--rl-focus: #a78bfa;", "--rl-focus: #a78bfb;",
          expect=TOKENS + "dark has the value of DESIGN section 3 for every token")
@mutation(THEME_CSS, "--rl-bad-solid: #e11d48;", "--rl-bad-solid: #e11d49;",
          expect=TOKENS + "dark has the value of DESIGN section 3 for every token")
@mutation(THEME_CSS, "--el-color-error: var(--rl-bad);", "--el-color-error: var(--rl-ok);",
          expect=TOKENS + "maps the Element Plus colours onto the status tokens, with error as an alias of danger")
@mutation(THEME_CSS, "--el-component-size: 44px;", "--el-component-size: 40px;",
          expect=TOKENS + "controls are 36 px high from the tablet width and 44 px below it")
@mutation(THEME_CSS, "--el-button-size: var(--el-component-size);", "--el-button-size: 32px;",
          expect=TOKENS + "a default button follows the control scale, a small or large one keeps its own size")
@mutation(THEME_CSS, "height: var(--el-button-size);", "height: 32px;",
          expect=TOKENS + "a default button follows the control scale, a small or large one keeps its own size")
@mutation(THEME_CSS, "--el-checkbox-input-border: 1px solid var(--rl-control);",
          "--el-checkbox-input-border: 1px solid var(--rl-line-strong);",
          expect=TOKENS + "form controls take the control border colour and the focus ring is two pixels")
@mutation(THEME_CSS, "--el-disabled-bg-color: var(--rl-raised);", "--el-disabled-bg-color: var(--rl-surface);",
          expect=TOKENS + "disabled controls are a grey fill")
@mutation(THEME_CSS, "--el-color-success-light-9: #e2f4ec;", "--el-color-success-light-9: #e2f4ed;",
          expect=LADDER_FILL)
@mutation(THEME_CSS, "--el-color-warning-light-8: rgb(251 191 36 / 13%);",
          "--el-color-warning-light-8: rgb(251 191 36 / 14%);", expect=LADDER_FILL)
@mutation(THEME_CSS, "--el-color-danger-light-8: #fde7ec;", "--el-color-danger-light-8: var(--rl-bad-bg);",
          expect=LADDER)
@mutation(THEME_CSS, "--el-color-info-light-9: rgb(185 174 201 / 12%);",
          "--el-color-info-light-9: var(--rl-neutral-bg);", expect=LADDER)
@mutation(THEME_CSS, "--el-color-primary-light-9: #2e1d4b;", "--el-color-primary-light-9: #2e1d4c;",
          expect=LADDER_FILL)
@mutation(THEME_CSS, "--el-color-error-light-9: var(--el-color-danger-light-9);",
          "--el-color-error-light-9: var(--rl-bad-bg);",
          expect=TOKENS + "maps the Element Plus colours onto the status tokens, with error as an alias of danger")
@mutation(THEME_CSS, "--el-color-danger-light-3: #d25976;", "--el-color-danger-light-3: #b45264;",
          expect=TOKENS + "every Element Plus ladder step is set explicitly in each theme and differs between them")
@mutation(THEME_CSS, "--el-border-radius-base: 10px;", "--el-border-radius-base: 6px;",
          expect=BRAND_COLOURS + "the brand radii are 10 px for controls, 16 px for cards and dialogs, 12 px for chips")
@mutation(THEME_CSS, "--el-button-hover-bg-color: var(--rl-primary-hover);",
          "--el-button-hover-bg-color: var(--rl-focus);",
          expect=BRAND_COLOURS + "the primary is the brand purple in both themes, hover #6d28d9, "
                                 "white text on a solid button")
@mutation(f"{SRC}/main.ts", "be-vietnam-pro/latin-500.css", "be-vietnam-pro/latin-300.css",
          expect=FONTS + "load the latin and vietnamese subsets of the weights the design uses, and nothing else")
@mutation(f"{SRC}/components/layout/BrandLogo.vue", 'alt="Ghidorah"', 'alt="Relay"',
          expect=LOGO + "names itself Ghidorah and is only the wide logo, whatever the width")
@mutation(f"{SRC}/components/layout/BrandLogo.vue", 'ui.theme === "dark" ? logoDark : logoLight',
          'ui.theme === "dark" ? logoLight : logoDark',
          expect=LOGO + "shows the logo with light letters in the dark theme and dark letters in the light theme")
# package.json, vite.config.ts and index.html are baked into the web image, not mounted, so a mutant there would never
# reach the run: their pins (exact font versions, assetsInlineLimit, the title) are plain assertions in the specs.
@pytest.mark.web_unit
def test_the_tokens_the_fonts_and_the_brand_are_pinned():
    vitest("tests/unit/theme.spec.ts", "tests/unit/brand.spec.ts")


# ---- the login page keeps no password, the build strips data: URLs ------------------------------------------------
LOGIN_FAILED = "FAILED tests/unit/app-shell.spec.ts > the login page > "


@mutation(f"{SRC}/views/LoginView.vue", '    password.value = "";\n', "",
          expect=LOGIN_FAILED + "clears the password after a failed sign-in, from the field and from the page's state")
@mutation(f"{SRC}/views/LoginView.vue", "    reveal.value = false;\n", "",
          expect=LOGIN_FAILED + "clears the password after a successful sign-in too")
@pytest.mark.web_unit
def test_the_login_page_clears_the_password():
    vitest("tests/unit/app-shell.spec.ts")


STRIP = f"{SRC}/styles/strip-data-urls.ts"
STRIP_FAILED = "FAILED tests/unit/strip-data-urls.spec.ts > the build step that drops data: URLs from vendor CSS > "


@mutation(STRIP, 'if (DATA_URL_IN_VALUE.test(decl.value.replace(COMMENT, ""))) decl.remove();',
          "if (false) decl.remove();",
          expect=STRIP_FAILED + "removes a declaration that carries a data: URL, quoted or not, in any case, "
                                "and keeps its neighbours")
@mutation(STRIP, "/(?:^|[\\s(,])[\"']?data:[^\\s\"')]*,/i;", "/(?:^|[\\s(,])[\"']?data:[^\\s\"')]*,/;",
          expect=STRIP_FAILED + "removes a declaration that carries a data: URL, quoted or not, in any case, "
                                "and keeps its neighbours")
@mutation(STRIP, "/(?:^|[\\s(,])[\"']?data:[^\\s\"')]*,/i;", "/data:/i;",
          expect=STRIP_FAILED + "keeps a plain url() to a file and a value that only mentions data")
@mutation(STRIP, 'decl.value.replace(COMMENT, "")', "decl.value",
          expect=STRIP_FAILED + "sees a data URL behind a comment inside the value")
@mutation(STRIP, "(?:^|[\\s(,])", "(?:^|[(])",
          expect=STRIP_FAILED + "removes a declaration whose data URL is a quoted string inside image-set(), "
                                "first or later in the list")
# vite.config.ts is baked into the web image, so the wiring test ("is wired into the PostCSS step") is a plain
# assertion on the config, not a marker.
@pytest.mark.web_unit
def test_the_build_strips_data_urls_from_vendor_css():
    vitest("tests/unit/strip-data-urls.spec.ts")
