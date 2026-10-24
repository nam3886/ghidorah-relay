"""Mutation markers for the Settings, Prompt frames and Test views of the admin web. Each mutant must turn the Vitest
spec it names red, with the exact failed test; `webunit.vitest` runs the spec and scripts/mutate.py does the mutating.
A plain `check.sh` deselects these tests. The rules covered:
- Test tab storage: the key only in sessionStorage and only after a model load, the form in localStorage, the image
  limits (10 files, 12 MiB, images only) on restore and before every write, the public Bearer call;
- the Generate polling (3 s, 600 tries) and its generation guard, sign-out and a 401 ending it;
- Prompt frames: the `gen` guard on answers and errors, the busy rules, the lock toggle keeping the draft, the inline
  4xx refusal, the preview and apply-all drops;
- Settings: the JSON-then-string value parse, the dirty rule, the cards, the provider scope, the password change.
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
SETTINGS_POOLS = f"{SRC}/components/settings/settings-pools.ts"
SETTING_FIELD = f"{SRC}/components/settings/SettingField.vue"
SETTINGS_CARDS = f"{SRC}/components/settings/settings-cards.ts"
SETTINGS_VIEW = f"{SRC}/views/SettingsView.vue"
PASSWORD = f"{SRC}/components/settings/PasswordForm.vue"
PRICES = f"{SRC}/components/settings/PricesTable.vue"
VALUE_TABLE = f"{SRC}/components/settings/ValueTable.vue"
CONTAINERS = f"{SRC}/components/settings/ContainerLimits.vue"
TEST_MODELS = f"{SRC}/components/test/test-models.ts"
TEST_RESULT = f"{SRC}/components/test/TestResult.vue"
TEST_RESULT_MODEL = f"{SRC}/components/test/test-result-model.ts"
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
FRAMES_VIEW_SPEC = FAILED + "frames/frames-view.spec.ts > the editor > "
MODEL_SPEC = FAILED + "frames/frames-model.spec.ts > frame lines > "

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
              + "the saved form > is kept under relay_test_form with the model and the nine fields, and nothing else")
BODY = FORM_SPEC + "the request body > "
BODY_KINDS = BODY + ("sends duration for a video that names its lengths and for nothing else, style for an image and "
                     "not for a video")
BODY_TRIM = BODY + "sends ratio, count as n, and title trimmed, and leaves empty ones out"
BODY_RESOLUTION = BODY + ("sends the resolution in options for a model that names resolutions, under one the JSON "
                          "sets, and not for others")
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
@mutation(FORM, "if (resolutionsOf(model).length && resolution) {", "if (resolution) {", expect=BODY_RESOLUTION)
@mutation(FORM, "body.options = { resolution, ...own };", "body.options = { ...own, resolution };",
          expect=BODY_RESOLUTION)
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
PICKER = VIEW_SPEC + "the model picker and the wallet > "
WALLET_ROWS = PICKER + "shows the wallet limits of /v1/points as rows: day and total, and unlimited with its use"
DURATION_SELECT = PICKER + "a model with a duration list gets a select of them, and the heading names them"
NOT_TAKEN = (PICKER + "a model that takes no ratio, style or image (meta) shows none of them and sends none, even "
             + "ones saved for another model")
OWN_STYLES = PICKER + "a dola image model offers its own styles when the provider's options name none"
DISPLAY_NAME = (PICKER + "a model chip shows its display name, else its id; the chosen one has a check and what "
                + "it sends below")
RESULT_CARD = (VIEW_SPEC + "the result card > shows the step and word of a job in progress, its short id "
               + "and the fields it carries, and the id opens the job")
FAILED_JOB = VIEW_SPEC + "the result card > a finished job has no empty slot, and a failed one shows the reason"


@mutation(TEST_VIEW, "session.reset();", "", expect=SIGN_OUT)
@mutation(TEST_VIEW, "generate.stop();\n  session.reset();", "session.reset();", expect=SIGN_OUT)
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
@mutation(TEST_VIEW, "} finally {\n    loadingModels.value = false;", "} finally {", expect=LOAD_FREE)
@mutation(TEST_VIEW, "form[name] = value;", "", expect=DURATION_SELECT)
@mutation(TEST_VIEW, 'ratio: hasRatio.value ? form.ratio : "",', "ratio: form.ratio,", expect=NOT_TAKEN)
@mutation(TEST_VIEW, "const picked = images ? files.value : [];", "const picked = files.value;", expect=NOT_TAKEN)
@mutation(TEST_MODELS, "?.max_images !== 0;", "?.max_images !== -1;", expect=NOT_TAKEN)
@mutation(TEST_VIEW, ": stylesOf(chosen.value).map(", ": [].map(", expect=OWN_STYLES)
@mutation(TEST_MODELS, "wallet.day_limit === null && wallet.total_limit === null", "false", expect=WALLET_ROWS)
@mutation(TEST_MODELS, "`${wallet.used_day} / ${wallet.day_limit}`", "`${wallet.used_day}`", expect=WALLET_ROWS)
@mutation(TEST_MODELS, '`${head ? `${head} or ` : ""}${last} s`', '`${head ? `${head}, ` : ""}${last} s`',
          expect=DURATION_SELECT)
@mutation(TEST_MODELS, "typeof m.name === \"string\" && m.name ? m.name : m.id", "m.id", expect=DISPLAY_NAME)
@mutation(TEST_RESULT, "ui.openJob(props.job.id)", "ui.openJob('')", expect=RESULT_CARD)
@mutation(TEST_RESULT_MODEL, 'waiting: "2/4",', 'waiting: "3/4",', expect=RESULT_CARD)
@mutation(TEST_RESULT_MODEL, "return job.stage || job.status;", "return job.status;", expect=RESULT_CARD)
@mutation(TEST_RESULT_MODEL, 'add("Created", clockTime(job.created_at));', "", expect=RESULT_CARD)
@mutation(TEST_RESULT, 'typeof props.job?.error === "string" ? props.job.error : ""', '""', expect=FAILED_JOB)
@pytest.mark.web_unit
def test_test_view_rules():
    vitest("tests/unit/test/test-view.spec.ts")


# ---- page-test-login: the Test page drawn as the design (the result card frames, the wallet order, the play box) -----
WALLET_ORDER = PICKER + "lists the points wallet first, then the caps by name, whatever order the points answer has"
FRAMES_OF_RESULT = VIEW_SPEC + "the result card frames > "
DONE_FRAME = (FRAMES_OF_RESULT + "draws a done job as a title bar over the media and the ledger: job, model, duration "
              + "and time taken")
FAILED_CODE = (VIEW_SPEC + "the result card > a failed job with a code names it in bold before the reason, as the "
               + "job drawer does")
BY_VALUE = PICKER + "offers and sends a ratio by the value the provider takes, not by its label"
STYLE_VALUE = PICKER + "shows a style by its label and sends the value the provider takes"
MODEL_RATIOS = (PICKER + "lists the picked model's own ratios over the provider's: a video model offers 21:9 and "
                + "not 2:3")
RATIO_FALLBACK = (PICKER + "puts the model's default in place of a ratio the next model does not take, and never "
                  + "sends it")
REFUNDED_POINTS = (VIEW_SPEC + "the result card > a refunded failed job strikes its charge, then 0 and refunded, as "
                   + "the job drawer does")
OPTION_VALUE = FORM_SPEC + "optionValue > takes the value a provider takes, and the label only when there is no value"


@mutation(TEST_MODELS, ".sort(([a], [b]) => a.localeCompare(b))", ".sort(([a], [b]) => b.localeCompare(a))",
          expect=WALLET_ORDER)
@mutation(TEST_MODELS, '["points", answer.points] as const,', '["points", null] as const,', expect=WALLET_ORDER)
@mutation(TEST_RESULT, 'typeof props.job?.error_code === "string" ? props.job.error_code : ""', '""',
          expect=FAILED_CODE)
@mutation(TEST_VIEW, "(extras.ratios ?? []).map(optionValue)", "(extras.ratios ?? []).map(optionLabel)",
          expect=BY_VALUE)
@mutation(TEST_VIEW, "({ value: optionValue(o), label: optionLabel(o) })",
          "({ value: optionLabel(o), label: optionLabel(o) })", expect=STYLE_VALUE)
@mutation(TEST_VIEW, "label: optionLabel(o) })", "label: optionValue(o) })", expect=STYLE_VALUE)
@mutation(TEST_VIEW, "return own.length ? own : withSaved(ratios.value, form.ratio);",
          "return withSaved(ratios.value, form.ratio);", expect=MODEL_RATIOS)
@mutation(TEST_VIEW, "if (own.length && form.ratio && !own.includes(form.ratio)) form.ratio = defaultRatioOf(model);",
          "", expect=RATIO_FALLBACK)
@mutation(TEST_MODELS, 'typeof ratio === "string" && ratiosOf(m).includes(ratio) ? ratio : ""', '""',
          expect=RATIO_FALLBACK)
@mutation(TEST_RESULT_MODEL, 'if (typeof job.points === "number") {', "if (false) {", expect=RESULT_CARD)
@mutation(TEST_RESULT_MODEL, "job.refunded === true", "false", expect=REFUNDED_POINTS)
@mutation(TEST_RESULT_MODEL, 'if (took) rows.push({ label: "Took", value: took });', "", expect=DONE_FRAME)
@mutation(TEST_RESULT_MODEL, "return fmtTook(ms);", "return `${Math.round(ms / 1000)}s`;", expect=DONE_FRAME)
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
TABLE = SETTINGS_SPEC + "the tabs > "
DIRTY = TABLE + "Save is disabled until a value changed, the field says edited, and an edit back disables it again"
SAVE_SENDS = TABLE + "Save sends the parsed value, reloads both lists and says Saved"
SCOPE = (SETTINGS_SPEC
         + "measured prices > follows the global provider filter and reloads only the prices when it changes")
RESET_RULE = TABLE + "Reset is offered for a stored value only, deletes it and says Reset"
NAV_MOVES = (SETTINGS_SPEC + "the General links > lists the five other sections in order, marks the first as "
             + "current and moves the mark on a click")
NAV_AREA = (SETTINGS_SPEC + "the General links > scrolls the page's scroll area to the card minus the anchor, "
            + "and nothing else")
NAV_WINDOW = (SETTINGS_SPEC + "the General links > scrolls the window where the page has no scroll area of its own "
              + "(a phone)")
REFUSED = TABLE + "a refused value is a toast and a line under the field, keeps what was typed, and no Saved"
MODELS_SAVE = TABLE + ("a model switch and a price input edit the drafts; Save sends each changed setting, then "
                       "reads again")
STRIP_PICK = TABLE + ("draws General first, then a tab per provider; a pick shows that provider's sections, "
                      "General its own")
RULES = SETTINGS_SPEC + "the card rules > "
PRICES_SPEC = SETTINGS_SPEC + "measured prices > "
PRICES_FIGURES = PRICES_SPEC + "shows duration as Ns or -, flags a disagreement and shows 0 for none"
PASSWORD_SPEC = SETTINGS_SPEC + "change password > "
LOGIN = PASSWORD_SPEC + "sends both passwords, ends the session here and lands on login"
OLD_FIELD = PASSWORD_SPEC + "shows a refusal that names old_password on that field, and stays"
NEW_FIELD = PASSWORD_SPEC + "shows a refusal that names new_password on that field"
MARGIN = (SETTINGS_SPEC + "the model tables and the bar > count the margin over the tiers that have both a sale "
          + "price and a cost, toned by the lowest")
POOLS_RESET = (SETTINGS_SPEC + "the pools editor > tells the reset on the GMT+7 clock and stores the zone of "
               + "a whole hour")
POOLS_EMPTY = (SETTINGS_SPEC + "the pools editor > drops an emptied tier, gives a pool with no window a day window "
               + "in the shared zone, and an edit back is no change")
STRING_VALUE = 'typeof item.value === "string" ? item.value : JSON.stringify(item.value)'
BOTH_PARAMS = 'err.param === "old_password" || err.param === "new_password"'


@mutation(SETTING_VALUE, "return text;", "return null;", expect=PARSE)
@mutation(SETTING_VALUE, STRING_VALUE, "JSON.stringify(item.value)", expect=TEXT)
@mutation(SETTINGS_VIEW, "return !!item && drafts[key] !== valueText(item);", "return !!item;", expect=DIRTY)
@mutation(SETTING_FIELD, 'props.item.source === "db"', "true", expect=RESET_RULE)
@mutation(SETTINGS_VIEW, "        errors[key] = errorText(err);\n", "", expect=REFUSED)
@mutation(SETTINGS_VIEW, "for (const item of list) if (!(item.key in errors)) drafts[item.key]",
          "for (const item of list) if (true) drafts[item.key]", expect=REFUSED)
@mutation(SETTINGS_VIEW, "for (const key of keys.filter(edited)) {", "for (const key of keys.slice(0, 1)) {",
          expect=MODELS_SAVE)
@mutation(SETTINGS_VIEW, 'groups.value.includes(scope.provider) ? scope.provider : "core"', '"core"',
          expect=STRIP_PICK)
@mutation(SETTINGS_CARDS, "if (!card.basic.length) [card.basic, card.advanced] = [card.advanced, []];", "",
          expect=RULES + "put the claims switch, the models and the prices in their own place, basic first, the rest "
                         "under Advanced")
@mutation(SETTINGS_CARDS, "...new Set([...(item?.keys ?? []), ...Object.keys(table)]),",
          "...new Set([...Object.keys(table)]),",
          expect=RULES + "draw a blank input for every declared option the table does not hold")
@mutation(SETTINGS_CARDS, "  if (!text.trim()) return JSON.stringify(rest);\n", "",
          expect=RULES + "draw a blank input for every declared option the table does not hold")
@mutation(SETTINGS_CARDS, '"costs", "pools", "send_path"] as const', '"pools", "send_path"] as const',
          expect=RULES + "put the costs in their own place beside the prices")
@mutation(SETTINGS_CARDS, '(input.label === "points" ? "cost" : `${input.label} cost`)', "input.label",
          expect=RULES + "put the costs in their own place beside the prices")
@mutation(SETTINGS_CARDS, '.filter((k) => names.includes(k.split("@")[0]!))', ".filter((k) => names.includes(k))",
          expect=RULES + "find a model's prices by its id or an alias, a tier after @, and the keys no model has")
@mutation(SETTINGS_CARDS, '/^\\d+$/.test(text.trim()) ? Number(text.trim()) : text', "Number(text)",
          expect=RULES + "send a whole price as a number and anything else as typed")
@mutation(SETTINGS_CARDS, "return same ? valueText(item) : JSON.stringify(next);", "return JSON.stringify(next);",
          expect=RULES + "keep the paused models in the catalog's order, and a switch off and on again is no change")
@mutation(SETTINGS_VIEW, "current.value = id;", "", expect=NAV_MOVES)
@mutation(SETTINGS_VIEW, "if (area) area.scrollTo({ top: area.scrollTop + offset });",
          'if (area) card.scrollIntoView({ block: "start" });', expect=NAV_AREA)
@mutation(SETTINGS_VIEW, "else window.scrollTo({ top: window.scrollY + offset });",
          'else card.scrollIntoView({ block: "start" });', expect=NAV_WINDOW)
@mutation(SETTINGS_VIEW, "- (area ? area.getBoundingClientRect().top : 0) - anchor;",
          "- (area ? area.getBoundingClientRect().top : 0);", expect=NAV_AREA)
@mutation(PRICES, 'const NONE = "—";', 'const NONE = "-";', expect=PRICES_FIGURES)
@mutation(PRICES, ': "0"', ': "none"', expect=PRICES_FIGURES)
@mutation(SETTINGS_VIEW, 'await putSetting(key, parseValue(drafts[key] ?? ""));',
          'await putSetting(key, drafts[key] ?? "");', expect=SAVE_SENDS)
@mutation(SETTINGS_VIEW, "watch(() => scope.provider, loadPrices);", "watch(() => scope.provider, () => undefined);",
          expect=SCOPE)
@mutation(SETTINGS_VIEW, "getPrices(scope.provider)", 'getPrices("")', expect=SCOPE)
@mutation(SETTINGS_CARDS, 'tone: lo < 0 ? "bad" : lo === 0 ? "warn" : "ok"', 'tone: "ok"', expect=MARGIN)
@mutation(SETTINGS_POOLS, "if (!text.trim()) delete w.budget[tier];", "if (!text.trim()) w.budget[tier] = 0;",
          expect=POOLS_EMPTY)
@mutation(SETTINGS_POOLS, "  if (off < -12 * 60) off += DAY_MIN;\n", "", expect=POOLS_RESET)
@mutation(SETTINGS_POOLS, "if (!had && !text.trim()) return finish(pools, item);", "", expect=POOLS_EMPTY)
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
LOAD_OFF = FRAMES_SPEC + "busy states > a load turns every control off except the pickers"
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
EDITOR_SPEC = FRAMES_SPEC + "the editor > "
ASKS_FRAME = (EDITOR_SPEC + "asks before another frame replaces an unsaved draft; Keep editing changes nothing, "
              + "Discard picks it")
ASKS_PROVIDER = (EDITOR_SPEC + "asks before another provider replaces an unsaved draft too, "
                 + "and a clean draft goes at once")
READ_FAILED_KEEPS = EDITOR_SPEC + "keeps the frame it had, after the toast, when the read of the next one fails"
RELOAD_ASKS = FRAMES_SPEC + "an action that reloads the editor over an unsaved draft > "
COMES_BACK = FRAMES_SPEC + "opening > comes back to the frame it had"
FORGETS_FRAME = FRAMES_SPEC + "opening > forgets the remembered frame when the remembered provider is gone"
PREVIEW_MODEL = "t.target === DEFAULT_TARGET ? kindOf(t.kind)?.models[0]?.model : t.target"
QUIET = FRAMES_SPEC + "the quiet preview > "
QUIET_DRAFT = QUIET + "drops an answer for a draft that was replaced while it was on its way"
QUIET_EDITOR = (QUIET + "drops an answer for a frame that was replaced while it was on its way, "
                + "though the text is the same")
QUIET_NOTHING = QUIET + "sends nothing while a load or a write is on, or for a kind with no model"
QUIET_REFUSED = QUIET + "shows no refusal and no toast: a draft the server refuses just has no preview"
QUIET_BUSY = QUIET_NOTHING
DIRTY = FRAMES_VIEW_SPEC + "Save is disabled until the draft differs from the loaded text, and 'unsaved' says so"
SAME_FRAME = EDITOR_SPEC + "picking the frame that is already in the editor does nothing"
SETTLES = (FRAMES_SPEC + "opening > falls back to the first kind when the one it had is gone, "
           + "and to the default when its model is gone")


@mutation(FRAMES, STALE_ERROR_GUARD, "throw err;", expect=STALE_FAILURE)
@mutation(FRAMES, STALE_ANSWER_GUARD, "draw(answer);", expect=STALE_ANSWER)
@mutation(FRAMES, "gen++;\n    settle(p, k, t);", "settle(p, k, t);", expect=STALE_ANSWER)
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
@mutation(FRAMES, "draft.value === text && previewPrompt.value === prompt && mine === gen",
          "previewPrompt.value === prompt && mine === gen", expect=QUIET_DRAFT)
@mutation(FRAMES, "draft.value === text && previewPrompt.value === prompt && mine === gen",
          "draft.value === text && previewPrompt.value === prompt", expect=QUIET_EDITOR)
@mutation(FRAMES, "} catch {\n      if (stillAsked()) preview.value = null;", "} catch {", expect=QUIET_REFUSED)
@mutation(FRAMES, "if (!t || controlsOff.value) return;\n    const model = previewModel(t);",
          "if (!t) return;\n    const model = previewModel(t);", expect=QUIET_BUSY)
@mutation(FRAMES, "if (drew) applied.value = answer;", "applied.value = answer;", expect=APPLY_MOVED)
@mutation(FRAMES, "drew = await loadFrames(false, t, started);", "drew = await loadFrames(false, t);",
          expect=APPLY_MOVED)
@mutation(FRAMES, PREVIEW_MODEL, "t.target", expect=PREVIEW_DEFAULT)
@mutation(FRAMES, "Object.assign(memory, { provider: p, kind: k, target: t });", "", expect=OWN_PROVIDER)
@mutation(FRAMES, 'names.includes(preferred) ? preferred : (names[0] ?? "")', 'names[0] ?? ""', expect=START_PROVIDER)
@mutation(FRAMES, "Object.assign(memory, { provider: p, kind: k, target: t });", "", expect=COMES_BACK)
@mutation(FRAMES, 'kind = target = memory.kind = memory.target = "";', "", expect=FORGETS_FRAME)
@mutation(FRAMES, "=> dirty.value && !(await askDiscard());", "=> false;",
          expect=RELOAD_ASKS + "Follow the default asks first, and Keep editing leaves the draft and sends nothing")
@mutation(FRAMES, "=> dirty.value && !(await askDiscard());", "=> dirty.value;",
          expect=RELOAD_ASKS + "Roll back asks first, and Keep editing leaves the draft and sends nothing")
@mutation(FRAMES, "shown.value.target === editTarget) return;\n      if (dirty.value && !(await askDiscard())) return;",
          "shown.value.target === editTarget) return;", expect=ASKS_FRAME)
@mutation(FRAMES, "!failed.value) return;\n      if (dirty.value && !(await askDiscard())) return;",
          "!failed.value) return;", expect=ASKS_PROVIDER)
@mutation(FRAMES, "shown.value?.kind === editKind && shown.value.target === editTarget",
          "false", expect=SAME_FRAME)
@mutation(FRAMES, 'target: row.kind === at.kind && known ? at.target : defaultTargetOf(row),',
          "target: at.target,", expect=SETTLES)
@mutation(FRAMES, "if (await keepsDraft()) return;\n      const started = gen;\n" +
          "      const done = await changing(() =>\n"
          "        guarded(async () => {\n          await putFrame(t, null);",
          "const started = gen;\n      const done = await changing(() =>\n        guarded(async () => {\n"
          "          await putFrame(t, null);",
          expect=RELOAD_ASKS + "Follow the default asks first, and Keep editing leaves the draft and sends nothing")
@mutation(FRAMES, "if (await keepsDraft()) return;\n      const started = gen;\n" +
          "      const done = await changing(() =>\n"
          "        guarded(async () => {\n          const answer = await rollbackFrame",
          "const started = gen;\n      const done = await changing(() =>\n        guarded(async () => {\n"
          "          const answer = await rollbackFrame",
          expect=RELOAD_ASKS + "Roll back asks first, and Keep editing leaves the draft and sends nothing")
@mutation(FRAMES, "if (mine === gen) settle(at.provider, before.kind, before.target);", "", expect=READ_FAILED_KEEPS)
@mutation(FRAMES, "previewFrame(model, previewPrompt.value, text)",
          "previewFrame(model, previewPrompt.value, loaded.value)", expect=PREVIEW_SENDS)
@mutation(FRAMES, "warnings.value = saved.warnings;", "warnings.value = [];", expect=SAVE_WARNS)
@mutation(FRAMES, "await lockFrame(t, locked);", "await lockFrame(t, true);", expect=LOCK_SENDS)
@mutation(FRAMES, "await putFrame(t, null);", 'await putFrame(t, "");', expect=FOLLOW_NULL)
@mutation(FRAMES, "resetFrame(p, resetKind)", 'resetFrame(p, "image")', expect=RESET_KIND)
@mutation(FRAMES, "applied.value = null;\n    preview.value = null;\n    previewPrompt.value = SAMPLE_PROMPT;",
          "applied.value = null;\n    previewPrompt.value = SAMPLE_PROMPT;", expect=REDRAWN)
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
LEAVE = FRAMES_VIEW_SPEC + "leaving the page asks only for a typed draft, and is refused during a write"


@mutation(FRAMES_VIEW, "() => frames.dirty,", "() => false,", expect=LEAVE)
@mutation(FRAMES, "shown.value !== null && draft.value !== loaded.value", "false", expect=DIRTY)
@mutation(FRAMES_VIEW, "() => frames.busy,", "() => false,", expect=LEAVE)
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
KINDS_SPEC = FAILED + "frames/frames-model.spec.ts > the kinds > "
VIDEO_FIRST = KINDS_SPEC + "video comes first and the input is left alone"
KIND_OPENS = KINDS_SPEC + "a kind opens on its first own frame, else its default; the counts say who follows"
TEXT_SPEC = FAILED + "frames/frames-model.spec.ts > the frame text > "
CHECKS = TEXT_SPEC + "each marker must be in once; the checks count them"
PICKS = TEXT_SPEC + "picks the sample prompt and the tracking code out of a preview, in the order they occur"


@mutation(FRAMES_MODEL, ': "no version"', ': "none"', expect=MODEL_SPEC + "version tag")
@mutation(FRAMES_MODEL, '"unlocked"} ${dayMonth(', '"locked"} ${dayMonth(',
          expect=MODEL_SPEC + "lock cell, lock sentence and lock history")
@mutation(FRAMES_MODEL, '"Unlocked"} ${dayMonth(', '"Locked"} ${dayMonth(',
          expect=MODEL_SPEC + "lock cell, lock sentence and lock history")
@mutation(FRAMES_MODEL, "MONTHS[d.getMonth()]", "MONTHS[d.getMonth() + 1]",
          expect=MODEL_SPEC + "day and time are day, month name and a two digit clock, in the browser time zone")
@mutation(FRAMES_MODEL, "two(d.getHours())}:${two(d.getMinutes())", "two(d.getMinutes())}:${two(d.getHours())",
          expect=MODEL_SPEC + "day and time are day, month name and a two digit clock, in the browser time zone")
@mutation(FRAMES_MODEL, 'Number(b.kind === "video") - Number(a.kind === "video")', "0", expect=VIDEO_FIRST)
@mutation(FRAMES_MODEL, 'kind.models.find((m) => m.source === "own")?.model ?? DEFAULT_TARGET',
          "DEFAULT_TARGET", expect=KIND_OPENS)
@mutation(FRAMES_MODEL, "kind.models.length - ownCount(kind)", "kind.models.length", expect=KIND_OPENS)
@mutation(FRAMES_MODEL, "return { prompt, tracking, ok: prompt === 1 && tracking === 1 };",
          "return { prompt, tracking, ok: prompt >= 1 && tracking >= 1 };", expect=CHECKS)
@mutation(FRAMES_MODEL, "if (f.at < from) continue;", "", expect=PICKS)
@mutation(FRAMES_MODEL, ".filter((n) => n)\n", "\n", expect=PICKS)
@mutation(FRAMES_MODEL, "info.current && info.current.text !== null", "info.current && true", expect=LOADED_TEXT)
@mutation(FRAMES_MODEL, "`${err.message} (${err.param})` : err.message", "err.message : err.message",
          expect=MODEL_SPEC + "a refusal carries its field")
@mutation(FRAMES_MODEL, 'names.join(", ") || "-"', 'names.join(", ")', expect=SUMMARY)
@pytest.mark.web_unit
def test_frames_lines():
    vitest("tests/unit/frames/frames-model.spec.ts")


# ---- page-frames-settings round: the drawn section titles, a refusal as a field error, the password error's field ---
TITLES = (FAILED + "frames/frames-view.spec.ts > the page > lists the provider with its kinds on the left: "
          + "Video and Image, where each text comes from, the open kind marked")


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


@mutation(SETTINGS_VIEW, 'errorText(err), "the settings")', 'errorText(err), "the jobs")', expect=LOAD_WORDS)
@mutation(SETTINGS_VIEW, 'errorText(err), "the measured prices")', "errorText(err))", expect=LOAD_WORDS)
@mutation(SETTING_VALUE, ' || value === ""', "", expect=BOOT_DASH)
@mutation(SETTING_VALUE, "if (password.length < PASSWORD_MIN)", "if (password.length < 1)", expect=PASSWORD_RULE)
@mutation(SETTING_VALUE, "if (password.length > PASSWORD_MAX)", "if (false)", expect=PASSWORD_RULE)
@mutation(PASSWORD, "if (fieldErrors.new_password) return;", "", expect=FORM_CHECK)
@mutation(SETTING_VALUE, "shown.length > LONG_DEFAULT", "shown.length > 1000", expect=LONG_DEFAULT)
@mutation(SETTING_VALUE, "JSON.stringify(value, null, 2)", "JSON.stringify(value)", expect=LONG_DEFAULT)
@mutation(SETTINGS_VIEW, "settingsLoading.value && !items.value.length && !settingsError.value", "false",
          expect=LOADING_ROWS)
@mutation(PRICES, "props.loading && !props.rows.length && !props.error", "false", expect=LOADING_ROWS)
@mutation(VALUE_TABLE, "props.loading && !props.rows.length && !props.error", "false", expect=LOADING_ROWS)
@mutation(CONTAINERS, "props.loading && !props.rows.length && !props.error", "false", expect=LOADING_ROWS)
@pytest.mark.web_unit
def test_settings_errors_as_drawn():
    vitest("tests/unit/settings/settings.spec.ts")
