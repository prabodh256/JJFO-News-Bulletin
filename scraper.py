"""
JJFO News Bulletin — Scraper v11
Improvements over v10:
  1. Per-company Google News RSS (replaces sector-keyword feeds) — eliminates global noise
  2. Expanded GARBAGE filter — kills stock-analyst / insider-sale content
  3. Fuzzy deduplication via difflib — no more 10x SpaceX stories
  4. India/startup relevance scoring — drops irrelevant foreign content
  5. Competitor section cleaned with same filters + name-in-title check
  6. Monthly competitor auto-discovery via sector Google News RSS
     → writes newly found competitors back to competitors.csv
  7. Cron timezone note: set workflow cron to "30 2 * * *" for 8 AM IST
"""

import csv
import os
import re
import json
import time
import logging
import smtplib
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, date
from difflib import SequenceMatcher
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR        = Path(__file__).parent
COMPANIES_CSV   = BASE_DIR / "companies.csv"
RECIPIENTS_CSV  = BASE_DIR / "recipients.csv"
COMPETITORS_CSV = BASE_DIR / "competitors.csv"
LAST_DISCOVERY  = BASE_DIR / ".last_competitor_discovery"   # tracks monthly run

# ── Email config (from GitHub Actions secrets) ────────────────────────────────
SMTP_HOST   = os.environ.get("SMTP_HOST",   "smtp.gmail.com")
SMTP_PORT   = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER   = os.environ.get("SMTP_USER",   "")
SMTP_PASS   = os.environ.get("SMTP_PASS",   "")
FROM_EMAIL  = os.environ.get("FROM_EMAIL",  SMTP_USER)

# ── SEARCH_ALIAS ──────────────────────────────────────────────────────────────
# Map portfolio company names → better Google News search terms
SEARCH_ALIAS = {
    "Orange Credit":   "Orange Retail Finance",
    "Terra":           "Letsterra gaming",
    "Grip":            "Gripinvest",
    "Rooter":          "Rooter.gg esports",
    "White Owl":       "White Owl brewery",
    "Sadhan":          "Sadhan microfinance",
    "Arata":           "Arata personal care",
    "WorkIndia":       "WorkIndia jobs platform",
    "Right Health":    "Wysa mental health",
}

# ── AMBIGUOUS_NAMES ───────────────────────────────────────────────────────────
# Company names too short/generic to search alone — append India
AMBIGUOUS_NAMES = {
    "Terra", "Grip", "Clear", "Roots", "Leaf", "Wave", "Arc",
    "Nova", "Bolt", "Flux", "Sage", "Peak", "Bloom", "Rise",
    "Volt", "Core", "Axon", "Base", "Edge", "Hive",
}

# ── GARBAGE patterns ──────────────────────────────────────────────────────────
GARBAGE_PATTERNS = [
    r"valuation shifts? signal",
    r"moving average",
    r"price target",
    r"open interest surge",
    r"analyst (upgrade|downgrade|initiat)",
    r"(buy|sell|hold) rat(ing|ed)",
    r"is rated (hold|buy|sell)",
    r"zacks (industry|rank|rating)",
    r"(bullish|bearish) signal",
    r"(technical|momentum) signal",
    r"sells? \$[\d,]+ in (shares|stock)",
    r"(buys?|acquires?) \$[\d,]+ in (shares|stock)",
    r"stock (drops?|surges?|slides?|rallies?) \d",
    r"(short interest|short seller)",
    r"\bIPO\b.*\bvaluation\b",
    r"hides? from (iranian|missile)",
    r"dollar tree",
    r"amazon\.com",
    r"walmart",
    r"minimalist.*apartment",
    r"minimalist.*decor",
    r"clear (lens|glasses|eyeglasses|frame)",
    r"pet probiotics market",
    r"herb oil market",
    r"yoga industry",
    r"wellness stipend",
    r"sligo bridge",
    r"iron ore export",
    r"hedge fund returns? .{0,10}hfr",
    r"private banking reporting",
    r"shadow banking entit",
    r"eba consults",
    r"meituan",
    r"baemin",
    r"hisense.*tv",
    r"quantum dot.*tv",
    r"semiconductor design market",
    r"wit studio.*ai",
    r"generative ai.*semiconductor",
    r"musicians.{0,20}mental health",
    r"noah levine.*protocol",
    r"brandpoint platform",
    r"ess engages mz group",
    r"gst summons",
    r"caclubindia",
    r"videography.*advocate",
    r"\b(malaysia|sarawak|hong kong|china|germany|vzb|latin america)\b.{0,40}(fund|insurtech|invest)",
    r"mpf equity fund",
    r"spac merger.*terra quantum",
]
GARBAGE_RE = re.compile("|".join(GARBAGE_PATTERNS), re.IGNORECASE)

