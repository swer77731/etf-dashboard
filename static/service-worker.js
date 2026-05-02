// ETF 觀察室 Service Worker
// 版本號:每次更新部署都會 bump
const CACHE_VERSION = 'etf-watch-v1.0.0';
const RUNTIME_CACHE = 'etf-watch-runtime';

// 安裝時預先快取的核心資源
const CORE_ASSETS = [
  '/',
  '/manifest.json',
  '/static/icons/icon-192x192.png',
  '/static/icons/icon-512x512.png',
  '/static/icons/apple-touch-icon.png',
];

// 安裝階段:預快取核心
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) => {
      return cache.addAll(CORE_ASSETS);
    }).then(() => self.skipWaiting())
  );
});

// 啟用階段:清除舊版 cache
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((key) => key !== CACHE_VERSION && key !== RUNTIME_CACHE)
            .map((key) => caches.delete(key))
      );
    }).then(() => self.clients.claim())
  );
});

// fetch 策略:
// - 同源 HTML(導航):network first(永遠拿最新內容)
// - 同源 static(icons / css / js):cache first
// - API 請求:network only(資料一定要最新)
// - 第三方資源:cache first(降低延遲)
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // 只處理 GET
  if (request.method !== 'GET') return;

  // API 請求:不快取,永遠 fetch network
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/auth/')) {
    return;
  }

  // 導航(HTML 頁面):network first,失敗才回快取
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          // 成功就更新 cache
          const copy = response.clone();
          caches.open(RUNTIME_CACHE).then((cache) => cache.put(request, copy));
          return response;
        })
        .catch(() => {
          // network 失敗,回快取
          return caches.match(request).then((cached) => cached || caches.match('/'));
        })
    );
    return;
  }

  // static 資源:cache first
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(request).then((cached) => {
        if (cached) return cached;
        return fetch(request).then((response) => {
          const copy = response.clone();
          caches.open(CACHE_VERSION).then((cache) => cache.put(request, copy));
          return response;
        });
      })
    );
    return;
  }

  // 其他:network first
});
