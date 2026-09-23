"""payever API constants and status-to-Odoo-state mapping."""

# Technical name of this module, resolved at runtime so that XML IDs and asset
# lookups keep working when the addon directory is named differently.
MODULE_NAME = (
    __name__.split('.')[2] if __name__.startswith('odoo.addons.')
    else 'payment_payever_official'
)

SANDBOX_URL = 'https://proxy.staging.devpayever.com'
LIVE_URL = 'https://proxy.payever.org'

OAUTH_GRANT_TYPE = 'http://payever.org/api/payment'
OAUTH_SCOPE = 'API_CREATE_PAYMENT'

# Activated when the payever provider is enabled. Additional methods are
# created/linked by action_sync_payever_methods.
DEFAULT_PAYMENT_METHOD_CODES = [
    'payever',
]

# payever reports the paid amount and currency under different keys depending on
# the endpoint and API version; the first key found is used for verification.
AMOUNT_KEYS = ('total', 'amount', 'total_amount')
CURRENCY_KEYS = ('currency', 'currency_code')

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

# Only an explicit decline hides the method for the rest of the checkout
# session. Failed and cancelled methods remain available for another attempt.
DECLINED_STATUSES = ('STATUS_DECLINED',)
DECLINED_METHODS_SESSION_KEY = 'payever_declined_method_ids'
PAYMENT_RESULT_SESSION_KEY = 'payever_payment_result'
