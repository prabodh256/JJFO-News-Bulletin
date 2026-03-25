#!/usr/bin/env python3
"""
JJFO News Bulletin - Real-time Web Scraper
"""

import os
import csv
import requests
import feedparser
from datetime import datetime
from collections import defaultdict
from difflib import SequenceMatcher
from bs4 import BeautifulSoup
from resend import Resend
import time

# Configuration
RESEND_API_KEY = os.getenv('RESEND_API_KEY', '')
if not RESEND_API_KEY:
    print("ERROR: RESEND_API_KEY environment variable not set!")
    print("Available env vars:", list(os.environ.keys()))
    exit(1)

print(f"Using API Key: {RESEND_API_KEY[:10]}...")

try:
    resend = Resend(api_key=RESEND_API_KEY)
except Exception as e:
    print(f"ERROR initializing Resend: {e}")
    exit(1)

RECIPIENT_EMAIL = 'investment@jjfo.com'
COMPANIES_FILE = 'companies.csv'
RECIPIENTS_FILE = 'recipients.csv'
DISPATCH_LOG_FILE = 'dispatch_log.csv'

#!/usr/bin/env python3
"""
JJFO News Bulletin - Real-time Web Scraper
Scrapes from 15+ news sources (no API delays)
CSV-based storage (completely free)
"""

import os
import csv
import requests
import feedparser
from datetime import datetime, timedelta
from collections import defaultdict
from difflib import SequenceMatcher
from bs4 import BeautifulSoup
from resend import Resend
import time

# Configuration
RESEND_API_KEY = os.getenv('RESEND_API_KEY')
RECIPIENT_EMAIL = 'investment@jjfo.com'
COMPANIES_FILE = 'companies.csv'
RECIPIENTS_FILE = 'recipients.csv'
DISPATCH_LOG_FILE = 'dispatch_log.csv'

resend = Resend(RESEND_API_KEY)

# RSS Feed Sources (Real-time)
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

# Direct Scraping Sources (for sources without RSS feeds)
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
                name = row.get('Company Name', '').strip()
                if name:
                    companies[name] = {
                        'sector': row.get('Sector', ''),
                        'competitors': [c.strip() for c in row.get('Competitors', '').split(',') if c.strip()]
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
        recipients = [RECIPIENT_EMAIL]
    return recipients if recipients else [RECIPIENT_EMAIL]

def scrape_rss_feeds(companies):
    """Scrape RSS feeds from all sources"""
    news_items = defaultdict(list)
    
    for source_name, feed_url in RSS_FEEDS.items():
        try:
            print(f"Scraping {source_name}...")
            feed = feedparser.parse(feed_url)
            
            for entry in feed.entries[:30]:  # Get last 30 articles per source
                headline = entry.get('title', '')
                link = entry.get('link', '')
                
                if not headline or not link:
                    continue
                
                # Check if article mentions any company or competitor
                for company_name in companies.keys():
                    if company_name.lower() in headline.lower():
                        news_items[company_name].append({
                            'headline': headline,
                            'url': link,
                            'source': source_name,
                            'published': entry.get('published', datetime.now().isoformat()),
                        })
                        break
                
                # Also check competitors
                for company_name, data in companies.items():
                    for competitor in data.get('competitors', []):
                        if competitor.lower() in headline.lower() and company_name not in news_items:
                            news_items[company_name].append({
                                'headline': headline,
                                'url': link,
                                'source': source_name,
                                'published': entry.get('published', datetime.now().isoformat()),
                                'is_competitor': True
                            })
                            break
        except Exception as e:
            print(f"Error scraping {source_name}: {e}")
        
        time.sleep(1)  # Rate limiting
    
    return news_items

def scrape_direct_sources(companies):
    """Scrape news directly from sources without RSS feeds"""
    news_items = defaultdict(list)
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    
    for source_name, source_url in SOURCES.items():
        try:
            print(f"Scraping {source_name}...")
            response = requests.get(source_url, headers=headers, timeout=10)
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Find all article links
            articles = soup.find_all('a', limit=50)
            
            for article in articles:
                headline = article.get_text(strip=True)
                link = article.get('href', '')
                
                if not headline or not link or len(headline) < 10:
                    continue
                
                # Make relative URLs absolute
                if link.startswith('/'):
                    link = source_url.rstrip('/') + link
                elif not link.startswith('http'):
                    link = source_url.rstrip('/') + '/' + link
                
                # Check if article mentions any company
                for company_name in companies.keys():
                    if company_name.lower() in headline.lower():
                        news_items[company_name].append({
                            'headline': headline,
                            'url': link,
                            'source': source_name,
                            'published': datetime.now().isoformat(),
                        })
                        break
        except Exception as e:
            print(f"Error scraping {source_name}: {e}")
        
        time.sleep(2)  # Rate limiting
    
    return news_items

def merge_news(rss_news, direct_news):
    """Merge news from all sources"""
    merged = defaultdict(list)
    for company, articles in rss_news.items():
        merged[company].extend(articles)
    for company, articles in direct_news.items():
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
        'funding': ['funding', 'raised', 'series', 'investment', 'seed', 'round', 'capital', 'venture', 'investor'],
        'ma': ['acquisition', 'acquired', 'merger', 'merged', 'buyout', 'ipo', 'exit', 'acquires'],
        'regulatory': ['regulation', 'compliance', 'policy', 'rbi', 'sebi', 'ban', 'law', 'legal']
    }
    
    for company_name, news_list in news_items.items():
        is_portfolio = company_name in companies
        
        for news in news_list:
            headline_lower = news['headline'].lower()
            is_competitor = news.get('is_competitor', False)
            
            if any(kw in headline_lower for kw in keywords['funding']):
                if is_portfolio and not is_competitor:
                    categories['portfolio_funding'].append(news)
                elif is_competitor or not is_portfolio:
                    categories['competitor_funding'].append(news)
            elif any(kw in headline_lower for kw in keywords['ma']):
                if is_portfolio and not is_competitor:
                    categories['portfolio_ma'].append(news)
                elif is_competitor or not is_portfolio:
                    categories['competitor_ma'].append(news)
            elif any(kw in headline_lower for kw in keywords['regulatory']):
                categories['sector_news'].append(news)
            else:
                categories['other'].append(news)
    
    return categories

