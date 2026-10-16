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
TOTALS = (FV + "time zones > asks for the day totals in the zone of the browser (Asia/Saigon as Asia/Ho_Chi_Minh) "
          "and puts each on its own day's header")
JOB_ROW = f"{SRC}/components/jobs/job-row.ts"


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
@mutation(VIEW, ':disabled="!ids.length || rechecking"', ':disabled="rechecking"',
          expect=FV + "selection > the bulk buttons stay off until a job is ticked, and the count follows")
@mutation(VIEW, "Regenerate ${ids.value.length} job(s)? ", "Regenerate job(s)? ",
          expect=FV + "bulk regenerate > asks first, naming the count, today's price and the charge")
@mutation(VIEW, "picked.value = new Set(rows.map((r) => r.id));", "picked.value = picked.value;",
          expect=FV + "selection > the bulk buttons stay off until a job is ticked, and the count follows")
@mutation(VIEW, ':label="keyName(k)"', ':label="k.label"',
          expect=FV + "filters > every key option is readable: its label, else its prefix, else the start of its id")
@mutation(VIEW, '<template #cell-key="{ row }">{{ row.key_label || shortId(row.key_id) }}</template>',
          '<template #cell-key="{ row }">{{ row.key_label }}</template>',
          expect=FV + "filters > a job whose key has no label shows the start of the key id in the Key column")
@mutation(VIEW, '(job.status === "failed" ? "bad" : undefined)',
          '(job.status === "failed" || job.status === "unknown" ? "bad" : undefined)',
          expect=FV + "the list > a failed job is tinted and an unknown one is not")
@mutation(VIEW, "note: (n) => `${n} jobs shown`", "note: (n) => `${n} jobs`",
          expect=FV + "the list > groups the rows under one header per day, with the jobs shown for that day")
@mutation(VIEW, "  dayTotals.value = {};\n  void load(0);", "  void load(0);",
          expect=FV + "time zones > a change of the status drops the old day totals at once, "
                      "before the new list arrives")
@mutation(VIEW, "      tz: zone,\n", "", expect=TOTALS)
@mutation(VIEW, "dayTotals.value = out.day_totals ?? {};", "dayTotals.value = {};", expect=TOTALS)
@mutation(VIEW, "dayKey((first as JobSummary).created_ms, zone)", 'dayKey(0, "UTC")', expect=TOTALS)
@mutation(VIEW, '${total} ${total === 1 ? "job" : "jobs"}', "${total} jobs", expect=TOTALS)
@mutation(JOB_ROW, 'zone === "Asia/Saigon" ? "Asia/Ho_Chi_Minh" : zone', "zone", expect=TOTALS)
@mutation(JOB_ROW, 'new Intl.DateTimeFormat("en-CA", { timeZone: zone }).format(ms)',
          'new Intl.DateTimeFormat("en-CA", { timeZone: "UTC" }).format(ms)',
          expect=FV + "time zones > asks for the day totals in the zone of the browser "
                      "(America/Los_Angeles as America/Los_Angeles) and puts each on its own day's header")
@mutation(VIEW, 'v-show="!isPhone || ids.length"', 'v-show="true"',
          expect=FV + "selection > a phone shows the bulk bar only while a job is ticked, "
                      "with the count and both buttons")
@mutation(VIEW, '"Every generation job across keys, by day."', '"Every generation job across keys, grouped by day."',
          expect=FV + "the page head and the 24-hour band > a phone says 'by day' and has an icon-only Refresh "
                      "that names the update time")
@pytest.mark.web_unit
def test_the_jobs_view_pages_filters_selects_and_sends_what_is_ticked():
    vitest(VIEW_SPEC)


# ---- the 24-hour band, the hour filter and the 10-second read ----------------------------------------------------
BAND = f"{SRC}/components/jobs/hour-band.ts"
ROW = f"{SRC}/components/jobs/job-row.ts"
BAND_SPEC = "tests/unit/jobs/hour-band.spec.ts"
ROW_SPEC = "tests/unit/jobs/job-row.spec.ts"
FB = "FAILED " + BAND_SPEC + " > the 24-hour band > "
FR = "FAILED " + ROW_SPEC + " > job row text > "
HEAD = FV + "the page head and the 24-hour band > "
CHIP_WINDOW = HEAD + "a column filters the list to its hour, from the first page, and the chip names the hour"


@mutation(VIEW, "createdFromMs: hour.value ?? undefined,", "createdFromMs: undefined,", expect=CHIP_WINDOW)
@mutation(VIEW, "createdToMs: hour.value === null ? undefined : hour.value + HOUR_MS,",
          "createdToMs: hour.value === null ? undefined : hour.value,", expect=CHIP_WINDOW)
@mutation(VIEW, "watch([status, keyId, hour, () => scope.provider], () => {",
          "watch([status, keyId, () => scope.provider], () => {", expect=CHIP_WINDOW)
