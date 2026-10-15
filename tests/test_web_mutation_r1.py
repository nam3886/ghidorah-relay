"""Mutation markers of the Jobs views (cursor stack, selection, bulk actions, regenerate controls, media safety, the
job detail's actions): each breaks one rule in the TypeScript source and demands that its Vitest spec turns red on the
exact test that guards the rule (scripts/mutate.py runs these, no rebuild). An `expect` is the failed-test line the
runner prints: `FAILED <spec> > <describe> > <test title>`."""

import pytest
from webunit import vitest

from relay_core.testing.mutation import mutation

SRC = "apps/admin-web/src"
STACK = f"{SRC}/components/jobs/cursor-stack.ts"
PICK = f"{SRC}/components/jobs/selection.ts"
TEXT = f"{SRC}/components/jobs/job-text.ts"
KEPT = f"{SRC}/components/jobs/kept-key.ts"
BLOB = f"{SRC}/components/jobs/blob-actions.ts"
CONTROL = f"{SRC}/components/jobs/RegenerateControl.vue"
MEDIA = f"{SRC}/components/jobs/JobMedia.vue"
ACTIONS = f"{SRC}/components/jobs/JobActions.vue"
DIALOG = f"{SRC}/components/jobs/JobDetailDialog.vue"
VIEW = f"{SRC}/views/JobsView.vue"
FILTERS = f"{SRC}/components/jobs/job-filters.ts"
API = f"{SRC}/api/jobs.ts"
DETAIL_API = f"{SRC}/api/jobs-detail.ts"

STACK_SPEC = "tests/unit/jobs/cursor-stack.spec.ts"
PICK_SPEC = "tests/unit/jobs/selection.spec.ts"
TEXT_SPEC = "tests/unit/jobs/job-text.spec.ts"
API_SPEC = "tests/unit/jobs/jobs-api.spec.ts"
BLOB_SPEC = "tests/unit/jobs/blob-actions.spec.ts"
CONTROL_SPEC = "tests/unit/jobs/regenerate-control.spec.ts"
VIEW_SPEC = "tests/unit/jobs/jobs-view.spec.ts"
DETAIL_SPEC = "tests/unit/jobs/job-detail.spec.ts"

# the start of every failed-test line, by spec (the describe and the title follow)
FS = "FAILED " + STACK_SPEC + " > "
FP = "FAILED " + PICK_SPEC + " > "
FT = "FAILED " + TEXT_SPEC + " > "
FA = "FAILED " + API_SPEC + " > "
FB = "FAILED " + BLOB_SPEC + " > "
FC = "FAILED " + CONTROL_SPEC + " > "
FV = "FAILED " + VIEW_SPEC + " > "
FD = "FAILED " + DETAIL_SPEC + " > "


# ---- the keyset pager's cursor stack-----------------------------------------------------------------------------
SAME = FS + "cursor stack > forward and back give the same pages, asking for the same cursors"
CUT = FS + "cursor stack > a reload of an earlier page cuts the pages after it and takes its new cursor"
NEXT_KEPT = FS + "cursor stack > remembers where the next page starts once a page arrives with a cursor"


@mutation(STACK, "cursors.value = cursors.value.slice(0, at + 1);", "cursors.value = cursors.value;", expect=CUT)
@mutation(STACK, "if (next) cursors.value.push(next);", "void next;", expect=NEXT_KEPT)
@mutation(STACK, "Math.max(0, index.value - 1)", "index.value - 1",
          expect=FS + "cursor stack > Prev never goes below the first page")
@mutation(STACK, "cursors.value.length > index.value + 1", "cursors.value.length >= index.value + 1",
          expect=FS + "cursor stack > starts on page 1 with no cursor and nothing after it")
@mutation(STACK, "nextIndex: (): number => index.value + 1,", "nextIndex: (): number => index.value,", expect=SAME)
@mutation(STACK, "index.value = at;", "void at;", expect=SAME)
@pytest.mark.web_unit
def test_the_cursor_stack_gives_the_same_pages_forward_and_back():
    vitest(STACK_SPEC)


# ---- the selection-----------------------------------------------------------------------------------------------
@mutation(PICK, "items.filter((job) => picked.has(job.id))", "items.filter(() => true)",
          expect=FP + "selection > leaves out a ticked id that is no longer on the page")
@mutation(PICK, "if (on) next.add(id);", "if (true) next.add(id);",
          expect=FP + "selection > ticks and clears one job without touching the old set")
@mutation(PICK, "on ? new Set(items.map((job) => job.id)) : new Set()", "new Set(items.map((job) => job.id))",
          expect=FP + "selection > select all ticks every job of the page, and none clears them")
@pytest.mark.web_unit
def test_the_selection_is_the_ticked_jobs_of_the_page_in_table_order():
    vitest(PICK_SPEC)


# ---- the calls----------------------------------------------------------------------------------------------------
WORDS = FD + "conclusion, prompt and technical > the prompt is the customer's words and Copy prompt copies exactly that"
BARE = FA +"one job > re-check, fetch title, resend and revoke are bare POSTs"
PROXY = FA +"files > an output without a link opens through the outputs proxy, an input through the images proxy"


