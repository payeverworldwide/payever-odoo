# payever Payments for Odoo

Accept payments via payever directly in your Odoo 19.0 store — credit card, PayPal, BNPL, Santander Installments, iDEAL, Bancontact, Apple Pay, Google Pay and many more.

---

## Features

- **Redirect checkout** — customers are sent to the payever hosted checkout and redirected back after payment
- **All payever payment methods** — 23 methods bundled out-of-the-box; sync more at any time
- **Logo sync** — download payment method logos from your payever account with one click
- **Refunds** — full and partial refunds from Accounting → Payments
- **Manual capture** — authorize now, capture (ship goods) later
- **Void / cancel** — cancel authorised-but-not-yet-captured transactions
- **Webhook notifications** — asynchronous status updates with optional HMAC-SHA256 signature verification
- **Sandbox & live mode** — switch between staging and production environments

---

## Requirements

- Odoo **19.0**
- Python package: `requests` (see `requirements.txt`)
- A [payever](https://www.payever.org/) merchant account

---

## Installation

### Via Odoo Apps (recommended)

Install the module from the Odoo Apps store.

### Manual

1. Copy the `payment_payever` folder into your Odoo add-ons directory.
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Restart Odoo and update the app list (**Settings → Apps → Update App List**).
4. Install **payever Payments** from the Apps list.

---

## Configuration

1. Go to **Accounting → Configuration → Payment Providers** (or **Website → Configuration → Payment Providers**).
2. Open the **payever** provider.
3. In the **Credentials** tab fill in:
   - **Client ID** — from your payever account under *Connect → Shopsystems → Odoo*
   - **Client Secret** — from the same page
   - **Business UUID** — required for the payment method sync
4. Set the **State** to **Test** (sandbox) or **Enabled** (live).
5. Optionally set **Minimum Amount** and **Maximum Amount** limits.
6. Click **Sync Payment Methods from payever** to fetch available methods and their logos.
7. Enable the payment methods you want to offer and **Save**.

### Manual Capture

To use manual capture (authorize now, capture when you ship):

1. In your **payever business account**, enable delayed/manual capture for the relevant payment methods.
2. In Odoo, tick **Capture Amount Manually** on the payever provider form.
3. After a customer pays, the transaction will appear in **Authorized** state.
4. Go to the transaction record and click **Capture Transaction** to capture the payment.

> **Note:** payever controls capture behaviour at the account level. Enabling manual capture in Odoo alone is not sufficient — the setting must also be configured in your payever account.

### Refunds

1. Go to **Accounting → Customers → Payments**.
2. Open the payment you want to refund.
3. Click **Refund** and enter the amount.

---

## Callback URLs

The module registers the following public routes — no additional server configuration is needed:

| Route | Purpose |
|---|---|
| `POST /payment/payever/notification` | Asynchronous webhook from payever |
| `GET /payment/payever/return` | Customer redirect on success |
| `GET /payment/payever/failure` | Customer redirect on failure |
| `GET /payment/payever/cancel` | Customer redirect on cancel |
| `GET /payment/payever/pending` | Customer redirect when payment is pending |

---

## Payment Status Mapping

| payever status | Odoo state |
|---|---|
| `STATUS_NEW` | Pending |
| `STATUS_IN_PROCESS` | Pending (or Authorized when manual capture is enabled) |
| `STATUS_ACCEPTED` | Authorized |
| `STATUS_PAID` | Done (captured) |
| `STATUS_REFUNDED` | Done |
| `STATUS_FAILED` | Cancelled |
| `STATUS_DECLINED` | Cancelled |
| `STATUS_CANCELLED` | Cancelled |

---

## Development

```bash
# Run linting
pip install pylint pylint-odoo
pylint --load-plugins pylint_odoo payment_payever/

# Run with Docker (development only)
docker compose up -d
```

---

## License

LGPL-3 — see [LICENSE](LICENSE).
