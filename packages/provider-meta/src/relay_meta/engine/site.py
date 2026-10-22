"""meta's `SiteProfile` for the browser kit: everything the kit must know about the site (the kit holds none)."""

from relay_browser.session import SiteProfile
from relay_meta.constants import PROVIDER, SITE_HOST

META_SITE = SiteProfile(name=PROVIDER, host=SITE_HOST, locale="en-US")