@mutation(API, "regenerateCall(bulkAction(jobIds), ", "regenerateCall(bulkAction([]), ",
          expect=FA + "bulk actions > the same selection after a 503 sends the same key, in any order")
@mutation(DETAIL_API, "regenerateCall(jobAction(id), ", 'regenerateCall(jobAction("x"), ',
          expect=FA + "one job > regenerate posts only the key, kept under the action job:<id>")
@mutation(API, "key_id: q.keyId, ", "",
          expect=FA + "list > sends the status, provider, key and cursor that are set")
@mutation(API, '"queued", "submitting", ', '"queued", ',
          expect=FA + "list > the status filter offers submitting beside the others")
@mutation(DETAIL_API, 'role === "output" ? ', 'role !== "output" ? ', expect=PROXY)
@mutation(DETAIL_API, "`${path(id)}/resend-webhook`", "`${path(id)}/resend-webhooks`", expect=BARE)
@mutation(DETAIL_API, "`${path(id)}/revoke-links`", "`${path(id)}/revoke-link`", expect=BARE)
@pytest.mark.web_unit
def test_the_calls_send_the_paths_the_filters_and_one_key_per_action():
    vitest(API_SPEC)


# ---- toast texts and badges---------------------------------------------------------------------------------------
SPLIT = FT + "job text > the regenerate toast splits made, already made and failed"


@mutation(TEXT, "fetching: 3", "fetching: 2", expect=FT + "job text > the bar for fetching is at step 3")
@mutation(TEXT, "job.price > 0 && ", "",
          expect=FT + "job text > refunded means failed, charged, and nothing left charged")
@mutation(TEXT, 'type: skipped ? "info" : "success",', 'type: "success",',
          expect=FT + "job text > the re-check toast adds the jobs left alone and turns to info")
@mutation(TEXT, "out.created.length - replayed", "out.created.length", expect=SPLIT)
@mutation(TEXT, 'type: failed ? "info" : "success",', 'type: "success",', expect=SPLIT)
@pytest.mark.web_unit
def test_the_bulk_toasts_and_the_progress_bar_keep_their_rules():
    vitest(TEXT_SPEC)


# ---- blob files and the kept key record---------------------------------------------------------------------------
OPENS = FB + "blob files > opens in a new tab without an opener, then revokes the URL a minute later"


@mutation(BLOB, 'window.open(url, "_blank", "noopener")', 'window.open(url, "_blank")', expect=OPENS)
@mutation(BLOB, "const REVOKE_AFTER_MS = 60_000;", "const REVOKE_AFTER_MS = 600_000;", expect=OPENS)
@mutation(BLOB, "link.remove();", "void link;",
          expect=FB + "blob files > saves under the given name through a link that is gone right after")
@mutation(KEPT, 'typeof key === "string" && KEY_PATTERN.test(key)', 'typeof key === "string"',
          expect=FB + "kept key record > a value the server would refuse is not a kept key")
@pytest.mark.web_unit
def test_blob_urls_open_without_an_opener_and_are_revoked_and_the_kept_key_must_be_a_real_key():
    vitest(BLOB_SPEC)


# ---- the regenerate control---------------------------------------------------------------------------------------
NEW = FC + "start a new regenerate > "
CTRL = FC + "regenerate control > "


@mutation(CONTROL, "if (abandonOperation(props.action))", "if (true)",
          expect=NEW + "tells the owner when the key could not be dropped, and keeps offering it")
@mutation(CONTROL, "if (!ok) return;", "", expect=NEW + "warns of the double charge, and Cancel keeps the key")
@mutation(CONTROL, 'v-if="kept && !busy"', 'v-if="kept"', expect=NEW + "is hidden while a call is in flight")
@mutation(CONTROL, "if (!(await confirmDialog(props.confirm))) return;", "await confirmDialog(props.confirm);",
          expect=CTRL + "asks first with the text it was given, and Cancel sends nothing")
@pytest.mark.web_unit
def test_a_regenerate_asks_first_is_single_flight_and_a_kept_key_is_dropped_only_behind_a_warning():
    vitest(CONTROL_SPEC)


# ---- the Jobs view------------------------------------------------------------------------------------------------
TICKED = FV + "bulk regenerate > sends the ticked ids, says made, already made and failed, and clears the ticks"
KEEPS = (FV + "bulk regenerate > keeps the selection after a failure that is safe to retry, "
         "and the retry sends the same ids")
ALL_IDS = "items.value.map((j) => j.id)"
BULK_ANCHOR = "const out = await bulkRegenerate(ids.value);"
CLEARS_FIRST = "const sent = ids.value; picked.value = new Set(); const out = await bulkRegenerate(sent);"


@mutation(VIEW, "cursor: stack.cursorAt(at),", "cursor: null,",
          expect=FV + "paging > Next asks for the cursor the server gave, and Prev goes back to the page it left")
@mutation(VIEW, "picked.value = new Set(pickedIds(out.items, picked.value));", "picked.value = picked.value;",
          expect=FV + "selection > a new page starts with nothing ticked")
