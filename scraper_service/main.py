import json
import os
from typing import Any, Optional

import google.generativeai as genai
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException
from playwright.sync_api import sync_playwright
from pydantic import BaseModel

# Try to import stealth mode
STEALTH_AVAILABLE = False
stealth_sync = None
try:
    from playwright_stealth import stealth_sync  # type: ignore

    STEALTH_AVAILABLE = True
    print("✓ Stealth mode enabled")
except ImportError:
    print("WARNING: playwright-stealth not found or import failed. Stealth mode disabled.")

# Get API key from environment
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if GOOGLE_API_KEY:
    genai.configure(api_key=GOOGLE_API_KEY)  # type: ignore
    print("✓ Google API Key configured")
else:
    print("WARNING: GOOGLE_API_KEY not set. AI extraction will fail.")

app = FastAPI(title="Dedicated Browser Service")


class ScrapeRequest(BaseModel):
    url: str
    query: Optional[str] = None
    prompt: Optional[str] = None
    model_name: str = "gemini-2.5-flash"
    wait_time: int = 2
    stealth_mode: bool = True
    session_json: Optional[Any] = None
    pagination_enabled: bool = False
    start_page: int = 1
    end_page: int = 1
    page2_url: Optional[str] = None
    page3_url: Optional[str] = None


@app.get("/")
def health_check():
    return {"status": "ok", "service": "browser-microservice", "version": "1.1.1"}


def clean_html(html_content):
    soup = BeautifulSoup(html_content, "html.parser")

    # Remove scripts and styles
    for script in soup(["script", "style", "svg", "path", "noscript"]):
        script.extract()

    # Preserve Images: Replace img tags with (Image: URL)
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        alt = img.get("alt", "")
        if src:
            img.replace_with(f"{alt} (Image: {src}) ")

    # Preserve Links: Append (Link: URL) to anchor text
    for a in soup.find_all("a", href=True):
        if a.get_text(strip=True):
            a.replace_with(f"{a.get_text(strip=True)} (Link: {a['href']}) ")

    # Get text
    text = soup.get_text(separator=" ", strip=True)
    return text


def learn_pagination_pattern(
    page1_url: str, page2_url: str, page3_url: str, target_page: int, model_name: str
):
    """
    Use AI to learn the pagination pattern from example URLs and generate the URL for any page.
    Returns the predicted URL for the target page.
    """
    if not GOOGLE_API_KEY or not page2_url or not page3_url:
        return None

    try:
        model = genai.GenerativeModel(model_name)  # type: ignore
        prompt = f"""
        You are analyzing URL patterns for pagination.
        
        Here are example URLs:
        Page 1: {page1_url}
        Page 2: {page2_url}
        Page 3: {page3_url}
        
        Analyze the pattern and generate the URL for Page {target_page}.
        
        Look for patterns in:
        - Path changes (e.g., /page/2/, /page/3/)
        - Query parameters (e.g., ?page=2, ?startIndex=10)
        - URL structure changes
        
        Respond with ONLY the complete URL for Page {target_page}, nothing else.
        """

        response = model.generate_content(prompt)
        predicted_url = response.text.strip()

        print(f"AI predicted Page {target_page} URL: {predicted_url}")
        return predicted_url

    except Exception as e:
        print(f"Error learning pagination pattern: {e}")
        return None


def find_next_page_button(page, model_name: str):
    """
    Use AI to find the 'Next Page' button/link on the current page.
    Returns the selector for the next button, or None if not found.
    """
    if not GOOGLE_API_KEY:
        return None

    try:
        # Get page HTML
        html = page.content()
        soup = BeautifulSoup(html, "html.parser")

        # Find all links and buttons
        clickable_elements = []
        for elem in soup.find_all(["a", "button", "div", "span"]):
            text = elem.get_text(strip=True).lower()
            href = elem.get("href", "")
            # Look for pagination-related elements
            if (
                any(keyword in text for keyword in ["next", "more", "›", "→", "»"])
                or "page" in text
                or "pagination" in str(elem.get("class", []))
            ):
                clickable_elements.append(
                    {
                        "tag": elem.name,
                        "text": elem.get_text(strip=True)[:50],
                        "href": href,
                        "class": " ".join(elem.get("class", [])),
                        "id": elem.get("id", ""),
                    }
                )

        if not clickable_elements:
            return None

        # Ask Gemini to identify the next button
        model = genai.GenerativeModel(model_name)  # type: ignore
        prompt = f"""
        You are analyzing a webpage to find the "Next Page" button for pagination.
        
        Here are the clickable elements that might be the next page button:
        {clickable_elements[:20]}  
        
        Which element is most likely the "Next Page" button?
        Respond with ONLY the element's index number (0-{len(clickable_elements)-1}), or "NONE" if there's no clear next button.
        
        Response format: Just the number, nothing else.
        """

        response = model.generate_content(prompt)
        result = response.text.strip()

        if result.upper() == "NONE" or not result.isdigit():
            return None

        idx = int(result)
        if idx < 0 or idx >= len(clickable_elements):
            return None

        selected = clickable_elements[idx]
        print(f"AI selected next button: {selected}")

        # Build a selector for this element
        if selected["id"]:
            return f"#{selected['id']}"
        elif selected["class"]:
            return f".{selected['class'].split()[0]}"
        elif selected["href"]:
            return f"a[href='{selected['href']}']"
        else:
            return None

    except Exception as e:
        print(f"Error finding next button: {e}")
        return None


