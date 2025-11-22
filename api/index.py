
import asyncio
import json
import os
import random
import re
import uuid
from typing import Any, Dict, Optional

import stripe
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# import google.generativeai as genai
from fastapi import BackgroundTasks, Body, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import Page, sync_playwright
from pydantic import BaseModel
from supabase import Client, create_client

# Load environment variables immediately, before other imports might use them
load_dotenv()
load_dotenv(".env.local", override=True)
load_dotenv("env.local", override=True)

# Init Stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

# Init Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_ANON_KEY")
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    print(f"WARNING: Failed to init Supabase in backend: {e}")
    supabase = None

# VERCEL PLAYWRIGHT FIX (Keeping this for backup, but we want Remote mainly)
if os.getenv("VERCEL"):
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "/tmp/pw-browsers"


def ensure_browser_installed():
    # Skip this locally if not needed, or keep as fallback
    return None

# Plan Limits (MB)
PLAN_LIMITS = {
    "price_1SWK4S8nEz73sTkiiWWP5tQ2": 10.0,
    "price_1SWK6C8nEz73sTkimA2XyrU0": 50.0,
    "price_1SWK6p8nEz73sTkicVIwLUP7": 100.0,
}

# Stealth Logic (Optional dependency)
try:
    from playwright_stealth import stealth_sync

    STEALTH_AVAILABLE = True
except ImportError:
    STEALTH_AVAILABLE = False
    print("DEBUG: playwright-stealth not found. Stealth mode disabled.")

# Import our local replacement service
try:
    from .local_agentql_service import find_next_page_element, query_data_with_gemini
except ImportError:
    from local_agentql_service import find_next_page_element, query_data_with_gemini

app = FastAPI(title="DIY AgentQL Scraper API")

# Enable CORS so our frontend can talk to it
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount frontend static files FIRST, but exclude /api paths (which are defined below)
app.mount("/static", StaticFiles(directory="frontend"), name="static")

active_jobs = {}


class ScrapeRequest(BaseModel):
    url: str
    query: Optional[str] = None
    prompt: Optional[str] = None
    wait_time: int = 2
    use_local_backend: bool = True
    model_name: str = "gemini-2.0-flash-exp"
    pagination_enabled: bool = False
    max_pages: int = 3
    login_enabled: bool = False
    login_url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    session_json: Optional[Dict[str, Any]] = None
    stealth_mode: bool = False
    user_id: Optional[str] = None


class ScrapeResponse(BaseModel):
    job_id: str
    status: str


class JobStatusResponse(BaseModel):
    status: str
    data: Optional[Dict[str, Any]] = None
    message: Optional[str] = None
    pages_scraped: int = 0
    error: Optional[str] = None


@app.get("/config")
def get_config():
    return {
        "google_client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "supabase_url": os.getenv("SUPABASE_URL"),
        "supabase_anon_key": os.getenv(
            "SUPABASE_ANON_KEY"
        ),  # Fixed key name to match frontend expectation
    }


def check_data_usage(user_id: str):
    if not user_id or not supabase:
        return
    try:
        response = (
            supabase.table("profiles")
            .select("data_usage_mb_limit, data_usage_mb_used")
            .eq("id", user_id)
            .single()
            .execute()
        )
        if response.data:
            limit = float(response.data.get("data_usage_mb_limit") or 10.0)
            used = float(response.data.get("data_usage_mb_used") or 0.0)
            if used >= limit:
                raise Exception(
                    f"Data usage limit reached ({used:.2f}/{limit:.2f} MB). Please upgrade your plan."
                )
    except Exception as e:
        print(f"Error checking usage: {e}")
        if "limit reached" in str(e):
            raise e


def update_data_usage(user_id: str, amount_mb: float):
    if not user_id or not supabase:
        return
    try:
        supabase.rpc(
            "increment_data_usage", {"p_user_id": user_id, "p_amount_mb": amount_mb}
        ).execute()
        print(f"Updated usage for {user_id}: +{amount_mb:.4f} MB")
    except Exception as e:
        print(f"Failed to update usage: {e}")


