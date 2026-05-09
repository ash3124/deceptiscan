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

def get_brand_domains():
    """Fetch the brand domains mapping dynamically from the SQLite database."""
    conn = sqlite3.connect('reputation.db')
    c = conn.cursor()
    c.execute('SELECT brand_name, canonical_domain FROM brands')
    rows = c.fetchall()
    conn.close()
    
    brand_dict = {}
    for brand, domain in rows:
        brand_dict[brand] = domain if domain else None
    return brand_dict

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

    brand_dict = get_brand_domains()
    for brand, canonical in brand_dict.items():
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
# Advanced Feature Engines (AI Explanations & Reputation)
# ---------------------------------------------------------------------------

import sqlite3

def init_db():
    conn = sqlite3.connect('reputation.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS reputation
                 (domain TEXT PRIMARY KEY, safe_votes INTEGER, malicious_votes INTEGER)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS brands
                 (brand_name TEXT PRIMARY KEY, canonical_domain TEXT)''')
    
    # Check if we need to seed the default brands
    c.execute('SELECT COUNT(*) FROM brands')
    if c.fetchone()[0] == 0:
        default_brands = {
            'paypal':    'paypal.com',
            'paytm':     'paytm.com',
            'sbi':       'onlinesbi.sbi',
            'hdfc':      'hdfcbank.com',
            'flipkart':  'flipkart.com',
            'phonepe':   'phonepe.com',
            'irctc':     'irctc.co.in',
            'google':    'google.com',
            'facebook':  'facebook.com',
            'microsoft': 'microsoft.com',
            'apple':     'apple.com',
            'amazon':    'amazon.com',
            'netflix':   'netflix.com',
            'bank':      None,
            'github':    'github.com',
        }
        for brand, domain in default_brands.items():
            c.execute('INSERT INTO brands (brand_name, canonical_domain) VALUES (?, ?)', (brand, domain))
            
    conn.commit()
    conn.close()

init_db()

def generate_explanation(f, is_deceptive):
    reasons = []
    if f['brand_impersonation'] == 1:
        reasons.append("It imitates a trusted brand but redirects to an unofficial domain.")
    if f['has_exe'] == 1:
        reasons.append("The link leads directly to a potentially dangerous executable payload.")
    if f['has_ip'] == 1:
        reasons.append("It uses a direct IP address instead of a standard domain name.")
    if f['has_shortener'] == 1:
        reasons.append("It uses a URL shortener, which is often used to hide the true destination.")
    if f['has_suspicious_tld'] == 1:
        reasons.append("The domain uses a top-level domain (TLD) commonly associated with spam.")
    if f['semantic_similarity'] < 0.2:
        reasons.append("The text you clicked has almost nothing to do with the actual URL destination.")
    if f['has_tracker_param'] == 1:
        reasons.append("It contains tracking parameters often used in malicious redirect chains.")
    
    if not is_deceptive:
        return "This link appears safe based on its structure and destination."
    
    if not reasons:
        return "The AI engine flagged this link due to an unusual combination of structural anomalies."
    
    return " ".join(reasons)

# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.route('/predict', methods=['POST'])
def predict():
    try:
        data = request.json
        links = data.get('links', [])
        results = []

        for link in links:
            features_dict = extract_features(link['anchor_text'], link['destination_url'])
            X = pd.DataFrame([features_dict])[feature_cols]

            prediction = clf.predict(X)[0]
            probabilities = clf.predict_proba(X)[0]
            confidence = probabilities.max() * 100
            
            is_deceptive = bool(prediction)
            
            # Trust Override: If the domain is highly reputable (Tranco Top 1M)
            # and it is NOT attempting to impersonate another brand, force it to SAFE.
            if features_dict.get('is_popular_domain') == 1 and features_dict.get('brand_impersonation') == 0:
                is_deceptive = False

            explanation = generate_explanation(features_dict, is_deceptive)

            results.append({
                'anchor_text':        link['anchor_text'],
                'destination_url':    link['destination_url'],
                'is_deceptive':       is_deceptive,
                'confidence':         round(float(confidence), 1),
                'semantic_similarity': round(float(features_dict['semantic_similarity']), 3),
                'explanation':        explanation
            })

        return jsonify({'results': results})
    except Exception as e:
        import traceback
        return jsonify({'success': False, 'error': str(e), 'trace': traceback.format_exc()}), 500

@app.route('/brands', methods=['GET'])
def fetch_brands():
    """Return the current active brand domains dictionary for the frontend."""
    return jsonify(get_brand_domains())

@app.route('/reputation', methods=['GET'])
def get_reputation():
    url = request.args.get('url')
    ext = tldextract.extract(url)
    domain = f"{ext.domain}.{ext.suffix}"
    conn = sqlite3.connect('reputation.db')
    c = conn.cursor()
    c.execute('SELECT safe_votes, malicious_votes FROM reputation WHERE domain = ?', (domain,))
    row = c.fetchone()
    conn.close()
    if row:
        return jsonify({'domain': domain, 'safe_votes': row[0], 'malicious_votes': row[1]})
    return jsonify({'domain': domain, 'safe_votes': 0, 'malicious_votes': 0})

@app.route('/report', methods=['POST'])
def report_domain():
    data = request.json
    url = data.get('url')
    vote = data.get('vote')
    ext = tldextract.extract(url)
    domain = f"{ext.domain}.{ext.suffix}"
    
    conn = sqlite3.connect('reputation.db')
    c = conn.cursor()
    c.execute('SELECT safe_votes, malicious_votes FROM reputation WHERE domain = ?', (domain,))
    row = c.fetchone()
    if not row:
        c.execute('INSERT INTO reputation (domain, safe_votes, malicious_votes) VALUES (?, 0, 0)', (domain,))
    
    if vote == 'safe':
        c.execute('UPDATE reputation SET safe_votes = safe_votes + 1 WHERE domain = ?', (domain,))
    elif vote == 'malicious':
        c.execute('UPDATE reputation SET malicious_votes = malicious_votes + 1 WHERE domain = ?', (domain,))
        
    conn.commit()
    conn.close()
    return jsonify({'success': True})

import requests as req
from urllib.parse import urljoin

@app.route('/trace', methods=['POST'])
def trace_redirects():
    data = request.json
    url = data.get('url')
    chain = []
    current_url = url
    try:
        for _ in range(5):
            chain.append(current_url)
            resp = req.head(current_url, allow_redirects=False, timeout=3)
            if 300 <= resp.status_code < 400 and 'Location' in resp.headers:
                next_url = resp.headers['Location']
                if not next_url.startswith('http'):
                    next_url = urljoin(current_url, next_url)
                current_url = next_url
            else:
                break
    except Exception:
        pass
    return jsonify({'chain': chain})

from PIL import Image
from pyzbar.pyzbar import decode
import base64
import io

@app.route('/scan_qr', methods=['POST'])
def scan_qr():
    try:
        data = request.json
        image_b64 = data.get('image')
        if not image_b64:
            return jsonify({'urls': []})
        if image_b64.startswith('data:image'):
            image_b64 = image_b64.split(',')[1]
        image_data = base64.b64decode(image_b64)
        image = Image.open(io.BytesIO(image_data))
        decoded = decode(image)
        urls = [obj.data.decode('utf-8') for obj in decoded if obj.data.decode('utf-8').startswith('http')]
        return jsonify({'urls': urls})
    except Exception as e:
        return jsonify({'urls': [], 'error': str(e)})

@app.route('/health')
def health():
    return jsonify({'status': 'running', 'model_loaded': clf is not None})

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True)