# DeceptiScan

A browser extension that detects deceptive and phishing links in real time using machine learning. Unlike extensions that rely on static URL blacklists — which are ineffective against zero-day attacks — DeceptiScan computes the semantic relationship between the visible anchor text and the destination domain for every outbound link on a page.

---

## How It Works

The system has two components that work together:

**Backend (Flask API on Render)**
- Receives batches of anchor-text and destination-URL pairs from the extension.
- Extracts a 15-feature vector per link covering URL structure, domain semantics, and brand context.
- Runs the feature vector through a pre-trained Random Forest classifier.
- Returns a classification result (safe / deceptive) with a confidence score for each link.

**Extension (Chrome, Manifest V3)**
- Scans the page's anchor elements after load, skipping same-site and whitelisted links.
- Forwards unseen links to the service worker, which contacts the backend API.
- Visually annotates links on the page (red highlight for deceptive, blue for safe).
- Intercepts clicks on flagged links in the DOM capture phase and presents a warning modal before any navigation occurs.
- Blocks programmatic (untrusted) click and form-submit events used by pop-under ads.

---

## Feature Set

### Backend

| Feature | Description |
|---|---|
| Semantic similarity | SequenceMatcher ratio between anchor text and URL content (domain + path) |
| Brand impersonation | Detects brand keywords in anchor text that do not resolve to the brand's canonical domain |
| Suspicious TLD | Flags known high-abuse TLDs (.ru, .xyz, .tk, .ml, etc.) |
| Tracker parameters | Detects common affiliate and tracking query parameters |
| Executable extension | Flags links pointing to .exe, .bat, or .apk files |
| IP-based URL | Detects raw IP addresses used in place of a domain name |
| Subdomain depth | Counts subdomain levels (deep nesting is a common phishing pattern) |
| URL shortener | Detects bit.ly, t.co, tinyurl, and similar services |
| Special character count | Counts @, !, #, $, %, ^, & in the URL |
| Digit count | Counts numeric characters in the URL |
| Hyphen count | Counts hyphens (frequently used in lookalike domains) |
| Anchor text length | Character length of the visible link text |
| Anchor suspicious words | Presence of urgency/reward words (free, win, claim, verify, etc.) |
| Anchor word count | Word count of the anchor text |
| URL length | Total character length of the destination URL |

### Extension

- Local whitelist engine: popular and explicitly trusted domains skip the API entirely.
- Context-aware overrides: generic navigation phrases ("Log in", "Sign up") receive a similarity boost to prevent false positives on legitimate sites.
- Two-pass scanning: an initial pass 1 second after load, and a second pass at 3 seconds to capture JavaScript-rendered content.
- Manual scan: the popup button triggers an on-demand re-scan of the current page.

---

## Project Structure

```
deceptiscan/
├── backend/
│   ├── app.py              # Flask API, feature extraction, model inference
│   ├── requirements.txt    # Python dependencies
│   ├── models/             # Serialised model files (.pkl)
│   └── .gitignore
├── extension/
│   ├── manifest.json       # Manifest V3 configuration
│   ├── content.js          # DOM scanner and click interceptor
│   ├── background.js       # Service worker and API bridge
│   └── popup.html          # Manual scan interface
├── demo.html               # Self-contained test page with multiple link scenarios
└── README.md
```

---

## Setup and Deployment

### Backend (Render)

The backend is already live at `https://deceptiscan.onrender.com`.

To redeploy:
1. Push the `backend/` directory to a GitHub repository.
2. Create a new Web Service on Render and connect the repository.
3. Set the start command to:

   ```
   gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 1 --timeout 120
   ```
4. Render will handle the `$PORT` binding automatically.

### Extension (Chrome)

1. Open `chrome://extensions/` in Google Chrome.
2. Enable **Developer mode** (toggle in the top-right corner).
3. Click **Load unpacked** and select the `extension/` folder.
4. The extension is active immediately on all pages.

The extension is pre-configured to use the live Render backend. No local server is required.

---

## Local Development

To run the backend locally:

```bash
cd backend
pip install -r requirements.txt
python app.py
```

The server starts at `http://127.0.0.1:5000`. Update the `fetch` URL in `extension/background.js` to point to it while testing locally.

---
