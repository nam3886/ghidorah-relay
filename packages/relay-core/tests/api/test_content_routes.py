"""The content routes: a job's output files through the API with Range and a download filename, only
for the key that owns the job; the admin proxies for outputs and input images; debug media never served."""

from apikit import run_app
from doorkit import error_of
from test_status_and_media import DATA, plant

from relay_core.testing.mutation import mutation

MEDIA = "packages/relay-core/src/relay_core/api/routes_media.py"
ADMIN = "packages/relay-core/src/relay_core/admin/routes_jobs.py"
OWNER = "(%s::uuid IS NULL OR j.key_id = %s)"
ANY_OWNER = "(%s::uuid IS NOT NULL OR %s::uuid IS NOT NULL)"
ROLE = "AND m.role = %s AND m.idx = %s"
ANY_BUT_ROLE = "AND m.role <> %s AND m.idx = %s"


async def job_of(env, db, key, *, kind="video", outputs=1, mime="video/mp4"):
    """A job of `key` with `outputs` output files (each a different slice of DATA, so a mix-up shows)."""
    job = db.job(key.id, kind=kind)
    db.run("UPDATE jobs SET prompt = 'a prompt', model = 'fake-video', params = '{\"duration\": 5}' WHERE id = %s", job)
    for idx in range(outputs):
        await plant(env, db, job_id=job, idx=idx, data=DATA[idx * 100:], mime=mime)
    return job


@mutation(MEDIA, "mime=mime, idx=idx, job_id=str(job_id)", "mime=mime, idx=0, job_id=str(job_id)",
          expect="the second video is named _2")
def test_a_video_is_served_with_range_and_the_nth_one_gets_its_suffix(db):
    async def scenario(env):
        key = await env.new_key()
        job = await job_of(env, db, key, outputs=2)
        base = "/v1/video/generations/%s/content" % job
        return [await env.client.get(base, headers=key.headers),
                await env.client.get(base + "/1", headers=key.headers),
                await env.client.get(base + "/0", headers={**key.headers, "Range": "bytes=10-19"}),
                await env.client.get(base + "/2", headers=key.headers)]

    first, second, ranged, missing = run_app(db, scenario)
    assert first.status_code == 200 and first.content == DATA and first.headers["content-type"] == "video/mp4"
    assert first.headers["content-disposition"].startswith('inline; filename="a_prompt_5s.mp4"')
    assert second.content == DATA[100:]
    assert second.headers["content-disposition"].startswith('inline; filename="a_prompt_5s_2.mp4"'), (
        "the second video is named _2: %s" % second.headers["content-disposition"])
    assert ranged.status_code == 206 and ranged.content == DATA[10:20]
    assert ranged.headers["content-range"] == "bytes 10-19/%d" % len(DATA)
    assert error_of(missing)[:2] == (404, "resource.not_found")


def test_an_image_is_served_with_its_number_and_the_media_kind_must_match(db):
    async def scenario(env):
        key = await env.new_key()
        image = await job_of(env, db, key, kind="image", outputs=2, mime="image/png")
        video = await job_of(env, db, key)
        return [await env.client.get("/v1/images/generations/%s/content/1" % image, headers=key.headers),
                await env.client.get("/v1/images/generations/%s/content/0" % video, headers=key.headers),
                await env.client.get("/v1/video/generations/%s/content" % image, headers=key.headers),
                await env.client.get("/v1/images/generations/not-an-id/content/0", headers=key.headers),
                await env.client.get("/v1/images/generations/%s/content/-1" % image, headers=key.headers)]

    ok, wrong_kind, other_kind, malformed, negative = run_app(db, scenario)
    assert ok.status_code == 200 and ok.headers["content-type"] == "image/png"
    assert ok.headers["content-disposition"].startswith('inline; filename="a_prompt_5s_02.png"'), (
        ok.headers["content-disposition"])
    for r in (wrong_kind, other_kind, malformed, negative):
        assert error_of(r)[:2] == (404, "resource.not_found"), r.text


@mutation(MEDIA, OWNER, ANY_OWNER, expect="another key must get 404")
def test_another_key_and_a_missing_key_get_404_for_a_job_that_is_not_theirs(db):
    async def scenario(env):
        owner, other = await env.new_key(), await env.new_key()
        job = await job_of(env, db, owner)
        path = "/v1/video/generations/%s/content" % job
        return [await env.client.get(path, headers=other.headers), await env.client.get(path),
                await env.client.get(path, headers=owner.headers)]

    other, keyless, owner = run_app(db, scenario)
    assert error_of(other)[:2] == (404, "resource.not_found"), "another key must get 404"
    assert keyless.status_code == 401 and owner.status_code == 200


@mutation(MEDIA, ROLE, ANY_BUT_ROLE, expect="a debug object must never be served")
def test_debug_media_is_never_served_by_a_content_route(db):
    async def scenario(env):
        key = await env.new_key()
        job = db.job(key.id, kind="video")
        await plant(env, db, job_id=job, role="debug", idx=0, mime="text/plain", data=b"captured page",
                    token=False)
        admin = await env.admin_headers()
        return [await env.client.get("/v1/video/generations/%s/content" % job, headers=key.headers),
                await env.client.get("/admin/jobs/%s/content" % job, headers=admin),
                await env.client.get("/admin/jobs/%s/outputs/0/content" % job, headers=admin),
                await env.client.get("/admin/jobs/%s/images/0/content" % job, headers=admin)]

    for r in run_app(db, scenario):
        assert error_of(r)[:2] == (404, "resource.not_found"), "a debug object must never be served: %s" % r.text


@mutation(ADMIN, 'router.add_api_route("/jobs/{job_id}/images/{idx}/content", _admin_media("input")',
          'router.add_api_route("/jobs/{job_id}/images/{idx}/content", _admin_media("output")',
          expect="the input image is not the output")
def test_the_admin_proxies_serve_outputs_and_input_images_behind_the_session_and_list_them(db):
    async def scenario(env):
        key = await env.new_key()
        job = await job_of(env, db, key, outputs=2)
        await plant(env, db, job_id=job, role="input", idx=0, mime="image/png", data=b"\x89PNG\r\n\x1a\nxx",
                    expires="clock_timestamp() + interval '1 day'")
        admin = await env.admin_headers()
        get = lambda path, **h: env.client.get("/admin/jobs/%s/%s" % (job, path), headers={**admin, **h})  # noqa: E731
        return {"content": await get("content"), "second": await get("content/1"),
                "output": await get("outputs/1/content", Range="bytes=0-3"), "image": await get("images/0/content"),
                "outputs": await get("outputs"), "images": await get("images"),
                "nosession": await env.client.get("/admin/jobs/%s/content" % job)}

    got = run_app(db, scenario)
    assert got["content"].content == DATA and got["second"].content == DATA[100:]
    assert got["output"].status_code == 206 and got["output"].content == DATA[100:104]
    assert got["image"].content == b"\x89PNG\r\n\x1a\nxx", "the input image is not the output"
    assert [i["idx"] for i in got["outputs"].json()["items"]] == [0, 1]
    assert [i["mime"] for i in got["images"].json()["items"]] == ["image/png"]
    assert got["nosession"].status_code == 401
