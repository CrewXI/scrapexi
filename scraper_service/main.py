import asyncio
import json
import os
import random
from typing import Any, Dict, Optional

import google.generativeai as genai
import playwright
from bs4 import BeautifulSoup
from fastapi import Body, FastAPI, HTTPException
from playwright.sync_api import sync_playwright
from pydantic import BaseModel

app = FastAPI(title="Dedicated Browser Service")


class ScrapeRequest(BaseModel):
    url: str
    query: Optional[str] = None
    prompt: Optional[str] = None
    model_name: str = "gemini-2.5-flash"
    wait_time: int = 2
    stealth_mode: bool = True
    session_json: Optional[Any] = None


@app.get("/")
def health_check():
    return {"status": "ok", "service": "browser-microservice", "version": "1.1.1"}


def clean_html(html_content):
    soup = BeautifulSoup(html_content, "html.parser")

    # Remove scripts and styles
    for script in soup(["script", "style", "svg", "path", "noscript"]):
        script.extract()

    # Preserve Links: Append (Link: URL) to anchor text
    for a in soup.find_all("a", href=True):
        if a.get_text(strip=True):
            a.replace_with(f"{a.get_text(strip=True)} (Link: {a['href']}) ")

    # Get text
    text = soup.get_text(separator=" ", strip=True)
    return text


def extract_with_gemini(text_content: str, query: str, model_name: str):
    if not GOOGLE_API_KEY:
        return {"error": "Google API Key not configured on Scraper Service"}

    print(f"DEBUG: Sending {len(text_content)} chars to Gemini...")
    # Log the input text to debug context failures
    print(f"DEBUG: Gemini Input Context (First 5000 chars): {text_content[:5000]}")

    try:
        model = genai.GenerativeModel(model_name)

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
            if request.stealth_mode and STEALTH_AVAILABLE:
                try:
                    stealth_sync(page)
                except Exception as e:
                    print(f"Stealth failed: {e}")

            # 4. Navigate
            print(f"Navigating to {request.url}...")
            try:
                page.goto(request.url, timeout=60000, wait_until="domcontentloaded")
            except Exception as nav_error:
                print(f"Navigation Error (continuing anyway): {nav_error}")

            # 5. Wait - Robust Waiting
            print("Waiting for content...")
            # Wait for network idle (good for SPAs)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except:
                pass  # Ignore timeout if network never idles

            # Add explicit wait time
            page.wait_for_timeout(request.wait_time * 1000 + 2000)  # Add 2s buffer

            # 6. Extract HTML
            content = page.content()
            browser.close()

            # 7. AI Processing
            print(f"Scrape successful. Content length: {len(content)}")

            clean_text = clean_html(content)
            print(f"DEBUG: Extracted Text Preview: {clean_text[:500]}")

            if request.query or request.prompt:
                print(f"Processing with Gemini... Query: {request.query}")
                data = extract_with_gemini(
                    clean_text, request.query or request.prompt, request.model_name
                )
                return {"status": "success", "url": request.url, "data": data}
            else:
                # Raw HTML mode
                return {
                    "status": "success",
                    "url": request.url,
                    "content_length": len(content),
                    "html": content,
                }

    except Exception as e:
        print(f"Browser Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
