/*
 * content.js - DeceptiScan DOM Scanner and Click Interceptor
 *
 * Injected into every page at document_start. Responsible for:
 *   1. Discovering outbound links and forwarding them to the service worker
 *      for classification by the backend AI model.
 *   2. Visually annotating links on the page based on the classification result.
 *   3. Intercepting click events on flagged links and presenting a warning modal
 *      before allowing navigation.
 *   4. Blocking programmatic (untrusted) click and form-submit events, which
 *      are a common vector for pop-under ad injection.
 */

// -------------------------------------------------------------------------
// State
// -------------------------------------------------------------------------

// Tracks URLs that have already been queued or scanned to avoid duplicates.
const scannedUrls = new Set();

// Maps a URL to the array of anchor elements on the page that share that href.
const linkElementMap = new Map();

// Maps a URL to its classification result { isDeceptive, confidence }.
// Populated after the API responds; consulted on every click event.
const linkMetadataMap = new Map();

// -------------------------------------------------------------------------
// Configuration
// -------------------------------------------------------------------------

// Domains always treated as safe; links to these skip the API entirely.
const LOCAL_WHITELIST = [
    'google.com',
    'github.com',
    'microsoft.com',
    'youtube.com',
    'aicw.in',
];

// Anchor text phrases that describe common site navigation rather than content.
// These have no meaningful semantic relationship to a domain, so they are skipped.
const IGNORE_TEXTS = [
    'forgot password', 'login', 'sign in',
    'register', 'contact us', 'privacy policy',
];

// -------------------------------------------------------------------------
// Utility functions
// -------------------------------------------------------------------------

/**
 * Returns true if the given href resolves to the same hostname (or a
 * subdomain of it) as the current page. Same-site links are not deceptive
 * by definition and are excluded from scanning.
 */
function isSameSiteNavigation(anchorHref) {
    try {
        const linkHost = new URL(anchorHref).hostname.replace('www.', '');
        const pageHost = window.location.hostname.replace('www.', '');
        return (
            linkHost === pageHost ||
            linkHost.endsWith('.' + pageHost) ||
            pageHost.endsWith('.' + linkHost)
        );
    } catch {
        return false;
    }
}

// -------------------------------------------------------------------------
// DOM scanning
// -------------------------------------------------------------------------

/**
 * Walks the current page's anchor elements, filters out links that do not
 * need to be checked (already scanned, same-site, whitelisted, functional),
 * and sends the remaining links to the service worker for classification.
 *
 * Called once at page load and again after dynamic content is likely to have
 * settled (deferred calls in startup()).
 */
async function scanPage() {
    const anchors = document.querySelectorAll('a[href]');
    const newLinks = [];

    anchors.forEach(a => {
        const text = a.innerText.trim().toLowerCase();
        const href = a.href;

        // Skip non-HTTP links (mailto:, javascript:, data:, etc.)
        if (!href.startsWith('http')) return;

        // Skip already-scanned URLs
        if (scannedUrls.has(href)) return;

        // Skip navigation to the same site
        if (isSameSiteNavigation(href)) {
            scannedUrls.add(href);
            return;
        }

        // Skip explicitly trusted domains
        try {
            const hostname = new URL(href).hostname.replace('www.', '');
            if (LOCAL_WHITELIST.some(d => hostname === d || hostname.endsWith('.' + d))) {
                scannedUrls.add(href);
                return;
            }
        } catch {
            return;
        }

        // Skip common functional anchor texts
        if (IGNORE_TEXTS.some(t => text.includes(t))) {
            scannedUrls.add(href);
            return;
        }

        // Skip anchors with no meaningful text or pointing to local addresses
        if (text.length <= 1 || href.includes('127.0.0.1')) return;

        // Register the element reference so we can annotate it after classification
        if (!linkElementMap.has(href)) {
            linkElementMap.set(href, []);
        }
        linkElementMap.get(href).push(a);

        newLinks.push({ anchor_text: text, destination_url: href });
        scannedUrls.add(href);
    });

    if (newLinks.length === 0) return;

    console.log(`DeceptiScan: Queuing ${newLinks.length} links for classification.`);

    chrome.runtime.sendMessage(
        { type: 'SCAN_LINKS', links: newLinks },
        response => {
            if (chrome.runtime.lastError) {
                console.error('DeceptiScan: Messaging error -', chrome.runtime.lastError.message);
                return;
            }
            if (!response?.success) {
                console.error('DeceptiScan: API error -', response?.error);
                return;
            }

            applyResults(response.data.results);
        }
    );
}

