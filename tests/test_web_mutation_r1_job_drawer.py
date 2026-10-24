"""Mutation markers of the job drawer (key names, the timeline and the formats, blob files and the kept key, the
regenerate control, the media safety, the media viewer and the actions the job allows): each breaks one rule in the
TypeScript source and demands that its Vitest spec turns red on the exact test that guards the rule (scripts/mutate.py
runs these, no rebuild). An `expect` is the failed-test line the runner prints: `FAILED <spec> > <describe> > <test
title>`."""

import pytest
from webunit import vitest

from relay_core.testing.mutation import mutation

SRC = "apps/admin-web/src"
TEXT = f"{SRC}/components/jobs/job-text.ts"
KEPT = f"{SRC}/components/jobs/kept-key.ts"
BLOB = f"{SRC}/components/jobs/blob-actions.ts"
CONTROL = f"{SRC}/components/jobs/RegenerateControl.vue"
MEDIA = f"{SRC}/components/jobs/JobMedia.vue"
VIEWER = f"{SRC}/components/jobs/MediaViewer.vue"
ACTIONS = f"{SRC}/components/jobs/JobActions.vue"
DRAWER = f"{SRC}/components/jobs/JobDetailDrawer.vue"
TECH = f"{SRC}/components/jobs/JobTechnical.vue"

TEXT_SPEC = "tests/unit/jobs/job-text.spec.ts"
BLOB_SPEC = "tests/unit/jobs/blob-actions.spec.ts"
CONTROL_SPEC = "tests/unit/jobs/regenerate-control.spec.ts"
DETAIL_SPEC = "tests/unit/jobs/job-detail.spec.ts"

# the start of every failed-test line, by spec (the describe and the title follow)
FT = "FAILED " + TEXT_SPEC + " > "
FB = "FAILED " + BLOB_SPEC + " > "
FC = "FAILED " + CONTROL_SPEC + " > "
FD = "FAILED " + DETAIL_SPEC + " > "


# ---- key names, the timeline and the formats ---------------------------------------------------------------------
@mutation(TEXT, "key.label?.trim() || key.prefix?.trim() || shortId(key.id)", "key.label || shortId(key.id)",
          expect=FT + "key names > is the label, else the prefix, else the first 8 characters of the id")
@mutation(TEXT, "(b.at_ms ?? -Infinity) - (a.at_ms ?? -Infinity) || b.id - a.id",
          "(a.at_ms ?? -Infinity) - (b.at_ms ?? -Infinity) || b.id - a.id",
          expect=FT + "timeline > lists the newest event first, the later id first on equal times, and an event "
                      "with no time last")
@mutation(TEXT, "[...events].sort(", "events.sort(",
          expect=FT + "timeline > does not reorder the list it was given")
@mutation(TEXT, "  if (named) return named;\n", "",
          expect=FT + "timeline > a recovery is ok, a failed stage is bad, an unknown stage takes its source's tone, "
                      "else neutral")
@mutation(TEXT, 'accepted: "info"', 'accepted: "neutral"',
          expect=FT + "timeline > a refund is ok and an accepted job is info, whatever their source says")
@mutation(TEXT, "return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`;",
          "return `${d.getHours()}:${two(d.getMinutes())}:${two(d.getSeconds())}`;",
          expect=FT + "drawer formats > a clock is hh:mm:ss in local time, a day clock adds dd/mm, a missing time is "
                      "a dash")
@mutation(TEXT, "const m = Math.floor((total % 3600) / 60);", "const m = Math.floor(total / 60);",
          expect=FT + "drawer formats > how long a job took reads in seconds, minutes or hours")
@mutation(TEXT, "(n / 1_000_000).toFixed(1)", "(n / 1_000_000).toFixed(0)",
          expect=FT + "drawer formats > a size reads in bytes, KB or MB, powers of 1000")
@mutation(TEXT, 'n === 1 ? "" : "s"', '"s"',
          expect=FT + "drawer formats > a count takes an s except for one")
# page-drawer: a job's request parameter is the first of its names that is set
@mutation(TEXT, 'if (value !== undefined && value !== null && value !== "") return String(value);',
          "return String(value);", expect=FT + "a job's request parameter > is the first of its names that is set")
@pytest.mark.web_unit
def test_a_key_is_never_nameless_the_timeline_runs_newest_first_and_the_drawer_formats_are_exact():
    vitest(TEXT_SPEC)


# ---- blob files and the kept key record --------------------------------------------------------------------------
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


