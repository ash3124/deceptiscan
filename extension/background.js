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

const API_BASE = 'http://127.0.0.1:5000'; // Local testing

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.type === 'SCAN_LINKS') {
    fetch(`${API_BASE}/predict`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ links: request.links }),
    })
      .then(r => r.json())
      .then(data => sendResponse({ success: true, data }))
      .catch(err => sendResponse({ success: false, error: err.message }));
    return true;
  }
  
  if (request.type === 'GET_REPUTATION') {
    fetch(`${API_BASE}/reputation?url=${encodeURIComponent(request.url)}`)
      .then(r => r.json())
      .then(data => sendResponse(data))
      .catch(err => sendResponse({ error: err.message }));
    return true;
  }

  if (request.type === 'REPORT_URL') {
    fetch(`${API_BASE}/report`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: request.url, vote: request.vote }),
    })
      .then(r => r.json())
      .then(data => sendResponse(data))
      .catch(err => sendResponse({ error: err.message }));
    return true;
  }

  if (request.type === 'TRACE_URL') {
    fetch(`${API_BASE}/trace`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: request.url }),
    })
      .then(r => r.json())
      .then(data => sendResponse(data))
      .catch(err => sendResponse({ error: err.message }));
    return true;
  }

  if (request.type === 'SCAN_QR') {
    fetch(`${API_BASE}/scan_qr`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image: request.image }),
    })
      .then(r => r.json())
      .then(data => sendResponse(data))
      .catch(err => sendResponse({ error: err.message }));
    return true;
  }

  if (request.type === 'GET_BRANDS') {
    fetch(`${API_BASE}/brands`)
      .then(r => r.json())
      .then(data => sendResponse(data))
      .catch(err => sendResponse({ error: err.message }));
    return true;
  }
});