// -------------------------------------------------------------------------
// Result rendering
// -------------------------------------------------------------------------

/**
 * Iterates over the API classification results and:
 *   - Stores metadata in linkMetadataMap for click interception.
 *   - Applies visual styles to each anchor element on the page.
 */
function applyResults(results) {
    console.group(`DeceptiScan: Results for ${results.length} links`);

    results.forEach(result => {
        const isDeceptive = result.is_deceptive && result.confidence > 50;

        console.log(
            `[${isDeceptive ? 'DECEPTIVE' : 'SAFE'}]`,
            `Conf: ${result.confidence}%`,
            `| Sim: ${result.semantic_similarity}`,
            `| "${result.anchor_text}"`,
            `-> ${result.destination_url}`
        );

        // Store classification result for the click interceptor
        linkMetadataMap.set(result.destination_url, {
            isDeceptive,
            confidence: result.confidence,
        });

        const elements = linkElementMap.get(result.destination_url);
        if (!elements) return;

        elements.forEach(el => {
            if (isDeceptive) {
                // Pill-style highlight for deceptive links
                el.style.setProperty('background-color', 'rgba(226, 75, 74, 0.15)', 'important');
                el.style.setProperty('outline', '2px solid #E24B4A', 'important');
                el.style.setProperty('color', '#A32D2D', 'important');
                el.style.setProperty('border-radius', '3px', 'important');
                el.style.setProperty('padding', '1px 4px', 'important');
                el.style.setProperty('text-decoration', 'none', 'important');
                el.style.setProperty('display', 'inline-block', 'important');
                el.setAttribute(
                    'title',
                    `WARNING: Flagged as deceptive by DeceptiScan\nConfidence: ${result.confidence}% | Similarity: ${result.semantic_similarity}`
                );
            } else {
                // Subtle styling for verified safe links
                el.style.setProperty('color', '#185FA5', 'important');
                el.style.setProperty('text-decoration', 'none', 'important');
                el.setAttribute('title', `DeceptiScan: Safe (${result.confidence}% confidence)`);
            }
        });
    });

    console.groupEnd();
}

// -------------------------------------------------------------------------
// Warning modal
// -------------------------------------------------------------------------

/**
 * Injects the warning modal and its CSS into the current page once.
 * The modal is hidden by default and shown by the click interceptor when a
 * deceptive link is clicked.
 */
