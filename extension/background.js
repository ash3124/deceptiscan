/*
 * background.js - DeceptiScan Service Worker
 *
 * Acts as the bridge between the content script and the remote prediction API.
 * Runs as a Manifest V3 service worker; all network requests must originate
 * here because content scripts cannot contact cross-origin endpoints directly.
 *
 * Keep-alive: Chrome terminates idle service workers after ~30 seconds.
 * The interval below pings a lightweight platform API to keep the worker
 * active for the duration of the browser session.
 */

const keepAlive = () => setInterval(chrome.runtime.getPlatformInfo, 20000);
chrome.runtime.onStartup.addListener(keepAlive);
keepAlive();

/*
 * Message handler for SCAN_LINKS requests sent by content.js.
 *
 * Expects: { type: 'SCAN_LINKS', links: [{ anchor_text, destination_url }] }
 * Responds: { success: true, data: { results: [...] } }
 *        or { success: false, error: '...' }
 *
 Returns true to signal that sendResponse will be called asynchronously.
 */
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.type === 'SCAN_LINKS') {
    fetch('https://deceptiscan.onrender.com/predict', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ links: request.links }),
    })
      .then(r => r.json())
      .then(data => {
        sendResponse({ success: true, data });
      })
      .catch(err => {
        console.error('DeceptiScan: Fetch error -', err.message);
        sendResponse({ success: false, error: err.message });
      });

    return true; // Keep the message channel open for the async response.
  }
});