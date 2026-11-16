/* No private responses or documents are cached. The offline page has no user data. */
self.addEventListener('install',e=>self.skipWaiting());
self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));
self.addEventListener('fetch',e=>{if(e.request.mode==='navigate')e.respondWith(fetch(e.request).catch(()=>new Response('<!doctype html><meta name="viewport" content="width=device-width"><title>Job Coach offline</title><h1>Connect to your Job Coach server</h1><p>Your private workspace is not cached on this device. Restore your connection and reopen the app. Unsaved edits in an open page can be copied before reloading.</p>',{headers:{'Content-Type':'text/html'}})));});
