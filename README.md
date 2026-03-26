# payever Odoo Integration

Odoo 19 payment provider module for [payever](https://www.payever.org/) — accepts
PayPal, credit card, BNPL, Santander installments, Sofort, iDEAL and 30+ more
payment options via the [payever REST API v3](https://docs.payever.org/api/payments/v3).

---

## Modules

| Module | Description |
|--------|-------------|
| `payment_payever` | Core eCommerce payment provider (redirect checkout flow) |

---

## Features

- **OAuth 2.0 authentication** — tokens are fetched automatically and cached with auto-refresh
- **Redirect checkout** — customers are sent to the payever hosted checkout page
- **Notification handling** — async `POST` webhook updates transaction state
- **Refunds** — partial and full refunds via `POST /api/payment/refund/{id}`
- **Captures** — shipping-goods capture via `POST /api/payment/shipping-goods/{id}`
- **Cancellations** — void/cancel via `POST /api/payment/cancel/{id}`
- **Signature verification** — optional `x-payever-signature` HMAC-SHA256 check
- **Payment method sync** — one-click button to fetch live payment options from your account
- **20+ bundled payment methods** — PayPal, Credit Card, Santander Installments, iDEAL, Bancontact, Apple Pay, etc.

---

## Installation

1. Copy the `payment_payever` directory into your Odoo `addons` path.
2. Update your app list and install **payever Payments**.
3. Navigate to **Accounting → Configuration → Payment Providers**.
4. Open the **payever** provider record and fill in:
   - **Client ID** — 32-character hex from your payever account
   - **Client Secret** — 256-bit secret
   - **Business UUID** *(optional)* — needed for listing payment options
5. Set the provider **State** to *Test* (sandbox) or *Enabled* (live).
6. Click **Sync Payment Methods from payever** to fetch your enabled options.

### API URLs

| Environment | Base URL |
|-------------|----------|
| Sandbox     | `https://proxy.staging.devpayever.com` |
| Live        | `https://proxy.payever.org` |

### Credentials

- **Live**: Generate API keys in your payever account under **Connect → Shopsystems → API**.
- **Test**: Use the staging credentials from [https://docs.payever.org/resources/test-credentials](https://docs.payever.org/resources/test-credentials).

---

## Payment Flow

```
Customer → Odoo Checkout
    → POST /api/v3/payment   (create payment, get redirect_url)
    → Redirect to payever checkout
    → Customer completes payment
    → payever POSTs notification to /payment/payever/notification
    → Odoo updates transaction state
    → Customer redirected to /payment/payever/return (or failure/cancel/pending)
```

### Callback URLs registered with payever

| Purpose | Odoo URL |
|---------|----------|
| Success redirect | `/payment/payever/return?ref=REF&payment_id=--PAYMENT-ID--` |
| Failure redirect | `/payment/payever/failure?ref=REF&payment_id=--PAYMENT-ID--` |
| Cancel redirect  | `/payment/payever/cancel?ref=REF&payment_id=--PAYMENT-ID--` |
| Pending redirect | `/payment/payever/pending?ref=REF&payment_id=--PAYMENT-ID--` |
| Notification (webhook) | `/payment/payever/notification?ref=REF&payment_id=--PAYMENT-ID--` |

payever substitutes `--PAYMENT-ID--` with the real payment ID at runtime.

---

## Status Mapping

| payever Status | Odoo State |
|---------------|-----------|
| `STATUS_NEW` | pending |
| `STATUS_IN_PROCESS` | pending |
| `STATUS_ACCEPTED` | authorized |
| `STATUS_PAID` | done |
| `STATUS_FAILED` | cancel |
| `STATUS_DECLINED` | cancel |
| `STATUS_CANCELLED` | cancel |
| `STATUS_REFUNDED` | done |

---

## Requirements

- Odoo 19.0
- Python `requests` library (standard Odoo dependency)
