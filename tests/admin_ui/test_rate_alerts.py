"""The drift alerts dola declares: they register, and the content-refused rate counts likeness refusals too."""

from relay_core.providers import ProviderRegistry
from relay_core.testing.mutation import mutation
from relay_dola import errors as codes
from relay_dola.spec import DolaSpec

SPEC = "packages/provider-dola/src/relay_dola/spec.py"


@mutation(SPEC, "(codes.CONTENT_REFUSED, codes.LIKENESS_REFUSED)", "(codes.CONTENT_REFUSED,)",
          expect="likeness refusals count as content refusals")
def test_dola_declares_three_rate_alerts_that_register_and_content_refused_includes_likeness():
    spec = DolaSpec()
    ProviderRegistry().register(spec)
    by_setting = {a.setting: a for a in spec.rate_alerts}
    assert set(by_setting) == {"dola.alert_duration_mismatch_rate", "dola.alert_watermark_only_rate",
                               "dola.alert_content_refused_rate"}
    refused = by_setting["dola.alert_content_refused_rate"]
    assert set(refused.code) == {codes.CONTENT_REFUSED, codes.LIKENESS_REFUSED}, (
        "likeness refusals count as content refusals")
