#!/usr/bin/env python3
"""
Quick script to fix subscription for chisholm@crewxi.com
"""
import os
from supabase import create_client
from dotenv import load_dotenv

# Load environment variables
load_dotenv()
load_dotenv(".env.local", override=True)
load_dotenv("env.local", override=True)

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# User details
USER_EMAIL = "chisholm@crewxi.com"
SUBSCRIPTION_ID = "sub_1SX6r08nEz73sTkiWJADcN7i"
PRICE_ID = "price_1SWK4S8nEz73sTkiiWWP5tQ2"

# Plan limits mapping
PLAN_LIMITS = {
    "price_1SWK4S8nEz73sTkiiWWP5tQ2": 1000,   # Starter - $10/mo
    "price_1SWK6C8nEz73sTkimA2XyrU0": 5000,   # Pro - $30/mo
    "price_1SWK6p8nEz73sTkicVIwLUP7": 10000,  # Business - $50/mo
}

# Tier names mapping
TIER_NAMES = {
    "price_1SWK4S8nEz73sTkiiWWP5tQ2": "Starter",
    "price_1SWK6C8nEz73sTkimA2XyrU0": "Pro",
    "price_1SWK6p8nEz73sTkicVIwLUP7": "Business",
}

item_limit = PLAN_LIMITS.get(PRICE_ID, 1000)
tier_name = TIER_NAMES.get(PRICE_ID, "Starter")

print(f"🔧 Fixing subscription for: {USER_EMAIL}")
print(f"   Subscription ID: {SUBSCRIPTION_ID}")
print(f"   Price ID: {PRICE_ID}")
print(f"   Tier: {tier_name}")
print(f"   Limit: {item_limit}")
print()

# Update Supabase
result = supabase.table("profiles").update({
    "subscription_tier": tier_name,
    "subscription_status": "active",
    "subscription_id": SUBSCRIPTION_ID,
    "subscription_price_id": PRICE_ID,
    "items_limit": item_limit,
    # Keep items_used at 97 - don't reset it
}).eq("email", USER_EMAIL).execute()

print(f"✅ Profile updated successfully!")
print(f"   You now have: {item_limit - 97} items remaining ({item_limit} total)")
print()

# Verify the update
profile = supabase.table("profiles").select("*").eq("email", USER_EMAIL).execute()
if profile.data:
    p = profile.data[0]
    print(f"📊 Current profile status:")
    print(f"   Tier: {p['subscription_tier']}")
    print(f"   Status: {p['subscription_status']}")
    print(f"   Subscription ID: {p['subscription_id']}")
    print(f"   Items: {p['items_used']}/{p['items_limit']}")
    print(f"   One-time credits: {p['one_time_credits']}")

