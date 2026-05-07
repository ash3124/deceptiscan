"""
DeceptiScan - Backend API
=========================
Flask-based REST API that accepts batches of anchor-text / destination-URL
pairs, extracts hand-crafted URL and semantic features, and classifies each
link as deceptive or safe using a pre-trained Random Forest model.

Deployment target: Render Free Tier (512 MB RAM).
Start command: gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 1 --timeout 120
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import joblib
import tldextract
import re
import numpy as np
import pandas as pd
import os
from urllib.parse import urlparse

# Limit CPU thread usage to stay within Render's free-tier memory constraints.
os.environ['OMP_NUM_THREADS'] = '1'
os.environ['MKL_NUM_THREADS'] = '1'
os.environ['OPENBLAS_NUM_THREADS'] = '1'

# ---------------------------------------------------------------------------
# App initialisation
# ---------------------------------------------------------------------------

app = Flask(__name__)
CORS(app)

# Model references; populated lazily on the first prediction request.
clf = None
feature_cols = None

# ---------------------------------------------------------------------------
# String similarity (lightweight alternative to sentence-transformers)
# ---------------------------------------------------------------------------

from difflib import SequenceMatcher

def compute_similarity(a, b):
    """Return a [0, 1] similarity ratio between two strings using SequenceMatcher."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

def load_models():
    """Load the Random Forest classifier and feature column list from disk.
    
    Uses a module-level flag so the models are only loaded once per worker
    process (lazy initialisation on the first /predict request).
    """
    global clf, feature_cols
    if clf is None:
        print("DeceptiScan: Loading Random Forest model...")
        try:
            clf = joblib.load('models/deceptiscan_model.pkl')
            feature_cols = joblib.load('models/feature_cols.pkl')
            print("DeceptiScan: Model ready.")
        except Exception as e:
            import traceback
            error_msg = f"Model load failed: {str(e)}\n{traceback.format_exc()}"
            print(error_msg)
            raise RuntimeError(error_msg)

# ---------------------------------------------------------------------------
# Static configuration
# ---------------------------------------------------------------------------

def load_popular_domains():
    """Load the Tranco popular-domain list from disk (if available)."""
    try:
        if os.path.exists('data/popular_domains.csv'):
            df = pd.read_csv('data/popular_domains.csv')
            return df['domain'].tolist()
    except Exception as e:
        print(f"Could not load popular domains: {e}")
    return []

POPULAR_DOMAINS = load_popular_domains()

# Mapping of well-known brand keywords to their canonical registered domains.
# A value of None means no single canonical domain exists; any use with
# login-style anchor text is treated as suspicious.
BRAND_DOMAINS = {
    'paypal':    'paypal.com',
    'google':    'google.com',
    'facebook':  'facebook.com',
    'microsoft': 'microsoft.com',
    'apple':     'apple.com',
    'amazon':    'amazon.com',
    'netflix':   'netflix.com',
    'instagram': 'instagram.com',
    'twitter':   'twitter.com',
    'whatsapp':  'whatsapp.com',
    'bank':      None,
    'linkedin':  'linkedin.com',
    'dropbox':   'dropbox.com',
}

# Anchor text phrases that describe generic site navigation rather than
# content, and therefore have no meaningful semantic link to a domain name.
GENERIC_ACTION_TEXTS = [
    'log in', 'login', 'sign in', 'signin', 'register',
    'sign up', 'forgot password', 'create account',
    'my account', 'log out', 'logout', 'continue',
    'get started', 'join now', 'subscribe'
]

# Domains that are always considered safe regardless of model output.
TRUSTED_DOMAINS = list(set(['google.com', 'github.com', 'microsoft.com', 'aicw.in'] + POPULAR_DOMAINS))

# Common functional anchor texts that are skipped during scanning.
FUNCTIONAL_TEXTS = [
    'forgot password', 'login', 'sign in', 'register',
    'sign up', 'contact us', 'privacy policy',
    'terms of service', 'about us'
]

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def check_brand_impersonation(anchor_text, destination_url):
    """Return 1 if the anchor text mentions a known brand but the link
    resolves to a domain other than that brand's canonical domain, else 0.
    """
    text_lower = str(anchor_text).lower()
    ext = tldextract.extract(destination_url)
    actual_domain = f"{ext.domain}.{ext.suffix}".lower()

    for brand, canonical in BRAND_DOMAINS.items():
        if brand in text_lower:
            if canonical is None:
                return 1
            if actual_domain == canonical or actual_domain.endswith('.' + canonical):
                return 0
            else:
                return 1
    return 0


def is_generic_action(anchor_text):
    """Return True if the anchor text is a standard navigation action phrase
    (e.g. "Login", "Sign up") with no meaningful content to compare against a URL.
    """
    text = str(anchor_text).lower().strip()
    if len(text) > 30:
        return False
    return any(text == action or text.startswith(action) for action in GENERIC_ACTION_TEXTS)


