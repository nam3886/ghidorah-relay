"""relay e2e --provider dola: it refuses to spend credits unasked, and judges the delivered length with a tolerance."""

import asyncio

from relay_core.testing.mutation import mutation
from relay_dola import e2e

E2E = "packages/provider-dola/src/relay_dola/e2e.py"


@mutation(E2E, 'if env.get("RELAY_E2E_SPEND") != "yes":', "if False:", expect="== 2")
def test_it_refuses_to_spend_credits_unless_asked(capsys):
    assert asyncio.run(e2e.run({"DATABASE_URL": "postgresql://nowhere/x"})) == 2
    assert asyncio.run(e2e.run({"RELAY_E2E_SPEND": "no", "DATABASE_URL": "postgresql://nowhere/x"})) == 2
    assert "RELAY_E2E_SPEND=yes" in capsys.readouterr().err


@mutation(E2E, "return abs(measured - wanted) <= tolerance", "return True", expect="not e2e.measured_length_ok")
def test_the_length_must_match_within_the_tolerance():
    assert e2e.measured_length_ok(5.08, 5) and e2e.measured_length_ok(7.0, 5)
    assert not e2e.measured_length_ok(7.5, 5) and not e2e.measured_length_ok(2.0, 5)
