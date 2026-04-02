# pylint: disable=missing-module-docstring
{
    'name': 'payever Checkout',
    'version': '19.0.0',
    'category': 'eCommerce',
    'license': 'LGPL-3',
    'author': 'payever GmbH',
    'website': 'https://www.payever.org/',
    'summary': 'Accept payments via payever - credit card, PayPal, BNPL, installments and more',
    'depends': ['payment'],
    'data': [
        'security/ir.model.access.csv',
        'views/payment_provider_views.xml',
        'views/payment_transaction_views.xml',
        'views/payment_redirect_templates.xml',
        'data/payment_provider_data.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'images': [
        'static/description/cover.png',
    ],
}
