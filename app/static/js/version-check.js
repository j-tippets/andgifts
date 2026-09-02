// Notices when the app has been deployed since this page loaded, and
// prompts a refresh instead of silently running a mix of old and new
// assets. Complements (doesn't replace) the service worker's own
// auto-reload-on-update in base.html: that path only fires for
// browser/PWA installs and only re-checks sw.js on a fresh page load,
// so a long-lived open tab/session (this app's Today stack is mostly
// client-side fetch()es, not full navigations) can otherwise go a
// while without ever noticing a deploy happened. This checks on a
// plain interval and whenever the tab regains focus, independent of
// the service worker lifecycle.
(function () {
  var initialVersion = document.documentElement.getAttribute('data-app-version');
  if (!initialVersion) return;

  var CHECK_INTERVAL_MS = 5 * 60 * 1000;
  var dismissed = false;
  var banner = null;

  function showBanner() {
    if (banner || dismissed) return;
    banner = document.createElement('div');
    banner.className = 'version-banner';
    banner.setAttribute('role', 'status');

    var message = document.createElement('span');
    message.textContent = 'A new version of &Gifts is available.';

    var actions = document.createElement('div');
    actions.className = 'version-banner__actions';

    var dismissBtn = document.createElement('button');
    dismissBtn.type = 'button';
    dismissBtn.className = 'version-banner__dismiss';
    dismissBtn.textContent = 'Not now';
    dismissBtn.addEventListener('click', function () {
      dismissed = true;
      banner.remove();
      banner = null;
    });

    var refreshBtn = document.createElement('button');
    refreshBtn.type = 'button';
    refreshBtn.className = 'version-banner__cta';
    refreshBtn.textContent = 'Refresh';
    refreshBtn.addEventListener('click', function () {
      window.location.reload();
    });

    actions.appendChild(dismissBtn);
    actions.appendChild(refreshBtn);
    banner.appendChild(message);
    banner.appendChild(actions);
    document.body.appendChild(banner);
  }

  function checkVersion() {
    if (dismissed) return;
    fetch('/api/app-version', { cache: 'no-store' })
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (data) {
        if (data && data.version && data.version !== initialVersion) {
          showBanner();
        }
      })
      .catch(function () {
        // Network hiccup -- try again on the next interval rather than
        // treating it as a version mismatch.
      });
  }

  setInterval(checkVersion, CHECK_INTERVAL_MS);
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') checkVersion();
  });
})();