function injectUI() {
    if (document.getElementById('deceptiscan-modal')) return;

    // If the body isn't ready yet, retry shortly
    if (!document.body) {
        setTimeout(injectUI, 100);
        return;
    }

    const style = document.createElement('style');
    style.textContent = `
      #deceptiscan-modal {
        display: none;
        position: fixed;
        z-index: 2147483647;
        left: 0;
        top: 0;
        width: 100%;
        height: 100%;
        background-color: rgba(0, 0, 0, 0.8);
        backdrop-filter: blur(8px);
        -webkit-backdrop-filter: blur(8px);
        font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
        justify-content: center;
        align-items: center;
      }
      .ds-trust-card {
        width: 450px;
        background-color: #121417;
        border: 1px solid #2D333B;
        border-radius: 1rem;
        box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
        overflow: hidden;
      }
      .ds-header {
        padding: 2rem 0 1rem 0;
        display: flex;
        flex-direction: column;
        align-items: center;
      }
      .ds-icon-bg {
        background-color: rgba(245, 158, 11, 0.1);
        padding: 1rem;
        border-radius: 9999px;
        margin-bottom: 1rem;
      }
      .ds-title {
        font-size: 1.25rem;
        font-weight: 600;
        color: #f1f5f9;
        letter-spacing: -0.025em;
        margin: 0;
      }
      .ds-body {
        padding: 0 2rem 1.5rem 2rem;
        text-align: center;
      }
      .ds-subtitle {
        font-size: 0.875rem;
        color: #94a3b8;
        line-height: 1.625;
        margin: 0;
      }
      .ds-highlight {
        color: #fb923c;
        font-weight: 500;
      }
      .ds-url-container {
        margin-top: 1.5rem;
        padding: 0.75rem;
        background-color: #1C2128;
        border: 1px solid #2D333B;
        border-radius: 0.5rem;
        word-break: break-all;
      }
      .ds-url-text {
        font-size: 0.75rem;
        color: #cbd5e1;
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
        opacity: 0.8;
      }
      .ds-question {
        margin-top: 1.5rem;
        font-size: 0.875rem;
        font-weight: 500;
        color: #e2e8f0;
        margin-bottom: 0;
      }
      .ds-actions {
        padding: 0 2rem 2rem 2rem;
        display: flex;
        flex-direction: column;
        gap: 0.75rem;
      }
      .ds-btn-exit {
        width: 100%;
        padding: 0.75rem 0;
        background-color: #f1f5f9;
        color: #0f172a;
        font-weight: 700;
        border-radius: 0.75rem;
        border: none;
        transition: background-color 200ms;
        box-shadow: 0 10px 15px -3px rgba(255,255,255,0.05), 0 4px 6px -4px rgba(255,255,255,0.05);
        cursor: pointer;
        font-size: 1rem;
      }
      .ds-btn-exit:hover { background-color: #ffffff; }
      .ds-btn-enter {
        width: 100%;
        padding: 0.5rem 0;
        font-size: 0.75rem;
        font-weight: 500;
        color: #64748b;
        background: transparent;
        border: none;
        transition: color 200ms;
        cursor: pointer;
      }
      .ds-btn-enter:hover { color: #fb923c; }
    `;
    document.head.appendChild(style);

    const modal = document.createElement('div');
    modal.id = 'deceptiscan-modal';
    modal.innerHTML = `
      <div class="ds-trust-card">
        <div class="ds-header">
          <div class="ds-icon-bg">
            <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#F59E0B" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"></path>
              <line x1="12" y1="8" x2="12" y2="12"></line>
              <line x1="12" y1="16" x2="12.01" y2="16"></line>
            </svg>
          </div>
          <h1 class="ds-title">Security Warning</h1>
        </div>
        <div class="ds-body">
          <p class="ds-subtitle">
            DeceptiScan has flagged this link as potentially
            <span class="ds-highlight">malicious or deceptive</span>.
          </p>
          <div class="ds-url-container">
            <code class="ds-url-text" id="ds-target-url"></code>
          </div>
          <div id="ds-explanation" style="margin-top:1rem; font-size:0.875rem; color:#f43f5e; font-weight:500;"></div>
          <div id="ds-reputation" style="margin-top:0.5rem; font-size:0.75rem; color:#94a3b8;">Loading reputation...</div>
          <div id="ds-chain" style="margin-top:0.5rem; font-size:0.75rem; color:#94a3b8; word-break: break-all;"></div>
          <p class="ds-question">Are you sure you want to proceed?</p>
        </div>
        <div class="ds-actions">
          <button class="ds-btn-exit" id="ds-cancel">Go Back (Recommended)</button>
          <button class="ds-btn-enter" id="ds-proceed">Proceed Anyway</button>
          <button class="ds-btn-enter" id="ds-report-safe" style="color:#10b981; margin-top:0.5rem;">Report False Positive (Mark Safe)</button>
        </div>
      </div>
    `;
    document.body.appendChild(modal);

    document.getElementById('ds-cancel').onclick = () => {
        document.getElementById('deceptiscan-modal').style.display = 'none';
    };
    document.getElementById('ds-proceed').onclick = () => {
        window.location.href = pendingUrl;
    };
    document.getElementById('ds-report-safe').onclick = () => {
        chrome.runtime.sendMessage({ type: 'REPORT_URL', url: pendingUrl, vote: 'safe' });
        alert('Thank you for reporting. The community reputation has been updated.');
        document.getElementById('deceptiscan-modal').style.display = 'none';
    };
}

