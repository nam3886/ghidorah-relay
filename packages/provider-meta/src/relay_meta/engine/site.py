"""meta's `SiteProfile` for the browser kit: everything the kit must know about the site (the kit holds none).

The recorder keeps the bodies of `/api/graphql` (the `warmupConversation` call there names the conversation before
Enter; the `conversation` query is the thread a reconcile reads) and of the image upload (`rupload.meta.ai`, whose POST
answers `{media_id}` once the image is uploaded). The answer's WebSocket is never recorded: its URL carries an auth
token."""

import re

from relay_browser.session import SiteProfile
from relay_meta.constants import GRAPHQL_PATH, PROVIDER, SITE_HOST, UPLOAD_PATH

META_SITE = SiteProfile(name=PROVIDER, host=SITE_HOST, locale="en-US",
                        capture_paths=("^%s$" % GRAPHQL_PATH, "^%s" % re.escape(UPLOAD_PATH)))
