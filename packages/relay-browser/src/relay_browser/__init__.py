"""relay-browser: the only package that may import patchright.

Imported only by browser providers' engines, never by the API. It does not import relay_core: the browser token is
checked by what it carries and the redactor is passed in by the caller.

Modules: `session` (open_session, SiteProfile, ResponsePatch), `net` (NetRecorder), `ui`, `evidence`, `text`,
`forwarder`. This file imports none of them, so importing the package alone loads nothing else."""
