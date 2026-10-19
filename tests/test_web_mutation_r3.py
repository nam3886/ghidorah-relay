"""Mutation markers for the Settings, Prompt frames and Test views of the admin web. Each mutant must turn the Vitest
spec it names red, with the exact failed test; `webunit.vitest` runs the spec and scripts/mutate.py does the mutating.
A plain `check.sh` deselects these tests. The rules covered:
- Test tab storage: the key only in sessionStorage and only after a model load, the form in localStorage, the image
  limits (10 files, 12 MiB, images only) on restore and before every write, the public Bearer call;
- the Generate polling (3 s, 600 tries) and its generation guard, sign-out and a 401 ending it;
- Prompt frames: the `gen` guard on answers and errors, the busy rules, the lock toggle keeping the draft, the inline
  4xx refusal, the preview and apply-all drops;
- Settings: the JSON-then-string value parse, the dirty rule, the provider scope, the password change.
The runner reads every argument as a string literal, an f-string or a sum of module constants, so the expected failed
test lines are built that way: `<spec prefix> + "<describe> > <test title>"`."""

import pytest
from webunit import vitest

from relay_core.testing.mutation import mutation

SRC = "apps/admin-web/src"
FILES = f"{SRC}/lib/test-files.ts"
FORM = f"{SRC}/lib/test-form.ts"
API_TEST = f"{SRC}/api/test.ts"
KEY = f"{SRC}/components/test/test-key.ts"
SESSION = f"{SRC}/components/test/use-test-session.ts"
GENERATE = f"{SRC}/components/test/use-generate.ts"
TEST_VIEW = f"{SRC}/views/TestView.vue"
SETTING_VALUE = f"{SRC}/components/settings/settings-value.ts"
SETTINGS_TABLE = f"{SRC}/components/settings/SettingsTable.vue"
SETTINGS_VIEW = f"{SRC}/views/SettingsView.vue"
PASSWORD = f"{SRC}/components/settings/PasswordForm.vue"
PRICES = f"{SRC}/components/settings/PricesTable.vue"
TEST_MODELS = f"{SRC}/components/test/test-models.ts"
TEST_RESULT = f"{SRC}/components/test/TestResult.vue"
TEST_RESULT_MODEL = f"{SRC}/components/test/test-result-model.ts"
TEST_IMAGES = f"{SRC}/components/test/TestImages.vue"
FRAMES = f"{SRC}/components/frames/use-frames.ts"
FRAMES_MODEL = f"{SRC}/components/frames/frames-model.ts"
FRAMES_VIEW = f"{SRC}/views/FramesView.vue"
FRAME_EDITOR = f"{SRC}/components/frames/FrameEditor.vue"

FAILED = "FAILED tests/unit/"
FILES_SPEC = FAILED + "test/test-files.spec.ts > "
FORM_SPEC = FAILED + "test/test-form.spec.ts > "
API_SPEC = FAILED + "test/test-api.spec.ts > the public calls of the Test tab > "
SESSION_SPEC = FAILED + "test/test-session.spec.ts > the API key > "
GEN_SPEC = FAILED + "test/use-generate.spec.ts > generating > "
VIEW_SPEC = FAILED + "test/test-view.spec.ts > "
SETTINGS_SPEC = FAILED + "settings/settings.spec.ts > "
FRAMES_SPEC = FAILED + "frames/use-frames.spec.ts > "
FRAMES_VIEW_SPEC = FAILED + "frames/frames-view.spec.ts > the view > "
MODEL_SPEC = FAILED + "frames/frames-model.spec.ts > frame lines > "
EMPTY_MODELS = VIEW_SPEC + ("the empty state > keeps the model column with a hint while no model is loaded, and drops "
                            "the hint once models arrive")
WALLET_BOX = VIEW_SPEC + ("the empty state > the wallet limits sit in a closed block of their own, and the optional "
                          "fields say so")
FRAME_SHAPE_SPEC = (FRAMES_VIEW_SPEC + "the frame box shows the shape of a frame as its placeholder, and the lock "
                    "line sits on the checkbox cell")

# ---- the image limits, on restore and before every write ---------------------------------------------------------
TEN = FILES_SPEC + "the limits > keeps 10 files and drops the 11th"
EDGE = FILES_SPEC + "the limits > keeps 12 MiB exactly and drops what takes the total over it by one byte"
IMAGES_ONLY = FILES_SPEC + "the limits > keeps images only, by the blob's own type"
NOT_ARRAY = FILES_SPEC + "the limits > drops a value that is not an array whole, and entries that are not files"
ATTACH = FILES_SPEC + "attaching > adds after the files already attached and names those that do not fit"
ATTACH_SKIP = FILES_SPEC + "attaching > skips a non-image and a file that takes the total over 12 MiB"
WRITE_CHECK = FILES_SPEC + "writing > checks the list before every write: the store never receives more than the limits"
RESTORE_DROP = FILES_SPEC + "restoring > drops what breaks a limit, rewrites the store with what is left and says so"
RESTORE_EMPTY = FILES_SPEC + "restoring > an empty store gives an empty list and no notice"
RESTORE_FAILED = (FILES_SPEC + "restoring > a store that cannot be read is reported as a storage failure, "
                  + "with an empty list and no write")
EMPTY_STORED = "if (stored === null || stored === undefined) return { files: [], dropped: false, failed: false };"


@mutation(FILES, "kept.length >= MAX_IMAGES", "kept.length > MAX_IMAGES", expect=TEN)
@mutation(FILES, "bytes + f.blob.size > MAX_BYTES", "bytes + f.blob.size >= MAX_BYTES", expect=EDGE)
@mutation(FILES, "|| bytes + f.blob.size > MAX_BYTES", "|| f.blob.size > MAX_BYTES", expect=EDGE)
@mutation(FILES, '|| !f.blob.type.startsWith("image/")', "|| false", expect=IMAGES_ONLY)
@mutation(FILES, "Array.isArray(list) ? list : []", "list ?? []", expect=NOT_ARRAY)
@mutation(FILES, "files.length >= MAX_IMAGES", "files.length > MAX_IMAGES", expect=ATTACH)
@mutation(FILES, "|| bytes + file.size > MAX_BYTES", "|| file.size > MAX_BYTES", expect=ATTACH_SKIP)
@mutation(FILES, '!file.type.startsWith("image/") ||', "", expect=ATTACH_SKIP)
@mutation(FILES, "const checked = validFiles(files);", "const checked = files;", expect=WRITE_CHECK)
@mutation(FILES, "if (dropped) await saveFiles(files, store);", "", expect=RESTORE_DROP)
@mutation(FILES, EMPTY_STORED, "", expect=RESTORE_EMPTY)
@mutation(FILES, "return { files: [], dropped: false, failed: true };",
          "return { files: [], dropped: false, failed: false };", expect=RESTORE_FAILED)
