# -*- coding: utf-8 -*-

import hashlib
import hmac
import json
import logging
import pprint

from odoo import http
from odoo.http import request
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class PayeverController(http.Controller):
    """HTTP controller for payever callback URLs.

    URL scheme uses ``--PAYMENT-ID--`` placeholders that payever replaces with
    the actual payment ID before redirecting / POSTing.
    """

    _notification_url = '/payment/payever/notification'
    _return_url = '/payment/payever/return'
    _failure_url = '/payment/payever/failure'
    _cancel_url = '/payment/payever/cancel'
    _pending_url = '/payment/payever/pending'

    # ─────────────────────────────────────────────
    # NOTIFICATION (WEBHOOK)
    # ─────────────────────────────────────────────

    @http.route(
        _notification_url,
        type='http',
        methods=['POST'],
        auth='public',
        csrf=False,
        save_session=False,
    )
    def payever_notification(self, **kwargs):
        """Handle asynchronous payment status notifications from payever.

        payever sends a POST with a JSON body of the form::

            {
                "notification_type": "payment.changed",
                "data": {
                    "payment": {
                        "id": "<payment_id>",
                        "status": "STATUS_PAID",
                        "reference": "<our_order_ref>",
                        ...
                    }
                }
            }

        We also receive ``ref`` and ``payment_id`` as query-string parameters
        (substituted by payever from the placeholders we sent during create).
        """
        # Parse JSON body
        try:
            raw_body = request.httprequest.get_data(as_text=True)
            notification = json.loads(raw_body) if raw_body else {}
        except (json.JSONDecodeError, Exception) as exc:
            _logger.warning('payever notification: could not parse JSON body – %s', exc)
            return request.make_response('BAD REQUEST', status=400)

        _logger.info('payever notification received:\n%s', pprint.pformat(notification))

        payment_data = notification.get('data', {}).get('payment', {})
        if not payment_data:
            # Might be a test ping or empty notification; acknowledge gracefully
            return request.make_response('OK', status=200)

        payment_id = payment_data.get('id') or kwargs.get('payment_id', '')
        reference = payment_data.get('reference') or kwargs.get('ref', '')

        if not reference:
            _logger.warning('payever notification: no reference found in payload')
            return request.make_response('OK', status=200)

        # Find the Odoo transaction
        tx_sudo = self._get_tx_or_none(reference)
        if not tx_sudo:
            _logger.warning(
                'payever notification: transaction not found for reference %s', reference
            )
            return request.make_response('OK', status=200)

        # Optionally verify signature
        sig_header = request.httprequest.headers.get('x-payever-signature', '')
        if sig_header and not tx_sudo.provider_id._payever_verify_notification_signature(
            payment_id, sig_header
        ):
            _logger.warning(
                'payever notification: signature mismatch for payment_id=%s', payment_id
            )
            return request.make_response('FORBIDDEN', status=403)

        # Apply the status update
        try:
            tx_sudo._apply_updates(payment_data)
        except Exception as exc:
            _logger.exception(
                'payever notification: error updating transaction %s – %s', reference, exc
            )
        return request.make_response('OK', status=200)

    # ─────────────────────────────────────────────
    # CUSTOMER RETURN URLS
    # ─────────────────────────────────────────────

    @http.route(
        _return_url,
        type='http',
        methods=['GET'],
        auth='public',
        website=True,
    )
    def payever_return(self, ref=None, payment_id=None, **kwargs):
        """Customer return after a successful payment."""
        return self._handle_customer_return(ref, payment_id, expected_outcome='success')

    @http.route(
        _failure_url,
        type='http',
        methods=['GET'],
        auth='public',
        website=True,
    )
    def payever_failure(self, ref=None, payment_id=None, **kwargs):
        """Customer return after a failed payment."""
        return self._handle_customer_return(ref, payment_id, expected_outcome='failure')

    @http.route(
        _cancel_url,
        type='http',
        methods=['GET'],
        auth='public',
        website=True,
    )
    def payever_cancel(self, ref=None, payment_id=None, **kwargs):
        """Customer return after cancelling a payment."""
        return self._handle_customer_return(ref, payment_id, expected_outcome='cancel')

    @http.route(
        _pending_url,
        type='http',
        methods=['GET'],
        auth='public',
        website=True,
    )
    def payever_pending(self, ref=None, payment_id=None, **kwargs):
        """Customer return for a payment that is pending further processing."""
        return self._handle_customer_return(ref, payment_id, expected_outcome='pending')

    # ─────────────────────────────────────────────
    # PRIVATE HELPERS
    # ─────────────────────────────────────────────

    def _handle_customer_return(self, ref, payment_id, expected_outcome='success'):
        """Process a customer returning from the payever checkout page.

        Fetches fresh payment status from the payever API (if a payment_id is
        present) and updates the transaction, then redirects the customer to
        the appropriate Odoo page.
        """
        if not ref:
            _logger.warning('payever return: no ref parameter')
            return request.redirect('/web#action=payment.action_payment_status')

        tx_sudo = self._get_tx_or_none(ref)
        if not tx_sudo:
            _logger.warning('payever return: transaction not found for ref=%s', ref)
            return request.redirect('/web#action=payment.action_payment_status')

        # If we have a payment_id from the URL, store it and fetch fresh status
        if payment_id and payment_id != '--PAYMENT-ID--':
            tx_sudo.payever_payment_id = payment_id
            tx_sudo.provider_reference = payment_id
            try:
                response = tx_sudo.provider_id._payever_retrieve_payment(payment_id)
                result = response.get('result', {})
                if result and result.get('status'):
                    tx_sudo._apply_updates(result)
            except ValidationError as exc:
                _logger.warning(
                    'payever return: could not retrieve payment %s – %s', payment_id, exc
                )

        # Redirect to the standard Odoo payment status / landing page
        landing_route = tx_sudo.landing_route or '/payment/status'
        return request.redirect(landing_route)

    @staticmethod
    def _get_tx_or_none(reference):
        """Find a payever payment.transaction by Odoo reference.

        :param str reference: The transaction reference (e.g. 'S00001-1').
        :return: Sudo transaction record or None.
        """
        tx = request.env['payment.transaction'].sudo().search(
            [('reference', '=', reference), ('provider_code', '=', 'payever')],
            limit=1,
        )
        return tx or None
