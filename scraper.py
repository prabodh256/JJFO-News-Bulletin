#!/usr/bin/env python3
"""
JJFO News Bulletin - PRODUCTION SCRAPER v10.0 (Sniper & Radar Architecture)
- Start 1:30 AM GMT, Human-like delays (2.0-4.0s)
- Sniper Engine: site: filtered searches for exact matches
- Radar Engine: No-API sector-based discovery
- Hardened "Market Noise" and "Dated News" filters
"""

import os, csv, re, time, urllib.parse, requests, feedparser, random
from datetime import datetime, timezone
from collections import defaultdict
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from googlenewsdecoder import new_decoderv1

# ── Config ────────────────────────────────────────────────────────────────────
RESEND_API_KEY = os.getenv('RESEND_API_KEY', '')
if not RESEND_API_KEY:
    print("ERROR: RESEND_API_KEY not set"); exit(1)

COMPANIES_FILE  = 'companies.csv'
RECIPIENTS_FILE = 'recipients.csv'
DISPATCH_LOG    = 'dispatch_log.csv'

# Strictly curated startup publications for site: operator
TRUSTED_SITES = [
    "site:inc42.com", "site:yourstory.com", "site:entrackr.com", 
    "site:vccircle.com", "site:the-ken.com", "site:startuptalky.com"
]
SITE_QUERY = " OR ".join(TRUSTED_SITES)

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

AMBIGUOUS_NAMES = {
    'Arata', 'Terra', 'Grip', 'Loco', 'Wise', 'Simpl', 'Porter',
    'Orange Credit', 'Right Health', 'August Jewellery', 'White Owl',
    'Sadhan', 'Rooter', 'WorkIndia',
    'GRIP', 'SK Finance', 'AU SFB', 'PB Fintech', 'Zipsure', 'Zouk',
}

INDIA_KW = re.compile(
    r'\bindia\b|\bindian\b|\bstartup\b|\bcrore\b|\binr\b|\brupee\b|'
    r'\bmumbai\b|\bdelhi\b|\bbangalore\b|\bchennai\b|\bhyderabad\b|\bpune\b|'
    r'\bnse\b|\bbse\b|\bsebi\b|\brbi\b',
    re.IGNORECASE
)

GARBAGE = re.compile(
    r'cookie|privacy policy|javascript:|void\(|brand stor|sponsored|' \
    r'subscribe|newsletter|gold price|silver price|biography|personal finance|' \
    r'horoscope|astrology|recipe|cricket score|ipl|bollywood|mastering multi|' \
    r'oracle mastering|digital banking platform journey|robbed|video goes viral|' \
    r'regime change|protest|sea lanes|indian ocean|naval|deployment|' \
    r'stock to buy|buy target|upside target|technical breakout|top gainers|' \
    r'top losers|bulk deal|block deal|shares to be in focus|share price|' \
    r'stocks to watch|brokerage prefers|sell call|buy call|technical analysis|' \
    r'dividend|bonus issue|rights issue|corporate actions|market update|' \
    r'sensex|nifty|market opens|market closes|target of rs|target price|recommendation|analyst view|buy vikram solar|buy |' \
    r'stocks advance|holiday-curtailed|checks in at|airport|spotted at'
    , re.I
)

