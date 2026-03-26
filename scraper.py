#!/usr/bin/env python3
"""
JJFO News Bulletin - PRODUCTION SCRAPER v6
- Google News RSS per company (finds Agnikul, Dunzo, etc. directly)
- General RSS feeds for broader coverage
- Whole-word matching, min 4 chars
- 48-hour lookback, deduplication, prioritised email
"""

import os, csv, requests, feedparser, re, time, urllib.parse
from datetime import datetime, timezone
from collections import defaultdict
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime

# ── Config ───────────────────────────────────────────────────────────────────
RESEND_API_KEY  = os.getenv('RESEND_API_KEY', '')
if not RESEND_API_KEY:
    print("ERROR: RESEND_API_KEY not set!"); exit(1)
print(f"✓ API Key: {RESEND_API_KEY[:12]}...")

COMPANIES_FILE  = 'companies.csv'
RECIPIENTS_FILE = 'recipients.csv'
DISPATCH_LOG    = 'dispatch_log.csv'

# General RSS feeds (Indian startup ecosystem)
GENERAL_RSS = {
    'Inc42':        'https://inc42.com/feed/',
    'YourStory':    'https://yourstory.com/feed',
    'Entrackr':     'https://entrackr.com/feed/',
    'VCCircle':     'https://www.vccircle.com/feed/',
    'StartupTalky': 'https://www.startuptalky.com/feed/',
    'The Ken':      'https://the-ken.com/feed/',
    'TechCircle':   'https://techcircle.vccircle.com/feed/',
    'AIM':          'https://analyticsindiamag.com/feed/',
    'BW Disrupt':   'https://www.bwdisrupt.com/feed/',
}