def get_url_semantic_content(destination_url):
    """Extract a human-readable text representation of a URL's domain,
    path, and query string for use in similarity comparison.
    """
    ext = tldextract.extract(destination_url)
    parsed = urlparse(destination_url)

    domain_words = ext.domain.replace('-', ' ').replace('_', ' ')

    path = parsed.path.lower()
    path_words = re.sub(r'[^a-z\s]', ' ', path.replace('-', ' ').replace('_', ' '))
    path_words = ' '.join(w for w in path_words.split() if len(w) > 2)

    query = parsed.query.lower()
    query_words = re.sub(r'[^a-z\s]', ' ', query.replace('=', ' ').replace('&', ' '))

    combined = f"{domain_words} {path_words} {query_words}".strip()
    return combined if combined else domain_words

# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

def extract_features(anchor_text, destination_url):
    """Compute the full feature vector for a single (anchor_text, URL) pair.
    
    Features are designed to capture both URL structural anomalies and the
    semantic mismatch between what the link says and where it goes.
    
    Returns a dict whose keys match the trained model's feature column list.
    """
    load_models()

    url = str(destination_url).lower()
    text = str(anchor_text).lower()

    # Parse domain components
    ext = tldextract.extract(url)
    domain_name = ext.domain.replace('-', ' ')
    full_domain = f"{ext.domain}.{ext.suffix}"

    # Semantic similarity between anchor text and URL content
    url_content = get_url_semantic_content(url)
    sim = compute_similarity(text, url_content)

    # Determine link context and brand status
    is_generic_action_link = is_generic_action(text)
    brand_mismatch = check_brand_impersonation(text, url)

    # Boost similarity for safe generic action links to prevent false positives
    # (e.g. "Login" on any legitimate site would otherwise score low similarity)
    if is_generic_action_link and brand_mismatch == 0:
        sim = max(sim, 0.75)

    # Check against trusted domain whitelist
    is_trusted = any(full_domain == td or full_domain.endswith('.' + td) for td in TRUSTED_DOMAINS)

    suspicious_tlds = ['.ru', '.xyz', '.tk', '.ml', '.cf', '.ga', '.pw', '.biz', '.click']
    tracker_params = ['utm_source', 'click_id', 'ref=', 'affiliate', 'track', 'redirect']
    suspicious_words = [
        'free', 'click', 'win', 'prize', 'verify', 'urgent', 'limited',
        'offer', 'claim', 'download', 'confirm'
    ]

    return {
        # Semantic / content features
        'semantic_similarity':      1.0 if is_trusted else float(sim),
        'brand_impersonation':      brand_mismatch,
        'anchor_length':            len(text),
        'anchor_suspicious_words':  0 if is_generic_action_link else sum(1 for w in suspicious_words if w in text),
        'anchor_word_count':        len(text.split()),
        # URL structural features
        'url_length':               len(url),
        'has_suspicious_tld':       0 if is_trusted else int(any(t in url for t in suspicious_tlds)),
        'has_tracker_param':        int(any(p in url for p in tracker_params)),
        'has_exe':                  int(any(e in url for e in ['.exe', '.bat', '.apk'])),
        'has_ip':                   int(bool(re.search(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', url))),
        'subdomain_count':          len(ext.subdomain.split('.')) if ext.subdomain else 0,
        'has_shortener':            int(any(s in url for s in ['bit.ly', 't.co', 'tinyurl'])),
        'special_char_count':       len(re.findall(r'[@!#$%^&*]', url)),
        'digit_count':              len(re.findall(r'\d', url)),
        'hyphen_count':             url.count('-'),
    }

# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.route('/predict', methods=['POST'])
def predict():
    """Accept a JSON body with a 'links' array, run inference on each link,
    and return a list of classification results.

    Request body:
        { "links": [{ "anchor_text": "...", "destination_url": "..." }, ...] }

    Response body:
        { "results": [{ "anchor_text", "destination_url", "is_deceptive",
                        "confidence", "semantic_similarity" }, ...] }
    """
    try:
        data = request.json
        links = data.get('links', [])
        results = []

        for link in links:
            features_dict = extract_features(link['anchor_text'], link['destination_url'])

            # Build a DataFrame in the exact column order the model was trained on
            X = pd.DataFrame([features_dict])[feature_cols]

            prediction = clf.predict(X)[0]
            probabilities = clf.predict_proba(X)[0]
            confidence = probabilities.max() * 100

            results.append({
                'anchor_text':        link['anchor_text'],
                'destination_url':    link['destination_url'],
                'is_deceptive':       bool(prediction),
                'confidence':         round(float(confidence), 1),
                'semantic_similarity': round(float(features_dict['semantic_similarity']), 3),
            })

        return jsonify({'results': results})

    except Exception as e:
        import traceback
        return jsonify({'success': False, 'error': str(e), 'trace': traceback.format_exc()}), 500


@app.route('/health')
def health():
    """Simple health-check endpoint used by Render's uptime monitor."""
    return jsonify({'status': 'running', 'model_loaded': clf is not None})

# ---------------------------------------------------------------------------
# Entry point (local development only)
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    # In production this file is served by Gunicorn; the block below is for
    # running the server locally during development.
    app.run(host='127.0.0.1', port=5000, debug=True)