@pytest.mark.web_unit
def test_image_limits():
    vitest("tests/unit/test/test-files.spec.ts")


# ---- the form: localStorage, and the request body ----------------------------------------------------------------
SAVED_FORM = (FORM_SPEC
              + "the saved form > is kept under relay_test_form with the model and the eight fields, and nothing else")
BODY = FORM_SPEC + "the request body > "
BODY_KINDS = BODY + ("sends duration for a video that names its lengths and for nothing else, style for an image and "
                     "not for a video")
BODY_TRIM = BODY + "sends ratio, count as n, and title trimmed, and leaves empty ones out"
BODY_IMAGES = BODY + "sends the URL lines, then the attached files as data URIs, in that order"
BODY_OPTIONS = BODY + "parses the options as JSON and fails on text that is not JSON"
READ_FAILS = FORM_SPEC + "the saved form > reports a store that cannot be read as a failure"
WRITE_REPORTS = (FORM_SPEC + "the saved form > reports a refused write and a refused clear as failures, "
                 + "and a good one as success")
FORM_KEY = 'export const FORM_KEY = "relay_test_form";'
READ_BACK = FORM_SPEC + "the saved form > reads back what was saved"
READ_STRINGS = FORM_SPEC + "the saved form > ignores a field that is not a string, one field at a time"


@mutation(FORM, FORM_KEY, 'export const FORM_KEY = "relay_form";', expect=SAVED_FORM)
@mutation(FORM, "localStorage.setItem(FORM_KEY", "sessionStorage.setItem(FORM_KEY", expect=SAVED_FORM)
@mutation(FORM, 'model.kind === "video" && durationsOf(model).length && form.duration.trim()',
          'model.kind === "video" && form.duration.trim()', expect=BODY_KINDS)
@mutation(FORM, 'model.kind === "image" && style', "style", expect=BODY_KINDS)
@mutation(FORM, "body.n = Number(form.count)", "body.count = Number(form.count)", expect=BODY_TRIM)
@mutation(FORM, "...dataUris,", "", expect=BODY_IMAGES)
@mutation(FORM, ".map((line) => line.trim())", ".map((line) => line)", expect=BODY_IMAGES)
@mutation(FORM, "if (form.options.trim()) body.options", "if (form.options) body.options", expect=BODY_OPTIONS)
@mutation(FORM, "return { form, ok: false };", "return { form, ok: true };", expect=READ_FAILS)
@mutation(FORM, "JSON.stringify(out));\n    return true;", "JSON.stringify(out));\n    return false;",
          expect=WRITE_REPORTS)
@mutation(FORM, "return false;\n  }\n}\n\nexport function clearSavedForm",
          "return true;\n  }\n}\n\nexport function clearSavedForm", expect=WRITE_REPORTS)
@mutation(FORM, "return false;\n  }\n}\n\n// The label", "return true;\n  }\n}\n\n// The label", expect=WRITE_REPORTS)
@mutation(FORM, 'for (const field of ["model", ...FIELDS] as const)', "for (const field of FIELDS)", expect=READ_BACK)
@mutation(FORM, 'if (typeof values[field] === "string") form[field]', "if (values[field] !== undefined) form[field]",
          expect=READ_STRINGS)
@pytest.mark.web_unit
def test_form_storage_and_body():
    vitest("tests/unit/test/test-form.spec.ts")


# ---- the public call: Bearer key, no admin header, no cookies ----------------------------------------------------
BEARER = API_SPEC + "send the typed key as a Bearer header and no admin session header, without cookies"
ROUTES = API_SPEC + "use the public routes for the options, the points and both kinds of job"
REDACTED = API_SPEC + "never show the request's own key in an error text, even when the server echoes it"
ENVELOPE = API_SPEC + "fail with `code: message` from the error envelope"
NO_CODE = (API_SPEC
           + "fail with the bare message when the envelope has no code, and HTTP <status> when there is no envelope")


@mutation(API_TEST, 'credentials: "omit"', 'credentials: "include"', expect=BEARER)
@mutation(API_TEST, "Authorization: `Bearer ${key}`", "Authorization: key", expect=BEARER)
@mutation(API_TEST, '(code ? `${code}: ` : "")', '""', expect=ENVELOPE)
@mutation(API_TEST, "message || `HTTP ${status}`", "message", expect=NO_CODE)
@mutation(API_TEST, "text.split(key).join(\"[redacted]\")", "text", expect=REDACTED)
@mutation(API_TEST, 'redact(envelope.code || "")', 'envelope.code || ""', expect=REDACTED)
@mutation(API_TEST, 'kind === "image" ?', 'kind === "video" ?', expect=ROUTES)
@pytest.mark.web_unit
def test_public_call():
    vitest("tests/unit/test/test-api.spec.ts")


# ---- the API key: sessionStorage only, only after a successful load, cleared on sign-out and on 401 --------------
STORED = SESSION_SPEC + "goes to sessionStorage only after a successful model load, and nowhere else"
NOT_STORED = SESSION_SPEC + "is not stored when the load fails with a server error"
FORGOTTEN_IN_FLIGHT = SESSION_SPEC + "is not stored when it was forgotten while the load was in flight"
NEWEST = SESSION_SPEC + "is the one of the newest load: a slower older load is dropped"
ANSWERS_401 = SESSION_SPEC + "is forgotten at once when a call with it answers 401"
GOOD_KEPT = SESSION_SPEC + "a wrong key typed over a good kept one does not remove the good one"
LOAD_401 = SESSION_SPEC + "is dropped from storage when the load of the kept key answers 401"
FORGET = SESSION_SPEC + "is forgotten by Forget key: storage, memory and the box"
RESET = SESSION_SPEC + "is cleared by a reset (sign-out) together with the models and the points"
TRIMMED = SESSION_SPEC + "is trimmed"
NEWEST_GUARD = "if (!latest.isCurrent(ticket)) return false;\n      activeKey = key;"
NO_KEY = 'if (!key) throw new TestApiError(0, "", KEY_MISSING);'
LOAD_CALL = "const out = await listModels(key);"


