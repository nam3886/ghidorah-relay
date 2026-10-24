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
GROWS = "on a desktop the Key column grows to its longest value up to 120 px, and Job gives up the room"
AUTO = f"{SRC}/components/jobs/auto-columns.ts"

# the start of every failed-test line, by spec (the describe and the title follow)
FG = "FAILED " + PAGING_SPEC + " > "
FP = "FAILED " + PICK_SPEC + " > "
FT = "FAILED " + TEXT_SPEC + " > "
FA = "FAILED " + API_SPEC + " > "
FV = "FAILED " + VIEW_SPEC + " > "
TODAY = (FV + "time zones > today's header counts its jobs on this page and says shown, an earlier day says its "
         "total, and one job is 1 job")
TOTALS = (FV + "time zones > asks for the day totals in the zone of the browser, exactly as the browser reports it ")
JOB_ROW = f"{SRC}/components/jobs/job-row.ts"


# ---- the failed load of the list---------------------------------------------------------------------------------
@mutation(TEXT, "the relay answered ${err.status} (${said})", "the relay answered (${said})",
          expect=FT + "the failed load of the list > says what failed and what the relay answered, as drawn")
@pytest.mark.web_unit
def test_the_failed_load_of_the_list_is_worded_as_drawn():
    vitest(TEXT_SPEC)


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
@mutation(API, "      key_id: q.keyId,\n", "",
          expect=FA + "list > sends the status, provider and key that are set, with the offset and never a cursor")
@mutation(API, "      offset: q.offset,\n", "",
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
AT_REST = (FV + "selection > with nothing ticked the toolbar says 0 selected and has no bulk buttons; a tick shows "
                "them, and the count follows")
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
          expect=FV + "filters > shows All, not the placeholder, while nothing is filtered, "
                      "after the word Status or Key")
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
@mutation(VIEW, "Regenerate ${ids.value.length} job(s)? ", "Regenerate job(s)? ",
          expect=FV + "bulk regenerate > asks first, naming the count, today's price and the charge")
@mutation(VIEW, "picked.value = new Set(rows.map((r) => r.id));", "picked.value = picked.value;",
          expect=AT_REST)
@mutation(VIEW, "label: keyName(k) }", "label: k.label }",
          expect=FV + "filters > every key option is readable: its label, else its prefix, else the start of its id")
@mutation(VIEW, "keyNames.value.get(job.key_id) || ", "",
          expect=FV + "filters > a job whose key has no label shows the key as the Key filter names it")
@mutation(VIEW, "label: s.charAt(0).toUpperCase() + s.slice(1)", "label: s",
          expect=FV + "filters > the status options read as drawn")
@mutation(VIEW, "note: (n) => `${jobs(n)} shown`", "note: (n) => jobs(n)",
          expect=FV + "the list > groups the rows under one header per day, with the jobs shown for that day")
@mutation(VIEW, "  dayTotals.value = {};\n  void load(0);", "  void load(0);",
          expect=FV + "time zones > a change of the status drops the old day totals at once, "
                      "before the new list arrives")
@mutation(VIEW, "      tz: zone,\n", "", expect=TOTALS)
@mutation(VIEW, "dayTotals.value = out.day_totals ?? {};", "dayTotals.value = {};", expect=TOTALS)
@mutation(VIEW, "dayKey((first as JobSummary).created_ms, zone)", 'dayKey(0, "UTC")', expect=TOTALS)
@mutation(VIEW, '`${n} ${n === 1 ? "job" : "jobs"}`', "`${n} jobs`", expect=TODAY)
@mutation(VIEW, "if (day !== today && total !== undefined)", "if (total !== undefined)", expect=TODAY)
@mutation(JOB_ROW, "return Intl.DateTimeFormat().resolvedOptions().timeZone;", 'return "UTC";', expect=TOTALS)
@mutation(JOB_ROW, 'new Intl.DateTimeFormat("en-CA", { timeZone: zone }).format(ms)',
          'new Intl.DateTimeFormat("en-CA", { timeZone: "UTC" }).format(ms)',
          expect=FV + "time zones > asks for the day totals in the zone of the browser, exactly as the browser "
                      "reports it (America/Los_Angeles), and puts each on its own day's header")
