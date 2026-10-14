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