def extract_with_gemini(text_content: str, query: str, model_name: str):
    if not GOOGLE_API_KEY:
        return {"error": "Google API Key not configured on Scraper Service"}

    print(f"DEBUG: Sending {len(text_content)} chars to Gemini...")
    # Log the input text to debug context failures
    print(f"DEBUG: Gemini Input Context (First 5000 chars): {text_content[:5000]}")

    try:
        model = genai.GenerativeModel(model_name)  # type: ignore

        prompt = f"""
        You are a precise data extraction agent.
        
        CONTEXT:
        The user wants to extract information based on this query: "{query}"
        
        DATA SOURCE:
        {text_content[:100000]} 
        
        INSTRUCTIONS:
        1. Identify the data matching the query.
        2. Return ONLY a valid JSON object.
        3. The JSON should have meaningful keys matching the data (e.g., "products", "prices", "articles").
        4. If no data is found, return an empty JSON object {{}}.
        5. Do NOT include markdown formatting (```json). Just the raw JSON string.
        """

        response = model.generate_content(prompt)
        text_resp = response.text.replace("```json", "").replace("```", "").strip()
        print(f"DEBUG: Gemini Response: {text_resp[:100]}...")
        return json.loads(text_resp)

    except Exception as e:
        print(f"Gemini Error: {e}")
        return {"error": f"AI Extraction Failed: {str(e)}"}