def run_scrape_task(job_id: str, request: ScrapeRequest):
    print(f"DEBUG: Starting Job {job_id} for {request.url}")
    active_jobs[job_id] = {"status": "running", "data": None, "pages_scraped": 0}

    # ENTERPRISE MODE: Delegate to External Browser Service if configured
    browser_service_url = os.getenv("BROWSER_SERVICE_URL")
    
    # Debug Print: CRITICAL to see what Vercel sees
    print(f"DEBUG: BROWSER_SERVICE_URL is set to: '{browser_service_url}'")

    if browser_service_url:
        print(f"DEBUG: Offloading to Browser Service at {browser_service_url}")
        try:
            import requests
            # Forward the request to the microservice
            # Ensure we hit the /scrape endpoint
            target_url = f"{browser_service_url.rstrip('/')}/scrape"
            
            print(f"DEBUG: POSTing to {target_url}")
            
            resp = requests.post(
                target_url,
                json={
                    "url": request.url,
                    "wait_time": request.wait_time,
                    "stealth_mode": request.stealth_mode,
                    "session_json": request.session_json
                },
                timeout=120 # Long timeout for scraping
            )
            resp.raise_for_status()
            result = resp.json()
            
            # Simple Pass-through for now
            active_jobs[job_id]["status"] = "completed"
            active_jobs[job_id]["data"] = {"raw_html_preview": str(result.get("html"))[:500] + "..."} # Truncate for preview
            active_jobs[job_id]["message"] = "Remote Scrape Complete"
            return
            
        except Exception as e:
            print(f"CRITICAL ERROR: Remote Browser Failed: {e}")
            # DO NOT FALLBACK. Show the real error.
            active_jobs[job_id]["status"] = "failed"
            active_jobs[job_id]["error"] = f"Remote Service Configured but Failed: {str(e)}"
            return

    print("WARNING: No BROWSER_SERVICE_URL found. Attempting local browser launch (likely to fail on Vercel)...")
    
    try:
        # Sync Check usage (double check inside task)
        if request.user_id:
            try:
                check_data_usage(request.user_id)
            except Exception as e:
                raise e

        with sync_playwright() as playwright:
            if active_jobs[job_id].get("status") == "cancelled":
                return

            print("DEBUG: Launching browser...")
            # Stealth arguments to mimic real Chrome
            browser_args = []
            if request.stealth_mode:
                browser_args = [
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                    "--no-sandbox",
                ]
            
            launch_options = {
                "headless": True,
                "args": browser_args
            }
            
            browser = playwright.chromium.launch(**launch_options)

            context_args = {
                "ignore_https_errors": True,
                "user_agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    if request.stealth_mode
                    else None
                ),
            }
            if request.session_json:
                print("DEBUG: Loading session state...")
                context_args["storage_state"] = request.session_json

            context = browser.new_context(**context_args)
            page = context.new_page()
            
            # OPTIMIZATION: Block heavy resources (Images, Fonts, CSS)
            def block_heavy_resources(route):
                if route.request.resource_type in ["image", "media", "font", "stylesheet"]:
                    route.abort()
                else:
                    route.continue_()
            
            page.route("**/*", block_heavy_resources)

            if request.stealth_mode and STEALTH_AVAILABLE:
                print("DEBUG: Applying stealth patches...")
                stealth_sync(page)

            # --- LOGIN LOGIC ---
            if request.login_enabled and request.login_url and not request.session_json:
                print(f"DEBUG: Login required. Navigating to {request.login_url}...")
                page.goto(request.login_url)

                # Random sleep for stealth
                if request.stealth_mode:
                    page.wait_for_timeout(random.randint(1000, 3000))

                page.wait_for_load_state("networkidle")

                if request.username and request.password:
                    try:
                        if request.stealth_mode:
                            page.wait_for_timeout(random.randint(500, 1500))
                        page.fill(
                            'input[type="email"], input[name="email"], input[name="username"]',
                            request.username,
                        )

                        if request.stealth_mode:
                            page.wait_for_timeout(random.randint(500, 1500))
                        page.fill(
                            'input[type="password"], input[name="password"]', request.password
                        )

                        if request.stealth_mode:
                            page.wait_for_timeout(random.randint(500, 1500))
                        page.click(
                            'button[type="submit"], input[type="submit"], button:has-text("Log in"), button:has-text("Sign in")'
                        )

                        page.wait_for_load_state("networkidle")
                        page.wait_for_timeout(3000)
                    except Exception as e:
                        print(f"DEBUG: Auto-login failed: {e}")

            print(f"DEBUG: Navigating to {request.url}...")
            page.goto(request.url)
            page.wait_for_load_state("domcontentloaded")

            # Dynamic Wait Time (Base + Random Jitter if stealth)
            wait_ms = request.wait_time * 1000
            if request.stealth_mode:
                wait_ms += random.randint(1000, 4000)

            print(f"DEBUG: Waiting {wait_ms}ms...")
            page.wait_for_timeout(wait_ms)

            result_data = None
            message = "Success"
            pages_scraped = 0

            # --- PAGINATION ---
            if request.pagination_enabled and request.use_local_backend:
                aggregated_results = {}
                for i in range(request.max_pages):
                    if active_jobs[job_id].get("status") == "cancelled":
                        browser.close()
                        return

                    print(f"DEBUG: Scraping Page {i+1}...")
                    page_data = query_data_with_gemini(page, request.query, request.model_name)

                    if not aggregated_results:
                        aggregated_results = page_data
                    else:
                        for key, value in page_data.items():
                            if isinstance(value, list) and key in aggregated_results:
                                aggregated_results[key].extend(value)
                            elif key not in aggregated_results:
                                aggregated_results[key] = value

                    pages_scraped += 1

                    if i < request.max_pages - 1:
                        next_selector = find_next_page_element(page, request.model_name)
                        if next_selector:
                            try:
                                next_btn = page.query_selector(next_selector)
                                if next_btn:
                                    if request.stealth_mode:
                                        page.wait_for_timeout(random.randint(1000, 3000))
                                    next_btn.click()
                                    page.wait_for_load_state("networkidle", timeout=10000)

                                    # Pagination Wait
                                    page_wait = request.wait_time * 1000
                                    if request.stealth_mode:
                                        page_wait += random.randint(1000, 3000)
                                    page.wait_for_timeout(page_wait)
                                else:
                                    break
                            except:
                                break
                        else:
                            break

                result_data = aggregated_results
                message = f"Extraction Complete"

            elif request.use_local_backend:
                if active_jobs[job_id].get("status") == "cancelled":
                    browser.close()
                    return

                result_data = query_data_with_gemini(page, request.query, request.model_name)
                message = f"Extraction Complete"
                pages_scraped = 1

            browser.close()

            if active_jobs[job_id].get("status") != "cancelled":
                # Calculate Size
                size_mb = 0.0
                if result_data:
                    json_str = json.dumps(result_data)
                    size_bytes = len(json_str.encode("utf-8"))
                    size_mb = size_bytes / (1024 * 1024)

                if request.user_id:
                    update_data_usage(request.user_id, size_mb)

                active_jobs[job_id]["status"] = "completed"
                active_jobs[job_id]["data"] = result_data
                active_jobs[job_id]["message"] = f"{message} (Size: {size_mb:.4f} MB)"
                active_jobs[job_id]["pages_scraped"] = pages_scraped

    except Exception as e:
        print(f"ERROR in job {job_id}: {e}")
        active_jobs[job_id]["status"] = "failed"
        active_jobs[job_id]["error"] = str(e)


