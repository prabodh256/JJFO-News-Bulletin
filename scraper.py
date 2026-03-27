chennai\b|\bhyderabad\b|\bpune\b|'
    r'\bnse\b|\bbse\b|\bsebi\b|\brbi\b',
    re.IGNORECASE
)

GARBAGE = re.compile(
        r'cookie|privacy policy|javascript:|void\(|brand stor|sponsored|'
        r'subscribe|newsletter|gold price|silver price|biography|personal finance|'
        r'horoscope|astrology|recipe|cricket score|ipl|bollywood|mastering multi|'
        r'oracle mastering|digital banking platform journey',
        re.I
)

# -- Helpers ------------------------------------------------------------------
COMPANY_BLOCKLIST = {
        'Loco': re.compile(
                    r'\b(railway|railways|indian railway|locomotive|loco pilot|loco shed|'
                    r'wireless control|control system|traction|diesel loco|electric loco|'
                    r'rolling stock|train|rail|irctc|irfc|rlda|rites|rail vikas)\b', re.I
        ),
}

FALSE_CONTEXT = re.compile(
        r'\b(wireless|railway|railways|locomotive|loco pilot|control system|traction|'
        r'diesel|electric loco|shed|depot|division|zone|signal|track|coach|wagon|'
        r'bogie|pantograph|overhead|catenary|traction motor|brake|coupler|buffer|'
        r'rolling stock|rail|irctc|irfc|rlda|rites|rail vikas|crore order|supply order)\b',
        re.I
)

def is_proper_noun(headline, company_name):
        """
            Match company name as a proper noun (capitalized) in headline.
                FIX v9: Minimum name length lowered from 4 to 2 so short tickers like
                    'NSE', 'AU' (AU SFB) are no longer silently skipped.
                        """
        if len(company_name) < 2:
                    return False
                if company_name in COMPANY_BLOCKLIST:
                            if COMPANY_BLOCKLIST[company_name].search(headline):
                                      #!/usr/bin/env python3
"""
JJFO News Bulletin - PRODUCTION SCRAPER v9
- Google News RSS per company (India geo-filtered)
- Real article URLs via googlenewsdecoder
- Proper noun matching (avoids 'loco' matching 'Loco' company)
- India-context filtering for ambiguous names
- 48-hour lookback, dedup, prioritised email

FIXES vs v8:
  - Added TechCrunch + Economic Times to GENERAL_RSS
    - Removed dead/unreliable feeds (IndianWeb2, BW Disrupt)
      - decode_url() now logs failures instead of silently swallowing them
        - is_proper_noun() min-length lowered from 4 to 2 (fixes NSE, AU, etc.)
        """

import os, csv, re, time, urllib.parse, requests, feedparser
from datetime import datetime, timezone
from collections import defaultdict
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from googlenewsdecoder import new_decoderv1

# -- Config -------------------------------------------------------------------
RESEND_API_KEY = os.getenv('RESEND_API_KEY', '')
if not RESEND_API_KEY:
        print("ERROR: RESEND_API_KEY not set"); exit(1)
    print(f"OK API Key: {RESEND_API_KEY[:12]}...")

COMPANIES_FILE  = 'companies.csv'
RECIPIENTS_FILE = 'recipients.csv'
DISPATCH_LOG    = 'dispatch_log.csv'

# Supplementary Indian startup RSS feeds
# REMOVED: IndianWeb2 (feed frequently dead), BW Disrupt (feed unreliable)
# ADDED:   TechCrunch (global VC/startup coverage), Economic Times Startups
GENERAL_RSS = {
        'Inc42':        'https://inc42.com/feed/',
        'YourStory':    'https://yourstory.com/feed',
        'Entrackr':     'https://entrackr.com/feed/',
        'VCCircle':     'https://www.vccircle.com/feed/',
        'StartupTalky': 'https://www.startuptalky.com/feed/',
        'The Ken':      'https://the-ken.com/feed/',
        'TechCircle':   'https://techcircle.vccircle.com/feed/',
        'AIM':          'https://analyticsindiamag.com/feed/',
        'TechCrunch':   'https://techcrunch.com/feed/',
        'Economic Times Startups':
                        'https://economictimes.indiatimes.com/prime/technology-and-startups/rssfeeds/78570553.cms',
}

INDIAN_DOMAINS = {
        'inc42.com', 'yourstory.com', 'entrackr.com', 'vccircle.com',
        'startuptalky.com', 'the-ken.com', 'techcircle.vccircle.com',
        'analyticsindiamag.com', 'economictimes.indiatimes.com',
        'livemint.com', 'business-standard.com', 'moneycontrol.com',
        'ndtv.com', 'thehindu.com', 'hindustantimes.com', 'timesofindia.indiatimes.com',
        'businessline.com', 'financialexpress.com', 'manufacturingtodayindia.com',
        'indianexpress.com', 'swarajyamag.com', 'msn.com', 'rediff.com',
        'thehindubusinessline.com', 'cnbctv18.com', 'zeebiz.com',
}

# Company names that could match non-Indian entities - require India context
AMBIGUOUS_NAMES = {
        'Arata', 'Terra', 'Grip', 'Loco', 'Wise', 'Simpl', 'Porter',
        'Orange Credit', 'Right Health', 'August Jewellery', 'White Owl',
        'Sadhan', 'Rooter', 'WorkIndia',
}

INDIA_KW = re.compile(
        r'\bindia\b|\bindian\b|\bstartup\b|\bcrore\b|\binr\b|\brupee\b|'
        r'\bmumbai\b|\bdelhi\b|\bbangalore\b|\b