# ---- the regenerate control --------------------------------------------------------------------------------------
NEW = FC + "start a new regenerate > "
CTRL = FC + "regenerate control > "


@mutation(CONTROL, "if (abandonOperation(props.action))", "if (true)",
          expect=NEW + "tells the owner when the key could not be dropped, and keeps offering it")
@mutation(CONTROL, "if (!ok) return;", "", expect=NEW + "warns of the double charge, and Cancel keeps the key")
@mutation(CONTROL, "if (!(await confirmDialog(props.confirm))) return;", "await confirmDialog(props.confirm);",
          expect=CTRL + "asks first with the text it was given, and Cancel sends nothing")
@pytest.mark.web_unit
def test_a_regenerate_asks_first_is_single_flight_and_a_kept_key_is_dropped_only_behind_a_warning():
    vitest(CONTROL_SPEC)


# ---- the job drawer ----------------------------------------------------------------------------------------------
SHOW = FD + "which actions show, and where > "
EACH = FD + "what each action sends and says > "
MEDIA_GROUP = FD + "results and references > "
SCRIPT_URL = MEDIA_GROUP + "a script URL is never drawn: no preview, and Open goes through the admin session"
REFERENCE = MEDIA_GROUP + "a reference opens through the input route, has no Download, and is counted in its heading"
VIEWER_BUTTONS = MEDIA_GROUP + "the viewer's buttons are ours with tooltips"
DONE_ROW = SHOW + "a done job offers Regenerate… in the overview row and Recover under More actions"
RUNNING = SHOW + "a job still running offers neither Recover nor Regenerate…"
# page-drawer: the Technical tab's wording
TECH_GROUP = FD + "conclusion, ledger, prompt and technical > "
EMPTY_DASH = TECH_GROUP + "a value that is not set is the list's empty-cell dash"
FACTS = TECH_GROUP + "lists the technical facts, leaving out what the server does not have"
WEBHOOK = TECH_GROUP + "words a delivered webhook by its HTTP code, a retry by its state and code"
# the Overview's action row is drawn only when it has a button: Download, Re-check, Regenerate… or Copy share link
ROW_IF = "(outputs.length && !isPhone) || job.can_recheck || (finished && !isPhone) || links.length"


@mutation(MEDIA, "const src = safeMediaUrl(item.url);", "const src = item.url;", expect=SCRIPT_URL)
@mutation(MEDIA, "fetchMediaBlob(props.job.id, props.role, idx)", 'fetchMediaBlob(props.job.id, "output", idx)',
          expect=REFERENCE)
@mutation(MEDIA, ".filter((s) => s.kind)", ".filter(() => true)",
          expect=MEDIA_GROUP + "an image is a tile whose viewer lists every previewable result, a video included, "
                               "an item with no link left out")
@mutation(ACTIONS, 'v-if="finished && props.withRegenerate"', 'v-if="props.withRegenerate"', expect=RUNNING)
@mutation(ACTIONS, '<div v-if="finished" class="ja-recover">',
          "<div v-if=\"props.job.status === 'done'\" class=\"ja-recover\">",
          expect=SHOW + "a failed job offers Regenerate… in the row too, and Recover only under More actions")
@mutation(ACTIONS, "props.job.external_ref && props.job.account_label",
          "props.job.external_ref || props.job.account_label",
          expect=SHOW + "Fetch title needs both the upstream reference and the account")
@mutation(ACTIONS, "hasLinks.value ||", "true ||",
          expect=SHOW + "More actions has its own heading, drawn only when it has a button")
@mutation(ACTIONS, "account.value.trim()", "account.value",
          expect=EACH + "Recover sends the trimmed account, polls the command and reads the job again")
@mutation(ACTIONS, '${out.replayed ? "Already made: job " : "New job "}', '${"New job "}',
          expect=EACH + "a regenerate the server already did says Already made")
@mutation(DRAWER, "if (!latest.isCurrent(ticket)) return;\n    job.value = out;", "job.value = out;",
          expect=FD + "opening > drops the answer for a job that is no longer the open one")
@mutation(DRAWER, "newestFirst(job.value?.events ?? [])", "(job.value?.events ?? [])",
          expect=FD + "timeline > lists the events newest first with their clock time, and marks a recovery")
@mutation(DRAWER, 'isRefunded(j) ? "refunded"', 'back ? "refunded"',
          expect=TECH_GROUP + "a done job with a net below its price shows what was charged and no refund")
@mutation(DRAWER, "outputs.value.length > 1", "true",
          expect=SHOW + "Download sits first in the row of a job with results, and says all for more than one")
