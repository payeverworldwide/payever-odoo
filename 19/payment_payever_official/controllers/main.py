"""payever payment controller — handles webhook and customer return URLs."""
import json
import logging
import pprint

from odoo import http
from odoo.addons.payment.controllers.post_processing import PaymentPostProcessing
from odoo.http import request

from .. import const

_logger = logging.getLogger(__name__)


class PayeverController(http.Controller):
    """HTTP controller for payever callback URLs.

    During payment creation, ``--PAYMENT-ID--`` placeholders are embedded in the
    callback URLs. payever replaces them with the real payment ID before
    redirecting the customer or POSTing the webhook.
    """

    _notification_url = '/payment/payever/notification'
    _return_url = '/payment/payever/return'
    _failure_url = '/payment/payever/failure'
    _cancel_url = '/payment/payever/cancel'
    _pending_url = '/payment/payever/pending'

    # -------------------------------------------------------------------------
    # WEBHOOK
    # -------------------------------------------------------------------------

    @http.route(
        _notification_url,
        type='http',
        methods=['POST'],
        auth='public',
        csrf=False,
        save_session=False,
    )
    def payever_notification(self, **_kwargs):
        """Handle asynchronous payment status notifications (webhooks) from payever.

        Expected JSON body::

            {
                "notification_type": "payment.changed",
                "data": {
                    "payment": {
                        "id": "<payment_id>",
                        "status": "STATUS_PAID",
                        "reference": "<odoo_reference>",
                        ...
                    }
                }
            }

        The ``ref`` and ``payment_id`` query-string parameters are also filled in
        by payever from the placeholder we supplied during create.

        The body is only used to identify the transaction and the payment; the
        status itself is always read back from the payever API before the
        transaction is updated.
        """
        try:
            raw = request.httprequest.get_data(as_text=True)
            notification = json.loads(raw) if raw else {}
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _logger.warning('payever notification: cannot parse JSON body – %s', exc)
            return request.make_response('BAD REQUEST', status=400)

        _logger.info('payever notification received:\n%s', pprint.pformat(notification))

        payment_data = notification.get('data', {}).get('payment', {})
        if not payment_data:
            return request.make_response('OK', status=200)

        payment_id = payment_data.get('id') or _kwargs.get('payment_id', '')
        reference = payment_data.get('reference') or _kwargs.get('ref', '')

        if not reference:
            _logger.warning('payever notification: no reference in payload')
            return request.make_response('OK', status=200)

        tx_sudo = self._get_tx_or_none(reference)
        if not tx_sudo:
            _logger.warning('payever notification: no transaction for reference %s', reference)
            return request.make_response('OK', status=200)

        sig = request.httprequest.headers.get('x-payever-signature', '')
        if sig and not tx_sudo.provider_id._payever_verify_notification_signature(payment_id, sig):
            _logger.warning(
                'payever notification: signature mismatch for payment_id=%s', payment_id
            )
            return request.make_response('FORBIDDEN', status=403)

        try:
            self._apply_verified_payment(tx_sudo, payment_id)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _logger.exception(
                'payever notification: error processing transaction %s – %s', reference, exc
            )

        return request.make_response('OK', status=200)

    # -------------------------------------------------------------------------
    # CUSTOMER RETURN URLS
    # -------------------------------------------------------------------------

    @http.route(
        _return_url, type='http', methods=['GET'], auth='public', csrf=False,
    )
    def payever_return(self, ref=None, payment_id=None, **_kwargs):
        """Redirect target after a successful payment."""
        return self._handle_customer_return(ref, payment_id)

    @http.route(
        _failure_url, type='http', methods=['GET'], auth='public', csrf=False,
    )
    def payever_failure(self, ref=None, payment_id=None, **_kwargs):
        """Redirect target after a failed payment."""
        return self._handle_customer_return(
            ref, payment_id, retry_checkout=True, checkout_result='failed'
        )

    @http.route(
        _cancel_url, type='http', methods=['GET'], auth='public', csrf=False,
    )
    def payever_cancel(self, ref=None, payment_id=None, **_kwargs):
        """Redirect target when the customer cancels at checkout."""
        return self._handle_customer_return(
            ref, payment_id, retry_checkout=True, checkout_result='cancelled'
        )

    @http.route(
        _pending_url, type='http', methods=['GET'], auth='public', csrf=False,
    )
    def payever_pending(self, ref=None, payment_id=None, **_kwargs):
        """Redirect target for payments pending further processing."""
        return self._handle_customer_return(ref, payment_id, use_payment_status=True)

    # -------------------------------------------------------------------------
    # PRIVATE HELPERS
    # -------------------------------------------------------------------------

    def _handle_customer_return(
        self, ref, payment_id, retry_checkout=False, checkout_result=None,
        use_payment_status=False,
    ):
        """Retrieve fresh payment status from payever and redirect the customer.

        All four return URL handlers (success / failure / cancel / pending)
        converge here. We always fetch the live status from payever so that the
        transaction state in Odoo reflects reality regardless of which URL was
        hit.

        Unpaid webshop transactions send the customer back to the payment step
        of the checkout instead of the confirmation page, so that the order can
        be paid again. Declined payment methods are hidden there for the rest of
        the session.

        :param bool retry_checkout: Whether payever reported the payment as
            failed or cancelled through the URL, so that the checkout is resumed
            even when the payment status is not final yet.
        """
        _fallback = '/web#action=payment.action_payment_status'

        if not ref:
            _logger.warning('payever return: missing ref parameter')
            return request.redirect(_fallback)

        tx_sudo = self._get_tx_or_none(ref)
        if not tx_sudo:
            _logger.warning('payever return: transaction not found for ref=%s', ref)
            return request.redirect(_fallback)

        payment_data = self._apply_verified_payment(tx_sudo, payment_id)

        order_sudo = self._get_checkout_order(tx_sudo)
        payment_status = (payment_data or {}).get('status')
        if order_sudo and payment_status in const.DECLINED_STATUSES:
            self._remember_declined_method(order_sudo, tx_sudo.payment_method_id)

        if use_payment_status and payment_data:
            PaymentPostProcessing.monitor_transaction(tx_sudo)
            return request.redirect('/payment/status')

        if order_sudo and (
            tx_sudo.state in ('cancel', 'error')
            or (retry_checkout and tx_sudo.state not in ('authorized', 'done'))
        ):
            checkout_result = {
                'STATUS_CANCELLED': 'cancelled',
                'STATUS_FAILED': 'failed',
                'STATUS_DECLINED': 'declined',
            }.get(payment_status, checkout_result)
            if checkout_result:
                request.session[const.PAYMENT_RESULT_SESSION_KEY] = checkout_result
            return request.redirect('/shop/payment')

        return request.redirect(tx_sudo.landing_route or '/payment/status')

    @staticmethod
    def _apply_verified_payment(tx_sudo, payment_id):
        """Read the payment back from payever and update *tx_sudo* accordingly.

        Both the webhook and the customer return URLs are public routes whose
        parameters are attacker-controllable, so the payment is fetched from the
        payever API and checked against the transaction before anything is
        applied.

        :param tx_sudo: The `payment.transaction` record, in sudo mode.
        :param str payment_id: The payever payment ID from the callback.
        :return: The verified payever payment data, or None when it was rejected.
        :rtype: dict | None
        """
        payment_data = tx_sudo._payever_fetch_payment_data(payment_id)
        if not payment_data or not tx_sudo._payever_payment_data_matches(payment_data):
            return None
        tx_sudo._process('payever', payment_data)
        return payment_data

    @staticmethod
    def _get_checkout_order(tx_sudo):
        """Return the draft webshop cart of the current session paid by *tx_sudo*.

        The order must be the cart of the visitor's session so that a public
        return URL cannot be used to resume somebody else's checkout.
        """
        if 'sale_order_ids' not in tx_sudo._fields:
            return None
        cart_id = request.session.get('sale_order_id')
        order_sudo = tx_sudo.sale_order_ids.filtered(
            lambda order: order.id == cart_id and order.state == 'draft'
        )
        return order_sudo[:1] or None

    @staticmethod
    def _remember_declined_method(order_sudo, payment_method):
        """Hide *payment_method* from the payment step of *order_sudo*."""
        if not payment_method or payment_method.code == 'payever':
            return
        declined = dict(request.session.get(const.DECLINED_METHODS_SESSION_KEY) or {})
        order_key = str(order_sudo.id)
        method_ids = list(declined.get(order_key) or [])
        if payment_method.id not in method_ids:
            method_ids.append(payment_method.id)
        declined[order_key] = method_ids
        request.session[const.DECLINED_METHODS_SESSION_KEY] = declined

    @staticmethod
    def _get_tx_or_none(reference):
        """Return the payever transaction for *reference*, or None."""
        tx = request.env['payment.transaction'].sudo().search(
            [('reference', '=', reference), ('provider_code', '=', 'payever')],
            limit=1,
        )
        return tx or None