@mutation(VIEW, "auth.onSignOut(() => {\n  hour.value = null;\n})", "auth.onSignOut(() => {\n  void 0;\n})",
          expect=HEAD + "an hour filter drops the ticks, and a sign-out clears the hour")
@mutation(VIEW, "  () => void loadHours(),", "  () => void 0,", expect=HEAD + "the band follows the provider scope")
@mutation(VIEW, "if (document.hidden || loading.value) return;", "if (loading.value) return;",
          expect=HEAD + "reads the list and the band again every 10 s while the tab is shown, never while it is hidden")
@mutation(VIEW, "if (document.hidden || loading.value) return;", "if (document.hidden) return;",
          expect=HEAD + "a read still under way is not overtaken by the next tick")
@mutation(VIEW, "const POLL_MS = 10_000;", "const POLL_MS = 20_000;",
          expect=HEAD + "reads the list and the band again every 10 s while the tab is shown, never while it is hidden")
@mutation(VIEW, "  clearInterval(timer);\n", "  void timer;\n", expect=HEAD + "stops reading once the page is gone")
@mutation(VIEW, "  if (!document.hidden) poll();", "  void 0;",
          expect=HEAD + "coming back to the tab reads at once, and leaving it reads nothing")
@mutation(f"{SRC}/components/jobs/HourBand.vue", 'role="group"', 'role="img"',
          expect=HEAD + "the columns are a labelled group, not an image, so every hour link stays reachable")
@pytest.mark.web_unit
def test_the_hour_filter_the_band_follows_scope_and_sign_out_and_the_read_pauses_while_hidden():
    vitest(VIEW_SPEC)


@mutation(BAND, "export const BAND_SCALE = 20;", "export const BAND_SCALE = 10;",
          expect=FB + "draws a segment as its count over 20, so the peak hour of 16 jobs is 80% of the column")
@mutation(BAND, "(count / scale) * 100", "(count / scale) * 90",
          expect=FB + "draws a segment as its count over 20, so the peak hour of 16 jobs is 80% of the column")
@mutation(BAND, "Math.max(BAND_SCALE, ...hours.map(hourTotal))", "BAND_SCALE",
          expect=FB + "a busier hour than the scale raises the scale, so no column is taller than its box")
@mutation(BAND, "  if (h.failed > 0) parts.push(", "  if (h.failed >= 0) parts.push(",
          expect=FB + "titles a column with its hour and the counts that are not zero")
@mutation(BAND, "  if (h.in_progress > 0) parts.push(", "  if (h.in_progress >= 0) parts.push(",
          expect=FB + "titles a column with its hour and the counts that are not zero")
@mutation(BAND, "hours.filter((h) => h.failed > 0)", "hours.filter((h) => h.failed >= 0)",
          expect=FB + "describes the chart by its busiest hour and the hours that had failures")
@mutation(BAND, "failures.length > 1 ?", "failures.length > 2 ?",
          expect=FB + "the chart label copes with one failure hour, none, and an empty day")
@mutation(BAND, 'const tail = failures.length ? `failures at ${list}` : "no failures";',
          'const tail = `failures at ${list}`;',
          expect=FB + "the chart label copes with one failure hour, none, and an empty day")
@mutation(BAND, "if (!peak) return", "if (peak) return",
          expect=FB + "the chart label copes with one failure hour, none, and an empty day")
@mutation(BAND, "hours.filter((_, i) => i % step === 0)", "hours.filter((_, i) => i % step === 1)",
          expect=FB + "labels every third hour (eight labels), every sixth on a phone (four)")
@mutation(BAND, "`Hour ${hourLabel(ms)}–${hourLabel(ms + HOUR_MS)}`",
          "`Hour ${hourLabel(ms)}-${hourLabel(ms + HOUR_MS)}`",
          expect=FB + "names the hour chip with an en dash between the hour and the next")
@mutation(BAND, "export const HOUR_MS = 3_600_000;", "export const HOUR_MS = 3_000_000;",
          expect=FB + "names the hour chip with an en dash between the hour and the next")
@mutation(BAND, "if (split) {", "if (!split) {",
          expect=FB + "the desktop ledger has eight rows and folds dead and disabled accounts into one")
@mutation(BAND, "count(jobs.in_progress + jobs.queued)", "count(jobs.in_progress)",
          expect=FB + "the desktop ledger has eight rows and folds dead and disabled accounts into one")
@mutation(BAND, "`${accounts.usable} / ${accounts.available}`", "`${accounts.available} / ${accounts.usable}`",
          expect=FB + "the desktop ledger has eight rows and folds dead and disabled accounts into one")
@mutation(BAND, "if (!stats) return [];", "if (!stats) return [{ label: \"x\", value: 0 }];",
          expect=FB + "has no ledger until the stats arrive")
@mutation(BAND, 'const failed = sum("failed");', "const failed = 0;",
          expect=FB + "counts the title and the legend from the 24 columns")
@pytest.mark.web_unit
def test_the_band_draws_titles_scales_and_labels_the_hours_and_the_ledger_from_the_stats():
    vitest(BAND_SPEC)


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
