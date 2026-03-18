// =============================================
// Validation CM — Service Worker
// =============================================
// Developpeur : bumper APP_VERSION a chaque deploiement.
// =============================================

var APP_VERSION = '1.3.0';
var CACHE_NAME  = 'validation-cm-v' + APP_VERSION;

var PRECACHE_FILES = [
  './index.html',
  './manifest.json',
  './icon-192.png',
  './icon-512.png',
  './favicon.ico',
  './favicon.png'
];

// ----- INSTALL -----
self.addEventListener('install', function(event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function(cache) {
      return cache.addAll(PRECACHE_FILES);
    })
  );
  self.skipWaiting();
});

// ----- ACTIVATE -----
self.addEventListener('activate', function(event) {
  event.waitUntil(
    caches.keys().then(function(cacheNames) {
      return Promise.all(
        cacheNames
          .filter(function(name) { return name !== CACHE_NAME; })
          .map(function(name) { return caches.delete(name); })
      );
    }).then(function() {
      return self.clients.claim();
    })
  );
});

// ----- FETCH -----
self.addEventListener('fetch', function(event) {
  var request = event.request;

  if (request.method !== 'GET') return;

  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request).then(function(networkResponse) {
        if (networkResponse && networkResponse.status === 200) {
          caches.open(CACHE_NAME).then(function(cache) {
            cache.put(request, networkResponse.clone());
          });
        }
        return networkResponse;
      }).catch(function() {
        return caches.match(request);
      })
    );
    return;
  }

  event.respondWith(
    caches.match(request).then(function(cachedResponse) {
      if (cachedResponse) return cachedResponse;
      return fetch(request).then(function(networkResponse) {
        if (networkResponse && networkResponse.status === 200
            && request.url.startsWith(self.location.origin)) {
          var responseClone = networkResponse.clone();
          caches.open(CACHE_NAME).then(function(cache) {
            cache.put(request, responseClone);
          });
        }
        return networkResponse;
      });
    }).catch(function() {
      if (request.mode === 'navigate') {
        return caches.match('./index.html');
      }
    })
  );
});

// ----- MESSAGE -----
self.addEventListener('message', function(event) {
  if (event.data && event.data.type === 'GET_VERSION') {
    event.ports[0].postMessage({ version: APP_VERSION });
  }
});
