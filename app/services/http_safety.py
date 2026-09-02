"""
A single place to validate a client-supplied redirect target before
ever passing it to redirect() -- used anywhere a "next"/"next_url"
value comes from a form field or query string rather than being built
server-side with url_for(). Without this, an attacker can craft a
link like /some/endpoint?next=https://evil.example.com and have it
redirect a logged-in victim straight there once they click it (an
"open redirect") -- useful for phishing since the link itself points
at this app's own trusted domain.

Standard Flask idiom for this check: resolve the candidate against
the current request's own host with urljoin (so a plain relative path
resolves to something on this host, and an absolute URL resolves to
itself), then require the result to actually be on this host. Doing
the comparison this way -- rather than a naive
`target.startswith("/")` -- correctly rejects the tricks that a
naive check misses:
- Protocol-relative URLs ("//evil.example.com") LOOK like a path but
  browsers treat them as "same scheme, different host" and follow
  them off-site.
- Backslash variants ("/\\evil.example.com") that some browsers
  historically normalized to protocol-relative URLs.
- A scheme-relative or fully-qualified URL pointing at a different
  host entirely.
"""
from urllib.parse import urlparse, urljoin

from flask import request


def is_safe_redirect_target(target):
    """True if `target` resolves to a URL on this app's own host --
    safe to pass to redirect(). False for anything else (a missing/
    empty value, a different host, a non-http(s) scheme like
    javascript:), in which case the caller should fall back to a
    known-safe url_for(...) destination instead of using `target`."""
    if not target:
        return False

    host_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ("http", "https") and test_url.netloc == host_url.netloc
