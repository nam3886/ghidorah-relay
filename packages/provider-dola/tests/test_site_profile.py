"""dola's SiteProfile carries what the live measurement of 05/10 found (see engine/site.py comments)."""

from relay_browser.session import CHROME_BACKGROUND_HOSTS
from relay_core.testing.mutation import mutation
from relay_dola.engine.site import DOLA_SITE

SITE_FILE = "packages/provider-dola/src/relay_dola/engine/site.py"


@mutation(SITE_FILE, '"samantha_web_web_id", "flow_tea_user_id")', '"samantha_web_web_id")',
          expect="flow_tea_user_id")
@mutation(SITE_FILE, '("__tea_cache_tokens_495671", "for_ban_appeal_sec_user_id",', '("for_ban_appeal_sec_user_id",',
          expect="__tea_cache_tokens_495671")
def test_the_account_bound_local_storage_keys_are_declared_for_the_strip():
    assert set(DOLA_SITE.local_storage_token_keys) == {
        "__tea_cache_tokens_495671", "for_ban_appeal_sec_user_id", "samantha_web_web_id", "flow_tea_user_id"}
    assert not any(k.startswith(("text.", "version.")) for k in DOLA_SITE.local_storage_token_keys), (
        "the 1.1 MB UI string table stays cached")


@mutation(SITE_FILE, 'DOLA_REFUSE_HOSTS: tuple[str, ...] = ("www.google.com",)',
          "DOLA_REFUSE_HOSTS: tuple[str, ...] = ()", expect="www.google.com")
def test_the_proxy_forwarder_refuses_google_because_dola_uses_its_own_captcha():
    assert "www.google.com" in DOLA_SITE.refuse_hosts
    assert set(CHROME_BACKGROUND_HOSTS) <= set(DOLA_SITE.refuse_hosts)
