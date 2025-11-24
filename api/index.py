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
# Prefer Service Role Key for Backend to bypass RLS for logging/billing
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")

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
    model_name: str = "gemini-2.5-flash-lite"
    pagination_enabled: bool = False
    max_pages: int = 3
    page2_url: Optional[str] = None
    page3_url: Optional[str] = None
    login_enabled: bool = False
    login_url: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    session_json: Optional[Any] = None
    stealth_mode: bool = False
    user_id: Optional[str] = None


class ScrapeResponse(BaseModel):
    job_id: str
    status: str


class JobStatusResponse(BaseModel):
    status: str
    data: Optional[Any] = None  # Can be Dict, List, or any JSON-serializable data
    message: Optional[str] = None
    pages_scraped: int = 0
    error: Optional[str] = None
    config: Optional[Dict[str, Any]] = None  # Add config to response


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
    """Check if user has exceeded their item/contact limit"""
    if not user_id or not supabase:
        return
    try:
        response = (
            supabase.table("profiles")
            .select("items_limit, items_used")
            .eq("id", user_id)
            .single()
            .execute()
        )
        if response.data:
            # Default limits by plan: Free=100, Starter=1500, Pro=4000, Business=100000
            limit = int(response.data.get("items_limit") or 100)
            used = int(response.data.get("items_used") or 0)
            if used >= limit:
                raise Exception(
                    f"Item limit reached ({used:,}/{limit:,} contacts/leads). Please upgrade your plan."
                )
    except Exception as e:
        print(f"Error checking usage: {e}")
        if "limit reached" in str(e):
            raise e


def update_data_usage(user_id: str, item_count: int):
    """Update the number of items/contacts scraped for this user"""
    if not user_id or not supabase:
        return
    try:
        supabase.rpc(
            "increment_items_usage", {"p_user_id": user_id, "p_item_count": item_count}
        ).execute()
        print(f"Updated usage for {user_id}: +{item_count} items")
    except Exception as e:
        print(f"Failed to update usage: {e}")


