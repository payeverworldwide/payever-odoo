"""Display payever return messages on the webshop payment page."""

from odoo import http
from odoo.http import request
from odoo.addons.website_sale.controllers.main import WebsiteSale

from .. import const


class PayeverWebsiteSale(WebsiteSale):
    """Add a one-time payever result to the checkout rendering context."""

    @http.route()
    def shop_payment(self, **post):
        response = super().shop_payment(**post)
        qcontext = getattr(response, 'qcontext', None)
        if qcontext is not None:
            result = request.session.pop(const.PAYMENT_RESULT_SESSION_KEY, None)
            if result in ('cancelled', 'failed', 'declined'):
                qcontext['payever_payment_result'] = result
        return response