@mutation(KEY, "sessionStorage.setItem(TEST_KEY, key)", "localStorage.setItem(TEST_KEY, key)", expect=STORED)
@mutation(SESSION, "saveKey(key);", "", expect=STORED)
@mutation(SESSION, LOAD_CALL, "saveKey(key);\n      " + LOAD_CALL, expect=NOT_STORED)
@mutation(SESSION, "latest.invalidate();", "", expect=FORGOTTEN_IN_FLIGHT)
@mutation(SESSION, NEWEST_GUARD, "activeKey = key;", expect=NEWEST)
@mutation(SESSION, "err.status === 401 && key === activeKey", "err.status === 400 && key === activeKey",
          expect=ANSWERS_401)
@mutation(SESSION, "readKey() === key", "true", expect=GOOD_KEPT)
@mutation(SESSION, "err.status === 401 && readKey()", "err.status === 400 && readKey()", expect=LOAD_401)
@mutation(SESSION, NO_KEY, "", expect=FORGET)
@mutation(SESSION, "models.value = [];", "", expect=RESET)
@mutation(SESSION, "typedKey.value.trim()", "typedKey.value", expect=TRIMMED)
@pytest.mark.web_unit
def test_api_key_storage_rules():
    vitest("tests/unit/test/test-session.spec.ts")


# ---- Generate: 3 s, 600 tries, the generation guard ------------------------------------------------------------
POLLS = GEN_SPEC + "polls every 3 seconds up to 600 times"
STOPS = GEN_SPEC + "stop() (sign-out, a 401, the tab destroyed) ends the loop and the state stays as it was"
GIVES_UP = GEN_SPEC + "gives up after 600 polls and goes idle"
TERMINAL = GEN_SPEC + "ends on done, failed and completed"
PUBLIC_401 = GEN_SPEC + "a public 401 with a poll in flight ends the loop, forgets the key and shows the error"
SIGN_OUT_POLL = GEN_SPEC + ("auth.onSignOut with a poll in flight ends the loop, clears the key "
                            "and drops the late answer")
DOUBLE_CLICK = GEN_SPEC + "a double click during the POST sends exactly one request"
IN_FLIGHT = GEN_SPEC + "the answer of a poll that was in flight when a new Generate started is dropped"
NEW_ENDS_OLD = GEN_SPEC + "a new Generate ends the older loop: its answers are dropped and it asks no more"
STALE_ERROR = GEN_SPEC + "shows an error as `code: message` and goes idle; a stale error is dropped"
SLEEP = "await sleep(POLL_INTERVAL_MS);"
GET_JOB = "(key) => getJob(key, model.kind, made.id));"
STALE_GUARD = "\n        if (!live()) return;"


@mutation(GENERATE, "POLL_INTERVAL_MS = 3000", "POLL_INTERVAL_MS = 1000", expect=POLLS)
@mutation(GENERATE, "POLL_MAX_TRIES = 600", "POLL_MAX_TRIES = 60", expect=POLLS)
@mutation(GENERATE, "i < POLL_MAX_TRIES &&", "i <= POLL_MAX_TRIES &&", expect=GIVES_UP)
@mutation(GENERATE, '"failed", "completed"', '"failed"', expect=TERMINAL)
@mutation(GENERATE, SLEEP + STALE_GUARD, SLEEP, expect=STOPS)
@mutation(GENERATE, "latest.invalidate();", "", expect=STOPS)
@mutation(GENERATE, GET_JOB + STALE_GUARD, GET_JOB, expect=IN_FLIGHT)
@mutation(GENERATE, "if (submitting.value) return;", "", expect=DOUBLE_CLICK)
@mutation(GENERATE, "submitting.value = true;", "", expect=DOUBLE_CLICK)
@mutation(GENERATE, "} finally {\n        submitting.value = false;", "} finally {", expect=DOUBLE_CLICK)
@mutation(GENERATE, "if (live()) running.value = false;", "running.value = false;", expect=NEW_ENDS_OLD)
@mutation(GENERATE, "if (live()) session.status.value =", "session.status.value =", expect=STALE_ERROR)
@mutation(SESSION, "err.status === 401 && key === activeKey", "err.status === 400 && key === activeKey",
          expect=PUBLIC_401)
@mutation(SESSION, 'status.value = "Idle";', "", expect=SIGN_OUT_POLL)
@pytest.mark.web_unit
def test_generate_polling_and_generation_guard():
    vitest("tests/unit/test/use-generate.spec.ts")


# ---- the tab itself: sign-out, the running state, the notices, the form on every input -----------------------------
RUNNING = VIEW_SPEC + "a running job > "
SIGN_OUT = RUNNING + "a sign-out ends the loop and drops the key and the models"
NO_DOUBLE = RUNNING + "disables Generate only while its own request is in flight, then enables it again"
SECOND_GENERATE = RUNNING + "a second Generate while the first job is polling ends the first loop and starts a new one"
DROPPED = (VIEW_SPEC
           + "images > restores stored images, and a restore that drops anything "
           + "rewrites the store and shows the notice")
REFUSING = VIEW_SPEC + "storage that refuses, each says so at once > "
RESTORE_BLOCKED = REFUSING + "IndexedDB blocked on restore: the notice is there when the tab opens"
READ_BLOCKED = REFUSING + "localStorage blocked on read: the notice is there when the tab opens"
WRITE_BLOCKED = (REFUSING + "localStorage blocked on write: the notice shows on the first input, "
                 + "and goes when a write works again")
EVERY_INPUT = VIEW_SPEC + "the tab > keeps the key in sessionStorage only, the form in localStorage on every input"
BLOCKED = (REFUSING + "IndexedDB blocked on write: the notice shows when an image is attached, "
           + "with localStorage working")
FORM_RESTORED = VIEW_SPEC + "the tab > restores the form, the kept key, the models and the picked model after a reload"
LOAD_ONCE = (VIEW_SPEC + "Load models > shows a busy state and sends one request for a double click and an Enter "
             + "while the load is on its way")
