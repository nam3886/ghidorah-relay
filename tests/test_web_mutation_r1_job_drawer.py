"""Mutation markers of the job detail (key names and the timeline, blob files and the kept key, the regenerate control,
the media safety and the actions the job allows): each breaks one rule in the TypeScript source and demands that its
Vitest spec turns red on the exact test that guards the rule (scripts/mutate.py runs these, no rebuild). An `expect` is
the failed-test line the runner prints: `FAILED <spec> > <describe> > <test title>`."""

import pytest
from webunit import vitest

from relay_core.testing.mutation import mutation

SRC = "apps/admin-web/src"
TEXT = f"{SRC}/components/jobs/job-text.ts"
KEPT = f"{SRC}/components/jobs/kept-key.ts"
BLOB = f"{SRC}/components/jobs/blob-actions.ts"
CONTROL = f"{SRC}/components/jobs/RegenerateControl.vue"
MEDIA = f"{SRC}/components/jobs/JobMedia.vue"
ACTIONS = f"{SRC}/components/jobs/JobActions.vue"
DIALOG = f"{SRC}/components/jobs/JobDetailDialog.vue"

TEXT_SPEC = "tests/unit/jobs/job-text.spec.ts"
BLOB_SPEC = "tests/unit/jobs/blob-actions.spec.ts"
CONTROL_SPEC = "tests/unit/jobs/regenerate-control.spec.ts"
DETAIL_SPEC = "tests/unit/jobs/job-detail.spec.ts"

# the start of every failed-test line, by spec (the describe and the title follow)
FT = "FAILED " + TEXT_SPEC + " > "
FB = "FAILED " + BLOB_SPEC + " > "
FC = "FAILED " + CONTROL_SPEC + " > "
FD = "FAILED " + DETAIL_SPEC + " > "


# ---- key names and the timeline-----------------------------------------------------------------------------------
@mutation(TEXT, "key.label?.trim() || key.prefix?.trim() || shortId(key.id)", "key.label || shortId(key.id)",
          expect=FT + "key names > is the label, else the prefix, else the first 8 characters of the id")
@mutation(TEXT, "(b.at_ms ?? -Infinity) - (a.at_ms ?? -Infinity) || b.id - a.id",
          "(a.at_ms ?? -Infinity) - (b.at_ms ?? -Infinity) || b.id - a.id",
          expect=FT + "timeline > lists the newest event first, the later id first on equal times, and an event "
                      "with no time last")
@mutation(TEXT, "[...events].sort(", "events.sort(",
          expect=FT + "timeline > does not reorder the list it was given")
@mutation(TEXT, 'if (e.stage === "recovered") return "ok";', "",
          expect=FT + "timeline > a recovery is ok, a failed stage is bad, an unknown stage takes its source's tone, "
                      "else neutral")
@mutation(TEXT, '? "danger"', '? "error"',
          expect=FT + "timeline > maps a tone to the Element Plus types of the alert and of the timeline item")
@pytest.mark.web_unit
def test_a_key_is_never_nameless_and_the_timeline_runs_newest_first():
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


# ---- the job detail-----------------------------------------------------------------------------------------------
WORDS = FD + "conclusion, prompt and technical > the prompt is the customer's words and Copy prompt copies exactly that"
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
@mutation(ACTIONS, '@confirm="revoke"', '@cancel="revoke"',
          expect=EACH + "Revoke asks first and says how many links stopped")
@mutation(MEDIA, '.filter((s) => s.kind === "image")', ".filter(() => true)",
          expect=MEDIA_GROUP + "an image is a thumbnail whose viewer lists every previewable image of the list, "
                               "and a video is left out")
@mutation(ACTIONS, '${out.replayed ? "Already made: job " : "New job "}', '${"New job "}',
          expect=EACH + "a regenerate the server already did says Already made")
@mutation(DIALOG, 'class="block">{{ job.prompt }}</pre>', 'class="block">{{ job.sent_text }}</pre>', expect=WORDS)
@mutation(DIALOG, 'CopyButton :text="job.prompt"', 'CopyButton :text="job.sent_text ?? \'\'"', expect=WORDS)
@mutation(DIALOG, "if (!latest.isCurrent(ticket)) return;\n    job.value = out;", "job.value = out;",
          expect=FD + "opening > drops the answer for a job that is no longer the open one")
@mutation(DIALOG, "newestFirst(job.value?.events ?? [])", "(job.value?.events ?? [])",
          expect=FD + "timeline > lists the events newest first, and marks a recovery")
@mutation(DIALOG, "narrow.value ? 1 : 2", "2",
          expect=FD + "layout > the facts are one column under 640 px and two above, the wide Params row spanning both")
@pytest.mark.web_unit
def test_the_detail_draws_media_only_from_safe_links_and_shows_the_actions_the_job_allows():
    vitest(DETAIL_SPEC)