@mutation(FILTERS, "(value: string): string => value || ALL", "(value: string): string => value",
          expect=FV + "filters > shows All statuses and All keys, not the placeholder, while nothing is filtered")
@mutation(FILTERS, 'value === ALL ? "" : value', "value",
          expect=FV + "filters > choosing All again sends no filter, and the label follows a real choice")
@mutation(VIEW, "stack.reset();", "",
          expect=FV + "paging > a filter change whose load fails still forgets the cursors of the old filter")
@mutation(VIEW, "(JOB_STATUSES as readonly string[]).includes(wanted)", "true",
          expect=FV + "filters > a status in the link that is not one of ours is ignored")
@mutation(VIEW, 'if (typeof route.query.key_id === "string") keyId.value = route.query.key_id;', "",
          expect=FV + "filters > a key id in the link sets the key filter")
@mutation(VIEW, "if (!latest.isCurrent(ticket)) return;", "",
          expect=FV + "the list > drops an old answer that arrives after a newer one")
@mutation(VIEW, BULK_ANCHOR, f"const out = await bulkRegenerate({ALL_IDS});", expect=TICKED)
@mutation(VIEW, BULK_ANCHOR, CLEARS_FIRST, expect=KEEPS)
@mutation(VIEW, "const out = await bulkRecheck(sent);", f"const out = await bulkRecheck({ALL_IDS});",
          expect=FV + "bulk re-check > posts the ticked ids in table order, says how many, and clears the ticks")
@mutation(VIEW, ':disabled="!ids.length || rechecking"', ':disabled="rechecking"',
          expect=FV + "selection > the bulk buttons stay off until a job is ticked, and the count follows")
@mutation(VIEW, "Regenerate ${ids.value.length} job(s)? ", "Regenerate job(s)? ",
          expect=FV + "bulk regenerate > asks first, naming the count, today's price and the charge")
@pytest.mark.web_unit
def test_the_jobs_view_pages_filters_selects_and_sends_what_is_ticked():
    vitest(VIEW_SPEC)


# ---- the job detail-----------------------------------------------------------------------------------------------
SHOW = FD + "which actions show > "
EACH = FD + "what each action sends and says > "
MEDIA_GROUP = FD + "results and references > "
SCRIPT_URL = MEDIA_GROUP + "a script URL is never drawn: no preview, and Open goes through the admin session"
OPEN_GONE = MEDIA_GROUP + "an item whose link is gone gets Open, which fetches it as a blob and opens it"
REFERENCE = MEDIA_GROUP + "a reference opens through the input route and has no Download"


@mutation(MEDIA, "const src = safeMediaUrl(item.url);", "const src = item.url;", expect=SCRIPT_URL)
@mutation(MEDIA, 'v-if="!s.kind"', 'v-if="false"', expect=OPEN_GONE)
@mutation(MEDIA, "v-if=\"props.role === 'output'\"", 'v-if="true"', expect=REFERENCE)
@mutation(MEDIA, "fetchMediaBlob(props.job.id, props.role, idx)", 'fetchMediaBlob(props.job.id, "output", idx)',
          expect=REFERENCE)
@mutation(ACTIONS, 'v-if="props.job.can_recheck"', 'v-if="true"',
          expect=SHOW + "a done job offers Recover and Regenerate, and nothing else without more facts")
@mutation(ACTIONS, '<template v-if="finished">', '<template v-if="true">',
          expect=SHOW + "a job still running offers neither Recover nor Regenerate")
@mutation(ACTIONS, "props.job.external_ref && props.job.account_label",
          "props.job.external_ref || props.job.account_label",
          expect=SHOW + "Fetch title needs both the upstream reference and the account")
@mutation(ACTIONS, 'v-if="hasLinks"', 'v-if="true"',
          expect=SHOW + "Revoke share links only while some media still has a link")
@mutation(ACTIONS, "account.value.trim()", "account.value",
          expect=EACH + "Recover sends the trimmed account, polls the command and reads the job again")
@mutation(ACTIONS, "if (!ok) return;", "", expect=EACH + "Revoke asks first and says how many links stopped")
@mutation(ACTIONS, '${out.replayed ? "Already made: job " : "New job "}', '${"New job "}',
          expect=EACH + "a regenerate the server already did says Already made")
@mutation(DIALOG, 'class="block">{{ job.prompt }}</pre>', 'class="block">{{ job.sent_text }}</pre>', expect=WORDS)
@mutation(DIALOG, 'CopyButton :text="job.prompt"', 'CopyButton :text="job.sent_text ?? \'\'"', expect=WORDS)
@mutation(DIALOG, "if (!latest.isCurrent(ticket)) return;\n    job.value = out;", "job.value = out;",
          expect=FD + "opening > drops the answer for a job that is no longer the open one")
@mutation(DIALOG, "e.stage === 'recovered' ? 'ok' : 'neutral'", "e.stage === 'recovered' ? 'neutral' : 'neutral'",
          expect=FD + "timeline > lists the events in order, and marks a recovery")
@pytest.mark.web_unit
def test_the_detail_draws_media_only_from_safe_links_and_shows_the_actions_the_job_allows():
    vitest(DETAIL_SPEC)