let pendingUrl = '';

document.addEventListener('submit', e => {
    if (!e.isTrusted) {
        console.warn('DeceptiScan: Blocked untrusted form submission.');
        e.preventDefault();
        e.stopImmediatePropagation();
    }
}, true);

document.addEventListener('click', e => {
    let target = e.target;
    while (target && target.tagName !== 'A') {
        target = target.parentNode;
    }

    if (!target || !target.href) return;

    if (!e.isTrusted) {
        console.warn('DeceptiScan: Blocked untrusted click on', target.href);
        e.preventDefault();
        e.stopImmediatePropagation();
        return;
    }

    const clickedUrl = target.href.toLowerCase().trim().replace(/\/$/, '');

    let foundMetadata = null;
    for (const [storedUrl, data] of linkMetadataMap) {
        if (storedUrl.toLowerCase().trim().replace(/\/$/, '') === clickedUrl) {
            foundMetadata = data;
            break;
        }
    }

    if (foundMetadata && foundMetadata.isDeceptive) {
        e.preventDefault();
        e.stopImmediatePropagation();

        const modal = document.getElementById('deceptiscan-modal');
        if (modal) {
            pendingUrl = target.href;
            document.getElementById('ds-target-url').innerText = target.href;
            document.getElementById('ds-explanation').innerText = foundMetadata.explanation || '';
            document.getElementById('ds-reputation').innerText = 'Loading reputation...';
            document.getElementById('ds-chain').innerText = 'Tracing redirects...';
            modal.style.display = 'flex';

            chrome.runtime.sendMessage({ type: 'GET_REPUTATION', url: target.href }, res => {
                if (res && res.domain) {
                    document.getElementById('ds-reputation').innerText = `Community: ${res.safe_votes} Safe, ${res.malicious_votes} Malicious`;
                }
            });

            chrome.runtime.sendMessage({ type: 'TRACE_URL', url: target.href }, res => {
                if (res && res.chain) {
                    document.getElementById('ds-chain').innerHTML = `<strong>Redirect Chain:</strong><br>` + res.chain.join(' <br>➔ ');
                } else {
                    document.getElementById('ds-chain').innerText = 'No redirects detected.';
                }
            });
        }
    }
}, true);

// -------------------------------------------------------------------------
// Advanced Detection Engines
// -------------------------------------------------------------------------
function detectHoneypots() {
    const anchors = document.querySelectorAll('a[href]');
    const honeypots = [];
    const elementsMap = new Map();

    anchors.forEach(a => {
        const rect = a.getBoundingClientRect();
        const style = window.getComputedStyle(a);
        let isHidden = false;

        const hasText = a.textContent.trim().length > 0;

        if (hasText && (rect.left < -999 || rect.top < -999 || parseInt(style.textIndent) < -999)) {
            isHidden = true;
        }

        if (hasText && (style.opacity === '0' || parseInt(style.zIndex) < 0 || style.visibility === 'hidden')) {
            isHidden = true;
        }

        if (rect.width > window.innerWidth * 0.5 && rect.height > window.innerHeight * 0.5) {
            if (style.opacity === '0' || parseInt(style.zIndex) > 900) {
                isHidden = true;
            }
        }

        if (isHidden && a.href.startsWith('http') && !isSameSiteNavigation(a.href)) {
            honeypots.push({ anchor_text: a.textContent.trim() || "Hidden Link", destination_url: a.href });
            if (!elementsMap.has(a.href)) elementsMap.set(a.href, []);
            elementsMap.get(a.href).push(a);
        }
    });

    if (honeypots.length === 0) return;

    chrome.runtime.sendMessage({ type: 'SCAN_LINKS', links: honeypots }, response => {
        if (!response || !response.success) return;
        
        response.data.results.forEach(result => {
            if (result.is_deceptive) {
                const elements = elementsMap.get(result.destination_url);
                if (elements) {
                    elements.forEach(a => {
                        a.style.setProperty('display', 'block', 'important');
                        a.style.setProperty('opacity', '1', 'important');
                        a.style.setProperty('visibility', 'visible', 'important');
                        a.style.setProperty('border', '3px dashed #f43f5e', 'important');
                        a.style.setProperty('background', 'rgba(244, 63, 94, 0.2)', 'important');
                        a.style.setProperty('z-index', '9999', 'important');
                        a.style.setProperty('position', 'relative', 'important');
                        a.style.setProperty('padding', '10px', 'important');
                        a.style.setProperty('margin-top', '10px', 'important');
                        a.style.setProperty('text-align', 'center', 'important');
                        a.style.setProperty('color', '#f43f5e', 'important');
                        a.style.setProperty('font-weight', 'bold', 'important');
                        a.setAttribute('title', `DECEPTISCAN WARNING: Malicious Honeypot\n${result.explanation || ''}`);
                        console.warn('DeceptiScan: Confirmed malicious honeypot:', a.href);
                    });
                }
            }
        });
    });
}

