# DeceptiScan

DeceptiScan is a machine-learning-powered Chrome Extension designed to protect users from zero-day phishing attacks, malvertising, and deceptive web navigation. 

Instead of relying solely on static blacklists, DeceptiScan actively analyzes the **semantic relationship** between the text you click and the underlying destination URL, using a combination of Natural Language Processing (NLP) and a Random Forest Classifier.

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
- Visually annotates links on the page (red highlight for deceptive).
- Intercepts clicks on flagged links in the DOM capture phase and presents a warning modal before any navigation occurs.
- Blocks programmatic (untrusted) click and form-submit events used by pop-under ads.
-  Extracts the destination URL from decoded QR data and forwards it through the same phishing detection pipeline used for anchor links.
- Scans the full DOM on every page load, not just visible anchor elements.
- Identifies hidden anchors by checking computed opacity below 0.05, elements positioned entirely outside the viewport, and zero-dimension elements.
- Flags pages where structural similarity to a known brand exceeds the detection threshold but the current domain does not match that brand's canonical domains.
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
2. Turn on **Developer mode** (toggle in the top right).
3. Click **Load unpacked**.
4. Select the `extension/` folder from this project.

*(Note: If deploying your backend to a live server, ensure you update the API URL in `extension/background.js` before packaging the extension).*

---

## 🧪 Testing the Extension

We have provided a local HTML file containing various attack scenarios (Semantic Mismatches, Tracker Parameters, URL Shorteners, etc.).

1. Ensure the backend server is running.
2. Ensure the extension is loaded in Chrome.
3. Open a local web server to host the demo page:
   ```bash
   python -m http.server 8000
   ```
4. Navigate to `http://localhost:8000/demo.html` in your browser. You will see the deceptive links highlighted automatically!

---

## 🧠 How the AI Works

The core of DeceptiScan is a Random Forest model trained on both legitimate web traffic and datasets like PhishTank. It evaluates 13 distinct features in real-time:

1.  **Semantic Similarity**: Cosine similarity between the link text and the destination URL content.
2.  **Lexical Heuristics**: Detection of suspicious TLDs (`.xyz`, `.tk`), common scam keywords (`free`, `urgent`), IP-based URLs, and executable file extensions.
3.  **Brand Impersonation**: Checks if the text references a major brand (e.g., Google, Amazon) while the destination domain does not match the canonical brand domain.

---

## 🛡️ Privacy First

DeceptiScan is designed to protect your privacy. It only scans the `href` destinations of links explicitly rendered on the page. It does not track your browsing history, cookies, or session data, and implements local domain whitelisting to prevent unnecessary API calls.