# ── Load Data ────────────────────────────────────────────────────────────────
def load_companies():
    companies = {}
    try:
        with open(COMPANIES_FILE, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            next(reader)
            for row in reader:
                if not row: continue
                name = row[0].strip()
                sector = row[1].strip() if len(row) > 1 else ''
                competitors = [c.strip() for c in row[2:] if c.strip() and c.strip() not in ('-','N/A','')]
                if name:
                    companies[name] = {'sector': sector, 'competitors': competitors}
    except FileNotFoundError:
        print(f"ERROR: {COMPANIES_FILE} not found")
    return companies

def load_recipients():
    recipients = []
    try:
        with open(RECIPIENTS_FILE, 'r', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                e = row.get('Email','').strip()
                if e: recipients.append(e)
    except FileNotFoundError:
        print(f"ERROR: {RECIPIENTS_FILE} not found")
    return recipients

# ── Helpers ───────────────────────────────────────────────────────────────────
def whole_word(text, term):
    if len(term) < 4: return False
    return bool(re.search(r'\b' + re.escape(term) + r'\b', text, re.IGNORECASE))

def within_48h(pub_str):
    if not pub_str: return True
    try:
        pub = parsedate_to_datetime(pub_str)
        now = datetime.now(timezone.utc)
        if pub.tzinfo is None: pub = pub.replace(tzinfo=timezone.utc)
        return (now - pub).total_seconds() <= 172800
    except:
        return True

GARBAGE = re.compile(
    r'cookie|privacy policy|javascript:|void\(|brand stor|sponsored|'
    r'subscribe|newsletter|gold price|silver price|biography|personal finance',
    re.IGNORECASE
)

def is_garbage(headline, url):
    return bool(GARBAGE.search(headline)) or 'brandstories' in url or 'javascript' in url.lower()

def google_news_url(query):
    q = urllib.parse.quote(query)
    return f'https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en'

# ── Scrape ────────────────────────────────────────────────────────────────────
def scrape_google_news_per_company(companies):
    """Query Google News RSS for each portfolio company and competitor."""
    matched = defaultdict(list)
    print("\n  [Google News - Portfolio Companies]")
    for name, data in companies.items():
        try:
            feed = feedparser.parse(google_news_url(name))
            found = 0
            for entry in feed.entries[:10]:
                headline = entry.get('title','').strip()
                link     = entry.get('link','').strip()
                pub      = entry.get('published','')
                if not headline or not link: continue
                if is_garbage(headline, link): continue
                if not within_48h(pub): continue
                matched[name].append({
                    'headline': headline, 'url': link,
                    'source': 'Google News', 'is_comp': False,
                    'competitor': None, 'portfolio': name,
                })
                found += 1
            if found:
                print(f"    {name}: {found} articles")
            time.sleep(0.3)
        except Exception as e:
            print(f"    {name}: ERROR {str(e)[:40]}")

    print("\n  [Google News - Competitors]")
    for name, data in companies.items():
        for comp in data['competitors']:
            try:
                feed = feedparser.parse(google_news_url(comp))
                for entry in feed.entries[:5]:
                    headline = entry.get('title','').strip()
                    link     = entry.get('link','').strip()
                    pub      = entry.get('published','')
                    if not headline or not link: continue
                    if is_garbage(headline, link): continue
                    if not within_48h(pub): continue
                    # Only add if competitor is in headline (main subject)
                    if whole_word(headline, comp):
                        matched[name].append({
                            'headline': headline, 'url': link,
                            'source': 'Google News', 'is_comp': True,
                            'competitor': comp, 'portfolio': name,
                        })
                time.sleep(0.2)
            except:
                pass

    return matched

def scrape_general_rss(companies):
    """Scrape general RSS feeds for any company mentions."""
    matched = defaultdict(list)
    print("\n  [General RSS Feeds]")
    for source, url in GENERAL_RSS.items():
        try:
            feed = feedparser.parse(url)
            found = 0
            for entry in feed.entries[:80]:
                headline = entry.get('title','').strip()
                link     = entry.get('link','').strip()
                pub      = entry.get('published','')
                if not headline or not link: continue
                if is_garbage(headline, link): continue
                if not within_48h(pub): continue
                # Portfolio match
                for name in companies:
                    if whole_word(headline, name):
                        matched[name].append({
                            'headline': headline, 'url': link,
                            'source': source, 'is_comp': False,
                            'competitor': None, 'portfolio': name,
                        })
                        found += 1
                        break
                else:
                    # Competitor match
                    for name, data in companies.items():
                        for comp in data['competitors']:
                            if whole_word(headline, comp):
                                matched[name].append({
                                    'headline': headline, 'url': link,
                                    'source': source, 'is_comp': True,
                                    'competitor': comp, 'portfolio': name,
                                })
                                found += 1
                                break
                        else:
                            continue
                        break
            if found:
                print(f"    {source}: {found} matched")
        except Exception as e:
            print(f"    {source}: ERROR {str(e)[:40]}")
    return matched

def merge(a, b):
    merged = defaultdict(list)
    for d in (a, b):
        for k, v in d.items():
            merged[k].extend(v)
    return merged

# ── Categorise & Dedup ────────────────────────────────────────────────────────
FUNDING_KW = re.compile(r'fund|rais|series [a-z]|invest|seed|crore|million|capital|round', re.I)
MA_KW      = re.compile(r'acqui|merger|ipo|buyout|exit|takeover', re.I)
REG_KW     = re.compile(r'regulat|rbi|sebi|policy|ban|law|legal|compliance', re.I)

def categorise(matched):
    cats = defaultdict(list)
    for articles in matched.values():
        for a in articles:
            h = a['headline']
            if   FUNDING_KW.search(h): cats['comp_funding' if a['is_comp'] else 'port_funding'].append(a)
            elif MA_KW.search(h):      cats['comp_ma'      if a['is_comp'] else 'port_ma'].append(a)
            elif REG_KW.search(h):     cats['sector'].append(a)
            else:                      cats['other'].append(a)
    return cats

def dedup(articles):
    seen, unique = [], []
    for a in articles:
        if not any(SequenceMatcher(None, a['headline'].lower(), s.lower()).ratio() > 0.85 for s in seen):
            unique.append(a); seen.append(a['headline'])
    return unique

# ── Format Email ──────────────────────────────────────────────────────────────
def format_email(cats):
    today = datetime.now().strftime('%B %d, %Y')
    lines = [
        f"JJFO News Bulletin — {today}", "",
        "Your daily curated news digest from India's most promising startups.",
        "Stay ahead with the latest funding, M&A, and sector updates.", "",
    ]
    sections = [
        ('FUNDING — PORTFOLIO COMPANIES', 'port_funding'),
        ('FUNDING — COMPETITORS',         'comp_funding'),
        ('M&A — PORTFOLIO COMPANIES',     'port_ma'),
        ('M&A — COMPETITORS',             'comp_ma'),
        ('REGULATORY / SECTOR NEWS',      'sector'),
        ('OTHER NEWS',                    'other'),
    ]
    total = 0
    for title, key in sections:
        articles = dedup(cats.get(key, []))
        if not articles: continue
        lines += [f"\n{'='*55}", title, '='*55]
        for a in articles[:15]:
            label = f"[{a['portfolio']} ← {a['competitor']}] " if a['is_comp'] else f"[{a['portfolio']}] "
            lines += [f"\n• {label}{a['headline']}", f"  {a['url']}"]
            total += 1
    lines.append(f"\n\n— JJFO Intelligence Engine | {today} | {total} articles")
    return "\n".join(lines), total

# ── Send Email ────────────────────────────────────────────────────────────────
def send_email(body, recipients):
    html = ("<html><body>"
            "<pre style='font-family:Arial,sans-serif;font-size:14px;"
            "line-height:1.7;white-space:pre-wrap;max-width:700px;'>"
            + body + "</pre></body></html>")
    for to in recipients:
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={"from":    "JJFO News <noreply@jjfo.in>",
                  "to":      to,
                  "subject": f"JJFO News Bulletin — {datetime.now().strftime('%B %d, %Y')}",
                  "html":    html},
            timeout=15
        )
        if r.status_code == 200:
            print(f"  ✓ Sent to {to}")
        else:
            print(f"  ✗ Failed ({r.status_code}): {r.text[:120]}")
            return False
    return True

def log(status, count):
    try:
        with open(DISPATCH_LOG,'a',newline='') as f:
            csv.writer(f).writerow([datetime.now().isoformat(), status, count])
    except: pass

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("\n" + "="*55)
    print("JJFO NEWS BULLETIN v6 — PRODUCTION SCRAPER")
    print("="*55)
    print(f"Run: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}\nLookback: 48 hours\n")

    companies  = load_companies()
    recipients = load_recipients()
    if not companies:  print("ABORT: No companies loaded");  return
    if not recipients: print("ABORT: No recipients loaded"); return
    print(f"✓ {len(companies)} portfolio companies")
    print(f"✓ {len(recipients)} recipient(s)")

    print("\n── Scraping ──────────────────────────────────────────")
    gn_matched   = scrape_google_news_per_company(companies)
    rss_matched  = scrape_general_rss(companies)
    all_matched  = merge(gn_matched, rss_matched)

    total_articles = sum(len(v) for v in all_matched.values())
    print(f"\n  Total matched (before dedup): {total_articles}")

    if not all_matched:
        print("WARNING: No matching articles in past 48 hours.")
        log('No Articles', 0); return

    print("\n── Categorising & Formatting ─────────────────────────")
    cats = categorise(all_matched)
    body, total = format_email(cats)
    print(f"✓ {total} articles after deduplication")

    print("\n── Sending Email ─────────────────────────────────────")
    if send_email(body, recipients):
        log('Success', total)
        print(f"\n✓ SUCCESS — {total} articles dispatched")
    else:
        log('Failed', 0)
        print("\n✗ FAILED — email not sent")
    print("="*55+"\n")

if __name__ == '__main__':
    main()
