"""meta's `SiteProfile` for the browser kit: everything the kit must know about the site (the kit holds none).

The recorder keeps the bodies of `/api/graphql` only: the `warmupConversation` call there names the conversation before
Enter. The answer's WebSocket is never recorded: its URL carries an auth token."""

from relay_browser.session import SiteProfile
from relay_meta.constants import GRAPHQL_PATH, PROVIDER, SITE_HOST

META_SITE = SiteProfile(name=PROVIDER, host=SITE_HOST, locale="en-US", capture_paths=("^%s$" % GRAPHQL_PATH,))
