"""payever API constants and status-to-Odoo-state mapping."""
SANDBOX_URL = 'https://proxy.staging.devpayever.com'
LIVE_URL = 'https://proxy.payever.org'

OAUTH_GRANT_TYPE = 'http://payever.org/api/payment'
OAUTH_SCOPE = 'API_CREATE_PAYMENT'

PAYEVER_TO_ODOO_STATUS = {
    'STATUS_NEW': 'pending',
    'STATUS_IN_PROCESS': 'pending',
    'STATUS_ACCEPTED': 'authorized',
    'STATUS_PAID': 'done',
    'STATUS_FAILED': 'cancel',
    'STATUS_DECLINED': 'cancel',
    'STATUS_CANCELLED': 'cancel',
    'STATUS_REFUNDED': 'done',
}
