#!/usr/bin/env python3
"""
JJFO News Bulletin - PRODUCTION SCRAPER v8
- Google News RSS per company (India geo-filtered)
- Real article URLs via googlenewsdecoder
- Proper noun matching (avoids 'loco' matching 'Loco' company)
- India-context filtering for ambiguous names
- 48-hour lookback, dedup, prioritised email
"""

import os, csv, re, time, urllib.parse, requests, feedparser
from datetime import datetime, timezone
from collections import defaultdict
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from googlenewsdecoder import new_decoderv1

# ── Config ────────────────────────────────────────────────────────────────────
RESEND_API_KEY = os.getenv('RESEND_API_KEY', '')
if not RESEND_API_KEY:
    print("ERROR: RESEND_API_KEY not set"); exit(1)
print(f"✓ API Key: {RESEND_API_KEY[:12]}...")

COMPANIES_FILE  = 'companies.csv'
RECIPIENTS_FILE = 'recipients.csv'
DISPATCH_LOG    = 'dispatch_log.csv'

# Supplementary Indian startup RSS feeds
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

INDIAN_DOMAINS = {
    'inc42.com', 'yourstory.com', 'entrackr.com', 'vccircle.com',
    'startuptalky.com', 'the-ken.com', 'techcircle.vccircle.com',
    'analyticsindiamag.com', 'bwdisrupt.com', 'economictimes.indiatimes.com',
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
    r'\bmumbai\b|\bdelhi\b|\bbangalore\b|\bchennai\b|\bhyderabad\b|\bpune\b|'
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

# ── Helpers ───────────────────────────────────────────────────────────────────
# Words that, when immediately following the company name, indicate it is NOT the startup
FALSE_CONTEXT = re.compile(
    r'\b(wireless|railway|locomotive|loco pilot|control system|traction|diesel|electric loco|'
    r'shed|depot|division|zone|section|signal|track|coach|wagon|bogie|pantograph|'
    r'overhead|catenary|traction motor|brake|coupler|buffer)\b',
    re.I
)

def is_proper_noun(headline, company_name):
    """
    Match company name as a proper noun (capitalized) in headline.
    Also checks that the 10 words after the match don't contain railway/technical context
    that would indicate it's not the startup.
    """
    if len(company_name) < 4:
        return False
    pattern = r'\b' + re.escape(company_name) + r'\b'
    for m in re.finditer(pattern, headline, re.IGNORECASE):
        matched_text = headline[m.start():m.end()]
        if not matched_text[0].isupper():
            continue
        # Check surrounding context (20 chars before + 60 chars after)
        context = headline[max(0, m.start()-20):m.end()+60]
        if FALSE_CONTEXT.search(context):
            continue
        return True
    return False

def within_48h(pub_str):
    if not pub_str:
        return True
    try:
        pub = parsedate_to_datetime(pub_str)
        now = datetime.now(timezone.utc)
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        return (now - pub).total_seconds() <= 172800
    except:
        return True

def is_garbage(headline, url=''):
    return (bool(GARBAGE.search(headline)) or
            'brandstories' in url or
            'javascript' in url.lower() or
            len(headline.strip()) < 15)

def is_india_relevant(company_name, headline, source_href=''):
    if company_name not in AMBIGUOUS_NAMES:
        return True
    for domain in INDIAN_DOMAINS:
        if domain in source_href:
            return True
    return bool(INDIA_KW.search(headline))

def decode_url(gn_url):
    """Decode Google News redirect URL to real article URL."""
    if 'news.google.com' not in gn_url:
        return gn_url
    try:
        result = new_decoderv1(gn_url)
        if result and result.get('status'):
            return result['decoded_url']
    except:
        pass
    return gn_url

def clean_title(title):
    """Remove ' - Source Name' suffix from Google News titles."""
    return re.sub(r'\s*[-—]\s*[A-Z][^-—]{2,40}$', '', title).strip()

def google_news_url(query):
    q = urllib.parse.quote(query + ' India')
    return f'https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en'

# ── Data Loading ──────────────────────────────────────────────────────────────
def load_companies():
    companies = {}
    try:
        with open(COMPANIES_FILE, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            next(reader)
            for row in reader:
                if not row or not row[0].strip():
                    continue
                name = row[0].strip()
                sector = row[1].strip() if len(row) > 1 else ''
                competitors = [c.strip() for c in row[2:]
                               if c.strip() and c.strip() not in ('-', 'N/A', '')]
                companies[name] = {'sector': sector, 'competitors': competitors}
    except FileNotFoundError:
        print(f"ERROR: {COMPANIES_FILE} not found")
    return companies

def load_recipients():
    recipients = []
    try:
        with open(RECIPIENTS_FILE, 'r', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                e = row.get('Email', '').strip()
                if e:
                    recipients.append(e)
    except FileNotFoundError:
        print(f"ERROR: {RECIPIENTS_FILE} not found")
    return recipients

# ── Scraping ──────────────────────────────────────────────────────────────────
def scrape_google_news(companies):
    matched = defaultdict(list)

    print("\n  [Google News — Portfolio Companies]")
    for name, data in companies.items():
        try:
            feed = feedparser.parse(google_news_url(name))
            found = 0
            for entry in feed.entries[:10]:
                headline = entry.get('title', '').strip()
                pub      = entry.get('published', '')
                source   = entry.get('source', {})
                src_href = source.get('href', '')
                src_name = source.get('title', '')

                if not headline: continue
                if is_garbage(headline): continue
                if not within_48h(pub): continue
                if not is_proper_noun(headline, name): continue
                if not is_india_relevant(name, headline, src_href): continue

                real_url = decode_url(entry.get('link', ''))
                display  = clean_title(headline)
                time.sleep(0.2)

                matched[name].append({
                    'headline': display, 'url': real_url,
                    'source': src_name, 'is_comp': False,
                    'competitor': None, 'portfolio': name,
                })
                found += 1
            if found:
                print(f"    {name}: {found} articles")
            time.sleep(0.3)
        except Exception as e:
            print(f"    {name}: ERROR {str(e)[:50]}")

    print("\n  [Google News — Competitors]")
    for name, data in companies.items():
        for comp in data['competitors']:
            if len(comp) < 4:
                continue
            try:
                feed = feedparser.parse(google_news_url(comp))
                for entry in feed.entries[:6]:
                    headline = entry.get('title', '').strip()
                    pub      = entry.get('published', '')
                    source   = entry.get('source', {})
                    src_href = source.get('href', '')
                    src_name = source.get('title', '')

                    if not headline: continue
                    if is_garbage(headline): continue
                    if not within_48h(pub): continue
                    if not is_proper_noun(headline, comp): continue
                    if not is_india_relevant(comp, headline, src_href): continue

                    real_url = decode_url(entry.get('link', ''))
                    display  = clean_title(headline)
                    time.sleep(0.2)

                    matched[name].append({
                        'headline': display, 'url': real_url,
                        'source': src_name, 'is_comp': True,
                        'competitor': comp, 'portfolio': name,
                    })
                time.sleep(0.3)
            except:
                pass

    return matched

def scrape_general_rss(companies):
    matched = defaultdict(list)
    print("\n  [General RSS Feeds]")

    for source, url in GENERAL_RSS.items():
        try:
            feed = feedparser.parse(url)
            found = 0
            for entry in feed.entries[:80]:
                headline = entry.get('title', '').strip()
                link     = entry.get('link', '').strip()
                pub      = entry.get('published', '')

                if not headline or not link: continue
                if is_garbage(headline, link): continue
                if not within_48h(pub): continue

                matched_any = False
                for name in companies:
                    if is_proper_noun(headline, name):
                        if not is_india_relevant(name, headline, link):
                            continue
                        matched[name].append({
                            'headline': headline, 'url': link,
                            'source': source, 'is_comp': False,
                            'competitor': None, 'portfolio': name,
                        })
                        found += 1
                        matched_any = True
                        break

                if not matched_any:
                    for name, data in companies.items():
                        for comp in data['competitors']:
                            if len(comp) < 4:
                                continue
                            if is_proper_noun(headline, comp):
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
            print(f"    {source}: ERROR {str(e)[:50]}")

    return matched

def merge(a, b):
    merged = defaultdict(list)
    for d in (a, b):
        for k, v in d.items():
            merged[k].extend(v)
    return merged

# ── Categorise & Dedup ────────────────────────────────────────────────────────
FUNDING_KW = re.compile(
    r'\bfund\b|rais(es|ed|ing)\b|series [a-z]\b|invest(ment|or|ed)?\b|'
    r'seed round|crore|million|\$\d|capital round|pre-series|valuation',
    re.I
)
MA_KW  = re.compile(r'\bacquir|\bmerger\b|\bipo\b|\bbuyout\b|\bexit\b|\btakeover\b|\bacquisition\b', re.I)
REG_KW = re.compile(r'\bregulat|\brbi\b|\bsebi\b|\bpolicy\b|\bban\b|\blegal\b|\bcompliance\b|\bgovernment\b|\bministry\b', re.I)

def categorise(matched):
    cats = defaultdict(list)
    # Cap each portfolio company at 3 articles total across all categories
    company_count = defaultdict(int)
    for articles in matched.values():
        for a in articles:
            key = a['portfolio']
            if company_count[key] >= 3:
                continue
            h = a['headline']
            if   FUNDING_KW.search(h): cats['comp_funding' if a['is_comp'] else 'port_funding'].append(a)
            elif MA_KW.search(h):      cats['comp_ma'      if a['is_comp'] else 'port_ma'].append(a)
            elif REG_KW.search(h):     cats['sector'].append(a)
            else:                      cats['other'].append(a)
            company_count[key] += 1
    return cats

def dedup(articles):
    seen, unique = [], []
    for a in articles:
        h = a['headline'].lower()
        if not any(SequenceMatcher(None, h, s.lower()).ratio() > 0.82 for s in seen):
            unique.append(a)
            seen.append(a['headline'])
    return unique

# ── Format Email ──────────────────────────────────────────────────────────────
def format_email_html(cats):
    """Build a fully HTML email where each headline is a clickable hyperlink."""
    today = datetime.now().strftime('%B %d, %Y')
    sections = [
        ('FUNDING — PORTFOLIO COMPANIES', 'port_funding'),
        ('FUNDING — COMPETITORS',         'comp_funding'),
        ('M&A — PORTFOLIO COMPANIES',     'port_ma'),
        ('M&A — COMPETITORS',             'comp_ma'),
        ('REGULATORY / SECTOR NEWS',      'sector'),
        ('OTHER NEWS',                    'other'),
    ]

    body_parts = []
    total = 0
    for title, key in sections:
        articles = dedup(cats.get(key, []))
        if not articles:
            continue
        body_parts.append(
            f"<h3 style='margin:28px 0 8px;color:#1a1a2e;font-size:13px;"
            f"letter-spacing:1px;text-transform:uppercase;border-bottom:2px solid #e8e8e8;"
            f"padding-bottom:6px;'>{title}</h3>"
            f"<ul style='margin:0;padding-left:18px;'>"
        )
        for a in articles[:12]:
            label = (f"<span style='color:#888;font-size:12px;'>"
                     f"{a['portfolio']} &larr; {a['competitor']}</span>"
                     if a['is_comp']
                     else f"<span style='color:#888;font-size:12px;'>{a['portfolio']}</span>")
            safe_url  = a['url'].replace('&', '&amp;')
            safe_head = a['headline'].replace('<', '&lt;').replace('>', '&gt;')
            body_parts.append(
                f"<li style='margin-bottom:10px;'>"
                f"{label} "
                f"<a href='{safe_url}' style='color:#0057b7;text-decoration:none;font-weight:500;'"
                f" target='_blank'>{safe_head}</a>"
                f"</li>"
            )
            total += 1
        body_parts.append("</ul>")

    sections_html = "\n".join(body_parts)

    html = f"""
<html>
<body style='margin:0;padding:0;background:#f0f2f5;'>
<div style='max-width:680px;margin:30px auto;background:#ffffff;
            border-radius:10px;overflow:hidden;
            font-family:Arial,Helvetica,sans-serif;
            box-shadow:0 2px 8px rgba(0,0,0,0.08);'>

  <!-- Header -->
  <div style='background:#1a1a2e;padding:28px 32px;'>
    <div style='font-size:22px;font-weight:700;color:#ffffff;letter-spacing:0.5px;'>
      JJFO News Bulletin
    </div>
    <div style='font-size:13px;color:#a0a8c0;margin-top:4px;'>{today}</div>
  </div>

  <!-- Intro -->
  <div style='padding:20px 32px 8px;font-size:14px;color:#444;line-height:1.7;
              border-bottom:1px solid #f0f0f0;'>
    Your daily curated intelligence from India&rsquo;s most promising startups.
    Stay ahead with the latest funding, M&amp;A, and sector developments.
  </div>

  <!-- Articles -->
  <div style='padding:16px 32px 24px;font-size:14px;color:#333;line-height:1.8;'>
    {sections_html}
  </div>

  <!-- Footer -->
  <div style='background:#f8f8f8;padding:16px 32px;font-size:12px;color:#999;
              border-top:1px solid #e8e8e8;text-align:center;'>
    JJFO Intelligence Engine &bull; {today} &bull; {total} articles
  </div>

</div>
</body>
</html>"""
    return html, total

# ── Send Email ────────────────────────────────────────────────────────────────
def send_email(html, recipients):
    success = True
    for to in recipients:
        r = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "from":    "JJFO News <noreply@jjfo.in>",
                "to":      to,
                "subject": f"JJFO News Bulletin \u2014 {datetime.now().strftime('%B %d, %Y')}",
                "html":    html
            },
            timeout=15
        )
        if r.status_code == 200:
            print(f"  ✓ Sent to {to}")
        else:
            print(f"  ✗ Failed ({r.status_code}): {r.text[:120]}")
            success = False
    return success

def log_dispatch(status, count):
    try:
        with open(DISPATCH_LOG, 'a', newline='') as f:
            csv.writer(f).writerow([datetime.now().isoformat(), status, count])
    except:
        pass

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("\n" + "="*55)
    print("JJFO NEWS BULLETIN v8 — PRODUCTION SCRAPER")
    print("="*55)
    print(f"Run: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("Lookback: 48 hours | India-filtered | Real URLs\n")

    companies  = load_companies()
    recipients = load_recipients()

    if not companies:  print("ABORT: No companies loaded");  return
    if not recipients: print("ABORT: No recipients loaded"); return

    print(f"✓ {len(companies)} portfolio companies")
    print(f"✓ {len(recipients)} recipient(s)")

    print("\n── Scraping ──────────────────────────────────────────")
    gn_matched  = scrape_google_news(companies)
    rss_matched = scrape_general_rss(companies)
    all_matched = merge(gn_matched, rss_matched)

    total_raw = sum(len(v) for v in all_matched.values())
    print(f"\n  Total matched (before dedup): {total_raw}")

    if not all_matched:
        print("WARNING: No articles found in past 48 hours")
        log_dispatch('No Articles', 0)
        return

    print("\n── Categorising & Formatting ─────────────────────────")
    cats = categorise(all_matched)
    html, total = format_email_html(cats)
    print(f"✓ {total} articles after deduplication")

    print("\n── Sending Email ─────────────────────────────────────")
    if send_email(html, recipients):
        log_dispatch('Success', total)
        print(f"\n✓ SUCCESS — {total} articles dispatched")
    else:
        log_dispatch('Failed', 0)
        print("\n✗ FAILED — email not sent")

    print("="*55 + "\n")

if __name__ == '__main__':
    main()
