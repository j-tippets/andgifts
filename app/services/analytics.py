"""Bridges server-side events into the client-side GTM dataLayer.

The GA4 property is fed entirely through GTM (container GTM-WQVQNDV),
which only ever reads window.dataLayer in the browser -- it has no way
to see something that happened purely in a Flask view. Most of our
meaningful events (signup, contact created, gift approved, charge
confirmed) are decided server-side though, often right before a
redirect. queue_event() bridges that gap: it stores the event on the
Flask session, and base.html renders + clears the queue as
dataLayer.push() calls on whatever page the user lands on next.

This works for the normal request -> commit -> redirect -> render
cycle. It does NOT work for an AJAX (fetch) call whose response is
never inserted into the DOM -- see dashboard.approve_action, which is
called from today.js's fetch()-based approve flow. For that case, use
pop_pending_events() directly and return the events in a JSON response
instead, with the caller pushing them into dataLayer itself. See
today.js's submitApprove for the client side of that.
"""

from flask import session

SESSION_KEY = "_analytics_queue"


def queue_event(event, **params):
    """Queue a dataLayer event to render on the next page the current
    session loads (via base.html) or to be returned directly to an
    AJAX caller (via pop_pending_events()). `event` is the GA4/GTM
    event name; params become top-level dataLayer keys alongside it."""
    payload = {"event": event}
    payload.update(params)
    queue = session.get(SESSION_KEY, [])
    queue.append(payload)
    session[SESSION_KEY] = queue


def pop_pending_events():
    """Returns and clears whatever's queued. base.html calls this on
    every render (including non-AJAX pages) so nothing queued from a
    redirect gets rendered twice. AJAX routes that queue events and
    then respond with JSON instead of a redirect should call this
    directly and include the result in their response body."""
    return session.pop(SESSION_KEY, [])