@app.post("/scrape")
def scrape(request: ScrapeRequest):
    """
    Main scraping endpoint with 4-minute hard timeout.
    """
    import signal
    import time

    start_time = time.time()
    MAX_EXECUTION_TIME = 240  # 4 minutes in seconds

    # Set a hard timeout (only works on Unix-like systems)
    def timeout_handler(signum, frame):
        raise TimeoutError(f"Scrape operation exceeded {MAX_EXECUTION_TIME/60} minute timeout")

    try:
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(MAX_EXECUTION_TIME)
    except Exception:
        # Windows doesn't support SIGALRM, skip timeout on Windows
        print("WARN: Signal-based timeout not available on this platform")

    print(f"Received scrape request for: {request.url}")

    # Normalize session_json
    if request.session_json:
        if isinstance(request.session_json, list):
            print("DEBUG: detected list for session_json, wrapping in {'cookies': ...}")
            request.session_json = {"cookies": request.session_json}

        # Sanitize Cookies (Fix SameSite casing)
        if "cookies" in request.session_json:
            for cookie in request.session_json["cookies"]:
                if "sameSite" in cookie:
                    val = cookie["sameSite"]
                    # Map common values to Playwright's strict expectations
                    if val in ["no_restriction", "unspecified"]:
                        cookie["sameSite"] = "None"
                    elif val.lower() == "lax":
                        cookie["sameSite"] = "Lax"
                    elif val.lower() == "strict":
                        cookie["sameSite"] = "Strict"
                    elif val.lower() == "none":
                        cookie["sameSite"] = "None"
                    elif val not in ["Strict", "Lax", "None"]:
                        # Unknown value, remove it to avoid crash
                        del cookie["sameSite"]

    try:
        with sync_playwright() as p:
            # 1. Launch Browser
            browser_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",  # Critical for Docker
                "--disable-accelerated-2d-canvas",
                "--no-first-run",
                "--no-zygote",
                "--single-process",
                "--disable-gpu",
                "--ignore-certificate-errors",
            ]

            browser = p.chromium.launch(headless=True, args=browser_args)

            # 2. Context & Page
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                ignore_https_errors=True,
            )

            if request.session_json:
                context = browser.new_context(
                    storage_state=request.session_json, ignore_https_errors=True
                )

            page = context.new_page()

            # 3. Apply Stealth
            if request.stealth_mode and STEALTH_AVAILABLE and stealth_sync:
                try:
                    stealth_sync(page)  # type: ignore
                except Exception as e:
                    print(f"Stealth failed: {e}")

            # 4. Multi-Page Scraping
            all_content = []
            pages_to_scrape = (
                request.end_page - request.start_page + 1 if request.pagination_enabled else 1
            )
            current_page_num = request.start_page  # Track the actual page number we're on

            # Determine the starting URL
            starting_url = request.url
            if request.pagination_enabled and request.start_page > 1:
                # If starting on page > 1, use AI to predict the starting URL
                if request.page2_url and request.page3_url:
                    print(f"Predicting starting URL for page {request.start_page}...")
                    predicted_start = learn_pagination_pattern(
                        request.url,
                        request.page2_url,
                        request.page3_url,
                        request.start_page,
                        request.model_name,
                    )
                    if predicted_start:
                        starting_url = predicted_start
                        print(f"Using AI-predicted start URL: {starting_url}")
                    else:
                        print(
                            f"Warning: Could not predict page {request.start_page} URL, starting from page 1"
                        )
                        current_page_num = 1
                else:
                    print(
                        f"Warning: No example URLs provided, starting from page 1 instead of page {request.start_page}"
                    )
                    current_page_num = 1

            # Navigate to the starting page
            print(f"Navigating to starting page {current_page_num}: {starting_url}...")
            try:
                page.goto(starting_url, timeout=60000, wait_until="domcontentloaded")
            except Exception as nav_error:
                print(f"Navigation Error (continuing anyway): {nav_error}")

            for i in range(pages_to_scrape):
                # Wait for content
                print(f"Waiting for content on page {current_page_num}...")
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass

                # Add explicit wait time
                page.wait_for_timeout(request.wait_time * 1000 + 2000)

                # Extract HTML for this page
                page_content = page.content()
                all_content.append(page_content)
                print(f"Page {current_page_num} scraped ({len(page_content)} bytes)")

                # If this isn't the last page, navigate to next
                if request.pagination_enabled and i < pages_to_scrape - 1:
                    next_page_num = current_page_num + 1  # The actual next page number
                    next_url = None

                    # Strategy 1: Use AI pattern learning if example URLs provided
                    if request.page2_url and request.page3_url:
                        print(f"Using AI pattern learning for page {next_page_num}...")
                        next_url = learn_pagination_pattern(
                            request.url,
                            request.page2_url,
                            request.page3_url,
                            next_page_num,
                            request.model_name,
                        )

                        if next_url:
                            try:
                                print(f"Navigating to AI-predicted URL: {next_url}")
                                page.goto(next_url, timeout=60000, wait_until="domcontentloaded")
                                current_page_num += 1  # Increment page counter
                                continue  # Skip other strategies
                            except Exception as e:
                                print(f"AI-predicted URL failed: {e}")
                                next_url = None

                    # Strategy 2: Try to find and click "Next" button
                    if not next_url:
                        print(f"Looking for 'Next' button...")
                        next_selector = find_next_page_button(page, request.model_name)

                        if next_selector:
                            try:
                                print(f"Clicking next button: {next_selector}")
                                page.click(next_selector, timeout=5000)
                                page.wait_for_timeout(2000)  # Wait for navigation
                                current_page_num += 1  # Increment page counter
                                continue  # Success, move to next iteration
                            except Exception as e:
                                print(f"Failed to click next button: {e}")

                    # Strategy 3: Fallback to URL pattern guessing
                    print("Trying URL pattern fallback...")
                    import re

                    current_url = page.url

                    if "page/" in current_url:
                        next_url = re.sub(r"/page/\d+/?", f"/page/{next_page_num}/", current_url)
                    elif "?" in current_url:
                        next_url = f"{current_url}&page={next_page_num}"
                    else:
                        next_url = f"{current_url.rstrip('/')}/page/{next_page_num}/"

                    try:
                        print(f"Navigating to fallback URL: {next_url}")
                        page.goto(next_url, timeout=60000, wait_until="domcontentloaded")
                        current_page_num += 1  # Increment page counter
                    except Exception as fallback_error:
                        print(f"All pagination strategies failed: {fallback_error}")
                        break  # Stop pagination if we can't navigate

            # Combine all pages
            content = "\n\n".join(all_content)
            browser.close()

            # 7. AI Processing
            print(f"Scrape successful. Content length: {len(content)}")

            clean_text = clean_html(content)
            print(f"DEBUG: Extracted Text Preview: {clean_text[:500]}")

            if request.query or request.prompt:
                query_text = request.query or request.prompt or ""
                print(f"Processing with Gemini... Query: {query_text}")
                data = extract_with_gemini(clean_text, query_text, request.model_name)

                # Cancel timeout alarm (success)
                try:
                    signal.alarm(0)
                except Exception:
                    pass

                return {"status": "success", "url": request.url, "data": data}
            else:
                # Raw HTML mode
                # Cancel timeout alarm (success)
                try:
                    signal.alarm(0)
                except Exception:
                    pass

                return {
                    "status": "success",
                    "url": request.url,
                    "content_length": len(content),
                    "html": content,
                }

    except TimeoutError as te:
        print(f"Timeout Error: {te}")
        # Cancel alarm
        try:
            signal.alarm(0)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Operation timed out: {str(te)}") from te

    except Exception as e:
        print(f"Browser Error: {e}")
        # Cancel alarm
        try:
            signal.alarm(0)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e)) from e


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
