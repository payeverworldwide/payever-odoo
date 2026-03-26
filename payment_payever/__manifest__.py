# -*- coding: utf-8 -*-
{
    'name': 'payever Payments',
    'version': '19.0.1.0',
    'category': 'eCommerce',
    'license': 'LGPL-3',
    'author': 'payever',
    'website': 'https://www.payever.org/',
    'summary': 'Accept payments via payever — credit card, PayPal, BNPL, installments and more',
    'description': """
        Integrate payever's payment gateway into Odoo.
        Supports redirect checkout (all payment options), notifications,
        refunds, captures (shipping-goods) and cancellations via the
        payever REST API v3.
    """,
    'depends': ['payment'],
    'data': [
        'security/ir.model.access.csv',
        'views/payment_provider_views.xml',
        'views/payment_transaction_views.xml',
        'views/payment_redirect_templates.xml',
        'data/payment_method_data.xml',
        'data/payment_provider_data.xml',
    ],
    'assets': {
        'web.assets_frontend': [],
    },
    'installable': True,
    'post_init_hook': 'post_init_hook',
    'images': [
        'static/description/cover.png',
    ],
}
