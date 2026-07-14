// &Gifts service worker
//
// Scope is intentionally narrow: this app is server-rendered and
// session/CSRF-sensitive, so we do NOT cache HTML pages or API responses.
// The only job here is (a) satisfy the "installable PWA" requirement for
// Android/Capacitor, and (b) speed up repeat loads of static assets
// (css/js/icons/fonts) that don't change per-request.
//
// Bump CACHE_VERSION any time main.css / today.js change in a way that
// should bust old cached copies for already-installed users.
const CACHE_VERSION = "ag-static-v1";
const STATIC_ASSET_PATTERN = /\/static\/(css|js|icons)\//;

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== CACHE_VERSION)
          .map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const { request } = event;

  // Only ever intercept same-origin GET requests for static assets.
  // Everything else (pages, forms, POSTs, cross-origin) passes straight
  // through to the network untouched.
  if (request.method !== "GET" || !STATIC_ASSET_PATTERN.test(request.url)) {
    return;
  }

  event.respondWith(
    caches.open(CACHE_VERSION).then(async (cache) => {
      const cached = await cache.match(request);
      if (cached) return cached;

      const response = await fetch(request);
      if (response.ok) {
        cache.put(request, response.clone());
      }
      return response;
    })
  );
});
