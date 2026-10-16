"""Mutation markers of the Jobs list (offset paging, selection, bulk actions, the calls the list and the detail make,
toast texts): each breaks one rule in the TypeScript source and demands that its Vitest spec turns red on the exact
test that guards the rule (scripts/mutate.py runs these, no rebuild). An `expect` is the failed-test line the runner
prints: `FAILED <spec> > <describe> > <test title>`."""

import pytest
from webunit import vitest

from relay_core.testing.mutation import mutation

SRC = "apps/admin-web/src"
PAGING = f"{SRC}/components/jobs/paging.ts"
PICK = f"{SRC}/components/jobs/selection.ts"
TEXT = f"{SRC}/components/jobs/job-text.ts"
VIEW = f"{SRC}/views/JobsView.vue"
FILTERS = f"{SRC}/components/jobs/job-filters.ts"
API = f"{SRC}/api/jobs.ts"
DETAIL_API = f"{SRC}/api/jobs-detail.ts"

PAGING_SPEC = "tests/unit/jobs/paging.spec.ts"
PICK_SPEC = "tests/unit/jobs/selection.spec.ts"
TEXT_SPEC = "tests/unit/jobs/job-text.spec.ts"
API_SPEC = "tests/unit/jobs/jobs-api.spec.ts"
VIEW_SPEC = "tests/unit/jobs/jobs-view.spec.ts"

# the start of every failed-test line, by spec (the describe and the title follow)
FG = "FAILED " + PAGING_SPEC + " > "
FP = "FAILED " + PICK_SPEC + " > "
FT = "FAILED " + TEXT_SPEC + " > "
FA = "FAILED " + API_SPEC + " > "
FV = "FAILED " + VIEW_SPEC + " > "


# ---- the offset math of the pager---------------------------------------------------------------------------------
@mutation(PAGING, "export const PAGE_SIZE = 10;", "export const PAGE_SIZE = 50;",
          expect=FG + "paging > starts at 10 a page and offers 10, 20, 50 and 100")
@mutation(PAGING, "[10, 20, 50, 100]", "[10, 20, 50]",
          expect=FG + "paging > starts at 10 a page and offers 10, 20, 50 and 100")
@pytest.mark.web_unit
def test_the_jobs_list_pages_by_10_with_the_sizes_10_20_50_and_100():
    vitest(PAGING_SPEC)


# ---- the selection-----------------------------------------------------------------------------------------------
@mutation(PICK, "items.filter((job) => picked.has(job.id))", "items.filter(() => true)",
          expect=FP + "selection > leaves out a ticked id that is no longer on the page")
@mutation(PICK, "items.filter((job) => picked.has(job.id)).map((job) => job.id)",
          "items.filter((job) => picked.has(job.id)).map((job) => job.id).reverse()",
          expect=FP + "selection > gives the ticked jobs in table order")
@pytest.mark.web_unit
def test_the_selection_is_the_ticked_jobs_of_the_page_in_table_order():
    vitest(PICK_SPEC)


# ---- the calls----------------------------------------------------------------------------------------------------
BARE = FA +"one job > re-check, fetch title, resend and revoke are bare POSTs"
PROXY = FA +"files > an output without a link opens through the outputs proxy, an input through the images proxy"


@mutation(API, "regenerateCall(bulkAction(jobIds), ", "regenerateCall(bulkAction([]), ",
          expect=FA + "bulk actions > the same selection after a 503 sends the same key, in any order")
@mutation(DETAIL_API, "regenerateCall(jobAction(id), ", 'regenerateCall(jobAction("x"), ',
          expect=FA + "one job > regenerate posts only the key, kept under the action job:<id>")
@mutation(API, ", key_id: q.keyId", "",
          expect=FA + "list > sends the status, provider and key that are set, with the offset and never a cursor")
@mutation(API, "offset: q.offset, ", "",
          expect=FA + "list > sends the page size and offset and drops every empty filter")
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


# ---- the Jobs view------------------------------------------------------------------------------------------------
TICKED = FV + "bulk regenerate > sends the ticked ids, says made, already made and failed, and clears the ticks"
KEEPS = (FV + "bulk regenerate > keeps the selection after a failure that is safe to retry, "
         "and the retry sends the same ids")
ALL_IDS = "items.value.map((j) => j.id)"
BULK_ANCHOR = "const out = await bulkRegenerate(ids.value);"
CLEARS_FIRST = "const sent = ids.value; picked.value = new Set(); const out = await bulkRegenerate(sent);"


@mutation(VIEW, "offset: at,", "offset: 0,",
          expect=FV + "paging > a picked page asks for its offset, and the pager follows the page that arrived")
@mutation(VIEW, "limit.value = size;", "void size;",
          expect=FV + "paging > a new page size starts again from the first page, with the new limit")
@mutation(VIEW, "load(at: number = offset.value)", "load(at: number = 0)",
          expect=FV + "paging > a reload (Refresh, a bulk action) stays on the page on screen")
@mutation(VIEW, "    offset.value = at;", "    offset.value = offset.value;",
          expect=FV + "paging > a picked page asks for its offset, and the pager follows the page that arrived")
@mutation(FILTERS, "(value: string): string => value || ALL", "(value: string): string => value",
          expect=FV + "filters > shows All statuses and All keys, not the placeholder, while nothing is filtered")
@mutation(FILTERS, 'value === ALL ? "" : value', "value",
          expect=FV + "filters > choosing All again sends no filter, and the label follows a real choice")
@mutation(VIEW, "offset.value = 0;", "void 0;",
          expect=FV + "paging > a filter change whose load fails still starts from the first page "
                      "when it is read again")
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
@mutation(VIEW, "picked.value = new Set(rows.map((r) => r.id));", "picked.value = picked.value;",
          expect=FV + "selection > the bulk buttons stay off until a job is ticked, and the count follows")
@mutation(VIEW, ':label="keyName(k)"', ':label="k.label"',
          expect=FV + "filters > every key option is readable: its label, else its prefix, else the start of its id")
@mutation(VIEW, "row.key_label || shortId(row.key_id)", "row.key_label",
          expect=FV + "filters > a job whose key has no label shows the start of the key id in the Key column")
@mutation(VIEW, 's === "failed" ? "exception"', 's === "failed" ? "success"',
          expect=FV + "the list > the bar is green when done and red when failed")
@mutation(VIEW, 'v-if="isRefunded(row)"', 'v-if="true"',
          expect=FV + "the list > draws one row per job with the merged cells")
@pytest.mark.web_unit
def test_the_jobs_view_pages_filters_selects_and_sends_what_is_ticked():
    vitest(VIEW_SPEC)
