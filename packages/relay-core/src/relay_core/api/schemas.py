"""The answers of the four native job calls, as documentation.

These models are written into `/openapi.json` by `openapi_docs.public_schema` and used by nothing else: the handlers
keep returning plain dicts, so the answer is not filtered, reordered or validated on the hot path and its key order is
the one `doors.job_status` builds. A test checks the real answers against them. Extra fields are allowed on purpose: a
generated client must tolerate additions, and a `status` or `stage` value it does not know means "still running".
Every field is required: a value that is not known is `null`.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# ISO 8601 with a UTC offset, as `datetime.isoformat()` writes it.
Moment = Annotated[str, Field(json_schema_extra={"format": "date-time"})]


class JobWarning(BaseModel):
    code: str = Field(description="Short machine-readable code of the note.")
    text: str = Field(description="English text of a non-fatal note about the request.")


class JobCreated(BaseModel):
    id: str = Field(description="Job id. Poll the status call with it.")
    status: Literal["queued", "running", "done", "failed"] = Field(
        description="A new job is queued. A repeated request with the same idempotency key returns the current status "
                    "of the job it made. More values may be added later; treat one you do not know as still running.")
    queue_position: Annotated[int, Field(ge=1)] | None = Field(
        description="Your place in the line among jobs waiting for capacity; null when the job is not queued.")
    warnings: list[JobWarning] = Field(description="Non-fatal notes about the request.")


class JobStatus(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{
        "id": "6f0c3c1e-7a58-4b8e-9d57-0a2d1f0e5a11", "status": "running", "stage": "waiting", "result_urls": [],
        "images": [], "error": None, "error_code": None, "refunded": False, "points": 10, "model": "example-video",
        "duration": 5, "ratio": "16:9", "n": None, "style": None, "title": None, "warnings": [],
        "queue_position": None, "created_at": "2026-10-18T10:00:00.120000+00:00", "finished_at": None,
        "progress": 45, "render_started_at": "2026-10-18T10:03:10.450000+00:00",
        "estimated_finish_at": "2026-10-18T10:45:00+00:00", "source_job_id": None}]})

    id: str = Field(description="The id of this job.")
    status: Literal["queued", "running", "done", "failed"] = Field(
        description="queued: waiting for capacity. running: see stage. done: the files are ready. failed: it ended "
                    "without a result. More values may be added later; treat one you do not know as still running.")
    stage: Literal["submitting", "waiting", "fetching", "unknown"] | None = Field(
        description="While status is running: submitting, the order is being placed with the provider; unknown, the "
                    "order was sent and is not confirmed yet; waiting, the provider is rendering; fetching, relay is "
                    "copying the finished files. Null otherwise. More values may be added later; treat one you do not "
                    "know as still running.")
    result_urls: list[str] = Field(description="Links to the video files of a finished video job, else empty.")
    images: list[str] = Field(description="Links to the image files of a finished image job, else empty.")
    error: str | None = Field(
        description="Message of a failed job, a fixed text per error code and never provider text; null otherwise.")
    error_code: str | None = Field(
        description="Code of a failed job; null otherwise.")
    refunded: bool = Field(description="True when the charge of a failed job was given back.")
    points: int = Field(
        description="Points the job's charge took; on a refunded job, the amount that was refunded. The net charge is "
                    "0 when refunded is true, else points.")
    model: str = Field(description="Model id the job was made with.")
    duration: int | None = Field(description="Seconds asked for (video jobs), else null.")
    ratio: str | None = Field(description="Aspect ratio asked for, else null.")
    n: int | None = Field(description="Number of outputs asked for, else null.")
    style: str | None = Field(description="Image style asked for, else null.")
    title: str | None = Field(
        description="The title sent when the job was created, else the name the provider gave it, else the first "
                    "words of the prompt.")
    warnings: list[JobWarning] = Field(description="Non-fatal notes about the request.")
    queue_position: int | None = Field(
        description="As in the create answer: your place in the line while queued; null once the job leaves the line.")
    created_at: Moment = Field(description="Creation time, server clock, ISO 8601 with its UTC offset.")
    finished_at: Moment | None = Field(description="Time the job reached done or failed, else null.")
    progress: Annotated[int, Field(ge=0, le=100)] | None = Field(
        description="Always an ESTIMATE (there is no separate flag): the provider reports no percentage, so this is "
                    "estimated from recent render times of this model and length. 0 before the render starts, then "
                    "rising while it renders, 95 while the files are copied, 100 only when done, null when failed.")
    render_started_at: Moment | None = Field(
        description="Time the current render attempt started (it restarts after a re-check). Null before the render "
                    "starts and for a failed job.")
    estimated_finish_at: Moment | None = Field(
        description="ESTIMATE of when the render ends, to the minute. Only while the render runs, is not overdue, and "
                    "there is enough recent data for this model and length; else null (no promise without data). "
                    "Null for a failed job.")
    source_job_id: str | None = Field(
        description="The earlier job whose output this job works on (an extend, an edit), as sent at creation; null "
                    "for a job made without one.")