@mutation(VIEW, '"Every generation job across keys, by day."', '"Every generation job across keys, grouped by day."',
          expect=FV + "the page head and the 10-second read > a phone says 'by day' and has an icon-only Refresh "
                      "that names the update time")
@mutation(VIEW, "if (!sameColumns(next, columnWidths.value)) columnWidths.value = next;", "columnWidths.value = null;",
          expect=GROWS)
@mutation(VIEW, "    if (body) widthWatch?.observe(body);\n", "",
          expect=FV + "the list > the widths follow the table element's own width")
@mutation(f"{SRC}/components/shared/DataTable.vue", '      emit("rows-drawn");\n', "", expect=GROWS)
@pytest.mark.web_unit
def test_the_jobs_view_pages_filters_selects_and_sends_what_is_ticked():
    vitest(VIEW_SPEC)


# ---- the 10-second read ------------------------------------------------------------------------------------------
ROW = f"{SRC}/components/jobs/job-row.ts"
ROW_SPEC = "tests/unit/jobs/job-row.spec.ts"
FR = "FAILED " + ROW_SPEC + " > job row text > "
HEAD = FV + "the page head and the 10-second read > "
EVERY_10S = HEAD + ("reads the list and the provider summary again every 10 s while the tab is shown, never while it "
                    "is hidden")


@mutation(VIEW, "if (document.hidden || loading.value) return;", "if (loading.value) return;",
          expect=EVERY_10S)
@mutation(VIEW, "if (document.hidden || loading.value) return;", "if (document.hidden) return;",
          expect=HEAD + "a read still under way is not overtaken by the next tick")
@mutation(VIEW, "const POLL_MS = 10_000;", "const POLL_MS = 20_000;", expect=EVERY_10S)
@mutation(VIEW, "  void stats.refresh();\n  void providers.refresh();", "  void stats.refresh();", expect=EVERY_10S)
@mutation(VIEW, "  clearInterval(timer);\n", "  void timer;\n", expect=HEAD + "stops reading once the page is gone")
@mutation(VIEW, "  if (!document.hidden) poll();", "  void 0;",
          expect=HEAD + "coming back to the tab reads at once, and leaving it reads nothing")
@pytest.mark.web_unit
def test_the_read_repeats_every_10_s_and_pauses_while_hidden():
    vitest(VIEW_SPEC)


@mutation(ROW, 'new Set(["queued", "submitting", "waiting", "fetching"])',
          'new Set(["queued", "submitting", "waiting"])',
          expect=FR + "a job is running while it is queued, submitting, waiting or fetching")
@mutation(ROW, "isRunning(job.status) && job.net_charged === 0 ? null : job.net_charged",
          "isRunning(job.status) ? null : job.net_charged",
          expect=FR + "net points: no figure for a running job that has charged nothing, the net for every other")
@mutation(ROW, 'ms ? new Date(ms).toLocaleTimeString("en-GB", { hour12: false }) : "—"',
          'ms ? new Date(ms).toLocaleTimeString("en-GB", { hour12: true }) : "—"',
          expect=FR + "clock is the local time of day on a 24-hour clock, and a dash for no time")
@pytest.mark.web_unit
def test_the_row_text_clock_running_steps_and_net_points():
    vitest(ROW_SPEC)


@mutation(AUTO, "Math.min(COLUMN_CAP, need.key)", "need.key", expect="neither column grows over the cap")
@mutation(AUTO, "Math.max(drawn.account, Math.min(COLUMN_CAP, need.account))", "Math.min(COLUMN_CAP, need.account)",
          expect="values that fit their drawn widths move nothing")
@mutation(AUTO, "Math.max(drawn.key, Math.min(COLUMN_CAP, need.key))", "Math.min(COLUMN_CAP, need.key)",
          expect="a drawn width over the cap is kept")
@mutation(AUTO, "job: drawn.job - (account - drawn.account) - (key - drawn.key)", "job: drawn.job",
          expect="Job gives up exactly that room")
@mutation(AUTO, "Math.abs(a.job - b.job) < 0.5", "Math.abs(a.job - b.job) < 5", expect="within half a pixel")
@pytest.mark.web_unit
def test_the_account_and_key_columns_grow_to_their_values_up_to_the_cap():
    vitest("tests/unit/jobs/auto-columns.spec.ts")