@app.post("/scrape", response_model=ScrapeResponse)
def scrape_endpoint(request: ScrapeRequest, background_tasks: BackgroundTasks):
    # Sync Check usage
    if request.user_id:
        try:
            check_data_usage(request.user_id)
        except Exception as e:
            # Return 402 Payment Required for quota exceeded
            raise HTTPException(status_code=402, detail=str(e))

    job_id = str(uuid.uuid4())
    background_tasks.add_task(run_scrape_task, job_id, request)
    return ScrapeResponse(job_id=job_id, status="queued")


@app.get("/job/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str):
    if job_id not in active_jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = active_jobs[job_id]
    return JobStatusResponse(
        status=job["status"],
        data=job.get("data"),
        message=job.get("message"),
        pages_scraped=job.get("pages_scraped", 0),
        error=job.get("error"),
    )


@app.post("/job/{job_id}/cancel")
def cancel_job(job_id: str):
    if job_id in active_jobs:
        active_jobs[job_id]["status"] = "cancelled"
        return {"message": "Job cancellation requested"}
    raise HTTPException(status_code=404, detail="Job not found")


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError as e:
        raise HTTPException(status_code=400, detail="Invalid signature")

    event_type = event["type"]
    data = event["data"]["object"]

    print(f"Received Stripe Webhook: {event_type}")

    if event_type in ["checkout.session.completed", "invoice.payment_succeeded"]:
        # Update Subscription
        # Get customer email
        customer_email = data.get("customer_email") or data.get("customer_details", {}).get("email")

        # If invoice payment, fetch customer email from customer ID if not present
        if not customer_email and "customer" in data:
            try:
                cust = stripe.Customer.retrieve(data["customer"])
                customer_email = cust.email
            except:
                pass

        if customer_email and supabase:
            # Get Subscription ID
            sub_id = data.get("subscription")
            if sub_id:
                # Retrieve Subscription to get Price ID
                try:
                    sub = stripe.Subscription.retrieve(sub_id)
                    if sub["items"]["data"]:
                        price_id = sub["items"]["data"][0]["price"]["id"]
                        limit_mb = PLAN_LIMITS.get(price_id, 10.0)  # Default to 10MB

                        # Update Supabase
                        supabase.rpc(
                            "update_subscription",
                            {
                                "p_email": customer_email,
                                "p_stripe_sub_id": sub_id,
                                "p_price_id": price_id,
                                "p_limit_mb": limit_mb,
                            },
                        ).execute()
                        print(
                            f"Updated subscription for {customer_email}: {price_id} -> {limit_mb}MB"
                        )
                except Exception as e:
                    print(f"Error updating subscription: {e}")

    return {"status": "success"}


# Serve Frontend (SPA)
@app.get("/")
async def read_index():
    return FileResponse("frontend/index.html")


@app.get("/dashboard")
async def read_dashboard():
    return FileResponse("frontend/dashboard.html")


@app.get("/dashboard.html")
async def read_dashboard_html():
    return FileResponse("frontend/dashboard.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
