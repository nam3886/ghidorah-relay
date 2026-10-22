"""Measured prices: per provider, model and requested duration, the points charged, the jobs the
upstream disagreed on and the lowest figure it named; a job the upstream did something else for is counted apart."""

import json

import pytest
from accountkit import done_job
from apikit import run_app
from jobkit import key

from relay_core.testing.mutation import mutation

ROUTES = "packages/relay-core/src/relay_core/admin/routes_prices.py"
SWAP = 'SUBSTITUTION = r"\\.(model_substituted|duration_drift)$"'
NO_SWAP = 'SUBSTITUTION = r"^$"'
LOWEST = "min(upstream_price) FILTER (WHERE NOT swapped AND upstream_price <> price)"
LOWEST_ANY = "min(upstream_price) FILTER (WHERE upstream_price <> price)"
DISAGREED = "count(*) FILTER (WHERE NOT swapped AND upstream_price <> price)"
DISAGREED_ANY = "count(*) FILTER (WHERE upstream_price <> price)"

fabricated = pytest.mark.fabricated_rows("plants priced jobs with no ledger rows", "no charge row in the ledger")


def priced(db, k, *, price=10, upstream=None, duration=5, model="fake-video", warning=None, provider="fake", ref=None):
    job = done_job(db, k, ref=ref or "p%d" % db.one("SELECT count(*) FROM jobs")[0])
    warnings = [{"code": warning, "text": "t"}] if warning else []
    db.run("UPDATE jobs SET price = %s, upstream_price = %s, model = %s, provider = %s, taken_at = clock_timestamp(), "
           "request = %s::jsonb, warnings = %s::jsonb WHERE id = %s",
           price, upstream, model, provider, json.dumps({"duration": duration}), json.dumps(warnings), job)
    return job


async def prices(env, query: str = ""):
    return await env.client.get("/admin/prices" + query, headers=await env.admin_headers())


@fabricated
@mutation(ROUTES, SWAP, NO_SWAP, expect="substituted")
@mutation(ROUTES, LOWEST, LOWEST_ANY, expect="lowest upstream")
@mutation(ROUTES, DISAGREED, DISAGREED_ANY, expect="disagreed")
def test_a_tier_shows_what_was_charged_the_disagreements_and_the_lowest_upstream_figure(db):
    k = key(db)
    priced(db, k, upstream=10)
    priced(db, k, upstream=8)
    priced(db, k, upstream=6)
    priced(db, k, upstream=2, warning="fake.duration_drift")      # another length was delivered: not about this tier
    priced(db, k, upstream=1, warning="fake.model_substituted")
    priced(db, k, upstream=None)                                  # the upstream named no figure: not a fact
    priced(db, k, upstream=3, duration=10)
    priced(db, k, upstream=1, model="fake-image", duration=None, price=1)

    async def scenario(env):
        return await prices(env)

    body = run_app(db, scenario).json()
    tiers = {(i["model"], i["duration"]): i for i in body["items"]}
    five = tiers[("fake-video", 5)]
    assert (five["jobs"], five["charged"]) == (3, 10), "substituted: the swapped jobs are counted apart"
    assert five["lowest_upstream"] == 6, "lowest upstream"
    assert five["disagreed"] == 2, "disagreed"
    assert five["substituted"] == 2, "substituted: the swapped jobs are counted apart"
    assert tiers[("fake-video", 10)]["disagreed"] == 1 and tiers[("fake-video", 10)]["lowest_upstream"] == 3
    image = tiers[("fake-image", None)]
    assert (image["jobs"], image["disagreed"], image["lowest_upstream"], image["charged"]) == (1, 0, None, 1)
    assert body["disagreed_total"] == 3 and body["substituted_total"] == 2


@fabricated
def test_the_provider_filter_and_a_provider_with_no_facts(db):
    k = key(db)
    priced(db, k, upstream=7)
    priced(db, k, upstream=7, provider="other", ref="o1")

    async def scenario(env):
        return await prices(env, "?provider=other"), await prices(env, "?provider=none"), await prices(env)

    other, none, everything = run_app(db, scenario)
    assert [i["provider"] for i in other.json()["items"]] == ["other"]
    assert none.json() == {"items": [], "disagreed_total": 0, "substituted_total": 0}
    assert len(everything.json()["items"]) == 2
