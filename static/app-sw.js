/* Harava phone app — the service worker (served as /app/sw.js, scope /app/).
 *
 * Its one job: "Share → Harava". When Harava is installed from Chrome on Android, it appears in the
 * share menu (the manifest's share_target). Sharing a Fieldwatch export POSTs the file to
 * /app/share; this worker catches that POST on the phone, keeps the file(s) in the browser's cache
 * storage and opens /app?shared=N. The app then uploads them with the normal sign-in, like
 * "Choose files…" (and asks only when you have several phones). Nothing else is cached or
 * intercepted: Harava always shows live data.
 */
'use strict';
const SHARE_CACHE = 'harava-share';

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'POST' || url.pathname !== '/app/share') return;   // everything else: the network
  event.respondWith((async () => {
    let n = 0;
    try {
      const form = await event.request.formData();
      const files = form.getAll('files').filter(f => f && typeof f !== 'string' && f.size > 0);
      await caches.delete(SHARE_CACHE);                    // one share at a time
      const cache = await caches.open(SHARE_CACHE);
      const names = [];
      for (const f of files) {
        names.push(f.name || `shared-${n + 1}`);
        await cache.put(new Request(`/app/shared-file/${n}`), new Response(f, {
          headers: { 'Content-Type': f.type || 'application/octet-stream', 'X-Harava-Name': encodeURIComponent(f.name || `shared-${n + 1}`) },
        }));
        n += 1;
      }
      await cache.put(new Request('/app/shared-meta'), new Response(JSON.stringify({
        n, names, at: Date.now(), text: String(form.get('title') || form.get('text') || ''),
      }), { headers: { 'Content-Type': 'application/json' } }));
    } catch (e) {
      n = -1;
    }
    return Response.redirect(`/app?shared=${n}`, 303);
  })());
});
