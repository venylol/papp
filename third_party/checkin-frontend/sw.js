/* eslint-disable no-restricted-globals */
"use strict";

const VERSION = new URL(self.location.href).searchParams.get("v") || "dev";
const CACHE_PREFIX = "checkin-assistant-cache-";
const CACHE_NAME = `${CACHE_PREFIX}${VERSION}`;

const CORE_ASSETS = [
  "./",
  "./index.html",
  "./manifest.webmanifest",
  `./manifest.webmanifest?v=${VERSION}`,
  "./styles.css",
  `./styles.css?v=${VERSION}`,
  "./app.js",
  `./app.js?v=${VERSION}`,
  "./papp-ap-ui.js",
  `./papp-ap-ui.js?v=${VERSION}`,
  "./checkin-i18n.js",
  `./checkin-i18n.js?v=${VERSION}`,
  "./tournament-adapter.js",
  `./tournament-adapter.js?v=${VERSION}`,
  "./papp-score-png-renderer.js",
  `./papp-score-png-renderer.js?v=${VERSION}`,
  "./papp-standings-png-renderer.js",
  `./papp-standings-png-renderer.js?v=${VERSION}`,
  "./vendor/html2canvas.min.js",
  `./vendor/html2canvas.min.js?v=${VERSION}`,
  "./icons/favicon-32.png",
  "./icons/apple-touch-icon.png",
  "./icons/icon-192.png",
  "./icons/icon-192-maskable.png",
  "./icons/icon-512.png",
  "./icons/icon-512-maskable.png",
];

async function matchCached(request) {
  const cache = await caches.open(CACHE_NAME);
  const exact = await cache.match(request);
  if (exact) return exact;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return null;

  // Versioned assets must not fall back to an older same-path cache entry.
  if (url.searchParams.has("v")) return null;

  const byPath = await cache.match(url.pathname);
  if (byPath) return byPath;

  if (request.mode === "navigate") {
    const nav = await cache.match("./index.html");
    if (nav) return nav;
  }

  return null;
}

async function putIfOk(request, response) {
  if (!response || response.status !== 200 || response.type === "opaque")
    return;

  const cache = await caches.open(CACHE_NAME);
  await cache.put(request, response.clone());

  const url = new URL(request.url);
  if (url.origin === self.location.origin && url.search) {
    await cache.put(url.pathname, response.clone());
  }
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE_NAME);
      await Promise.allSettled(CORE_ASSETS.map((asset) => cache.add(asset)));
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys
          .filter((k) => k.startsWith(CACHE_PREFIX) && k !== CACHE_NAME)
          .map((k) => caches.delete(k)),
      );
      await self.clients.claim();
    })(),
  );
});

self.addEventListener("message", (event) => {
  if (event && event.data && event.data.type === "SKIP_WAITING") {
    self.skipWaiting();
  }
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (!request || request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Navigation: network-first, fallback to cached shell.
  if (request.mode === "navigate") {
    event.respondWith(
      (async () => {
        try {
          const response = await fetch(request);
          await putIfOk(request, response);
          return response;
        } catch (_) {
          const cached = await matchCached(request);
          if (cached) return cached;
          throw _;
        }
      })(),
    );
    return;
  }

  // Static assets: cache-first, then network fallback.
  event.respondWith(
    (async () => {
      const cached = await matchCached(request);
      if (cached) return cached;

      const response = await fetch(request);
      await putIfOk(request, response);
      return response;
    })(),
  );
});
