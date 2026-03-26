# -*- coding: utf-8 -*-

import logging
import pprint
from werkzeug import urls

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError, UserError

from odoo.addons.payment_payever import const

_logger = logging.getLogger(__name__)


class PaymentTransactionPayever(models.Model):
    _inherit = 'payment.transaction'

    # Store the payever payment ID once we receive it from the notification or
    # return-URL callback. Maps to provider_reference for consistency with Odoo.
    payever_payment_id = fields.Char(
        string='payever Payment ID',
        readonly=True,
        help='Unique payment identifier assigned by payever.',
    )

    # ─────────────────────────────────────────────
    # RENDERING / CHECKOUT CREATION
    # ─────────────────────────────────────────────

    def _get_specific_rendering_values(self, processing_values):
        """Override of payment to return payever-specific rendering values.

        Calls the payever API to create a payment and returns the redirect URL
        so Odoo can redirect the customer to the payever checkout.
        """
        if self.provider_code != 'payever':
            return super()._get_specific_rendering_values(processing_values)

        payment_data = self._payever_create_payment_record()
        redirect_url = payment_data.get('redirect_url')
        if not redirect_url:
            raise ValidationError(
                _('payever did not return a checkout URL. Please try again.')
            )
        return {'api_url': redirect_url}

    def _payever_create_payment_record(self):
        """Build the create-payment payload and call the payever API.

        :return: Raw API response dict (contains 'redirect_url').
        :rtype: dict
        """
        self.ensure_one()
        payload = self._payever_prepare_payment_payload()
        _logger.info(
            'Creating payever payment for transaction %s:\n%s',
            self.reference,
            pprint.pformat(payload),
        )
        result = self.provider_id._payever_create_payment(payload)
        _logger.info(
            'payever create-payment response for %s:\n%s',
            self.reference,
            pprint.pformat(result),
        )
        # The call.id is a call/session identifier, NOT the payment_id.
        # The actual payment_id arrives via notification or return-URL params.
        if result.get('call', {}).get('status') == 'failed':
            raise ValidationError(
                _('payever payment creation failed: %s', result.get('error_description', ''))
            )
        return result

    def _payever_prepare_payment_payload(self):
        """Build the full JSON payload for POST /api/v3/payment.

        :return: Payload dict.
        :rtype: dict
        """
        self.ensure_one()
        base_url = self.provider_id.get_base_url()
        ref = self.reference

        # Route paths – kept in sync with PayeverController class attributes
        _r = '/payment/payever/return'
        _f = '/payment/payever/failure'
        _c = '/payment/payever/cancel'
        _p = '/payment/payever/pending'
        _n = '/payment/payever/notification'

        # Callback URLs – payever replaces --PAYMENT-ID-- at redirect/notification time
        success_url = urls.url_join(
            base_url, f'{_r}?ref={ref}&payment_id=--PAYMENT-ID--'
        )
        failure_url = urls.url_join(
            base_url, f'{_f}?ref={ref}&payment_id=--PAYMENT-ID--'
        )
        cancel_url = urls.url_join(
            base_url, f'{_c}?ref={ref}&payment_id=--PAYMENT-ID--'
        )
        pending_url = urls.url_join(
            base_url, f'{_p}?ref={ref}&payment_id=--PAYMENT-ID--'
        )
        notification_url = urls.url_join(
            base_url, f'{_n}?ref={ref}&payment_id=--PAYMENT-ID--'
        )

        partner = self.partner_id
        name_parts = (partner.name or '').split(' ', 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else first_name

        payload = {
            'channel': {
                'name': 'api',
                'type': 'ecommerce',
                'source': f'Odoo/{self.env["ir.module.module"].sudo().search([("name", "=", "base")], limit=1).latest_version or ""}',
            },
            'reference': ref,
            'purchase': {
                'amount': round(self.amount, 2),
                'currency': self.currency_id.name,
                'country': (
                    partner.country_id.code if partner.country_id else ''
                ),
                'delivery_fee': 0.0,
                'down_payment': 0.0,
            },
            'customer': {
                'type': 'person',
                'email': partner.email or '',
                'phone': partner.phone or partner.mobile or '',
            },
            'cart': self._payever_prepare_cart(),
            'urls': {
                'success': success_url,
                'failure': failure_url,
                'cancel': cancel_url,
                'pending': pending_url,
                'notification': notification_url,
            },
            'options': {
                'allow_separate_shipping_address': True,
                'allow_customer_types': ['person', 'organization'],
                'allow_cart_step': False,
                'allow_billing_step': False,
                'allow_shipping_step': False,
                'use_styles': True,
                'salutation_mandatory': False,
                'phone_mandatory': False,
            },
        }

        # Add billing address when available
        billing_address = self._payever_prepare_address(partner)
        if billing_address:
            payload['billing_address'] = billing_address

        # Optionally include specific payment method
        if self.payment_method_code and self.payment_method_code != 'payever':
            payload['payment_method'] = self.payment_method_code

        # Test mode flag
        if self.provider_id.state == 'test':
            payload['options']['test_mode'] = True

        return payload

    def _payever_prepare_cart(self):
        """Build cart items from linked sale order or invoice lines.

        Falls back to a single generic line if no order/invoice is attached.
        """
        self.ensure_one()
        lines = []

        if self.sale_order_ids:
            order = self.sale_order_ids[0]
            for line in order.order_line.filtered(lambda l: not l.display_type):
                if line.price_total == 0:
                    continue
                qty = line.product_uom_qty
                unit_price = line.price_reduce_taxinc
                lines.append({
                    'name': line.name or line.product_id.name or 'Item',
                    'identifier': line.product_id.default_code or str(line.id),
                    'sku': line.product_id.default_code or str(line.id),
                    'quantity': qty,
                    'unit_price': round(abs(unit_price), 2),
                    'total_amount': round(abs(line.price_total), 2),
                    'tax_rate': sum(line.tax_id.mapped('amount')) if line.tax_id else 0,
                    'total_tax_amount': round(abs(line.price_total - line.price_subtotal), 2),
                })

        elif self.invoice_ids:
            invoice = self.invoice_ids[0]
            for line in invoice.invoice_line_ids.filtered(
                lambda l: l.display_type not in ('line_section', 'line_note')
            ):
                if line.price_total == 0:
                    continue
                qty = abs(line.quantity) or 1
                unit_price = abs(line.price_total / qty)
                lines.append({
                    'name': line.name or (line.product_id.name if line.product_id else 'Item'),
                    'identifier': (
                        line.product_id.default_code or str(line.id)
                        if line.product_id else str(line.id)
                    ),
                    'sku': (
                        line.product_id.default_code or str(line.id)
                        if line.product_id else str(line.id)
                    ),
                    'quantity': qty,
                    'unit_price': round(unit_price, 2),
                    'total_amount': round(abs(line.price_total), 2),
                    'tax_rate': line.tax_ids[0].amount if line.tax_ids else 0,
                    'total_tax_amount': round(abs(line.price_total - line.price_subtotal), 2),
                })

        if not lines:
            # Generic fallback – required by the payever API
            lines = [{
                'name': self.reference,
                'identifier': self.reference,
                'sku': self.reference,
                'quantity': 1,
                'unit_price': round(self.amount, 2),
                'total_amount': round(self.amount, 2),
                'tax_rate': 0,
                'total_tax_amount': 0,
            }]

        return lines

    def _payever_prepare_address(self, partner):
        """Build a payever-compatible address dict from a res.partner record.

        :param partner: res.partner record.
        :return: Address dict or empty dict if insufficient data.
        :rtype: dict
        """
        if not partner:
            return {}

        name_parts = (partner.name or '').split(' ', 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else first_name

        # payever expects street and street_number separated.
        # Odoo stores them combined in partner.street; split on last space.
        street = partner.street or ''
        street_parts = street.rsplit(' ', 1)
        if len(street_parts) == 2 and street_parts[1].replace('-', '').isdigit():
            street_name = street_parts[0]
            street_number = street_parts[1]
        else:
            street_name = street
            street_number = ''

        return {
            'first_name': first_name,
            'last_name': last_name,
            'email': partner.email or '',
            'phone': partner.phone or partner.mobile or '',
            'street': street_name,
            'street_number': street_number,
            'city': partner.city or '',
            'zip': partner.zip or '',
            'country': partner.country_id.code if partner.country_id else '',
        }

    # ─────────────────────────────────────────────
    # STATE UPDATES
    # ─────────────────────────────────────────────

    def _apply_updates(self, payment_data):
        """Override of payment to process payever-specific payment data.

        payment_data is the ``data.payment`` object extracted from a payever
        notification, or the ``result`` object from a retrieve-payment response.
        Both share the same field names.
        """
        if self.provider_code != 'payever':
            return super()._apply_updates(payment_data)

        if self.state == 'done':
            return

        payever_id = payment_data.get('id')
        if payever_id:
            self.provider_reference = payever_id
            self.payever_payment_id = payever_id

        payment_status = payment_data.get('status', '')
        odoo_state = const.PAYEVER_TO_ODOO_STATUS.get(payment_status)

        if odoo_state == 'pending':
            self._set_pending()
        elif odoo_state == 'authorized':
            self._set_authorized()
        elif odoo_state == 'done':
            self._set_done()
        elif odoo_state == 'cancel':
            self._set_canceled(
                _('Payment cancelled by payever with status: %s', payment_status)
            )
        else:
            _logger.info(
                'payever: unknown status "%s" for transaction %s',
                payment_status,
                self.reference,
            )
            self._set_error(
                _('Received unknown payment status from payever: %s', payment_status)
            )

    # ─────────────────────────────────────────────
    # REFUND
    # ─────────────────────────────────────────────

    def _send_refund_request(self):
        """Override of payment to send a refund request to payever."""
        refund_tx = super()._send_refund_request()
        if self.provider_code != 'payever':
            return refund_tx

        payment_id = self.provider_reference or self.payever_payment_id
        if not payment_id:
            raise UserError(
                _('Cannot refund: payever payment ID is not set on this transaction.')
            )

        amount = abs(refund_tx.amount) if refund_tx else abs(self.amount)
        response = self.provider_id._payever_refund(payment_id, amount=amount)

        _logger.info(
            'payever refund response for transaction %s:\n%s',
            self.reference,
            pprint.pformat(response),
        )

        if response.get('call', {}).get('status') == 'failed':
            raise ValidationError(
                _('payever refund failed: %s', response.get('error_description', ''))
            )

        if refund_tx:
            result = response.get('result', {})
            new_status = result.get('status', '')
            if new_status in ('STATUS_REFUNDED', 'STATUS_CANCELLED'):
                refund_tx._set_done()
            else:
                refund_tx._set_pending()

        return refund_tx

    # ─────────────────────────────────────────────
    # CAPTURE (SHIPPING GOODS)
    # ─────────────────────────────────────────────

    def _send_capture_request(self):
        """Override of payment to send a capture (shipping-goods) request to payever."""
        if self.provider_code != 'payever':
            return super()._send_capture_request()

        payment_id = (
            self.source_transaction_id.provider_reference
            or self.source_transaction_id.payever_payment_id
        )
        if not payment_id:
            raise UserError(
                _('Cannot capture: payever payment ID is not set on the source transaction.')
            )

        amount = round(self.amount, 2)
        response = self.provider_id._payever_capture(payment_id, amount=amount)

        _logger.info(
            'payever capture response for transaction %s:\n%s',
            self.reference,
            pprint.pformat(response),
        )

        if response.get('call', {}).get('status') == 'failed':
            raise ValidationError(
                _('payever capture failed: %s', response.get('error_description', ''))
            )

        result = response.get('result', {})
        new_status = result.get('status', '')
        if new_status == 'STATUS_PAID':
            self._set_done()
        else:
            self._set_pending()

    # ─────────────────────────────────────────────
    # VOID / CANCEL
    # ─────────────────────────────────────────────

    def _send_void_request(self):
        """Override of payment to send a void/cancel request to payever."""
        if self.provider_code != 'payever':
            return super()._send_void_request()

        payment_id = (
            self.source_transaction_id.provider_reference
            or self.source_transaction_id.payever_payment_id
        )
        if not payment_id:
            raise UserError(
                _('Cannot void: payever payment ID is not set on the source transaction.')
            )

        response = self.provider_id._payever_cancel(payment_id)

        _logger.info(
            'payever void response for transaction %s:\n%s',
            self.reference,
            pprint.pformat(response),
        )

        if response.get('call', {}).get('status') == 'failed':
            raise ValidationError(
                _('payever void/cancel failed: %s', response.get('error_description', ''))
            )

        self._set_canceled()
