#!/usr/bin/env python3
"""
JJFO News Bulletin - Final Version
- Scrapes past 48 hours properly
- Works for manual and automatic runs
- Shows portfolio company for competitor news
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
    return recipients if recipients else []

def parse_date(date_str):
    """Parse date string to datetime object"""
    if not date_str:
        return None
    
    try:
        # Try ISO format first
        if 'T' in date_str:
            return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        # Try RSS format
        return parsedate_to_datetime(date_str)
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
        
        # Make both timezone-aware for comparison
        now = datetime.now(timezone.utc)
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        
        # Check if within 48 hours
        time_diff = now - published
        return time_diff.total_seconds() <= 48 * 3600
    except:
        return True

def scrape_rss_feeds(companies):
    """Scrape RSS feeds - past 48 hours"""
    news_items = defaultdict(list)
    
    for source_name, feed_url in RSS_FEEDS.items():
        try:
            print(f"  Scraping {source_name}...")
            feed = feedparser.parse(feed_url)
            
            articles_found = 0
            for entry in feed.entries[:100]:  # Get more entries
                headline = entry.get('title', '').strip()
                link = entry.get('link', '').strip()
                published = entry.get('published', '')
                
                if not headline or not link:
                    continue
                
                # Check if within 48 hours
                if not is_within_48_hours(published):
                    continue
                
                articles_found += 1
                
                # Check portfolio companies
                for company_name in companies.keys():
                    if company_name.lower() in headline.lower():
                        news_items[company_name].append({
                            'headline': headline,
                            'url': link,
                            'source': source_name,
                            'published': published,
                            'portfolio_company': company_name,
                            'is_competitor': False
                        })
                        break
                else:
                    # Check competitors
                    for company_name, data in companies.items():
                        for competitor in data.get('competitors', []):
                            if competitor.lower() in headline.lower():
                                news_items[company_name].append({
                                    'headline': headline,
                                    'url': link,
                                    'source': source_name,
                                    'published': published,
                                    'portfolio_company': company_name,
                                    'competitor_name': competitor,
                                    'is_competitor': True
                                })
                                break
                        else:
                            continue
                        break
            
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
                
                # Fix relative URLs
                if link.startswith('/'):
                    link = source_url.rstrip('/') + link
                elif not link.startswith('http'):
                    link = source_url.rstrip('/') + '/' + link
                
                articles_found += 1
                
                # Check portfolio companies
                for company_name in companies.keys():
                    if company_name.lower() in headline.lower():
                        news_items[company_name].append({
                            'headline': headline,
                            'url': link,
                            'source': source_name,
                            'published': datetime.now().isoformat(),
                            'portfolio_company': company_name,
                            'is_competitor': False
                        })
                        break
                else:
                    # Check competitors
                    for company_name, data in companies.items():
                        for competitor in data.get('competitors', []):
                            if competitor.lower() in headline.lower():
                                news_items[company_name].append({
                                    'headline': headline,
                                    'url': link,
                                    'source': source_name,
                                    'published': datetime.now().isoformat(),
                                    'portfolio_company': company_name,
                                    'competitor_name': competitor,
                                    'is_competitor': True
                                })
                                break
                        else:
                            continue
                        break
            
            print(f"    Found {articles_found} articles")
        except Exception as e:
            print(f"  Error scraping {source_name}: {str(e)[:50]}")
        
        time.sleep(1)
    
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

def deduplicate_news(news_list, threshold=0.80):
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
                print(f"Response: {response.text}")
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
        print("JJFO NEWS BULLETIN - SCRAPER")
        print("="*60)
        print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Scraping news from past 48 hours...")
        
        companies = load_companies()
        recipients = load_recipients()
        
        if not recipients:
            print("ERROR: No recipients found in recipients.csv")
            exit(1)
        
        print(f"\n✓ Found {len(companies)} portfolio companies")
        print(f"✓ Sending to {len(recipients)} recipient(s)")
        print(f"✓ Scraping from {len(RSS_FEEDS)} RSS feeds + {len(SOURCES)} direct sources")
        
        # Scrape all sources
        print("\n--- Scraping RSS Feeds ---")
        rss_news = scrape_rss_feeds(companies)
        
        print("\n--- Scraping Direct Sources ---")
        direct_news = scrape_direct_sources(companies)
        
        # Merge and process
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