LOAD_FREE = VIEW_SPEC + "Load models > is free again after a load that failed, so the next click sends a request"
FORGET_ALL = (VIEW_SPEC + "Forget key > clears everything of the key: the box, the stored key, the models, "
              + "the points and the status line")
KEY_OFF_PAGE = (VIEW_SPEC + "the key on the page > is never in the rendered page outside its password input, "
                + "before and after sign-out")
PICKER = VIEW_SPEC + "the model picker and the wallet > "
WALLET_ROWS = PICKER + "shows the wallet limits of /v1/points as rows: day and total, and unlimited with its use"
DURATION_SELECT = PICKER + "a model with a duration list gets a select of them, and the heading names them"
NO_DURATION = PICKER + ("a model with no durations list shows no Duration field and sends no duration, even one saved "
                        "for another model")
DISPLAY_NAME = (PICKER + "a model that names a display name shows it with its id under it; "
                + "one without shows the id alone")
NAMES_ONLY = (VIEW_SPEC + "images > the drag area never uploads by itself, and shows names and sizes, "
              + "never a picture")
RESULT_CARD = (VIEW_SPEC + "the result card > shows the step and word of a job in progress, its short id "
               + "and the fields it carries, and the id opens the job")
FAILED_JOB = VIEW_SPEC + "the result card > a finished job has no empty slot, and a failed one shows the reason"


@mutation(TEST_VIEW, "session.reset();", "", expect=SIGN_OUT)
@mutation(TEST_VIEW, "generate.stop();\n  session.reset();", "session.reset();", expect=SIGN_OUT)
@mutation(TEST_VIEW, ':disabled="!chosen || submitting"', ':disabled="!chosen"', expect=NO_DOUBLE)
@mutation(TEST_VIEW, ':disabled="!chosen || submitting"', ':disabled="!chosen || true"', expect=NO_DOUBLE)
@mutation(GENERATE, "const mine = latest.next();", "const mine = 1;", expect=SECOND_GENERATE)
@mutation(TEST_VIEW, "if (restored.dropped) note.value = DROPPED_NOTE;", "", expect=DROPPED)
@mutation(TEST_VIEW, "const persist = () => void (formFailed.value = !saveForm(form));",
          "const persist = () => undefined;", expect=EVERY_INPUT)
@mutation(TEST_VIEW, "const persist = () => void (formFailed.value = !saveForm(form));",
          "const persist = () => void saveForm(form);", expect=WRITE_BLOCKED)
@mutation(TEST_VIEW, "ref(!initial.ok)", "ref(false)", expect=READ_BLOCKED)
@mutation(TEST_VIEW, "note.value || (formFailed.value ? NOT_SAVED_NOTE : \"\")", "note.value", expect=WRITE_BLOCKED)
@mutation(TEST_VIEW, "if (restored.failed) note.value = NOT_SAVED_NOTE;", "", expect=RESTORE_BLOCKED)
@mutation(TEST_VIEW, 'note.value = saved.saved ? "" : NOT_SAVED_NOTE;', 'note.value = "";', expect=BLOCKED)
@mutation(TEST_VIEW, "files.value = restored.files;", "", expect=DROPPED)
@mutation(TEST_VIEW, "const form = reactive(initial.form);", "const form = reactive(emptyForm());",
          expect=FORM_RESTORED)
@mutation(TEST_VIEW, "if (loadingModels.value) return;", "", expect=LOAD_ONCE)
@mutation(TEST_VIEW, ':aria-busy="loadingModels"', ':aria-busy="false"', expect=LOAD_ONCE)
@mutation(TEST_VIEW, "} finally {\n    loadingModels.value = false;", "} finally {", expect=LOAD_FREE)
@mutation(TEST_VIEW, 'v-if="!groups.length"', 'v-if="false"', expect=EMPTY_MODELS)
@mutation(TEST_VIEW, '<details v-if="points" class="test-tab__points-box">',
          '<details v-if="points" open class="test-tab__points-box">', expect=WALLET_BOX)
@mutation(TEST_VIEW, "form[name] = value;", "", expect=DURATION_SELECT)
@mutation(TEST_VIEW, '@click="session.reset"', ':title="typedKey" @click="session.reset"', expect=KEY_OFF_PAGE)
@mutation(TEST_VIEW, '@click="session.reset"', '@click="session.forget"', expect=FORGET_ALL)
@mutation(TEST_MODELS, "wallet.day_limit === null && wallet.total_limit === null", "false", expect=WALLET_ROWS)
@mutation(TEST_MODELS, "`${wallet.used_day} / ${wallet.day_limit}`", "`${wallet.used_day}`", expect=WALLET_ROWS)
@mutation(TEST_MODELS, '`${head ? `${head} or ` : ""}${last} s`', '`${head ? `${head}, ` : ""}${last} s`',
          expect=DURATION_SELECT)
@mutation(TEST_MODELS, "typeof m.name === \"string\" && m.name ? m.name : m.id", "m.id", expect=DISPLAY_NAME)
@mutation(TEST_IMAGES, "{{ sizeText(f.blob.size) }}", "", expect=NAMES_ONLY)
@mutation(TEST_RESULT, "ui.openJob(props.job.id)", "ui.openJob('')", expect=RESULT_CARD)
@mutation(TEST_RESULT_MODEL, 'waiting: "2/4",', 'waiting: "3/4",', expect=RESULT_CARD)
@mutation(TEST_RESULT_MODEL, "return job.stage || job.status;", "return job.status;", expect=RESULT_CARD)
@mutation(TEST_RESULT_MODEL, 'add("Created", clockTime(job.created_at));', "", expect=RESULT_CARD)
@mutation(TEST_RESULT, "!finished && !media.length", "!media.length", expect=FAILED_JOB)
@mutation(TEST_RESULT, 'typeof props.job?.error === "string" ? props.job.error : ""', '""', expect=FAILED_JOB)
@pytest.mark.web_unit
def test_test_view_rules():
    vitest("tests/unit/test/test-view.spec.ts")


# ---- page-test-login: the Test page drawn as the design (the result card frames, the wallet order, the play box) -----
WALLET_ORDER = PICKER + "lists the video wallet first, whatever order the points answer has"
FRAMES_OF_RESULT = VIEW_SPEC + "the result card frames > "
EMPTY_FRAME = FRAMES_OF_RESULT + "draws nothing made yet as a title bar over the status line in the middle"
DONE_FRAME = (FRAMES_OF_RESULT + "draws a done job as a title bar over the media and the ledger: job, model, duration "
              + "and time taken")