def run_scrape_task(job_id: str, request: ScrapeRequest):
    print(f"DEBUG: Starting Job {job_id} for {request.url}")

    # Normalize session_json if it's a list (cookie array) -> Dict (storage_state)
    if request.session_json:
        if isinstance(request.session_json, list):
            print("DEBUG: detected list for session_json, wrapping in {'cookies': ...}")
            request.session_json = {"cookies": request.session_json}

        # Sanitize Cookies (Fix SameSite casing) for local execution
        if "cookies" in request.session_json:
            for cookie in request.session_json["cookies"]:
                if "sameSite" in cookie:
                    val = cookie["sameSite"]
                    if val in ["no_restriction", "unspecified"]:
                        cookie["sameSite"] = "None"
                    elif val.lower() == "lax":
                        cookie["sameSite"] = "Lax"
                    elif val.lower() == "strict":
                        cookie["sameSite"] = "Strict"
                    elif val.lower() == "none":
                        cookie["sameSite"] = "None"
                    elif val not in ["Strict", "Lax", "None"]:
                        del cookie["sameSite"]

    # Maintain local cache for speed/debugging, but DB is source of truth
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
                    "query": request.query,  # Pass query to backend
                    "prompt": request.prompt,  # Pass prompt to backend
                    "model_name": request.model_name,  # Pass model name
                    "wait_time": request.wait_time,
                    "stealth_mode": request.stealth_mode,
                    "pagination_enabled": request.pagination_enabled,
                    "start_page": 1,  # Frontend sends max_pages, convert to start/end
                    "end_page": request.max_pages if request.pagination_enabled else 1,
                    "page2_url": request.page2_url,
                    "page3_url": request.page3_url,
                    "session_json": request.session_json,
                },
                timeout=120,  # Long timeout for scraping
            )
            resp.raise_for_status()
            result = resp.json()

            # Handle Smart vs Raw response (with pagination support)
            final_data = None
            message = "Remote Scrape Complete"
            pages_scraped = 1

            if "data" in result:
                # AI Extraction Success (could be paginated or single page)
                data_result = result["data"]

                # Check if this is paginated data (has 'pages', 'all', 'pagination')
                if isinstance(data_result, dict) and "all" in data_result:
                    # Paginated result
                    final_data = data_result  # Store full structure with pages
                    pages_scraped = data_result.get("pagination", {}).get("total_pages", 1)
                    message = f"AI Extraction Complete ({pages_scraped} pages)"
                else:
                    # Single page result
                    final_data = data_result
                    message = "AI Extraction Complete"
            elif "html" in result:
                # Raw HTML fallback
                final_data = {"raw_html_preview": str(result["html"])[:500] + "..."}
                message = "Raw HTML Extracted"

            # Count items (rows/contacts/leads)
            item_count = 0
            if final_data:
                if isinstance(final_data, list):
                    item_count = len(final_data)
                elif isinstance(final_data, dict):
                    # Check for 'all' array (paginated results)
                    if "all" in final_data and isinstance(final_data["all"], list):
                        item_count = len(final_data["all"])
                    else:
                        # Find the first array in the dict
                        for value in final_data.values():
                            if isinstance(value, list):
                                item_count = len(value)
                                break

            if request.user_id and item_count > 0:
                update_data_usage(request.user_id, item_count)

            # Update Job Status in DB (Completion)
            try:
                print(f"✅ Remote job completed - {item_count} items from {pages_scraped} pages")
                supabase.table("jobs").update(
                    {
                        "status": "completed",
                        "item_count": item_count,
                        "completed_at": "now()",
                        "data": final_data,
                        "pages_scraped": pages_scraped,
                    }
                ).eq("id", job_id).execute()
            except Exception as e:
                print(f"Failed to update job history: {e}")

            return

        except Exception as e:
            print(f"CRITICAL ERROR: Remote Browser Failed: {e}")

            # Update DB with Failure
            try:
                supabase.table("jobs").update(
                    {
                        "status": "failed",
                        "error": f"Remote Service Failed: {str(e)}",
                        "completed_at": "now()",
                    }
                ).eq("id", job_id).execute()
            except Exception as log_err:
                print(f"Failed to log job failure: {log_err}")

            return

    print(
        "WARNING: No BROWSER_SERVICE_URL found. Attempting local browser launch (likely to fail on Vercel)..."
    )

    try:
        # Sync Check usage (double check inside task)
        if request.user_id:
            try:
                check_data_usage(request.user_id)
            except Exception as e:
                raise e

        with sync_playwright() as playwright:
            # Check DB for cancellation (optional but good)
            # ...

            print("DEBUG: Launching browser...")
            # Stealth arguments to mimic real Chrome
            browser_args = []
            if request.stealth_mode:
                browser_args = [
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                    "--no-sandbox",
                ]

            launch_options = {"headless": True, "args": browser_args}

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
                    # Basic Check
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
                # Count items (rows/contacts/leads)
                item_count = 0
                if result_data:
                    if isinstance(result_data, list):
                        item_count = len(result_data)
                    elif isinstance(result_data, dict):
                        # Find the first array in the dict (e.g., {"products": [...], "jobs": [...]})
                        for value in result_data.values():
                            if isinstance(value, list):
                                item_count = len(value)
                                break

                if request.user_id and item_count > 0:
                    update_data_usage(request.user_id, item_count)

                # Update DB Completion
                try:
                    print(f"✅ Job {job_id} COMPLETED - {item_count} items extracted")
                    supabase.table("jobs").update(
                        {
                            "status": "completed",
                            "item_count": item_count,
                            "completed_at": "now()",
                            "data": result_data,
                            "pages_scraped": pages_scraped,
                        }
                    ).eq("id", job_id).execute()
                    print(f"✅ Job {job_id} database updated successfully")
                except Exception as e:
                    print(f"❌ Failed to update job success: {e}")

                active_jobs[job_id]["status"] = "completed"
                active_jobs[job_id]["data"] = result_data
                print(f"✅ Job {job_id} marked as completed in active_jobs")

    except Exception as e:
        print(f"❌ ERROR in job {job_id}: {e}")
        import traceback

        print(f"❌ Traceback: {traceback.format_exc()}")
        # Update DB Failure
        try:
            supabase.table("jobs").update(
                {
                    "status": "failed",
                    "error": str(e),
                    "completed_at": "now()",
                }
            ).eq("id", job_id).execute()
            print(f"❌ Job {job_id} marked as failed in database")
        except Exception as log_err:
            print(f"❌ Failed to update job failure: {log_err}")