def deduplicate_news(news_list, threshold=0.75):
    """Remove duplicates using headline similarity"""
    unique = []
    seen = []
    
    for news in news_list:
        is_dup = False
        for seen_headline in seen:
            sim = SequenceMatcher(None, news['headline'].lower(), seen_headline.lower()).ratio()
            if sim > threshold:
                is_dup = True
                break
        
        if not is_dup:
            unique.append(news)
            seen.append(news['headline'])
    
    return unique

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
            body += f"\n{title}\n" + "-" * 40 + "\n"
            for item in news[:5]:
                body += f"• {item['headline']}\n  {item['url']}\n\n"
                total_articles += 1
    
    return body, total_articles

def send_email(body, recipients):
    """Send email via Resend"""
    try:
        for recipient in recipients:
            response = resend.emails.send(
                {
                    "from": "JJFO News <noreply@jjfo.in>",
                    "to": recipient,
                    "subject": f"JJFO News Bulletin - {datetime.now().strftime('%B %d, %Y')}",
                    "html": f"<pre>{body}</pre>"
                }
            )
            print(f"Email sent to {recipient}: {response}")
        return True
    except Exception as e:
        print(f"Error sending email: {e}")
        import traceback
        traceback.print_exc()
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
        print("Starting JJFO News Bulletin...")
        
        companies = load_companies()
        recipients = load_recipients()
        
        print(f"Found {len(companies)} companies")
        print(f"Sending to {len(recipients)} recipients")
        print(f"Scraping from {len(RSS_FEEDS)} RSS feeds + {len(SOURCES)} direct sources")
        
        # Scrape all sources
        print("\n=== Scraping RSS Feeds ===")
        rss_news = scrape_rss_feeds(companies)
        
        print("\n=== Scraping Direct Sources ===")
        direct_news = scrape_direct_sources(companies)
        
        # Merge and process
        all_news = merge_news(rss_news, direct_news)
        total = sum(len(v) for v in all_news.values())
        print(f"\nTotal articles found: {total}")
        
        # Categorize and send
        print("\nCategorizing news...")
        categorized = categorize_news(all_news, companies)
        
        print("Formatting email...")
        body, article_count = format_email(categorized)
        
        print(f"Sending email to {len(recipients)} recipient(s)...")
        if send_email(body, recipients):
            log_dispatch('Success', article_count)
            print("✓ Email sent successfully")
        else:
            log_dispatch('Failed', 0)
            print("✗ Email failed")
    
    except Exception as e:
        print(f"Fatal error: {e}")
        log_dispatch('Error', 0)

if __name__ == '__main__':
    main()