INDIA_TERMS   = re.compile(r"\b(india[n]?|bengaluru|mumbai|delhi|hyderabad|pune|chennai|startup|rs\b|crore|lakh|inr|sebi|nse|bse)\b", re.IGNORECASE)
STARTUP_TERMS = re.compile(r"\b(raises?|funding|round|seed|series [a-e]|ipo|acqui|merger|launch|expand|partner)\b", re.IGNORECASE)
FOREIGN_CITY  = re.compile(r"\b(new york|london|beijing|shanghai|seoul|tokyo|paris|berlin|singapore|dubai|riyadh|hong kong|malaysia|vietnam(?! +(invest|mou|maharashtra)))\b", re.IGNORECASE)

def relevance_score(title):
    score = 0
    score += 2 if INDIA_TERMS.search(title) else 0
    score += 1 if STARTUP_TERMS.search(title) else 0
    score -= 2 if FOREIGN_CITY.search(title) else 0
    return score

def is_duplicate(title, seen, threshold=0.82):
    t = title.lower().strip()
    for s in seen:
        if SequenceMatcher(None, t, s).ratio() > threshold:
            return True
    seen.append(t)
    return False

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; JJFOBot/1.0)"}

def fetch_gnews_rss(query, max_results=10):
    encoded = urllib.parse.quote(query)
    url = "https://news.google.com/rss/search?q=" + encoded + "&hl=en-IN&gl=IN&ceid=IN:en"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as resp:
            xml_data = resp.read()
        root = ET.fromstring(xml_data)
        items = []
        for item in root.findall(".//item")[:max_results]:
            title   = (item.findtext("title") or "").strip()
            link    = (item.findtext("link")  or "").strip()
            source  = (item.findtext("source") or "").strip()
            pubdate = (item.findtext("pubDate") or "").strip()
            if title:
                items.append({"title": title, "link": link, "source": source, "pubdate": pubdate})
        return items
    except Exception as e:
        log.warning("RSS fetch failed for '" + query + "': " + str(e))
        return []