PLAIN_FRAME = FRAMES_OF_RESULT + "a job still on its way keeps the plain card: no title bar"
PLAY_BOX = (VIEW_SPEC + "media > draws a video link as a box with a play button, and the video element only once it "
            + "is played")
FAILED_CODE = (VIEW_SPEC + "the result card > a failed job with a code names it in bold before the reason, as the "
               + "job drawer does")
BY_VALUE = PICKER + "offers and sends a ratio by the value the provider takes, not by its label"
CHIP_HINT = PICKER + "names a template chip by its whole text and shows the whole text in its hint"
STYLE_VALUE = PICKER + "shows a style by its label and sends the value the provider takes"
MODEL_RATIOS = (PICKER + "lists the picked model's own ratios over the provider's: a video model offers 21:9 and "
                + "not 2:3")
RATIO_FALLBACK = (PICKER + "puts the model's default in place of a ratio the next model does not take, and never "
                  + "sends it")
REFUNDED_POINTS = (VIEW_SPEC + "the result card > a refunded failed job strikes its charge, then 0 and refunded, as "
                   + "the job drawer does")
OPTION_VALUE = FORM_SPEC + "optionValue > takes the value a provider takes, and the label only when there is no value"


@mutation(TEST_MODELS, 'Number(b === "video") - Number(a === "video") || a.localeCompare(b)', "a.localeCompare(b)",
          expect=WALLET_ORDER)
@mutation(TEST_VIEW, "capital(m.kind)", "m.kind", expect=DISPLAY_NAME)
@mutation(TEST_VIEW, '<span class="num">{{ infoTail }}</span>', "{{ infoTail }}", expect=DURATION_SELECT)
@mutation(TEST_RESULT, "'result--bar': !job || done", "'result--bar': !job", expect=DONE_FRAME)
@mutation(TEST_RESULT, "'result--bar': !job || done", "'result--bar': true", expect=PLAIN_FRAME)
@mutation(TEST_RESULT, 'v-if="!job" class="result__empty"', 'v-if="false" class="result__empty"', expect=EMPTY_FRAME)
@mutation(TEST_RESULT, 'v-if="playing === i"', 'v-if="true"', expect=PLAY_BOX)
@mutation(TEST_RESULT, 'typeof props.job?.error_code === "string" ? props.job.error_code : ""', '""',
          expect=FAILED_CODE)
@mutation(TEST_VIEW, "(extras.ratios ?? []).map(optionValue)", "(extras.ratios ?? []).map(optionLabel)",
          expect=BY_VALUE)
@mutation(TEST_VIEW, "({ value: optionValue(o), label: optionLabel(o) })",
          "({ value: optionLabel(o), label: optionLabel(o) })", expect=STYLE_VALUE)
@mutation(TEST_VIEW, "label: optionLabel(o) })", "label: optionValue(o) })", expect=STYLE_VALUE)
@mutation(TEST_VIEW, '<FilterSelect\n            v-if="durations.length"', '<FilterSelect\n            v-if="false"',
          expect=DURATION_SELECT)
@mutation(TEST_VIEW, '<FilterSelect\n            v-if="durations.length"', '<FilterSelect\n            v-if="true"',
          expect=NO_DURATION)
@mutation(TEST_VIEW, "return own.length ? own : withSaved(ratios.value, form.ratio);",
          "return withSaved(ratios.value, form.ratio);", expect=MODEL_RATIOS)
@mutation(TEST_VIEW, "if (own.length && form.ratio && !own.includes(form.ratio)) form.ratio = defaultRatioOf(model);",
          "", expect=RATIO_FALLBACK)
@mutation(TEST_MODELS, 'typeof ratio === "string" && ratiosOf(m).includes(ratio) ? ratio : ""', '""',
          expect=RATIO_FALLBACK)
@mutation(TEST_RESULT_MODEL, 'if (typeof job.points === "number") {', "if (false) {", expect=RESULT_CARD)
@mutation(TEST_VIEW, ':content="t.show_value || t.prompt"', ':content="t.prompt.slice(0, 40)"', expect=CHIP_HINT)
@mutation(TEST_RESULT_MODEL, "job.refunded === true", "false", expect=REFUNDED_POINTS)
@mutation(TEST_RESULT_MODEL, 'if (took) rows.push({ label: "Took", value: took });', "", expect=DONE_FRAME)
@mutation(TEST_RESULT_MODEL, "minutes ? `${minutes} m ${seconds % 60} s` : `${seconds} s`", "`${seconds} s`",
          expect=DONE_FRAME)
@mutation(TEST_RESULT_MODEL, 'const rows = [{ label: "Job", value: shortJobId(job.id) }];',
          "const rows: Array<{ label: string; value: string }> = [];", expect=DONE_FRAME)
@pytest.mark.web_unit
def test_test_view_drawn_as_the_design():
    vitest("tests/unit/test/test-view.spec.ts")


@mutation(FORM, 'typeof option.value === "string" && option.value ? option.value : optionLabel(option)',
          "optionLabel(option)", expect=OPTION_VALUE)
@pytest.mark.web_unit
def test_test_view_option_values():
    vitest("tests/unit/test/test-form.spec.ts")


# ---- Settings: the value parse, the dirty rule, the scope, the password change -------------------------------------
PARSE = SETTINGS_SPEC + "setting values > are parsed as JSON first and fall back to the raw string"
TEXT = SETTINGS_SPEC + "setting values > start in the input as the string itself, or JSON for anything else"
TABLE = SETTINGS_SPEC + "the settings table > "
DIRTY = TABLE + "Save is disabled until the value changed, the row says edited, and an edit back disables it again"
SAVE_SENDS = TABLE + "Save sends the parsed value, reloads both lists and says Saved"
SCOPE = (SETTINGS_SPEC
         + "measured prices > follows the global provider filter and reloads only the prices when it changes")
RESET_RULE = (TABLE + "Reset is on for a stored value or an edit, "
              + "and off for a value that is only the environment or default")
NAV_MOVES = (SETTINGS_SPEC + "the sub-navigation > lists the four sections in order, marks the first as current "
             + "and moves the mark on a click")
NAV_AREA = (SETTINGS_SPEC + "the sub-navigation > scrolls the page's scroll area to the card minus the anchor, "
            + "and nothing else")
