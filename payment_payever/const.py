# -*- coding: utf-8 -*-

SANDBOX_URL = 'https://proxy.staging.devpayever.com'
LIVE_URL = 'https://proxy.payever.org'

OAUTH_GRANT_TYPE = 'http://payever.org/api/payment'
OAUTH_SCOPE = 'API_CREATE_PAYMENT'

# Payever transaction status → Odoo transaction state
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

# Payment methods supported by payever (method_code: display_name)
PAYMENT_METHODS = {
    'paypal': 'PayPal',
    'credit_card': 'Credit Card',
    'direct_debit': 'Direct Debit',
    'instant_payment': 'Instant Payment',
    'sofort': 'SOFORT Banking',
    'santander_installment': 'Santander Installments DE',
    'santander_installment_at': 'Santander Installments AT',
    'santander_installment_no': 'Santander Installments NO',
    'santander_installment_se': 'Santander Installments SE',
    'santander_installment_dk': 'Santander Installments DK',
    'santander_installment_fi': 'Santander Installments FI',
    'santander_installment_uk': 'Santander Installments UK',
    'santander_installment_nl': 'Santander Installments NL',
    'santander_invoice_no': 'Santander Invoice NO',
    'wiretransfer': 'Wire Transfer',
    'ideal': 'iDEAL',
    'bancontact': 'Bancontact',
    'apple_pay': 'Apple Pay',
    'google_pay': 'Google Pay',
    'openbank_pay_bnpl_de': 'Openbank Pay BNPL DE',
    'openbank_pay_bnpl': 'Openbank Pay BNPL NL',
    'ivy': 'IVY',
    'swish': 'Swish',
    'vipps': 'Vipps',
    'trustly': 'Trustly',
}
