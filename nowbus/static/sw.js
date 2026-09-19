/* 앱 셸 캐시.
 *
 * 계획서 §10-8 이 경고한 함정: service worker 캐시가 개발 중 갱신을 먹는다.
 * 그래서 셸도 **network-first** 로 둔다. 온라인이면 항상 최신을 받고, 캐시는
 * 오프라인이나 네트워크 실패 때만 쓴다. 재방문 즉시 렌더라는 목표는
 * skipWaiting + clients.claim 으로 새 워커가 곧바로 넘겨받아 달성한다.
 *
 * /api/* 는 절대 캐시하지 않는다. 실시간 도착정보가 캐시되면 이 프로그램의
 * 존재 이유가 사라진다.
 */
// UI 가 바뀌면 올린다. activate 에서 옛 캐시를 지우므로 폰에 남은
// 이전 app.js/style.css 가 오프라인에서 되살아나지 않는다.
const VERSION = 'nowbus-v2';
const SHELL = ['/', '/style.css', '/app.js', '/manifest.json', '/icons/icon-192.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;
  if (url.pathname.startsWith('/api/')) return;  // 실시간 데이터는 건드리지 않는다

  e.respondWith(
    fetch(e.request)
      .then((res) => {
        const copy = res.clone();
        caches.open(VERSION).then((c) => c.put(e.request, copy)).catch(() => {});
        return res;
      })
      .catch(() => caches.match(e.request).then((hit) => hit || caches.match('/')))
  );
});
