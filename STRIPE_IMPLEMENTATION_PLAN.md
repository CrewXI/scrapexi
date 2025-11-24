# 💳 STRIPE + SUPABASE CREDIT SYSTEM IMPLEMENTATION PLAN

## 📊 **PRICING STRUCTURE**

### **Subscription Tiers (Monthly)**
- **Free**: 100 items/month - $0
- **Starter**: 1,000 items/month - $10/mo (Price ID: `price_1SWK4S8nEz73sTkiiWWP5tQ2`)
- **Pro**: 5,000 items/month - $30/mo (Price ID: `price_1SWK6C8nEz73sTkimA2XyrU0`)
- **Business**: 10,000 items/month - $50/mo (Price ID: `price_1SWK6p8nEz73sTkicVIwLUP7`)

### **One-Time Purchase**
- **5,000 items** - $40 (one-time) - **NEED TO CREATE PRICE ID**

---

## 🔑 **KEY REQUIREMENTS**

1. **Annual Reset**: All credits (subscription + one-time) reset to 0 on January 1st
2. **Monthly Renewal**: Subscription credits refresh each month (items_used → 0)
3. **Credit Stacking**: One-time credits stack on top of subscription credits
4. **Proper Deduction**: Deduct from subscription credits first, then one-time credits
5. **Idempotency**: Prevent duplicate credit additions from webhook retries
6. **Frontend Display**: Show subscription credits and one-time credits separately

---

## ❓ **QUESTIONS TO ANSWER BEFORE STARTING**

1. **One-time Price ID**: Do you have the Stripe Price ID for the $40/5000 items product, or should we create it?

2. **Credit Accumulation**: If user has Pro (5000) + buys one-time (5000), do they get 10,000 total?

3. **Mid-month Upgrades**: If user upgrades Starter→Pro mid-month, do they get full 5000 or prorated?

4. **Annual Reset**: On Jan 1st, should we:
   - Reset items_used to 0? ✅
   - Reset one_time_credits to 0? ✅
   - Keep items_limit based on active subscription? ✅

5. **Cancellation**: When user cancels subscription:
   - Keep credits until end of billing period? OR
   - Lose credits immediately?
   - Keep one-time credits after cancellation?

---

## 📋 **IMPLEMENTATION PHASES**

### **Phase 1: Database Schema** (5 tasks)
- Add `one_time_credits` column (integer, default 0)
- Add `subscription_renewal_date` column (timestamp)
- Add `last_credit_refresh_date` column (timestamp)
- Create `add_subscription_credits()` RPC function
- Create `add_onetime_credits()` RPC function

### **Phase 2: Credit Logic** (5 tasks)
- Update `check_data_usage()` to include one-time credits
- Update `increment_items_usage()` RPC for proper deduction order
- Create `payment_transactions` table for idempotency
- Update `PLAN_LIMITS` constant with all price IDs
- Create Stripe one-time payment Price ID

### **Phase 3: Webhook Handlers** (5 tasks)
- Rewrite subscription webhook handler
- Add one-time payment webhook handler
- Add cancellation webhook handler
- Add failed payment webhook handler
- Update frontend credit display

### **Phase 4: Checkout Flow** (5 tasks)
- Create subscription checkout endpoint
- Create one-time checkout endpoint
- Build pricing page (frontend/pricing.html)
- Add Upgrade button to dashboard
- Update Terms of Service with annual reset policy

### **Phase 5: Testing** (10 tasks)
- Install Stripe CLI
- Test webhooks locally
- Test subscription purchase flow
- Test one-time purchase flow
- Test credit deduction
- Test monthly renewal
- Test cancellation
- Test upgrade/downgrade
- Test credit limit enforcement
- Deploy to production

### **Phase 6: Polish** (5 tasks)
- Add success/cancel redirect pages
- Add Stripe customer portal
- Add webhook event logging
- Document annual reset procedure
- Add email notifications (future)

---

## 🎯 **TOTAL TASKS: 35**

See task list for detailed breakdown of each task.

---

## 🔧 **TECHNICAL ARCHITECTURE**

### **Credit Calculation Formula**
```
Total Available Credits = (items_limit - items_used) + one_time_credits
```

### **Credit Deduction Order**
1. Deduct from subscription credits first (`items_used++`)
2. When `items_used >= items_limit`, deduct from `one_time_credits`

### **Webhook Events to Handle**
- `checkout.session.completed` - New subscription OR one-time purchase
- `invoice.payment_succeeded` - Monthly renewal
- `customer.subscription.updated` - Plan upgrade/downgrade
- `customer.subscription.deleted` - Cancellation
- `invoice.payment_failed` - Failed payment

---

## 📁 **FILES TO MODIFY**

- `api/index.py` - Webhook handlers, credit logic, checkout endpoints
- `frontend/dashboard.html` - Credit display, upgrade button
- `frontend/pricing.html` - NEW - Pricing page
- `frontend/terms-of-service.html` - Annual reset policy
- `frontend/checkout-success.html` - NEW - Success page
- `frontend/checkout-cancel.html` - NEW - Cancel page
- Supabase: Add columns, create RPC functions, create tables

---

## 🚀 **NEXT STEPS**

1. Answer the 5 questions above
2. Create one-time payment Price ID in Stripe
3. Start with Phase 1 (Database Schema)
4. Work through tasks sequentially
5. Test thoroughly with Stripe CLI before production deployment

