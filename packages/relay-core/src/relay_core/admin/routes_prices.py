"""Admin: measured prices.

`jobs.price` is the only figure a customer is charged; `jobs.upstream_price` is what the upstream said it took, stored
only once the order was taken. Per provider, model and requested duration this lists the points charged, how many
jobs the upstream disagreed on, and the lowest figure it named. A job the upstream did a different thing for (it
swapped the model, or delivered another length: a `.model_substituted` or `.duration_drift` warning) says nothing
about the price of the tier that was asked for, so it is counted apart and never as a disagreement.
"""

from fastapi import APIRouter, Depends
from psycopg_pool import AsyncConnectionPool

from relay_core.api.deps import get_pool
from relay_core.db import tx

router = APIRouter()

SUBSTITUTION = r"\.(model_substituted|duration_drift)$"
_PRICES = (
    "SELECT provider, model, duration, "
    "count(*) FILTER (WHERE NOT swapped), count(*) FILTER (WHERE NOT swapped AND upstream_price <> price), "
    "min(upstream_price) FILTER (WHERE NOT swapped AND upstream_price <> price), count(*) FILTER (WHERE swapped), "
    "(array_agg(price ORDER BY created_at DESC))[1] FROM ("
    " SELECT j.provider, j.model, NULLIF(j.request->>'duration', '') AS duration, j.price, j.upstream_price, "
    " j.created_at, EXISTS (SELECT 1 FROM jsonb_array_elements(j.warnings) w WHERE w->>'code' ~ %(swap)s) AS swapped "
    " FROM jobs j WHERE j.upstream_price IS NOT NULL AND (%(p)s::text IS NULL OR j.provider = %(p)s)) t "
    "GROUP BY provider, model, duration ORDER BY provider, model, duration")


@router.get("/prices")
async def prices(provider: str | None = None, pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    """One row per (provider, model, duration) that has a job with an upstream figure. `charged` is the price of the
    newest such job. `disagreed` counts jobs whose upstream figure differs from the charge; `lowest_upstream` is the
    smallest figure among them; `substituted` counts the jobs left out of both for the reason above."""
    async with tx(pool) as conn:
        rows = await (await conn.execute(_PRICES, {"p": provider, "swap": SUBSTITUTION})).fetchall()
    items = [{"provider": p, "model": m, "duration": None if d is None else int(float(d)), "jobs": n,
              "charged": charged, "disagreed": disagreed, "lowest_upstream": lowest, "substituted": swapped}
             for p, m, d, n, disagreed, lowest, swapped, charged in rows]
    return {"items": items, "disagreed_total": sum(i["disagreed"] for i in items),
            "substituted_total": sum(i["substituted"] for i in items)}
