#!/usr/bin/env python3
"""
JJFO News Bulletin - BALANCED Scraper
- Reads article body for better matching
- Smart matching: not too loose, not too strict
- Avoids false positives while catching real news
"""

import os
import csv
import requests
import feedparser
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from difflib import SequenceMatcher
from bs4 import BeautifulSoup
import time
from email.utils import parsedate_to_datetime

# Configuration
RESEND_API_KEY = os.getenv('RESEND_API_KEY', '')
COMPANIES_FILE = 'companies.csv'
RECIPIENTS_FILE = 'recipients.csv'
DISPATCH_LOG_FILE = 'dispatch_log.csv'

if not RESEND_API_KEY:
    print("ERROR: RESEND_API_KEY not set!")
    exit(1)

print(f"✓ Using Resend API Key: {RESEND_API_KEY[:20]}...")

# RSS Feed Sources
RSS_FEEDS = {
    'YourStory': 'https://yourstory.com/feed',
    'Inc42': 'https://inc42.com/feed/',
    'Entrackr': 'https://entrackr.com/feed/',
    'VCCircle': 'https://www.vccircle.com/feed/',
    'StartupTalky': 'https://www.startuptalky.com/feed/',
    'IndianWeb2': 'https://indianweb2.com/feed/',
    'TechCircle': 'https://techcircle.vccircle.com/feed/',
    'The Ken': 'https://the-ken.com/feed/',
    'Analytics India': 'https://analyticsindiamag.com/feed/',
    'BW Disrupt': 'https://www.bwdisrupt.com/feed/',
    'TechCrunch': 'https://techcrunch.com/feed/',
    'Economic Times': 'https://economictimes.indiatimes.com/rssfeedstopics.cms?feedtype=json&feed_id=1715249553',
}

# Direct Scraping Sources
SOURCES = {
    'Inc42 Plus': 'https://inc42plus.com/',
    'Moneycontrol Startups': 'https://www.moneycontrol.com/news/startups/',
    'Business Standard Startups': 'https://www.business-standard.com/startups',
    'Livemint Startups': 'https://www.livemint.com/startups',
}