def load_companies():
    companies = []
    with open(COMPANIES_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = row.get("company_name", row.get("name", "")).strip()
            sector = row.get("sector", "").strip()
            if not name:
                continue
            search = SEARCH_ALIAS.get(name, name)
            if name in AMBIGUOUS_NAMES and "India" not in search:
                search += " India startup"
            companies.append({"name": name, "sector": sector, "search": search})
    return companies

def load_competitors():
    if not COMPETITORS_CSV.exists():
        return []
    competitors = []
    with open(COMPETITORS_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            pc   = row.get("portfolio_company", "").strip()
            comp = row.get("competitor", "").strip()
            sec  = row.get("sector", "").strip()
            if pc and comp:
                competitors.append({"portfolio": pc, "competitor": comp, "sector": sec})
    return competitors

def load_recipients():
    emails = []
    with open(RECIPIENTS_CSV, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            e = row.get("email", "").strip()
            if e:
                emails.append(e)
    return emails

def is_garbage(title):
    return bool(GARBAGE_RE.search(title))

def filter_articles(articles, company_name, seen_titles):
    out = []
    for a in articles:
        t = a["title"]
        if is_garbage(t):
            continue
        if is_duplicate(t, seen_titles):
            continue
        out.append(a)
    return out

def competitor_name_in_title(title, competitor):
    pattern = re.compile(re.escape(competitor), re.IGNORECASE)
    return bool(pattern.search(title))

def should_run_discovery():
    if not LAST_DISCOVERY.exists():
        return True
    try:
        last = date.fromisoformat(LAST_DISCOVERY.read_text().strip())
        today = date.today()
        return (today.year, today.month) != (last.year, last.month)
    except Exception:
        return True

DISCOVERY_SECTORS = [
    "HR Tech India startup",
    "InsurTech India startup",
    "HealthTech India startup",
    "EV Mobility India startup",
    "Quick Commerce India startup",
    "SpaceTech India startup",
    "Digital Lending India startup",
    "FinTech India startup",
    "EdTech India startup",
    "AgriTech India startup",
    "Logistics India startup",
    "GreenTech India startup",
    "B2B SaaS India startup",
    "Gaming India startup",
    "Mental Health India startup",
]

COMPANY_IN_HEADLINE_RE = re.compile(
    r"^([A-Z][A-Za-z0-9]+([ \-][A-Za-z0-9]+){0,3})\s+(raises?|secures?|closes?|bags?|gets?|lands?|announces?)",
    re.MULTILINE
)

def discover_competitors(existing_competitors, portfolio_names):
    log.info("Running monthly competitor discovery...")
    existing_set = {c["competitor"].lower() for c in existing_competitors}
    new_rows = []
    seen_new = set()
    for sector_query in DISCOVERY_SECTORS:
        articles = fetch_gnews_rss(sector_query, max_results=20)
        time.sleep(1)
        for a in articles:
            title = a["title"]
            if is_garbage(title):
                continue
            m = COMPANY_IN_HEADLINE_RE.match(title)
            if not m:
                continue
            candidate = m.group(1).strip()
            if candidate.lower() in {p.lower() for p in portfolio_names}:
                continue
            if candidate.lower() in existing_set:
                continue
            if candidate.lower() in seen_new:
                continue
            if candidate.lower() in {"india", "the", "a", "an", "startup", "company"}:
                continue
            sector = sector_query.replace(" India startup", "").replace(" India", "")
            new_rows.append({
                "portfolio_company": "",
                "competitor": candidate,
                "sector": sector,
                "source": a["link"],
                "discovered_on": str(date.today()),
            })
            seen_new.add(candidate.lower())
            log.info("  New competitor candidate: " + candidate + " (" + sector + ")")
    return new_rows

def save_new_competitors(new_rows):
    if not new_rows:
        return
    file_exists = COMPETITORS_CSV.exists()
    fieldnames = ["portfolio_company", "competitor", "sector", "source", "discovered_on"]
    with open(COMPETITORS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        for row in new_rows:
            writer.writerow(row)
    log.info("Appended " + str(len(new_rows)) + " new competitor candidates to competitors.csv")

def mark_discovery_done():
    LAST_DISCOVERY.write_text(str(date.today()))

def scrape_portfolio(companies):
    seen_titles = []
    sector_articles = {}
    for co in companies:
        log.info("  Fetching: " + co["name"] + " (" + co["sector"] + ")")
        articles = fetch_gnews_rss(co["search"], max_results=8)
        articles = filter_articles(articles, co["name"], seen_titles)
        if not articles:
            continue
        sec = co["sector"] or "General"
        if sec not in sector_articles:
            sector_articles[sec] = []
        for a in articles:
            a["company"] = co["name"]
            sector_articles[sec].append(a)
        time.sleep(0.5)
    return sector_articles

def scrape_competitors(competitors):
    seen_titles = []
    results = []
    for comp in competitors:
        name = comp["competitor"]
        search = SEARCH_ALIAS.get(name, name)
        if name in AMBIGUOUS_NAMES and "India" not in search:
            search += " India"
        log.info("  Competitor: " + name + " (vs " + comp["portfolio"] + ")")
        articles = fetch_gnews_rss(search, max_results=6)
        for a in articles:
            t = a["title"]
            if is_garbage(t):
                continue
            if not competitor_name_in_title(t, name):
                continue
            if is_duplicate(t, seen_titles):
                continue
            if relevance_score(t) < -1:
                continue
            results.append({
                "portfolio":  comp["portfolio"],
                "competitor": name,
                "sector":     comp["sector"],
                "title":      t,
                "link":       a["link"],
                "source":     a["source"],
            })
        time.sleep(0.5)
    return results

def render_html(portfolio_sections, competitor_articles, today):
    def article_html(a):
        title = a.get("title", "")
        link  = a.get("link",  "#")
        src   = a.get("source", "")
        co    = a.get("company", "")
        src_str = " <span style='color:#888;font-size:12px;'>- " + src + "</span>" if src else ""
        co_str  = "<span style='background:#e8f4fd;color:#1a73e8;padding:1px 6px;border-radius:3px;font-size:11px;margin-right:6px;'>" + co + "</span>" if co else ""
        return "<div style='padding:8px 0;border-bottom:1px solid #f0f0f0;'>" + co_str + "<a href='" + link + "' style='color:#1a1a1a;text-decoration:none;font-size:14px;'>" + title + "</a>" + src_str + "</div>"

    portfolio_html = ""
    for sector, articles in sorted(portfolio_sections.items()):
        portfolio_html += "<h3 style='color:#1a73e8;margin:20px 0 8px;border-left:4px solid #1a73e8;padding-left:10px;'>" + sector + "</h3>"
        for a in articles:
            portfolio_html += article_html(a)

    comp_by_portfolio = {}
    for a in competitor_articles:
        key = a["competitor"] + " vs " + a["portfolio"]
        comp_by_portfolio.setdefault(key, []).append(a)

    competitor_html = ""
    for key, arts in sorted(comp_by_portfolio.items()):
        competitor_html += "<div style='margin:12px 0 4px;font-weight:bold;color:#c0392b;font-size:13px;'>" + key + "</div>"
        for a in arts:
            competitor_html += article_html(a)

    total_portfolio = sum(len(v) for v in portfolio_sections.values())
    total_comp = len(competitor_articles)

    no_port = "<p style='color:#888;'>No portfolio news today.</p>"
    no_comp = "<p style='color:#888;'>No competitor news today.</p>"

    return (
        "<!DOCTYPE html><html><head><meta charset='UTF-8'></head>"
        "<body style='font-family:Arial,sans-serif;max-width:800px;margin:0 auto;color:#1a1a1a;padding:20px;'>"
        "<div style='background:linear-gradient(135deg,#1a1a2e,#16213e);color:white;padding:24px;border-radius:8px;margin-bottom:24px;'>"
        "<h1 style='margin:0;font-size:24px;'>JJFO News Bulletin</h1>"
        "<p style='margin:4px 0 0;opacity:0.8;'>" + today + " &nbsp;·&nbsp; " + str(total_portfolio) + " portfolio articles &nbsp;·&nbsp; " + str(total_comp) + " competitor signals</p></div>"
        "<p style='color:#555;font-size:13px;font-style:italic;'>Your daily curated intelligence from India's most promising startups. Stay ahead with the latest funding, M&amp;A, and sector developments.</p>"
        "<h2 style='border-bottom:2px solid #1a73e8;padding-bottom:6px;'>FUNDING &amp; PORTFOLIO NEWS</h2>"
        + (portfolio_html if portfolio_html else no_port) +
        "<h2 style='border-bottom:2px solid #c0392b;padding-bottom:6px;margin-top:32px;'>COMPETITOR INTELLIGENCE</h2>"
        + (competitor_html if competitor_html else no_comp) +
        "<div style='margin-top:32px;padding:12px;background:#f9f9f9;border-radius:6px;font-size:12px;color:#888;'>Generated by JJFO Scraper v11 · " + today + "</div></body></html>"
    )

def send_email(html_body, recipients, today):
    if not SMTP_USER or not SMTP_PASS:
        log.error("SMTP credentials not set. Skipping email.")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "JJFO News Bulletin - " + today
    msg["From"]    = FROM_EMAIL
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html"))
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(FROM_EMAIL, recipients, msg.as_string())
        log.info("Email sent to " + str(len(recipients)) + " recipient(s)")
    except Exception as e:
        log.error("Email send failed: " + str(e))
        raise

def main():
    today_str = datetime.now().strftime("%B %d, %Y")
    log.info("=== JJFO Scraper v11 - " + today_str + " ===")
    companies       = load_companies()
    competitors     = load_competitors()
    recipients      = load_recipients()
    portfolio_names = {c["name"] for c in companies}
    log.info("Portfolio: " + str(len(companies)) + " | Competitors: " + str(len(competitors)) + " | Recipients: " + str(len(recipients)))
    if should_run_discovery():
        new_comps = discover_competitors(competitors, portfolio_names)
        if new_comps:
            save_new_competitors(new_comps)
            competitors = load_competitors()
        mark_discovery_done()
    else:
        log.info("Competitor discovery already ran this month - skipping.")
    log.info("--- Scraping portfolio companies ---")
    portfolio_sections = scrape_portfolio(companies)
    log.info("--- Scraping competitors ---")
    competitor_articles = scrape_competitors(competitors)
    log.info("Portfolio articles: " + str(sum(len(v) for v in portfolio_sections.values())))
    log.info("Competitor articles: " + str(len(competitor_articles)))
    html = render_html(portfolio_sections, competitor_articles, today_str)
    send_email(html, recipients, today_str)
    log.info("=== Done ===")

if __name__ == "__main__":
    main()