FALSE_CONTEXT = re.compile(r'railway|train|rail|wireless control|traction|locomotive|shed|depot|wagons|bhel|concord control|indian railways', re.I)
COMPANY_BLOCKLIST = {
    'Loco': re.compile(r'railway|train|rail|wireless control|traction|locomotive|shed|depot|wagons|bhel|concord control|indian railways', re.I)
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def is_proper_noun(headline, company_name):
    if len(company_name) < 4: return False
    if company_name in COMPANY_BLOCKLIST and COMPANY_BLOCKLIST[company_name].search(headline):
        return False
    
    # Ensure the company name is not just part of a list or a generic mention
    # e.g., "Ola Electric Top Gainer" -> "Top Gainer" is a red flag, but we catch that in GARBAGE.
    # We want to ensure the company is the subject.
    
    pattern = r'\b' + re.escape(company_name) + r'\b'
    for m in re.finditer(pattern, headline, re.IGNORECASE):
        matched_text = headline[m.start():m.end()]
        if not matched_text[0].isupper(): continue
        context = headline[max(0, m.start()-30):m.end()+80]
        if FALSE_CONTEXT.search(context): continue
        
        # Check if the company name is immediately followed by market noise terms
        noise_context = headline[m.end():m.end()+30]
        if re.search(r'\btop gainer\b|\btop loser\b|\bstock\b|\bshare\b', noise_context, re.I):
            continue
            
        return True
    return False

def within_48h(pub_str):
    if not pub_str: return False
    try:
        pub = parsedate_to_datetime(pub_str)
        now = datetime.now(timezone.utc)
        if pub.tzinfo is None: pub = pub.replace(tzinfo=timezone.utc)
        diff = (now - pub).total_seconds()
        if diff < 0: return True
        return diff <= 172800
    except: return False

def is_garbage(headline, url=''):
    return (bool(GARBAGE.search(headline)) or 'brandstories' in url or 'javascript' in url.lower() or len(headline.strip()) < 15)

def is_india_relevant(company_name, headline, source_href=''):
    if company_name not in AMBIGUOUS_NAMES: return True
    for domain in INDIAN_DOMAINS:
        if domain in source_href: return True
    return bool(INDIA_KW.search(headline))

def decode_url(gn_url):
    if 'news.google.com' not in gn_url: return gn_url
    try:
        result = new_decoderv1(gn_url)
        if result and result.get('status'): return result['decoded_url']
    except: pass
    return gn_url

def clean_title(title):
    return re.sub(r'\s*[-—]\s*[A-Z][^-—]{2,40}$', '', title).strip()

def sniper_url(query):
    strict_query = f'"{query}" ({SITE_QUERY}) when:2d'
    q = urllib.parse.quote(strict_query)
    return f'https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en'

def radar_url(sector_name):
    query = f'("{sector_name}" OR "{sector_name} startup") ({SITE_QUERY}) when:2d'
    q = urllib.parse.quote(query)
    return f'https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en'

def load_companies():
    companies = {}
    try:
        with open(COMPANIES_FILE, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            next(reader)
            for row in reader:
                if not row or not row[0].strip(): continue
                name = row[0].strip()
                sector = row[1].strip() if len(row) > 1 else ''
                competitors = [c.strip() for c in row[2:] if c.strip() and c.strip() not in ('-', 'N/A', '')]
                companies[name] = {'sector': sector, 'competitors': competitors}
    except FileNotFoundError: print(f"ERROR: {COMPANIES_FILE} not found")
    return companies

def load_recipients():
    recipients = []
    try:
        with open(RECIPIENTS_FILE, 'r', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                e = row.get('Email', '').strip()
                if e: recipients.append(e)
    except FileNotFoundError: print(f"ERROR: {RECIPIENTS_FILE} not found")
    return recipients

# ── Scraping Engines ──────────────────────────────────────────────────────────

def run_sniper(companies):
    matched = defaultdict(list)
    print("\n  [The Sniper — Exact Matches]")
    for name, data in companies.items():
        try:
            feed = feedparser.parse(sniper_url(name))
            found = 0
            for entry in feed.entries[:10]:
                headline = entry.get('title', '').strip()
                pub, source = entry.get('published', ''), entry.get('source', {})
                src_href, src_name = source.get('href', ''), source.get('title', '')
                if not headline or is_garbage(headline) or not within_48h(pub) or not is_proper_noun(headline, name) or not is_india_relevant(name, headline, src_href): continue
                real_url = decode_url(entry.get('link', ''))
                display = clean_title(headline)
                time.sleep(random.uniform(2.0, 4.0))
                matched[name].append({'headline': display, 'url': real_url, 'source': src_name, 'is_comp': False, 'competitor': None, 'portfolio': name})
                found += 1
            if found: print(f"    {name}: {found} articles")
            time.sleep(random.uniform(2.0, 4.0))
        except Exception as e: print(f"    {name}: ERROR {str(e)[:50]}")
    
    for name, data in companies.items():
        for comp in data['competitors']:
            if len(comp) < 4: continue
            try:
                feed = feedparser.parse(sniper_url(comp))
                found = 0
                for entry in feed.entries[:6]:
                    headline = entry.get('title', '').strip()
                    pub, source = entry.get('published', ''), entry.get('source', {})
                    src_href, src_name = source.get('href', ''), source.get('title', '')
                    if not headline or is_garbage(headline) or not within_48h(pub) or not is_proper_noun(headline, comp) or not is_india_relevant(comp, headline, src_href): continue
                    real_url = decode_url(entry.get('link', ''))
                    display = clean_title(headline)
                    time.sleep(random.uniform(2.0, 4.0))
                    matched[name].append({'headline': display, 'url': real_url, 'source': src_name, 'is_comp': True, 'competitor': comp, 'portfolio': name})
                    found += 1
                if found: print(f"    {comp}: {found} articles")
                time.sleep(random.uniform(2.0, 4.0))
            except Exception as e: print(f"    {comp}: ERROR {str(e)[:50]}")
    return matched

def run_radar(companies):
    radar_intel = defaultdict(list)
    print("\n  [The Radar — Sector Discovery]")
    unique_sectors = set(data['sector'] for data in companies.values() if data['sector'])
    
    for sector in unique_sectors:
        try:
            feed = feedparser.parse(radar_url(sector))
            found = 0
            for entry in feed.entries[:10]:
                headline = clean_title(entry.get('title', '').strip())
                pub, source = entry.get('published', ''), entry.get('source', {})
                src_href, src_name = source.get('href', ''), source.get('title', '')
                if not headline or is_garbage(headline) or not within_48h(pub): continue
                
                # Check if it's already a known company
                is_known = False
                for port_name, data in companies.items():
                    if is_proper_noun(headline, port_name) or any(is_proper_noun(headline, comp) for comp in data['competitors']):
                        is_known = True
                        break
                if is_known: continue
                
                real_url = decode_url(entry.get('link', ''))
                time.sleep(random.uniform(2.0, 4.0))
                radar_intel['sector_news'].append({'headline': headline, 'url': real_url, 'source': src_name, 'is_comp': False, 'competitor': f"Sector: {sector}", 'portfolio': sector})
                found += 1
            if found: print(f"    {sector}: {found} emerging news")
            time.sleep(random.uniform(2.0, 4.0))
        except Exception as e: print(f"    {sector}: ERROR {str(e)[:50]}")
    return radar_intel

def merge(a, b):
    merged = defaultdict(list)
    for d in (a, b):
        for k, v in d.items(): merged[k].extend(v)
    return merged

# ── Categorization & Formatting ───────────────────────────────────────────────

FUNDING_KW = re.compile(r'\bfund\b|rais(es|ed|ing)\b|series [a-z]\b|invest(ment|or|ed)?\b|seed round|crore|million|\$\d|capital round|pre-series|valuation|pre-seed|angel|debt funding|equity funding|strategic investment|venture capital|private equity|round|infusion|capital|growth|expansion', re.I)
MA_KW  = re.compile(r'\bacquir|\bmerger\b|\bipo\b|\bbuyout\b|\bexit\b|\btakeover\b|\bacquisition\b', re.I)
REG_KW = re.compile(r'\bregulat|\brbi\b|\bsebi\b|\bpolicy\b|\bban\b|\blegal\b|\bcompliance\b|\bgovernment\b|\bministry\b', re.I)

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

SOURCE_RANK = {
    'inc42': 1, 'yourstory': 1, 'entrackr': 1, 'vccircle': 1, 'techcrunch': 1, 'startuptalky': 1,
    'economic times': 2, 'economictimes': 2, 'et': 2, 'mint': 2, 'livemint': 2, 'business standard': 2, 'financial express': 2, 'businessline': 2, 'reuters': 2, 'bloomberg': 2,
    'the ken': 3, 'the morning context': 3, 'the cap table': 3,
    'ndtv': 4, 'hindustan times': 4, 'times of india': 4, 'moneycontrol': 4, 'cnbctv18': 4, 'zeebiz': 4,
}

def source_rank(article):
    src = article.get('source', '').lower()
    for key, rank in SOURCE_RANK.items():
        if key in src: return rank
    return 9

def dedup(articles):
    seen, unique = [], []
    for a in sorted(articles, key=source_rank):
        h = a['headline'].lower()
        if not any(SequenceMatcher(None, h, s.lower()).ratio() > 0.82 for s in seen):
            unique.append(a); seen.append(a['headline'])
    return unique

def format_email_html(cats):
    today = datetime.now().strftime('%B %d, %Y')
    sections = [
        ('FUNDING — PORTFOLIO COMPANIES', 'port_funding'), 
        ('FUNDING — COMPETITORS', 'comp_funding'), 
        ('M&A — PORTFOLIO COMPANIES', 'port_ma'), 
        ('M&A — COMPETITORS', 'comp_ma'), 
        ('REGULATORY / SECTOR NEWS', 'sector'), 
        ('OTHER NEWS', 'other')
    ]
    body_parts, total, shown_headlines = [], 0, []
    for title, key in sections:
        articles = dedup(cats.get(key, []))
        if not articles: continue
        body_parts.append(f"<h3 style='margin:28px 0 8px;color:#1a1a2e;font-size:13px;letter-spacing:1px;text-transform:uppercase;border-bottom:2px solid #e8e8e8;padding-bottom:6px;'>{title}</h3><ul style='margin:0;padding-left:18px;'>")
        seen_companies = set()
        for a in articles:
            company_key = (a['portfolio'], a.get('competitor') or '')
            if company_key in seen_companies: continue
            h = a['headline'].lower()
            if any(SequenceMatcher(None, h, s).ratio() > 0.82 for s in shown_headlines): continue
            seen_companies.add(company_key); shown_headlines.append(h)
            
            if a['competitor'] and "Sector:" in a['competitor']:
                label = f"<span style='color:#b22222;font-size:12px;font-weight:bold;'>{a['competitor']}</span>"
            else:
                label = f"<span style='color:#888;font-size:12px;'>{a['portfolio']} &larr; {a['competitor']}</span>" if a['is_comp'] else f"<span style='color:#888;font-size:12px;'>{a['portfolio']}</span>"
            
            safe_url, safe_head = a['url'].replace('&', '&amp;'), a['headline'].replace('<', '&lt;').replace('>', '&gt;')
            body_parts.append(f"<li style='margin-bottom:10px;'>{label} <a href='{safe_url}' style='color:#0057b7;text-decoration:none;font-weight:500;' target='_blank'>{safe_head}</a></li>")
            total += 1
        body_parts.append("</ul>")
    
    sections_html = "\n".join(body_parts)
    html = f"""<html><body style='margin:0;padding:0;background:#f0f2f5;'><div style='max-width:680px;margin:30px auto;background:#ffffff;border-radius:10px;overflow:hidden;font-family:Arial,Helvetica,sans-serif;box-shadow:0 2px 8px rgba(0,0,0,0.08);'><div style='background:#1a1a2e;padding:28px 32px;'><div style='font-size:22px;font-weight:700;color:#ffffff;letter-spacing:0.5px;'>JJFO News Bulletin</div><div style='font-size:13px;color:#a0a8c0;margin-top:4px;'>{today}</div></div><div style='padding:20px 32px 8px;font-size:14px;color:#444;line-height:1.7;border-bottom:1px solid #f0f0f0;'>Your daily curated intelligence from India&rsquo;s most promising startups. Stay ahead with the latest funding, M&amp;A, and sector developments.</div><div style='padding:16px 32px 24px;font-size:14px;color:#333;line-height:1.8;'>{sections_html}</div><div style='background:#f8f8f8;padding:16px 32px;font-size:12px;color:#999;border-top:1px solid #e8e8e8;text-align:center;'>JJFO Intelligence Engine &bull; {today} &bull; {total} articles</div></div></body></html>"""
    return html, total

def send_email(html, recipients):
    success = True
    for to in recipients:
        r = requests.post("https://api.resend.com/emails", headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"}, json={"from": "Prabodh <prabodh@jjfo.in>", "to": to, "subject": f"JJFO News Bulletin \u2014 {datetime.now().strftime('%B %d, %Y')}", "html": html}, timeout=15)
        if r.status_code == 200: print(f"  ✓ Sent to {to}")
        else: print(f"  ✗ Failed ({r.status_code}): {r.text[:120]}"); success = False
    return success

def log_dispatch(status, count):
    try:
        with open(DISPATCH_LOG, 'a', newline='') as f: csv.writer(f).writerow([datetime.now().isoformat(), status, count])
    except: pass

def main():
    print(f"Run: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    companies, recipients = load_companies(), load_recipients()
    if not companies or not recipients: return
    
    sniper_data = run_sniper(companies)
    radar_data = run_radar(companies)
    all_matched = merge(sniper_data, radar_data)
    
    if not all_matched: log_dispatch('No Articles', 0); return
    cats = categorise(all_matched)
    html, total = format_email_html(cats)
    if send_email(html, recipients): log_dispatch('Success', total)
    else: log_dispatch('Failed', 0)

if __name__ == '__main__': main()