function detectPhishingClones() {
    chrome.runtime.sendMessage({ type: 'GET_BRANDS' }, brands => {
        if (!brands || Object.keys(brands).length === 0) return;

        const title = document.title.toLowerCase();
        const domain = window.location.hostname.toLowerCase();

        for (const [brand, officialDomain] of Object.entries(brands)) {
            if (officialDomain && title.includes(brand) && !domain.includes(officialDomain) && !domain.includes('127.0.0.1')) {
                const banner = document.createElement('div');
                banner.innerHTML = `<div style="position:fixed; top:0; left:0; width:100%; background:#f43f5e; color:white; text-align:center; padding:12px; z-index:2147483647; font-weight:bold; font-family:sans-serif; box-shadow:0 4px 6px rgba(0,0,0,0.1);">
                    🚨 DECEPTISCAN ALERT: This page impersonates ${brand.toUpperCase()}! Official domain is ${officialDomain}. Do not enter credentials.
                </div>`;
                document.body.appendChild(banner);
                console.warn(`DeceptiScan: Phishing clone detected for ${brand}`);
            }
        }
    });
}

function extractQRCodes() {
    const images = document.querySelectorAll('img');
    images.forEach(img => {
        if (img.width > 50 && img.width < 600 && img.height > 50 && img.height < 600) {
            try {
                const canvas = document.createElement('canvas');
                canvas.width = img.width;
                canvas.height = img.height;
                const ctx = canvas.getContext('2d');
                ctx.drawImage(img, 0, 0, img.width, img.height);
                const dataURL = canvas.toDataURL('image/png');

                chrome.runtime.sendMessage({ type: 'SCAN_QR', image: dataURL }, response => {
                    if (response && response.urls && response.urls.length > 0) {
                        console.warn('DeceptiScan: QR Code detected with URLs:', response.urls);
                        chrome.runtime.sendMessage({ type: 'SCAN_LINKS', links: response.urls.map(u => ({ anchor_text: 'QR Code', destination_url: u })) }, res => {
                            if (res && res.data && res.data.results) {
                                res.data.results.forEach(r => {
                                    if (r.isDeceptive || r.is_deceptive) {
                                        img.style.border = '4px dotted #f43f5e';
                                        img.style.boxShadow = '0 0 15px 5px rgba(244, 63, 94, 0.6)';
                                        img.style.borderRadius = '8px';
                                        alert(`DECEPTISCAN QUISHING WARNING:\nThe QR code on this page points to a malicious link:\n${r.destination_url}`);
                                    }
                                });
                            }
                        });
                    }
                });
            } catch (e) { } // Ignore cross-origin canvas tainting
        }
    });
}

function startup() {
    console.log('DeceptiScan: Initialised.');
    injectUI();
    detectPhishingClones();
    setTimeout(() => { scanPage(); detectHoneypots(); extractQRCodes(); }, 1000);
    setTimeout(() => { scanPage(); detectHoneypots(); }, 3000);
}

if (document.readyState === 'complete' || document.readyState === 'interactive') {
    startup();
} else {
    window.addEventListener('load', startup);
}

window.addEventListener('deceptiscan-trigger', () => {
    console.log('DeceptiScan: Manual scan triggered from popup.');
    scanPage();
    detectHoneypots();
    extractQRCodes();
});