NAV_WINDOW = (SETTINGS_SPEC + "the sub-navigation > scrolls the window where the page has no scroll area of its own "
              + "(a phone)")
PRICES_SPEC = SETTINGS_SPEC + "measured prices > "
PRICES_FIGURES = PRICES_SPEC + "shows duration as Ns or -, flags a disagreement and shows 0 for none"
PASSWORD_SPEC = SETTINGS_SPEC + "change password > "
LOGIN = PASSWORD_SPEC + "sends both passwords, ends the session here and lands on login"
OLD_FIELD = PASSWORD_SPEC + "shows a refusal that names old_password on that field, and stays"
NEW_FIELD = PASSWORD_SPEC + "shows a refusal that names new_password on that field"
STRING_VALUE = 'typeof item.value === "string" ? item.value : JSON.stringify(item.value)'
BOTH_PARAMS = 'err.param === "old_password" || err.param === "new_password"'


@mutation(SETTING_VALUE, "return text;", "return null;", expect=PARSE)
@mutation(SETTING_VALUE, STRING_VALUE, "JSON.stringify(item.value)", expect=TEXT)
@mutation(SETTINGS_TABLE, "drafts[item.key] !== valueText(item)", "true", expect=DIRTY)
@mutation(SETTINGS_TABLE, 'item.source === "db" || edited(item)', "edited(item)", expect=RESET_RULE)
@mutation(SETTINGS_VIEW, "current.value = id;", "", expect=NAV_MOVES)
@mutation(SETTINGS_VIEW, "if (area) area.scrollTo({ top: area.scrollTop + offset });",
          'if (area) card.scrollIntoView({ block: "start" });', expect=NAV_AREA)
@mutation(SETTINGS_VIEW, "else window.scrollTo({ top: window.scrollY + offset });",
          'else card.scrollIntoView({ block: "start" });', expect=NAV_WINDOW)
@mutation(SETTINGS_VIEW, "- (area ? area.getBoundingClientRect().top : 0) - anchor;",
          "- (area ? area.getBoundingClientRect().top : 0);", expect=NAV_AREA)
@mutation(PRICES, 'const NONE = "—";', 'const NONE = "-";', expect=PRICES_FIGURES)
@mutation(PRICES, ': "0"', ': "none"', expect=PRICES_FIGURES)
@mutation(SETTINGS_VIEW, "await putSetting(key, parseValue(text));", "await putSetting(key, text);", expect=SAVE_SENDS)
@mutation(SETTINGS_VIEW, "watch(() => scope.provider, loadPrices);", "watch(() => scope.provider, () => undefined);",
          expect=SCOPE)
@mutation(SETTINGS_VIEW, "getPrices(scope.provider)", 'getPrices("")', expect=SCOPE)
@mutation(PASSWORD, "auth.signOut();", "", expect=LOGIN)
@mutation(PASSWORD, 'await router.replace({ name: "login" });', "", expect=LOGIN)
@mutation(PASSWORD, "fieldErrors[err.param] = err.message;", "notify.error(err.message);", expect=OLD_FIELD)
@mutation(PASSWORD, BOTH_PARAMS, 'err.param === "old_password"', expect=NEW_FIELD)
@pytest.mark.web_unit
def test_settings_rules():
    vitest("tests/unit/settings/settings.spec.ts")


# ---- Prompt frames: the gen guard, busy rules, lock draft, inline refusal, preview and apply-all drops -----------
STALE_ANSWER = FRAMES_SPEC + "stale answers > drops the answer of an editor that was replaced before it came back"
STALE_FAILURE = (FRAMES_SPEC
                 + "stale answers > drops the failure of an editor that was replaced as quietly as its answer")
LOAD_OFF = FRAMES_SPEC + "busy states > a load turns every control off except Edit and the provider select"
SAVE_NEEDS_CHANGE = (FRAMES_SPEC
                     + "opening > the draft starts as the current text, else the text in force, "
                     + "and Save waits for a change")
KEEPS_DRAFT = FRAMES_SPEC + "a lock toggle > keeps the typed draft of the editor that is open"
LOCK_FAILS = FRAMES_SPEC + "a lock toggle > reloads the table when the call fails, so the checkbox shows what is stored"
WRITE_ERRORS = FRAMES_SPEC + "write errors > "
OTHER_ERRORS = WRITE_ERRORS + "sends any other error (5xx, network, 401) to a toast and shows nothing beside the editor"
REFUSAL_MOVED = WRITE_ERRORS + "drops the refusal when another editor was opened while the write ran"
PREVIEW = FRAMES_SPEC + "preview > "
PREVIEW_EDITOR = PREVIEW + "drops the result when another editor was opened while it was on its way"
PREVIEW_DRAFT = PREVIEW + "drops the result when the draft changed while it was on its way"
PREVIEW_DEFAULT = PREVIEW + "for the default editor previews with the first model of the kind"
APPLY_MOVED = FRAMES_SPEC + "writes > Apply to all draws no summary when the operator moved to another editor meanwhile"
OWN_PROVIDER = FRAMES_SPEC + "opening > keeps its own provider when it comes back, whatever the global filter says"
START_PROVIDER = FRAMES_SPEC + "opening > starts on the global filter when it names a provider, else the first one"
STALE_ERROR_GUARD = "if (mine !== gen) return null;\n        throw err;"
STALE_ANSWER_GUARD = "if (mine !== gen) return null;\n      draw(answer);"
OPENS_OFF = "const opensOff = computed(() => busy.value);"
CONTROLS_OFF = "const controlsOff = computed(() => busy.value || loading.value > 0);"
PREVIEW_SENDS = PREVIEW + "sends the draft with the sample prompt for the open model and shows the answer"
SAVE_WARNS = (FRAMES_SPEC + "writes > Save sends the draft, then shows the warnings of the answer "
              + "after the redraw and a toast")
LOCK_SENDS = (FRAMES_SPEC + "a lock toggle > toggling another model's lock keeps this editor's draft too, "
              + "and Unlocked is its own toast")
FOLLOW_NULL = FRAMES_SPEC + "writes > Follow the default sends a null text"
RESET_KIND = (FRAMES_SPEC + "writes > Reset to the shipped default posts the kind of the section "
              + "and keeps the open editor")