@mutation(DRAWER, "`job-${shortId(job.value.id)}-${m.idx}`", "`job-${shortId(job.value.id)}-0`",
          expect=EACH + "Download saves every result as job-<id8>-<idx>, one file each")
# page-drawer (the drawer matched to the Ledger design): the action row and the phone's foot, the failed read's
# wording, the key name, the Technical wording, the media names and the viewer; the tile's shape, the viewer's own
# buttons and steps, the on-its-way banner and pill, the count of results, the empty-value dash
@mutation(MEDIA, "if (m.width && m.height) return `${m.width} / ${m.height}`;", "",
          expect=MEDIA_GROUP + "a tile takes its picture's shape")
@mutation(MEDIA, 'return asked ? `${asked[1]} / ${asked[2]}` : video ? "16 / 9" : "2 / 3";',
          'return video ? "16 / 9" : "2 / 3";', expect=MEDIA_GROUP + "a tile takes its picture's shape")
@mutation(MEDIA, 'jobParam(props.job.params, "ratio", "aspect_ratio")', 'jobParam(props.job.params, "ratio")',
          expect=MEDIA_GROUP + "a tile takes its picture's shape")
@mutation(VIEWER, "  zoom.value = 1;\n  turn.value = 0;\n", "  turn.value = 0;\n", expect=VIEWER_BUTTONS)
@mutation(VIEWER, "if (next < 0 || next >= props.items.length) return;", "if (next < 0) return;",
          expect=VIEWER_BUTTONS)
@mutation(DRAWER, 'return finished.value ? `${line} · ${plural(outputCount.value, "output")}` : line;',
          'return `${line} · ${plural(outputCount.value, "output")}`;',
          expect=TECH_GROUP + "a job on its way shows its step in the head's pill")
@mutation(DRAWER, 'fetching: "Fetching the result",', 'fetching: "fetching",',
          expect=TECH_GROUP + "a job on its way says what it is doing")
@mutation(DRAWER, "isRunning(j.status) ? `step ${progressStep(j.status)} of 4` : \"\"",
          "`step ${progressStep(j.status)} of 4`",
          expect=TECH_GROUP + "a job on its way says what it is doing")
@mutation(DRAWER, 'asText(j.account_label) || "—"', 'asText(j.account_label) || "-"',
          expect=EMPTY_DASH)
@mutation(TEXT, '  if (!ms) return "—";\n  const d = new Date(ms);\n  const tail',
          '  if (!ms) return "-";\n  const d = new Date(ms);\n  const tail',
          expect=EMPTY_DASH)
@mutation(DRAWER, ROW_IF, "true", expect=RUNNING)
@mutation(DRAWER, ROW_IF, "(outputs.length && !isPhone) || job.can_recheck || links.length", expect=DONE_ROW)
@mutation(DRAWER, ROW_IF, "(outputs.length && !isPhone) || (finished && !isPhone) || links.length",
          expect=SHOW + "Re-check only when the server says the job can be")
@mutation(DRAWER, 'loadFailedText(err, errorText(err), "the job")', "errorText(err)",
          expect=FD + "opening > a failed read is a message with Retry")
@mutation(DRAWER, "keyName({ id: j.key_id, label: j.key_label, prefix: j.key_prefix })", 'asText(j.key_label) || "-"',
          expect=TECH_GROUP + "a key without a label is named by its prefix")
@mutation(TECH, 'j.wallet_kind ? `${j.provider} ${j.wallet_kind}` : ""', "text(j.wallet_kind)", expect=FACTS)
@mutation(TECH, "text(j.frame_id) && `${j.media_type} v${j.frame_id}`", "text(j.frame_id)", expect=FACTS)
@mutation(TECH, 'c.status === "sent" ? "" : c.status', "c.status", expect=WEBHOOK)
@mutation(TECH, 'j.account_cost ? plural(j.account_cost, "credit") : ""', "text(j.account_cost)", expect=WEBHOOK)
@mutation(TECH, "words(j.key_label?.trim(), j.key_prefix)", "text(j.key_label)",
          expect=TECH_GROUP + "a phone lists Key and Account among the facts too")
@mutation(DRAWER, "tab.value = \"overview\";", "", expect=FD + "opening > another job opens on Overview, whatever tab "
                                                              "the last one was on")
@pytest.mark.web_unit
def test_the_drawer_draws_media_only_from_safe_links_and_shows_the_actions_the_job_allows():
    vitest(DETAIL_SPEC)
