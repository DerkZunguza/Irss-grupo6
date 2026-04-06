// service-worker.js — PWA cache strategy para Hardware Monitor
// Cache-first para assets estáticos, Network-first para chamadas API

const CACHE_NAME    = 'hw-monitor-v1';
const API_PREFIX    = '/api/';

// Assets estáticos a pré-carregar no install
const STATIC_ASSETS = [
  '/',
  '/index.html',
  '/css/style.css',
  '/js/app.js',
  '/manifest.json',
  'https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js',
];

// ── Install: pré-cache de assets estáticos ────────────────────────────────────
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      console.log('[SW] Pré-caching assets estáticos');
      return cache.addAll(STATIC_ASSETS).catch((err) => {
        console.warn('[SW] Falha ao pré-cachear alguns assets:', err);
      });
    })
  );
  self.skipWaiting();
});

// ── Activate: limpar caches antigos ──────────────────────────────────────────
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => k !== CACHE_NAME)
          .map((k) => {
            console.log('[SW] Removendo cache antigo:', k);
            return caches.delete(k);
          })
      )
    )
  );
  self.clients.claim();
});

// ── Fetch: estratégia híbrida ─────────────────────────────────────────────────
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Ignorar extensões de browser e métodos não-GET
  if (request.method !== 'GET') return;
  if (url.protocol === 'chrome-extension:') return;

  // API: Network-first com fallback de cache
  if (url.pathname.startsWith(API_PREFIX)) {
    event.respondWith(networkFirst(request));
    return;
  }

  // Assets estáticos: Cache-first com fallback de rede
  event.respondWith(cacheFirst(request));
});

// Network-first: tenta rede, cai para cache se offline
async function networkFirst(request) {
  try {
    const networkResponse = await fetch(request);
    // Cachear respostas GET bem-sucedidas da API
    if (networkResponse.ok) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, networkResponse.clone());
    }
    return networkResponse;
  } catch {
    const cached = await caches.match(request);
    if (cached) {
      console.log('[SW] Offline — usando cache para:', request.url);
      return cached;
    }
    // Resposta offline genérica para API
    return new Response(
      JSON.stringify({
        success: false,
        data: null,
        message: 'Sem conexão à internet. A usar dados em cache.',
      }),
      {
        status: 503,
        headers: { 'Content-Type': 'application/json' },
      }
    );
  }
}

// Cache-first: serve do cache, actualiza em background
async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;

  try {
    const networkResponse = await fetch(request);
    if (networkResponse.ok) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, networkResponse.clone());
    }
    return networkResponse;
  } catch {
    // Fallback para index.html em navegação
    if (request.mode === 'navigate') {
      return caches.match('/index.html');
    }
    throw new Error('Asset não encontrado em cache ou rede');
  }
}
