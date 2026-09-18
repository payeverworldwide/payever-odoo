"""payever payment transaction model."""
import logging
import pprint
from urllib.parse import urljoin

from odoo import _, fields, models
from odoo.exceptions import UserError, ValidationError

from .. import const

_logger = logging.getLogger(__name__)


class PaymentTransactionPayever(models.Model):
    """Extend payment.transaction with payever checkout and status-update logic."""

    _inherit = 'payment.transaction'

    payever_payment_id = fields.Char(
        string='payever Payment ID',
        readonly=True,
        help='Unique payment identifier assigned by payever.',
    )

    # -------------------------------------------------------------------------
    # CHECKOUT CREATION
    # -------------------------------------------------------------------------

    def _get_specific_rendering_values(self, processing_values):
        """Return payever-specific rendering values (redirect URL) for the checkout."""
        if self.provider_code != 'payever':
            return super()._get_specific_rendering_values(processing_values)

        result = self._payever_create_payment_record()
        redirect_url = result.get('redirect_url')
        if not redirect_url:
            raise ValidationError(
                _('payever did not return a checkout URL. Please try again.')
            )
        return {'api_url': redirect_url}

    def _payever_create_payment_record(self):
        """Build the payment payload and call POST /api/v3/payment.

        :return: Raw API response dict (contains ``redirect_url``).
        :rtype: dict
        """
        self.ensure_one()
        payload = self._payever_prepare_payment_payload()
        _logger.info(
            'payever: creating payment for transaction %s\n%s',
            self.reference, pprint.pformat(payload),
        )
        result = self.provider_id._payever_create_payment(payload)
        _logger.info(
            'payever: create-payment response for %s\n%s',
            self.reference, pprint.pformat(result),
        )
        if result.get('call', {}).get('status') == 'failed':
            raise ValidationError(_(
                'payever payment creation failed: %s', result.get('error_description', '')
            ))
        return result

    def _payever_prepare_payment_payload(self):
        """Build the full JSON body for POST /api/v3/payment."""
        self.ensure_one()
        base_url = self.provider_id.get_base_url()
        ref = self.reference

        _r = '/payment/payever/return'
        _f = '/payment/payever/failure'
        _c = '/payment/payever/cancel'
        _p = '/payment/payever/pending'
        _n = '/payment/payever/notification'

        def _url(path):
            return urljoin(base_url, f'{path}?ref={ref}&payment_id=--PAYMENT-ID--')

        partner = self.partner_id

        odoo_version = (
            self.env['ir.module.module'].sudo()
            .search([('name', '=', 'base')], limit=1).latest_version or ''
        )

        payload = {
            'channel': {
                'name': 'api',
                'type': 'ecommerce',
                'source': f'Odoo/{odoo_version}',
            },
            'reference': ref,
            'purchase': {
                'amount': round(self.amount, 2),
                'currency': self.currency_id.name,
                'country': partner.country_id.code if partner.country_id else '',
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
                'success': _url(_r),
                'failure': _url(_f),
                'cancel': _url(_c),
                'pending': _url(_p),
                'notification': _url(_n),
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

        billing = self._payever_prepare_address(partner)
        if billing:
            payload['billing_address'] = billing

        if self.payment_method_code and self.payment_method_code != 'payever':
            payload['payment_method'] = self.payment_method_code

        if self.provider_id.state == 'test':
            payload['options']['test_mode'] = True

        return payload

    def _payever_line_taxes(self, line):
        """Return the tax records on a sale or invoice line.

        Odoo 17 sale.order.line uses ``tax_id``; Odoo 18+ renamed it to
        ``tax_ids``. Invoice lines use ``tax_ids`` on both.
        """
        if 'tax_ids' in line._fields:
            return line.tax_ids
        return line.tax_id

    def _payever_prepare_cart(self):
        """Return a list of cart-item dicts built from the linked sale order or invoice."""
        self.ensure_one()
        lines = []

        if 'sale_order_ids' in self._fields and self.sale_order_ids:
            order = self.sale_order_ids[0]
            for line in order.order_line.filtered(lambda l: not l.display_type):
                if not line.price_total:
                    continue
                taxes = self._payever_line_taxes(line)
                lines.append({
                    'name': line.name or line.product_id.name or 'Item',
                    'identifier': line.product_id.default_code or str(line.id),
                    'sku': line.product_id.default_code or str(line.id),
                    'quantity': line.product_uom_qty,
                    'unit_price': round(abs(line.price_reduce_taxinc), 2),
                    'total_amount': round(abs(line.price_total), 2),
                    'tax_rate': sum(taxes.mapped('amount')) if taxes else 0,
                    'total_tax_amount': round(abs(line.price_total - line.price_subtotal), 2),
                })

        elif 'invoice_ids' in self._fields and self.invoice_ids:
            invoice = self.invoice_ids[0]
            for line in invoice.invoice_line_ids.filtered(
                lambda l: l.display_type not in ('line_section', 'line_note')
            ):
                if not line.price_total:
                    continue
                qty = abs(line.quantity) or 1
                taxes = self._payever_line_taxes(line)
                lines.append({
                    'name': line.name or (line.product_id.name if line.product_id else 'Item'),
                    'identifier': (line.product_id.default_code or str(line.id))
                                  if line.product_id else str(line.id),
                    'sku': (line.product_id.default_code or str(line.id))
                           if line.product_id else str(line.id),
                    'quantity': qty,
                    'unit_price': round(abs(line.price_total / qty), 2),
                    'total_amount': round(abs(line.price_total), 2),
                    'tax_rate': taxes[0].amount if taxes else 0,
                    'total_tax_amount': round(abs(line.price_total - line.price_subtotal), 2),
                })

        if not lines:
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
        """Return a payever address dict built from a res.partner record.

        payever expects the street name and number to be split. Odoo stores them
        combined, so we split on the last whitespace when the last token looks
        like a house number.

        :param partner: ``res.partner`` record.
        :return: Address dict, or empty dict when partner is falsy.
        :rtype: dict
        """
        if not partner:
            return {}

        name_parts = (partner.name or '').split(' ', 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else first_name

        street = partner.street or ''
        parts = street.rsplit(' ', 1)
        if len(parts) == 2 and parts[1].replace('-', '').isdigit():
            street_name, street_number = parts
        else:
            street_name, street_number = street, ''

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

    # -------------------------------------------------------------------------
    # STATE UPDATES
    # -------------------------------------------------------------------------

    def _get_tx_from_notification_data(self, provider_code, notification_data):
        """Override of `payment` to find the transaction based on payever data."""
        tx = super()._get_tx_from_notification_data(provider_code, notification_data)
        if provider_code != 'payever' or len(tx) == 1:
            return tx

        reference = notification_data.get('reference')
        payment_id = notification_data.get('id') or notification_data.get('payment_id')
        domain = [('provider_code', '=', 'payever')]
        if reference:
            tx = self.search(domain + [('reference', '=', reference)], limit=1)
        if not tx and payment_id and payment_id != '--PAYMENT-ID--':
            tx = self.search(domain + [
                '|',
                ('provider_reference', '=', payment_id),
                ('payever_payment_id', '=', payment_id),
            ], limit=1)
        if not tx:
            raise ValidationError(_(
                'payever: no transaction found matching reference %(ref)s / payment %(pid)s.',
                ref=reference, pid=payment_id,
            ))
        return tx

    def _process_notification_data(self, notification_data):
        """Process payever webhook or polling data and update the transaction state."""
        if self.provider_code != 'payever':
            return super()._process_notification_data(notification_data)
        return self._payever_process_payment_data(notification_data)

    def _payever_fetch_payment_data(self, payment_id):
        """Fetch the authoritative payment object from payever.

        Payment data received over a public route (webhook body or return URL
        query string) is never trusted on its own: the payment is always read
        back from the payever API before the transaction state is updated.

        :param str payment_id: The payever payment ID to read back.
        :return: The ``result`` object of the retrieve-payment response, or an
                 empty dict when it could not be retrieved.
        :rtype: dict
        """
        self.ensure_one()
        if not payment_id or payment_id == '--PAYMENT-ID--':
            _logger.warning(
                'payever: no payment id available to verify transaction %s', self.reference
            )
            return {}
        try:
            response = self.provider_id._payever_retrieve_payment(payment_id)
        except ValidationError as exc:
            _logger.warning(
                'payever: could not retrieve payment %s for transaction %s – %s',
                payment_id, self.reference, exc,
            )
            return {}
        result = response.get('result') or {}
        if not result.get('status'):
            _logger.warning(
                'payever: retrieve-payment returned no status for payment %s (transaction %s)',
                payment_id, self.reference,
            )
            return {}
        return result

    def _payever_payment_data_matches(self, payment_data):
        """Check that payever's payment data really belongs to this transaction.

        Guards against a payment of another order (or another amount) being
        attached to this transaction through the public callback routes.

        :param dict payment_data: The payment object returned by payever.
        :return: Whether the payment data may be applied to this transaction.
        :rtype: bool
        """
        self.ensure_one()

        reference = payment_data.get('reference')
        if reference and reference != self.reference:
            _logger.warning(
                'payever: payment data for reference %s does not match transaction %s; ignored',
                reference, self.reference,
            )
            return False

        currency = next(
            (payment_data[key] for key in const.CURRENCY_KEYS if payment_data.get(key)), None
        )
        if currency and currency != self.currency_id.name:
            _logger.warning(
                'payever: currency %s of payment data does not match %s of transaction %s; ignored',
                currency, self.currency_id.name, self.reference,
            )
            return False

        amount = next(
            (payment_data[key] for key in const.AMOUNT_KEYS if payment_data.get(key) is not None),
            None,
        )
        if amount is None:
            return True
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            _logger.warning(
                'payever: unreadable amount %r in payment data of transaction %s; ignored',
                amount, self.reference,
            )
            return False
        if self.currency_id.compare_amounts(abs(amount), abs(self.amount)) != 0:
            _logger.warning(
                'payever: amount %s of payment data does not match %s of transaction %s; ignored',
                amount, self.amount, self.reference,
            )
            return False
        return True

    def _payever_process_payment_data(self, payment_data):
        """Apply a payever payment-data dict to this transaction.

        ``payment_data`` is the ``result`` object of a retrieve-payment response;
        the webhook body shares the same field schema but is only used to locate
        the transaction, never to update it.

        Status mapping notes:
        - STATUS_ACCEPTED  → authorized (payment ready for capture)
        - STATUS_IN_PROCESS → authorized when manual capture is enabled on the
          provider (PayPal / Stripe with delayed capture in payever account),
          otherwise pending.
        - STATUS_PAID      → done (already captured by payever)
        """
        if self.state == 'done':
            return

        if not self._payever_payment_data_matches(payment_data):
            return

        payever_id = payment_data.get('id')
        if payever_id:
            self.provider_reference = payever_id
            self.payever_payment_id = payever_id

        payment_status = payment_data.get('status', '')
        odoo_state = const.PAYEVER_TO_ODOO_STATUS.get(payment_status)

        if (
            odoo_state == 'pending'
            and payment_status == 'STATUS_IN_PROCESS'
            and self.provider_id.capture_manually
        ):
            odoo_state = 'authorized'

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
            # Leave the transaction untouched: an unmapped status is a status
            # payever added after this module was released, not a failure.
            _logger.warning(
                'payever: unrecognised status "%s" for transaction %s; state left unchanged',
                payment_status, self.reference,
            )

    # -------------------------------------------------------------------------
    # REFUND
    # -------------------------------------------------------------------------

    def _send_refund_request(self, amount_to_refund=None):
        """Send a refund request to payever."""
        if self.provider_code != 'payever':
            return super()._send_refund_request(amount_to_refund=amount_to_refund)

        refund_tx = super()._send_refund_request(amount_to_refund=amount_to_refund)

        payment_id = self.provider_reference or self.payever_payment_id
        if not payment_id:
            raise UserError(
                _('Cannot refund: payever payment ID is not set on this transaction.')
            )

        amount = abs(refund_tx.amount) if refund_tx else abs(self.amount)
        response = self.provider_id._payever_refund(payment_id, amount=amount)

        _logger.info(
            'payever: refund response for %s\n%s',
            self.reference, pprint.pformat(response),
        )

        if response.get('call', {}).get('status') == 'failed':
            raise ValidationError(
                _('payever refund failed: %s', response.get('error_description', ''))
            )

        if refund_tx:
            new_status = response.get('result', {}).get('status', '')
            if new_status in ('STATUS_REFUNDED', 'STATUS_CANCELLED'):
                refund_tx._set_done()
            else:
                refund_tx._set_pending()
            refund_tx._execute_callback()

        return refund_tx

    # -------------------------------------------------------------------------
    # CAPTURE
    # -------------------------------------------------------------------------

    def _send_capture_request(self, amount_to_capture=None):
        """Override of `payment` to send a capture (shipping-goods) request to payever."""
        child_capture_tx = super()._send_capture_request(amount_to_capture=amount_to_capture)
        if self.provider_code != 'payever':
            return child_capture_tx

        payment_id = self.provider_reference or self.payever_payment_id
        if not payment_id:
            raise UserError(
                _('Cannot capture: payever payment ID is not set on this transaction.')
            )

        amount = round(amount_to_capture or abs(self.amount), 2)
        response = self.provider_id._payever_capture(payment_id, amount=amount)

        _logger.info(
            'payever: capture response for %s\n%s',
            self.reference, pprint.pformat(response),
        )

        if response.get('call', {}).get('status') == 'failed':
            raise ValidationError(
                _('payever capture failed: %s', response.get('error_description', ''))
            )

        target_tx = child_capture_tx or self
        new_status = response.get('result', {}).get('status', '')
        if new_status == 'STATUS_PAID':
            target_tx._set_done()
        else:
            target_tx._set_pending()
        target_tx._execute_callback()

        return child_capture_tx

    # -------------------------------------------------------------------------
    # VOID / CANCEL
    # -------------------------------------------------------------------------

    def _send_void_request(self, amount_to_void=None):
        """Override of `payment` to send a void/cancel request to payever."""
        child_void_tx = super()._send_void_request(amount_to_void=amount_to_void)
        if self.provider_code != 'payever':
            return child_void_tx

        payment_id = self.provider_reference or self.payever_payment_id
        if not payment_id:
            raise UserError(
                _('Cannot void: payever payment ID is not set on this transaction.')
            )

        response = self.provider_id._payever_cancel(payment_id)

        _logger.info(
            'payever: void response for %s\n%s',
            self.reference, pprint.pformat(response),
        )

        if response.get('call', {}).get('status') == 'failed':
            raise ValidationError(
                _('payever void/cancel failed: %s', response.get('error_description', ''))
            )

        (child_void_tx or self)._set_canceled()
        (child_void_tx or self)._execute_callback()
        return child_void_tx