@app.post("/scrape", response_model=ScrapeResponse)
def scrape_endpoint(request: ScrapeRequest, background_tasks: BackgroundTasks):
    # Validate pagination (max 10 pages)
    MAX_PAGES = 10
    if request.pagination_enabled and request.max_pages > MAX_PAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {MAX_PAGES} pages per search allowed. Please adjust your range.",
        )

    # Sync Check usage
    if request.user_id:
        try:
            check_data_usage(request.user_id)
        except Exception as e:
            # Return 402 Payment Required for quota exceeded
            raise HTTPException(status_code=402, detail=str(e))

    job_id = str(uuid.uuid4())

    # INITIAL INSERT: Create "running" job in DB immediately
    try:
        # Store full configuration in the job record
        job_config = {
            "pagination": request.pagination_enabled,
            "maxPages": request.max_pages,  # Frontend uses maxPages, backend uses max_pages
            "stealth": request.stealth_mode,
            "auth": (
                "cookie" if request.session_json else ("creds" if request.login_enabled else "none")
            ),
            "loginEnabled": request.login_enabled,
            "sessionEnabled": bool(request.session_json),
            "username": request.username,
            # Do not store password in plain text config if possible, but for history restoration it might be expected?
            # For security, let's NOT store the password in the config column.
            # The user will have to re-enter it if they reload history.
            "waitTime": request.wait_time,
            # "startPage": 1, # Backend doesn't receive start/end page distinct from max_pages logic yet?
            # Actually frontend sends max_pages calculated from start/end.
            # If we want to restore exactly start/end, we might need to accept them in request.
        }

        supabase.table("jobs").insert(
            {
                "id": job_id,
                "user_id": request.user_id,
                "url": request.url,
                "query": request.query,
                "status": "running",
                "created_at": "now()",
                "config": job_config,  # Save config
            }
        ).execute()
    except Exception as e:
        print(f"Failed to initialize job in DB: {e}")
        raise HTTPException(
            status_code=500, detail="Failed to initialize job. Database unavailable."
        )

    background_tasks.add_task(run_scrape_task, job_id, request)
    print(f"🚀 Job {job_id} queued and starting in background")
    return ScrapeResponse(job_id=job_id, status="queued")


@app.get("/job/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str):
    # 1. Check Database (Source of Truth)
    try:
        response = supabase.table("jobs").select("*").eq("id", job_id).single().execute()
        if response.data:
            job = response.data
            return JobStatusResponse(
                status=job["status"],
                data=job.get("data"),
                message=f"Status: {job['status']}",
                pages_scraped=job.get("pages_scraped", 0),
                error=job.get("error"),
                config=job.get("config"),
            )
    except Exception as e:
        # Only print if it's a real error, not just not found
        if "Results contain 0 rows" not in str(e):
            print(f"DB Fetch Error for {job_id}: {e}")

    # 2. Check In-Memory (Fallback for local dev without DB sync)
    if job_id in active_jobs:
        job = active_jobs[job_id]
        return JobStatusResponse(
            status=job["status"],
            data=job.get("data"),
            message=job.get("message"),
            pages_scraped=job.get("pages_scraped", 0),
            error=job.get("error"),
        )

    raise HTTPException(status_code=404, detail="Job not found")


@app.post("/job/{job_id}/cancel")
def cancel_job(job_id: str):
    # Update DB
    try:
        supabase.table("jobs").update({"status": "cancelled"}).eq("id", job_id).execute()
    except Exception as e:
        print(f"Cancel DB Update Failed: {e}")

    # Update Memory
    if job_id in active_jobs:
        active_jobs[job_id]["status"] = "cancelled"
        return {"message": "Job cancellation requested"}

    # If we updated DB, return success even if not in memory
    return {"message": "Job cancellation requested via DB"}


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


@app.get("/ScrapeXiLogo1.jpg")
async def read_logo():
    return FileResponse("frontend/ScrapeXiLogo1.jpg")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
