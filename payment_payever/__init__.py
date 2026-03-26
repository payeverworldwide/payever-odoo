# -*- coding: utf-8 -*-

from . import controllers
from . import models

# Payment method codes bundled with this module
_BUNDLED_METHOD_CODES = [
    'paypal', 'credit_card', 'direct_debit', 'instant_payment', 'sofort',
    'santander_installment', 'santander_installment_at', 'santander_installment_no',
    'santander_installment_se', 'santander_installment_dk', 'santander_installment_nl',
    'santander_installment_uk', 'santander_invoice_no', 'wiretransfer',
    'ideal', 'bancontact', 'apple_pay', 'google_pay', 'openbank_pay_bnpl_de',
    'ivy', 'swish', 'vipps', 'trustly',
]


def post_init_hook(env):
    """Link all existing payever providers to the bundled payment methods."""
    payever_providers = env['payment.provider'].with_context(active_test=False).search(
        [('code', '=', 'payever')]
    )
    if not payever_providers:
        return

    existing_methods = env['payment.method'].with_context(active_test=False).search(
        [('code', 'in', _BUNDLED_METHOD_CODES)]
    )
    if existing_methods:
        payever_providers.write({'payment_method_ids': [(6, 0, existing_methods.ids)]})
