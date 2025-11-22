
import os
import json
import random
import asyncio
from typing import Any, Dict, Optional
from fastapi import Body, FastAPI, HTTPException
from playwright.sync_api import sync_playwright
from pydantic import BaseModel

# Stealth Import Logic
try:
    # Try the standard import
    from playwright_stealth import stealth_sync

    STEALTH_AVAILABLE = True
except ImportError:
    STEALTH_AVAILABLE = False
    print("WARNING: playwright-stealth not found or import failed. Stealth mode disabled.")

app = FastAPI(title="Dedicated Browser Service")


class ScrapeRequest(BaseModel):
    url: str
    wait_time: int = 2
    stealth_mode: bool = True
    session_json: Optional[Dict[str, Any]] = None
    # Add other fields as needed


@app.get("/")
def health_check():
    return {"status": "ok", "service": "browser-microservice", "version": "1.0.2"}


@app.post("/scrape")
def scrape(request: ScrapeRequest):
    print(f"Received scrape request for: {request.url}")

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
                "--ignore-certificate-errors", # Add ignore cert errors flag
            ]

            browser = p.chromium.launch(headless=True, args=browser_args)

            # 2. Context & Page
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                ignore_https_errors=True # Explicitly ignore HTTPS errors
            )

            if request.session_json:
                context = browser.new_context(storage_state=request.session_json, ignore_https_errors=True)

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

            # 5. Wait
            print("Waiting for content...")
            page.wait_for_timeout(request.wait_time * 1000)

            # 6. Extract (Basic HTML for now, or you can integrate Gemini here too)
            content = page.content()
            
            # Log success
            print(f"Scrape successful. Content length: {len(content)}")

            # Capture snapshot for debug/virtual view (optional)
            # screenshot = page.screenshot(type='jpeg', quality=50)

            browser.close()

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
