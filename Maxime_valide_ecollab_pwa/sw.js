// =============================================
// Validation CM — Service Worker
// =============================================
// Developpeur : bumper APP_VERSION a chaque deploiement.
// Ce seul changement declenche le cycle complet de mise a jour.
// =============================================

var APP_VERSION = '1.4.26';
var CACHE_NAME  = 'validation-cm-v' + APP_VERSION;
// Delai au-dela duquel une navigation n'attend plus le reseau (reseau lent).
var NAV_TIMEOUT_MS = 2500;

var PRECACHE_FILES = [
  './index.html',
  './manifest.json',
  './icon-192.png',
  './icon-512.png',
  './favicon.ico',
  './favicon.png'
];

// ----- INSTALL -----
// Pre-cache les fichiers essentiels, puis activation immediate (skipWaiting).
// `cache: 'reload'` contourne le cache HTTP du navigateur : sans lui, une
// version perimee d'index.html peut etre pre-cachee telle quelle, et l'app
// continue d'afficher l'ancienne page alors que le SW annonce la nouvelle
// version. Un fichier manquant ne fait pas echouer toute l'installation.
self.addEventListener('install', function(event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function(cache) {
      return Promise.all(PRECACHE_FILES.map(function(url) {
        return fetch(url, { cache: 'reload' }).then(function(resp) {
          if (resp && resp.status === 200) return cache.put(url, resp);
        }).catch(function() {});
      }));
    })
  );
  self.skipWaiting();
});

// ----- ACTIVATE -----
// Supprime TOUS les anciens caches, puis prend le controle des clients.
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

  // Ignorer les requetes non-GET
  if (request.method !== 'GET') return;

  // Navigations HTML → reseau d'abord, cache en secours.
  // En "stale-while-revalidate", chaque deploiement s'affichait avec un
  // chargement de retard : l'app montrait l'ancienne page et ne recuperait la
  // nouvelle qu'au lancement suivant. On privilegie donc le reseau, sans
  // l'attendre indefiniment : passe NAV_TIMEOUT_MS (reseau lent) ou en cas
  // d'echec (hors ligne), on sert la page en cache.
  if (request.mode === 'navigate') {
    event.respondWith(
      caches.open(CACHE_NAME).then(function(cache) {
        var network = fetch(request).then(function(networkResponse) {
          if (networkResponse && networkResponse.status === 200) {
            cache.put(request, networkResponse.clone());
          }
          return networkResponse;
        });
        return cache.match(request).then(function(cachedResponse) {
          if (!cachedResponse) {
            return network.catch(function() { return caches.match('./index.html'); });
          }
          return Promise.race([
            network.catch(function() { return cachedResponse; }),
            new Promise(function(resolve) {
              setTimeout(function() { resolve(cachedResponse); }, NAV_TIMEOUT_MS);
            })
          ]);
        });
      })
    );
    return;
  }

  // Autres requetes → cache-first, fallback reseau
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
// Permet a la page de demander la version du SW
self.addEventListener('message', function(event) {
  if (event.data && event.data.type === 'GET_VERSION') {
    event.ports[0].postMessage({ version: APP_VERSION });
  }
});