REDRAWN = PREVIEW + "an editor that is redrawn starts with the sample prompt and no preview"
WRITE_FAILS = FRAMES_SPEC + "busy states > clears the busy flags when a write fails"
PREVIEW_MODEL = "t.target === DEFAULT_TARGET ? kindOf(t.kind)?.models[0]?.model : t.target"


@mutation(FRAMES, STALE_ERROR_GUARD, "throw err;", expect=STALE_FAILURE)
@mutation(FRAMES, STALE_ANSWER_GUARD, "draw(answer);", expect=STALE_ANSWER)
@mutation(FRAMES, "gen++;\n    provider.value = p;", "provider.value = p;", expect=STALE_ANSWER)
@mutation(FRAMES, OPENS_OFF, "const opensOff = computed(() => busy.value || loading.value > 0);", expect=LOAD_OFF)
@mutation(FRAMES, CONTROLS_OFF, "const controlsOff = computed(() => busy.value);", expect=LOAD_OFF)
@mutation(FRAMES, "!controlsOff.value && draft.value !== loaded.value", "!controlsOff.value", expect=SAVE_NEEDS_CHANGE)
@mutation(FRAMES, "keepDraft && shown.value", "false && shown.value", expect=KEEPS_DRAFT)
@mutation(FRAMES, "await loadFrames(true, openFor(t.provider)).catch(() => false);", "", expect=LOCK_FAILS)
@mutation(FRAMES, "err.status < 500 && err.status !== 401", "err.status !== 401", expect=OTHER_ERRORS)
@mutation(FRAMES, " && err.status !== 401", "", expect=OTHER_ERRORS)
@mutation(FRAMES, "mine === gen && (!stillAsked || stillAsked())", "(!stillAsked || stillAsked())",
          expect=REFUSAL_MOVED)
@mutation(FRAMES, "mine === gen && draft.value === text", "draft.value === text", expect=PREVIEW_EDITOR)
@mutation(FRAMES, "mine === gen && draft.value === text", "mine === gen", expect=PREVIEW_DRAFT)
@mutation(FRAMES, "if (drew) applied.value = answer;", "applied.value = answer;", expect=APPLY_MOVED)
@mutation(FRAMES, "drew = await loadFrames(false, t, started);", "drew = await loadFrames(false, t);",
          expect=APPLY_MOVED)
@mutation(FRAMES, PREVIEW_MODEL, "t.target", expect=PREVIEW_DEFAULT)
@mutation(FRAMES, "Object.assign(memory, { provider: p, kind: k, target: t });", "", expect=OWN_PROVIDER)
@mutation(FRAMES, 'names.includes(preferred) ? preferred : (names[0] ?? "")', 'names[0] ?? ""', expect=START_PROVIDER)
@mutation(FRAMES, "previewFrame(model, previewPrompt.value, text)",
          "previewFrame(model, previewPrompt.value, loaded.value)", expect=PREVIEW_SENDS)
@mutation(FRAMES, "warnings.value = saved.warnings;", "warnings.value = [];", expect=SAVE_WARNS)
@mutation(FRAMES, "await lockFrame(t, locked);", "await lockFrame(t, true);", expect=LOCK_SENDS)
@mutation(FRAMES, "await putFrame(t, null);", 'await putFrame(t, "");', expect=FOLLOW_NULL)
@mutation(FRAMES, "resetFrame(p, resetKind)", 'resetFrame(p, "image")', expect=RESET_KIND)
@mutation(FRAMES, "applied.value = null;\n    preview.value = null;", "applied.value = null;", expect=REDRAWN)
@mutation(FRAMES, "previewPrompt.value = SAMPLE_PROMPT;\n    if (view.info) {", "if (view.info) {", expect=REDRAWN)
@mutation(FRAMES, "} finally {\n      busy.value = false;", "} finally {", expect=WRITE_FAILS)
@mutation(FRAMES, "} finally {\n      loading.value--;", "} finally {", expect=WRITE_FAILS)
@pytest.mark.web_unit
def test_frames_controller_rules():
    vitest("tests/unit/frames/use-frames.spec.ts")


# ---- Prompt frames: the bodies and paths of the write calls ----------------------------------------------------
API_FRAMES = FAILED + "frames/frames-api.spec.ts > the write calls of the Prompt frames > "
LOCK_BODY = API_FRAMES + "lock sends the checkbox value as {locked} to the lock path of the encoded target"
FOLLOW_BODY = API_FRAMES + "Follow the default puts text: null and a save puts the text, on the target path"
RESET_PATH = API_FRAMES + "Reset to the shipped default posts /reset on the kind path, with no body"
PREVIEW_BODY = API_FRAMES + "the preview posts the model, the sample prompt and the draft text"
FRAMES_API = f"{SRC}/api/frames.ts"


@mutation(FRAMES_API, "body: { locked }", "body: { locked: true }", expect=LOCK_BODY)
@mutation(FRAMES_API, '{ method: "PUT", body: { text } }', '{ method: "PUT", body: { text: text ?? "" } }',
          expect=FOLLOW_BODY)
@mutation(FRAMES_API, "}/reset`", "}/restore`", expect=RESET_PATH)
@mutation(FRAMES_API, "body: { model, prompt, text }", "body: { model, prompt }", expect=PREVIEW_BODY)
@pytest.mark.web_unit
def test_frames_write_calls():
    vitest("tests/unit/frames/frames-api.spec.ts")


# ---- Prompt frames: what the view enables and shows ------------------------------------------------------------
LOAD_VIEW = (FRAMES_VIEW_SPEC
             + "during a load: Save and the other controls are off, the box is read-only, "
             + "Edit and the provider stay on")
VIDEO_FIRST = FRAMES_VIEW_SPEC + "draws video before image whatever order the server lists them in"
OPENS_MODEL = FRAMES_VIEW_SPEC + "opens the editor of a model: help text, Follow only for an own frame, no Apply to all"


@mutation(FRAMES_VIEW, ':disabled="frames.opensOff"', ':disabled="frames.controlsOff"', expect=LOAD_VIEW)
@mutation(FRAMES_VIEW, 'Number(b.kind === "video") - Number(a.kind === "video")', "0", expect=VIDEO_FIRST)
@mutation(FRAME_EDITOR, ':readonly="frames.controlsOff"', ':readonly="false"', expect=LOAD_VIEW)
@mutation(FRAME_EDITOR, "frames.shownModel.source === 'own'", "frames.shownModel.source === 'default'",
          expect=OPENS_MODEL)
