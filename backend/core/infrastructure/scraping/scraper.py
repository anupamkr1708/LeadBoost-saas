"""
Advanced scraping infrastructure with tiered approach
"""

import asyncio
import json
import re
import time
from typing import Dict, Optional, List, Any
from urllib.parse import urlparse, urljoin
import aiohttp
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from dataclasses import dataclass
from enum import Enum

from core.infrastructure.logging import get_logger

logger = get_logger(__name__)


class ScrapingMethod(str, Enum):
    JSON_LD = "json_ld"
    STRUCTURED_DATA = "structured_data"
    PLAYWRIGHT = "playwright"
    REQUESTS = "requests"
    BEAUTIFULSOUP = "beautifulsoup"


@dataclass
class ScrapingResult:
    success: bool
    data: Dict[str, Any]
    method: ScrapingMethod
    confidence: float
    processing_time: float
    error_message: Optional[str] = None


class TieredScraper:
    """
    Implements a tiered scraping approach prioritizing structured data extraction
    over aggressive bypasses, following ethical scraping standards.
    """

    def __init__(self, timeout: int = 25, max_retries: int = 2):
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = None

    async def __aenter__(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def scrape(self, url: str) -> ScrapingResult:
        """
        Main scraping method that implements the tiered approach:
        1. JSON-LD structured data extraction
        2. Meta tags and structured data
        3. Playwright for JavaScript-rendered content (as fallback)
        """
        start_time = time.time()

        # Tier 1: JSON-LD extraction (highest confidence, most reliable)
        result = await self._extract_json_ld(url)
        if result.success and result.confidence > 0.7:
            result.processing_time = time.time() - start_time
            return result

        # Tier 2: Meta tags and structured data
        result = await self._extract_meta_data(url)
        if result.success and result.confidence > 0.5:
            result.processing_time = time.time() - start_time
            return result

        # Tier 3: Playwright for JavaScript-heavy sites (fallback)
        result = await self._scrape_with_playwright(url)
        result.processing_time = time.time() - start_time
        return result

    async def _extract_json_ld(self, url: str) -> ScrapingResult:
        """Extract structured data from JSON-LD scripts"""
        try:
            if self.session is None:
                return ScrapingResult(
                    success=False,
                    data={},
                    method=ScrapingMethod.JSON_LD,
                    confidence=0.0,
                    processing_time=0.0,
                    error_message="Session is not initialized",
                )

            async with self.session.get(url) as response:
                if response.status != 200:
                    return ScrapingResult(
                        success=False,
                        data={},
                        method=ScrapingMethod.JSON_LD,
                        confidence=0.0,
                        processing_time=0.0,
                        error_message=f"HTTP {response.status}",
                    )

                content = await response.text()
                soup = BeautifulSoup(content, "html.parser")

                # Find JSON-LD scripts
                json_ld_scripts = soup.find_all("script", type="application/ld+json")

                if not json_ld_scripts:
                    return ScrapingResult(
                        success=False,
                        data={},
                        method=ScrapingMethod.JSON_LD,
                        confidence=0.0,
                        processing_time=0.0,
                        error_message="No JSON-LD found",
                    )

                # Parse all JSON-LD data
                all_data = {}
                for script in json_ld_scripts:
                    try:
                        json_data = json.loads(script.string)
                        if isinstance(json_data, list):
                            for item in json_data:
                                all_data.update(self._flatten_json(item))
                        else:
                            all_data.update(self._flatten_json(json_data))
                    except json.JSONDecodeError:
                        continue

                if not all_data:
                    return ScrapingResult(
                        success=False,
                        data={},
                        method=ScrapingMethod.JSON_LD,
                        confidence=0.0,
                        processing_time=0.0,
                        error_message="Invalid JSON-LD",
                    )

                # Calculate confidence based on data completeness
                confidence = self._calculate_json_ld_confidence(all_data)

                return ScrapingResult(
                    success=True,
                    data=all_data,
                    method=ScrapingMethod.JSON_LD,
                    confidence=confidence,
                    processing_time=0.0,  # Will be updated by parent
                )

        except Exception as e:
            logger.error(f"JSON-LD extraction failed for {url}: {str(e)}")
            return ScrapingResult(
                success=False,
                data={},
                method=ScrapingMethod.JSON_LD,
                confidence=0.0,
                processing_time=0.0,
                error_message=str(e),
            )

    async def _extract_meta_data(self, url: str) -> ScrapingResult:
        """Extract meta tags, title, and basic structured data"""
        try:
            if self.session is None:
                return ScrapingResult(
                    success=False,
                    data={},
                    method=ScrapingMethod.STRUCTURED_DATA,
                    confidence=0.0,
                    processing_time=0.0,
                    error_message="Session is not initialized",
                )

            async with self.session.get(url) as response:
                if response.status != 200:
                    return ScrapingResult(
                        success=False,
                        data={},
                        method=ScrapingMethod.STRUCTURED_DATA,
                        confidence=0.0,
                        processing_time=0.0,
                        error_message=f"HTTP {response.status}",
                    )

                content = await response.text()
                soup = BeautifulSoup(content, "html.parser")

                # Extract basic information
                data = {}

                # Title
                title_tag = soup.find("title")
                if title_tag:
                    data["title"] = title_tag.get_text().strip()

                # Meta description
                desc_tag = soup.find("meta", attrs={"name": "description"})
                if desc_tag:
                    data["description"] = desc_tag.get("content", "").strip()

                # Open Graph tags
                og_tags = soup.find_all("meta", property=re.compile(r"^og:"))
                for tag in og_tags:
                    prop = tag.get("property", "").replace("og:", "")
                    if prop:
                        data[f"og_{prop}"] = tag.get("content", "").strip()

                # Twitter Card tags
                twitter_tags = soup.find_all(
                    "meta", attrs={"name": re.compile(r"^twitter:")}
                )
                for tag in twitter_tags:
                    name = tag.get("name", "").replace("twitter:", "")
                    if name:
                        data[f"twitter_{name}"] = tag.get("content", "").strip()

                # Links
                links = []
                for link in soup.find_all("a", href=True):
                    href = link["href"]
                    if href.startswith(("http://", "https://")):
                        links.append(href)
                    elif href.startswith("/"):
                        links.append(urljoin(url, href))
                data["links"] = links

                # Calculate confidence based on available data
                confidence = self._calculate_meta_confidence(data)

                if not data:
                    return ScrapingResult(
                        success=False,
                        data={},
                        method=ScrapingMethod.STRUCTURED_DATA,
                        confidence=0.0,
                        processing_time=0.0,
                        error_message="No meta data found",
                    )

                return ScrapingResult(
                    success=True,
                    data=data,
                    method=ScrapingMethod.STRUCTURED_DATA,
                    confidence=confidence,
                    processing_time=0.0,  # Will be updated by parent
                )

        except Exception as e:
            logger.error(f"Meta data extraction failed for {url}: {str(e)}")
            return ScrapingResult(
                success=False,
                data={},
                method=ScrapingMethod.STRUCTURED_DATA,
                confidence=0.0,
                processing_time=0.0,
                error_message=str(e),
            )

    async def _scrape_with_playwright(self, url: str) -> ScrapingResult:
        """Scrape using Playwright for JavaScript-heavy sites"""
        try:
            # Check if Playwright is properly installed and available
            try:
                from playwright.async_api import async_playwright
            except ImportError:
                return ScrapingResult(
                    success=False,
                    data={},
                    method=ScrapingMethod.PLAYWRIGHT,
                    confidence=0.0,
                    processing_time=0.0,
                    error_message="Playwright not installed",
                )

            async with async_playwright() as p:
                try:
                    browser = await p.chromium.launch(
                        headless=True,
                        args=[
                            "--disable-blink-features=AutomationControlled",
                            "--no-sandbox",
                            "--disable-dev-shm-usage",
                            "--disable-infobars",
                            "--disable-extensions",
                            "--disable-plugins",
                            "--disable-images",  # Speed up by not loading images
                        ],
                    )
                except Exception as e:
                    logger.error(
                        f"Playwright browser launch failed for {url}: {str(e)}"
                    )
                    # Check if this is a Windows/Python compatibility issue
                    if (
                        "NotImplementedError" in str(e)
                        or "subprocess" in str(e).lower()
                    ):
                        # Try alternative scraping method for Windows compatibility
                        return await self._scrape_with_requests_fallback(url)
                    else:
                        return ScrapingResult(
                            success=False,
                            data={},
                            method=ScrapingMethod.PLAYWRIGHT,
                            confidence=0.0,
                            processing_time=0.0,
                            error_message=f"Browser launch failed: {str(e)}",
                        )

                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1366, "height": 768},
                    locale="en-US",
                    extra_http_headers={
                        "Accept-Language": "en-US,en;q=0.9",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                    },
                )

                page = await context.new_page()

                # Navigate to the page
                response = await page.goto(url, wait_until="domcontentloaded")

                if response and response.status != 200:
                    await browser.close()
                    return ScrapingResult(
                        success=False,
                        data={},
                        method=ScrapingMethod.PLAYWRIGHT,
                        confidence=0.0,
                        processing_time=0.0,
                        error_message=f"HTTP {response.status}",
                    )

                # Wait for page to load and render
                await page.wait_for_timeout(3000)  # Wait for React/Vue to render

                # Execute JavaScript to extract structured data
                data = await page.evaluate(
                    """
                    () => {
                        const result = {};
                        
                        // Basic page info
                        result.title = document.title || null;
                        result.url = window.location.href || null;
                        
                        // Meta tags
                        const metaDesc = document.querySelector("meta[name='description']");
                        result.meta_description = metaDesc?.content || null;
                        
                        const ogDesc = document.querySelector("meta[property='og:description']");
                        result.og_description = ogDesc?.content || null;
                        
                        // JSON-LD structured data
                        result.jsonld = Array.from(
                            document.querySelectorAll("script[type='application/ld+json']")
                        ).map(s => {
                            try { return JSON.parse(s.innerText); }
                            catch { return null; }
                        }).filter(Boolean);
                        
                        // Visible text content
                        result.text_content = document.body.innerText.slice(0, 8000);
                        
                        // Links
                        result.links = Array.from(document.querySelectorAll("a[href]"))
                            .map(a => a.href)
                            .filter(href => href && href.startsWith('http'));
                        
                        // Contact info patterns
                        const text = result.text_content.toLowerCase();
                        const emailPattern = /([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})/;
                        const phonePattern = /(\+?\d{1,2}[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})/;
                        
                        const emailMatch = text.match(emailPattern);
                        const phoneMatch = text.match(phonePattern);
                        
                        result.email = emailMatch ? emailMatch[1] : null;
                        result.phone = phoneMatch ? phoneMatch[1] : null;
                        
                        // Company name patterns
                        const domain = window.location.hostname.replace('www.', '');
                        result.potential_company_name = domain.split('.')[0];
                        
                        return result;
                    }
                """
                )

                await browser.close()

                if not data:
                    return ScrapingResult(
                        success=False,
                        data={},
                        method=ScrapingMethod.PLAYWRIGHT,
                        confidence=0.3,  # Lower confidence as this is a fallback
                        processing_time=0.0,
                        error_message="No data extracted by Playwright",
                    )

                # Calculate confidence for Playwright extraction
                confidence = self._calculate_playwright_confidence(data)

                return ScrapingResult(
                    success=True,
                    data=data,
                    method=ScrapingMethod.PLAYWRIGHT,
                    confidence=confidence,
                    processing_time=0.0,  # Will be updated by parent
                )

        except Exception as e:
            logger.error(f"Playwright scraping failed for {url}: {str(e)}")
            # Check if this is a Windows/Python compatibility issue
            if "NotImplementedError" in str(e) or "subprocess" in str(e).lower():
                # Try alternative scraping method for Windows compatibility
                return await self._scrape_with_requests_fallback(url)
            else:
                return ScrapingResult(
                    success=False,
                    data={},
                    method=ScrapingMethod.PLAYWRIGHT,
                    confidence=0.0,
                    processing_time=0.0,
                    error_message=str(e),
                )

    def _flatten_json(
        self, obj: Any, parent_key: str = "", sep: str = "_"
    ) -> Dict[str, Any]:
        """Flatten nested JSON structure"""
        items = []

        if isinstance(obj, dict):
            for k, v in obj.items():
                new_key = f"{parent_key}{sep}{k}" if parent_key else k
                if isinstance(v, (dict, list)):
                    items.extend(self._flatten_json(v, new_key, sep=sep).items())
                else:
                    items.append((new_key, v))
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                new_key = f"{parent_key}{sep}{i}" if parent_key else str(i)
                if isinstance(v, (dict, list)):
                    items.extend(self._flatten_json(v, new_key, sep=sep).items())
                else:
                    items.append((new_key, v))

        return dict(items)

    def _calculate_json_ld_confidence(self, data: Dict[str, Any]) -> float:
        """Calculate confidence score for JSON-LD extraction"""
        score = 0.0

        # Check for important business information
        if "name" in data or "legalName" in data:
            score += 0.3
        if "description" in data:
            score += 0.2
        if "url" in data:
            score += 0.1
        if "email" in data or "telephone" in data:
            score += 0.1
        if "address" in data:
            score += 0.2
        if "foundingDate" in data:
            score += 0.1

        # Additional business-specific properties
        business_properties = [
            "employeeCount",
            "revenue",
            "founded",
            "industry",
            "contactPoint",
            "location",
            "logo",
        ]
        for prop in business_properties:
            if prop in str(data).lower():
                score += 0.1

        return min(score, 1.0)

    def _calculate_meta_confidence(self, data: Dict[str, Any]) -> float:
        """Calculate confidence score for meta data extraction"""
        score = 0.0

        if data.get("title"):
            score += 0.3
        if data.get("description"):
            score += 0.3
        if data.get("og_title") or data.get("og_description"):
            score += 0.2
        if len(data.get("links", [])) > 0:
            score += 0.1
        if data.get("og_image"):
            score += 0.1

        return min(score, 1.0)

    def _calculate_playwright_confidence(self, data: Dict[str, Any]) -> float:
        """Calculate confidence score for Playwright extraction"""
        score = 0.3  # Base confidence for Playwright as it's a fallback

        if data.get("title"):
            score += 0.2
        if data.get("meta_description") or data.get("og_description"):
            score += 0.2
        if data.get("email"):
            score += 0.2
        if data.get("phone"):
            score += 0.1
        if data.get("links") and len(data["links"]) > 5:
            score += 0.1
        if data.get("potential_company_name"):
            score += 0.1

        return min(score, 1.0)

    async def _scrape_with_requests_fallback(self, url: str) -> ScrapingResult:
        """Fallback scraping method using requests and BeautifulSoup for Windows compatibility"""
        try:
            import requests
            from bs4 import BeautifulSoup
            import time

            start_time = time.time()

            # Set headers to mimic a real browser
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            }

            response = requests.get(url, headers=headers, timeout=25)

            if response.status_code != 200:
                return ScrapingResult(
                    success=False,
                    data={},
                    method=ScrapingMethod.REQUESTS,
                    confidence=0.0,
                    processing_time=time.time() - start_time,
                    error_message=f"HTTP {response.status_code}",
                )

            content = response.text
            soup = BeautifulSoup(content, "html.parser")

            # Extract data similar to what Playwright would extract
            data = {}

            # Title
            title_tag = soup.find("title")
            if title_tag:
                data["title"] = title_tag.get_text().strip()

            # Meta description
            desc_tag = soup.find("meta", attrs={"name": "description"})
            if desc_tag:
                data["meta_description"] = desc_tag.get("content", "").strip()

            # Open Graph tags
            og_desc = soup.find("meta", property="og:description")
            if og_desc:
                data["og_description"] = og_desc.get("content", "").strip()

            # JSON-LD structured data
            json_ld_scripts = soup.find_all("script", type="application/ld+json")
            jsonld_data = []
            for script in json_ld_scripts:
                try:
                    import json

                    json_data = json.loads(script.string)
                    jsonld_data.append(json_data)
                except:
                    continue
            data["jsonld"] = jsonld_data

            # Visible text content
            body_text = soup.body.get_text() if soup.body else ""
            data["text_content"] = body_text[:8000]

            # Links
            links = []
            for link in soup.find_all("a", href=True):
                href = link["href"]
                if href.startswith(("http://", "https://")):
                    links.append(href)
            data["links"] = links

            # Basic contact info extraction
            text_lower = data["text_content"].lower() if "text_content" in data else ""

            # Email extraction
            import re

            email_pattern = r"([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})"
            email_match = re.search(email_pattern, text_lower)
            if email_match:
                data["email"] = email_match.group(1)

            # Phone extraction
            phone_pattern = r"(\+?\d{1,2}[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})"
            phone_match = re.search(phone_pattern, text_lower)
            if phone_match:
                data["phone"] = phone_match.group(1)

            # Company name from domain
            from urllib.parse import urlparse

            domain = urlparse(url).netloc.replace("www.", "")
            data["potential_company_name"] = domain.split(".")[0]

            if not data:
                return ScrapingResult(
                    success=False,
                    data={},
                    method=ScrapingMethod.REQUESTS,
                    confidence=0.2,  # Lower confidence for fallback method
                    processing_time=time.time() - start_time,
                    error_message="No data extracted by requests fallback",
                )

            # Calculate confidence for requests extraction
            confidence = (
                self._calculate_playwright_confidence(data) * 0.8
            )  # Slightly lower confidence for fallback

            return ScrapingResult(
                success=True,
                data=data,
                method=ScrapingMethod.REQUESTS,
                confidence=confidence,
                processing_time=time.time() - start_time,
            )

        except Exception as e:
            logger.error(f"Requests fallback scraping failed for {url}: {str(e)}")
            return ScrapingResult(
                success=False,
                data={},
                method=ScrapingMethod.REQUESTS,
                confidence=0.0,
                processing_time=0.0,
                error_message=str(e),
            )


# Global scraper instance for reuse
scraper_instance = None


async def get_scraper() -> TieredScraper:
    """Get or create a global scraper instance"""
    global scraper_instance
    if scraper_instance is None:
        scraper_instance = TieredScraper()
    return scraper_instance