def load_companies():
    """Load companies from CSV"""
    companies = {}
    try:
        with open(COMPANIES_FILE, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row.get('Portfolio Company', '').strip()
                if name:
                    companies[name] = {
                        'sector': row.get('Sector', ''),
                        'competitors': [c.strip() for c in row.get('Competitors', '').split(',') if c.strip() and c.strip() != '-' and c.strip() != 'N/A']
                    }
    except FileNotFoundError:
        print(f"Warning: {COMPANIES_FILE} not found")
    return companies

def load_recipients():
    """Load email recipients from CSV"""
    recipients = []
    try:
        with open(RECIPIENTS_FILE, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                email = row.get('Email', '').strip()
                if email:
                    recipients.append(email)
    except FileNotFoundError:
        print(f"Warning: {RECIPIENTS_FILE} not found")
    return recipients if recipients else []

def parse_date(date_str):
    """Parse date string to datetime object"""
    if not date_str:
        return None
    
    try:
        if 'T' in str(date_str):
            return datetime.fromisoformat(str(date_str).replace('Z', '+00:00'))
        return parsedate_to_datetime(str(date_str))
    except:
        return None

def is_within_48_hours(published_date_str):
    """Check if article is from past 48 hours"""
    if not published_date_str:
        return True
    
    try:
        published = parse_date(published_date_str)
        if not published:
            return True
        
        now = datetime.now(timezone.utc)
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        
        time_diff = now - published
        return time_diff.total_seconds() <= 48 * 3600
    except:
        return True

def fetch_article_body(url):
    """Fetch article body from URL"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Extract text from article body
        article_text = soup.get_text()
        return article_text[:1000]  # First 1000 chars
    except:
        return ""

def match_company_in_text(headline, body, company_name):
    """
    Match company in headline and body
    More flexible matching
    """
    text = (headline + " " + body).lower()
    company_lower = company_name.lower()
    
    # Direct match
    if company_lower in text:
        return True
    
    # Partial match (first word or significant part)
    words = company_lower.split()
    for word in words:
        if len(word) > 3 and word in text:
            return True
    
    return False

def match_competitor_in_text(headline, body, company_name, competitor_name):
    """
    Match competitor in text
    But avoid if competitor is the main subject
    """
    headline_lower = headline.lower()
    body_lower = body.lower()
    text = headline_lower + " " + body_lower
    competitor_lower = competitor_name.lower()
    
    # Skip if competitor is at start of headline (main subject)
    if headline_lower.startswith(competitor_lower):
        return False
    
    # Skip if competitor appears in first 5 words AND followed by action verb
    words = headline_lower.split()
    if competitor_lower in words[:5]:
        action_verbs = ['raises', 'raised', 'launches', 'acquires', 'announces', 'expands', 'partners']
        for verb in action_verbs:
            if verb in headline_lower[:100]:
                return False
    
    # Check if competitor is mentioned in text
    if competitor_lower in text:
        # Also check if portfolio company is mentioned (context)
        company_lower = company_name.lower()
        if company_lower in body_lower:
            return True
        
        # Or if it's in a sector/market context
        context_keywords = ['sector', 'market', 'industry', 'startup', 'space', 'ecosystem', 'compete', 'competitor']
        if any(kw in body_lower for kw in context_keywords):
            return True
    
    return False

def scrape_rss_feeds(companies):
    """Scrape RSS feeds - past 48 hours"""
    news_items = defaultdict(list)
    
    for source_name, feed_url in RSS_FEEDS.items():
        try:
            print(f"  Scraping {source_name}...")
            feed = feedparser.parse(feed_url)
            
            articles_found = 0
            for entry in feed.entries[:100]:
                headline = entry.get('title', '').strip()
                link = entry.get('link', '').strip()
                published = entry.get('published', '')
                
                if not headline or not link:
                    continue
                
                if not is_within_48_hours(published):
                    continue
                
                # Fetch article body
                body = fetch_article_body(link)
                
                articles_found += 1
                
                # Check portfolio companies
                matched = False
                for company_name in companies.keys():
                    if match_company_in_text(headline, body, company_name):
                        news_items[company_name].append({
                            'headline': headline,
                            'url': link,
                            'source': source_name,
                            'published': published,
                            'portfolio_company': company_name,
                            'is_competitor': False
                        })
                        matched = True
                        break
                
                # Check competitors (only if portfolio company not matched)
                if not matched:
                    for company_name, data in companies.items():
                        for competitor in data.get('competitors', []):
                            if match_competitor_in_text(headline, body, company_name, competitor):
                                news_items[company_name].append({
                                    'headline': headline,
                                    'url': link,
                                    'source': source_name,
                                    'published': published,
                                    'portfolio_company': company_name,
                                    'competitor_name': competitor,
                                    'is_competitor': True
                                })
                                matched = True
                                break
                        if matched:
                            break
                
                time.sleep(0.2)  # Be nice to servers
            
            print(f"    Found {articles_found} recent articles")
        except Exception as e:
            print(f"  Error scraping {source_name}: {str(e)[:50]}")
        
        time.sleep(0.5)
    
    return news_items

def scrape_direct_sources(companies):
    """Scrape direct sources - past 48 hours"""
    news_items = defaultdict(list)
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    
    for source_name, source_url in SOURCES.items():
        try:
            print(f"  Scraping {source_name}...")
            response = requests.get(source_url, headers=headers, timeout=15)
            soup = BeautifulSoup(response.content, 'html.parser')
            
            articles_found = 0
            articles = soup.find_all('a', limit=150)
            
            for article in articles:
                headline = article.get_text(strip=True)
                link = article.get('href', '').strip()
                
                if not headline or not link or len(headline) < 10:
                    continue
                
                # Skip broken links
                if 'javascript' in link.lower() or link.startswith('#'):
                    continue
                
                if link.startswith('/'):
                    link = source_url.rstrip('/') + link
                elif not link.startswith('http'):
                    link = source_url.rstrip('/') + '/' + link
                
                # Fetch article body
                body = fetch_article_body(link)
                
                articles_found += 1
                
                # Check portfolio companies
                matched = False
                for company_name in companies.keys():
                    if match_company_in_text(headline, body, company_name):
                        news_items[company_name].append({
                            'headline': headline,
                            'url': link,
                            'source': source_name,
                            'published': datetime.now().isoformat(),
                            'portfolio_company': company_name,
                            'is_competitor': False
                        })
                        matched = True
                        break
                
                # Check competitors (only if portfolio company not matched)
                if not matched:
                    for company_name, data in companies.items():
                        for competitor in data.get('competitors', []):
                            if match_competitor_in_text(headline, body, company_name, competitor):
                                news_items[company_name].append({
                                    'headline': headline,
                                    'url': link,
                                    'source': source_name,
                                    'published': datetime.now().isoformat(),
                                    'portfolio_company': company_name,
                                    'competitor_name': competitor,
                                    'is_competitor': True
                                })
                                matched = True
                                break
                        if matched:
                            break
                
                time.sleep(0.2)
            
            print(f"    Found {articles_found} articles")
        except Exception as e:
            print(f"  Error scraping {source_name}: {str(e)[:50]}")
        
        time.sleep(1)
    
    return news_items

def merge_news(*news_sources):
    """Merge news from all sources"""
    merged = defaultdict(list)
    for source in news_sources:
        for company, articles in source.items():
            merged[company].extend(articles)
    return merged

def categorize_news(news_items, companies):
    """Categorize news by priority"""
    categories = {
        'portfolio_funding': [],
        'competitor_funding': [],
        'portfolio_ma': [],
        'competitor_ma': [],
        'sector_news': [],
        'other': []
    }
    
    keywords = {
        'funding': ['funding', 'raised', 'series', 'investment', 'seed', 'round', 'capital', 'venture', 'investor', 'fund'],
        'ma': ['acquisition', 'acquired', 'merger', 'merged', 'buyout', 'ipo', 'exit', 'acquires'],
        'regulatory': ['regulation', 'compliance', 'policy', 'rbi', 'sebi', 'ban', 'law', 'legal']
    }
    
    for company_name, news_list in news_items.items():
        for news in news_list:
            headline_lower = news['headline'].lower()
            is_competitor = news.get('is_competitor', False)
            
            if any(kw in headline_lower for kw in keywords['funding']):
                if not is_competitor:
                    categories['portfolio_funding'].append(news)
                else:
                    categories['competitor_funding'].append(news)
            elif any(kw in headline_lower for kw in keywords['ma']):
                if not is_competitor:
                    categories['portfolio_ma'].append(news)
                else:
                    categories['competitor_ma'].append(news)
            elif any(kw in headline_lower for kw in keywords['regulatory']):
                categories['sector_news'].append(news)
            else:
                categories['other'].append(news)
    
    return categories

def deduplicate_news(news_list, threshold=0.85):
    """Remove duplicates using headline similarity"""
    unique = []
    seen = []
    
    for news in news_list:
        is_dup = False
        for seen_news in seen:
            sim = SequenceMatcher(None, news['headline'].lower(), seen_news['headline'].lower()).ratio()
            if sim > threshold:
                is_dup = True
                break
        
        if not is_dup:
            unique.append(news)
            seen.append(news)
    
    return unique

def format_headline_with_company(news):
    """Format headline with portfolio company name if competitor"""
    headline = news['headline']
    
    if news.get('is_competitor'):
        portfolio_company = news.get('portfolio_company', '')
        return f"[{portfolio_company}] {headline}"
    
    return headline

def format_email(categorized):
    """Format email body"""
    body = "Your daily curated news digest from India's most promising startups.\nStay ahead with the latest funding, M&A, and sector updates.\n\n"
    
    sections = [
        ('FUNDING - PORTFOLIO COMPANIES', 'portfolio_funding'),
        ('FUNDING - COMPETITORS', 'competitor_funding'),
        ('M&A - PORTFOLIO COMPANIES', 'portfolio_ma'),
        ('M&A - COMPETITORS', 'competitor_ma'),
        ('SECTOR NEWS', 'sector_news'),
        ('OTHER NEWS', 'other')
    ]
    
    total_articles = 0
    for title, key in sections:
        news = deduplicate_news(categorized[key])
        if news:
            body += f"\n{title}\n" + "-" * 50 + "\n"
            for item in news[:10]:
                formatted_headline = format_headline_with_company(item)
                body += f"• {formatted_headline}\n  {item['url']}\n\n"
                total_articles += 1
    
    return body, total_articles

def send_email_via_resend(body, recipients):
    """Send email via Resend API"""
    try:
        for recipient in recipients:
            url = "https://api.resend.com/emails"
            
            headers = {
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "from": "JJFO News <noreply@jjfo.in>",
                "to": recipient,
                "subject": f"JJFO News Bulletin - {datetime.now().strftime('%B %d, %Y')}",
                "html": f"<pre style='font-family: Arial, sans-serif; white-space: pre-wrap;'>{body}</pre>"
            }
            
            response = requests.post(url, json=payload, headers=headers)
            
            if response.status_code == 200:
                print(f"✓ Email sent to {recipient}")
            else:
                print(f"✗ Failed to send to {recipient}: {response.status_code}")
                return False
        
        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        return False

def log_dispatch(status, count):
    """Log to CSV"""
    try:
        with open(DISPATCH_LOG_FILE, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([datetime.now().isoformat(), status, count])
    except Exception as e:
        print(f"Error logging: {e}")

def main():
    try:
        print("\n" + "="*60)
        print("JJFO NEWS BULLETIN - BALANCED SCRAPER")
        print("="*60)
        print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Scraping news from past 48 hours...")
        
        companies = load_companies()
        recipients = load_recipients()
        
        if not recipients:
            print("ERROR: No recipients found in recipients.csv")
            exit(1)
        
        if not companies:
            print("ERROR: No companies found in companies.csv")
            exit(1)
        
        print(f"\n✓ Found {len(companies)} portfolio companies")
        print(f"✓ Sending to {len(recipients)} recipient(s)")
        
        # Scrape all sources
        print("\n--- Scraping RSS Feeds ---")
        rss_news = scrape_rss_feeds(companies)
        
        print("\n--- Scraping Direct Sources ---")
        direct_news = scrape_direct_sources(companies)
        
        # Merge all sources
        all_news = merge_news(rss_news, direct_news)
        total = sum(len(v) for v in all_news.values())
        print(f"\n✓ Total articles found: {total}")
        
        if total == 0:
            print("WARNING: No articles found in past 48 hours")
        
        # Categorize and send
        print("\nCategorizing news...")
        categorized = categorize_news(all_news, companies)
        
        print("Formatting email...")
        body, article_count = format_email(categorized)
        
        if article_count == 0:
            print("WARNING: No articles to send")
            log_dispatch('No Articles', 0)
        else:
            print(f"Sending email with {article_count} articles to {len(recipients)} recipient(s)...")
            if send_email_via_resend(body, recipients):
                log_dispatch('Success', article_count)
                print(f"\n✓ SUCCESS: Email sent with {article_count} articles")
            else:
                log_dispatch('Failed', 0)
                print("\n✗ FAILED: Email could not be sent")
        
        print("="*60 + "\n")
    
    except Exception as e:
        print(f"\n✗ FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        log_dispatch('Error', 0)

if __name__ == '__main__':
    main()