@mutation(FRAME_EDITOR, 'v-if="frames.shown.target === DEFAULT_TARGET"', 'v-if="true"', expect=OPENS_MODEL)
@mutation(FRAME_EDITOR, ':placeholder="FRAME_SHAPE"', "", expect=FRAME_SHAPE_SPEC)
@pytest.mark.web_unit
def test_frames_view_rules():
    vitest("tests/unit/frames/frames-view.spec.ts")


# ---- Prompt frames: the history timeline -------------------------------------------------------------------------
TIMELINE = FAILED + "frames/frames-model.spec.ts > the history > "


@mutation(FRAMES_MODEL, "[...history].sort((a, b) => b.id - a.id)", "[...history].sort((a, b) => a.id - b.id)",
          expect=TIMELINE + "lists the newest version first and leaves the input alone")
@mutation(FRAMES_MODEL, "[...history].sort(", "history.sort(",
          expect=TIMELINE + "lists the newest version first and leaves the input alone")
@pytest.mark.web_unit
def test_the_frames_history_runs_newest_first():
    vitest("tests/unit/frames/frames-model.spec.ts")


# ---- Prompt frames: the lines and texts ------------------------------------------------------------------------
LOADED_TEXT = MODEL_SPEC + "the text the editor starts with is the current own text, else the text in force, else empty"
SUMMARY = MODEL_SPEC + "the apply-to-all summary lists applied, kept and unchanged, with - for none"


@mutation(FRAMES_MODEL, ': "no version"', ': "none"', expect=MODEL_SPEC + "version tag")
@mutation(FRAMES_MODEL, '"unlocked"} ${shortDay(', '"locked"} ${shortDay(',
          expect=MODEL_SPEC + "lock line and lock history")
@mutation(FRAMES_MODEL, "two(d.getMonth() + 1)", "two(d.getMonth())",
          expect=MODEL_SPEC + "day and time are day first, two digits each, in the browser time zone")
@mutation(FRAMES_MODEL, "info.current && info.current.text !== null", "info.current && true", expect=LOADED_TEXT)
@mutation(FRAMES_MODEL, "`${err.message} (${err.param})` : err.message", "err.message : err.message",
          expect=MODEL_SPEC + "a refusal carries its field")
@mutation(FRAMES_MODEL, 'names.join(", ") || "-"', 'names.join(", ")', expect=SUMMARY)
@pytest.mark.web_unit
def test_frames_lines():
    vitest("tests/unit/frames/frames-model.spec.ts")


# ---- page-frames-settings round: the drawn section titles, a refusal as a field error, the password error's field ---
REFUSED = FRAMES_VIEW_SPEC + "shows a refused save beside the editor as an alert with the field"
NOT_REFUSED = FRAMES_VIEW_SPEC + "marks no box invalid while nothing was refused"
TITLES = FRAMES_VIEW_SPEC + "titles each kind's section as drawn: Video, Image"


@mutation(FRAME_EDITOR, ":aria-invalid=\"frames.refusal ? 'true' : undefined\"", ':aria-invalid="undefined"',
          expect=REFUSED)
@mutation(FRAME_EDITOR, ":aria-describedby=\"frames.refusal ? 'frame-refusal' : undefined\"",
          ':aria-describedby="undefined"', expect=REFUSED)
@mutation(FRAME_EDITOR, "{ 'is-invalid': frames.refusal }", "{ 'is-invalid': true }", expect=NOT_REFUSED)
@mutation(FRAMES_MODEL, "kind.charAt(0).toUpperCase() + kind.slice(1)", "kind", expect=TITLES)
@pytest.mark.web_unit
def test_frames_drawn_looks():
    vitest("tests/unit/frames/frames-view.spec.ts")


LOAD_WORDS = TABLE + "shows an empty list and a failed load with Retry"
BOOT_DASH = SETTINGS_SPEC + "setting values > show a boot-only value that is unset or empty as a dash"
PASSWORD_RULE = SETTINGS_SPEC + "setting values > hold a new password to the server's rule, 10 to 256 characters"
LONG_DEFAULT = SETTINGS_SPEC + "setting values > show a long default on one line, the whole value laid out in its note"
FORM_CHECK = (PASSWORD_SPEC + "checks the length in the form before sending, in the drawn words: "
              + "at least 10, at most 256")
LOADING_ROWS = (SETTINGS_SPEC + "the settings page > draws the first read as the drawn loading rows: each table's "
                + "header over five rows of bars")


@mutation(PASSWORD, "fieldErrors.new_password ? 'new-password-error' : undefined", "undefined", expect=NEW_FIELD)
@mutation(SETTINGS_VIEW, 'errorText(err), "the settings")', 'errorText(err), "the jobs")', expect=LOAD_WORDS)
@mutation(SETTINGS_VIEW, 'errorText(err), "the measured prices")', "errorText(err))", expect=LOAD_WORDS)
@mutation(SETTING_VALUE, ' || value === ""', "", expect=BOOT_DASH)
@mutation(SETTING_VALUE, "if (password.length < PASSWORD_MIN)", "if (password.length < 1)", expect=PASSWORD_RULE)
@mutation(SETTING_VALUE, "if (password.length > PASSWORD_MAX)", "if (false)", expect=PASSWORD_RULE)
@mutation(PASSWORD, "if (fieldErrors.new_password) return;", "", expect=FORM_CHECK)
@mutation(SETTING_VALUE, "shown.length > LONG_DEFAULT", "shown.length > 1000", expect=LONG_DEFAULT)
@mutation(SETTING_VALUE, "JSON.stringify(value, null, 2)", "JSON.stringify(value)", expect=LONG_DEFAULT)
@mutation(SETTINGS_TABLE, "props.loading && !props.items.length && !props.error", "false", expect=LOADING_ROWS)
@mutation(PRICES, "props.loading && !props.rows.length && !props.error", "false", expect=LOADING_ROWS)
@mutation(SETTINGS_VIEW, ':empty="!envOnly.length && !settingsLoading"', ':empty="!envOnly.length"',
          expect=LOADING_ROWS)
@pytest.mark.web_unit
def test_settings_errors_as_drawn():
    vitest("tests/unit/settings/settings.spec.ts")
