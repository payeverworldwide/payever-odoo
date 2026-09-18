"""payever payment provider model."""
import base64
import hashlib
import hmac
import importlib.resources as pkg_resources
import json
import logging
from datetime import timedelta

import psycopg2

import requests

from odoo import Command, _, api, fields, models, service, SUPERUSER_ID
from odoo.exceptions import ValidationError
from odoo.modules.registry import Registry

from .. import const
from .. import static

_logger = logging.getLogger(__name__)


class PaymentProviderPayever(models.Model):
    """Extend payment.provider with payever-specific fields and API logic."""

    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('payever', 'payever')],
        ondelete={'payever': 'set default'},
    )

    payever_client_id = fields.Char(
        string='Client ID',
        required_if_provider='payever',
        groups='base.group_system',
        help='Client ID from your payever account (Connect → Shopsystems → API).',
    )
    payever_client_secret = fields.Char(
        string='Client Secret',
        required_if_provider='payever',
        groups='base.group_system',
        help='Client Secret associated with your Client ID.',
    )
    payever_business_uuid = fields.Char(
        string='Business UUID',
        groups='base.group_user',
        help='Business UUID from your payever account, sent as the x-payever-business header. '
             'Leave empty unless your organisation manages several businesses under a single '
             'set of API keys; that setup must be activated by payever support first.',
    )
    payever_access_token = fields.Char(
        string='Access Token',
        groups='base.group_system',
        copy=False,
    )
    payever_token_expires_at = fields.Datetime(
        string='Token Expiry',
        groups='base.group_system',
        copy=False,
    )
    payever_debug_logging = fields.Boolean(
        string='Debug Logging',
        help='Log every payever API request and response to ir.logging for troubleshooting.',
    )
    payever_currency_id = fields.Many2one(
        comodel_name='res.currency',
        string='Amount Currency',
        default=lambda self: self.env.ref('base.EUR', raise_if_not_found=False),
        help='Currency used for the minimum and maximum checkout amount limits, '
             'and for the payment method sync.',
    )
    payever_minimum_amount = fields.Monetary(
        string='Minimum Checkout Amount',
        currency_field='payever_currency_id',
        help='Minimum order amount accepted by payever at checkout. 0 = no restriction.',
    )
    payever_maximum_amount = fields.Monetary(
        string='Maximum Checkout Amount',
        currency_field='payever_currency_id',
        help='Maximum order amount accepted by payever at checkout. 0 = no restriction.',
    )

    # -------------------------------------------------------------------------
    # FEATURE SUPPORT
    # -------------------------------------------------------------------------

    def _compute_feature_support_fields(self):
        """Override of `payment` to declare refund and manual capture support."""
        super()._compute_feature_support_fields()
        self.filtered(lambda p: p.code == 'payever').update({
            'support_refund': 'partial',
            'support_manual_capture': 'partial',
        })

    def _get_default_payment_method_codes(self):
        """Override of `payment` to return the default payment method codes."""
        default_codes = super()._get_default_payment_method_codes()
        if self.code != 'payever':
            return default_codes
        return const.DEFAULT_PAYMENT_METHOD_CODES

    # -------------------------------------------------------------------------
    # AVAILABILITY
    # -------------------------------------------------------------------------

    @api.model
    def _get_compatible_providers(self, company_id, partner_id, amount, currency_id=None, **kwargs):
        """Override of `payment` to apply the payever minimum and maximum amount."""
        providers = super()._get_compatible_providers(
            company_id, partner_id, amount, currency_id=currency_id, **kwargs
        )
        if not amount:
            return providers  # Validation operations have no amount to check.

        company = self.env['res.company'].browse(company_id)
        currency = self.env['res.currency'].browse(currency_id) if currency_id else None
        currency = currency or company.currency_id

        excluded = self.env['payment.provider']
        for provider in providers.filtered(lambda p: p.code == 'payever'):
            limit_currency = provider.payever_currency_id or currency
            converted_amount = currency._convert(
                amount, limit_currency, company, fields.Date.context_today(self)
            ) if limit_currency != currency else amount

            minimum = provider.payever_minimum_amount
            maximum = provider.payever_maximum_amount
            below = minimum and limit_currency.compare_amounts(converted_amount, minimum) < 0
            above = maximum and limit_currency.compare_amounts(converted_amount, maximum) > 0
            if below or above:
                excluded |= provider

        return providers - excluded

    # -------------------------------------------------------------------------
    # REDIRECT FORM
    # -------------------------------------------------------------------------

    def _get_redirect_form_view(self, is_validation=False):
        if self.code != 'payever':
            return super()._get_redirect_form_view(is_validation)

        view = self.env.ref(
            f'{const.MODULE_NAME}.payever_redirect_form', raise_if_not_found=False
        )
        if not view:  # The addon directory may carry another name.
            view = self.redirect_form_view_id or self.env['ir.ui.view'].sudo().search(
                [('key', '=like', '%.payever_redirect_form')], limit=1
            )
        if not view:
            raise ValidationError(_(
                'The payever redirect form is missing. Upgrade the payever Checkout module '
                'to restore it, then start the payment again.'
            ))
        return view

    # -------------------------------------------------------------------------
    # BACKEND ACTION: SYNC PAYMENT METHODS
    # -------------------------------------------------------------------------

    def action_sync_payever_methods(self):
        """Fetch available payment methods from payever, create missing ones, and update logos."""
        self.ensure_one()
        try:
            methods_data = self._payever_list_payment_options()
        except ValidationError as e:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Sync Failed'),
                    'message': str(e),
                    'type': 'danger',
                },
            }
        if not methods_data:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('No Methods Found'),
                    'message': _('payever returned an empty list of payment methods.'),
                    'type': 'warning',
                },
            }

        for method_info in methods_data:
            code = method_info.get('payment_method', '')
            name = method_info.get('name', code)
            if not code:
                continue

            image_b64 = self._payever_download_logo(method_info.get('logo'), code)

            existing = self.env['payment.method'].with_context(active_test=False).search(
                [('code', '=', code)], limit=1
            )
            if existing:
                # Only fill in a logo when the method has none, so that logos of
                # methods shared with other providers are left untouched.
                vals = {'provider_ids': [Command.link(self.id)]}
                if image_b64 and not existing.image:
                    vals['image'] = image_b64
                existing.write(vals)
            else:
                self.env['payment.method'].create({
                    'name': name,
                    'code': code,
                    'active': True,
                    'image': image_b64 or self._payever_fallback_logo(),
                    'support_refund': 'partial',
                    'provider_ids': [Command.link(self.id)],
                })

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Sync Complete'),
                'message': _('payever payment methods have been updated.'),
                'type': 'success',
            },
        }

    def _payever_fallback_logo(self):
        """Return the module icon as base64, used when a method has no remote logo."""
        try:
            logo_data = pkg_resources.files(static).joinpath('description/icon.png').read_bytes()
            return base64.b64encode(logo_data)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _logger.warning('payever: could not load fallback payment method logo: %s', exc)
        return False

    def _payever_download_logo(self, logo_url, code):
        """Download a payment method logo and return it as base64 bytes, or False on failure."""
        if not logo_url:
            return False
        try:
            resp = requests.get(logo_url, timeout=10)
            if resp.status_code == 200:
                return base64.b64encode(resp.content)
        except requests.exceptions.RequestException as exc:
            _logger.warning('payever: could not download logo for %s: %s', code, exc)
        return False

    # -------------------------------------------------------------------------
    # OAUTH TOKEN
    # -------------------------------------------------------------------------

    def _payever_get_base_url(self):
        """Return the API base URL for the current mode (test/live)."""
        self.ensure_one()
        return const.SANDBOX_URL if self.state == 'test' else const.LIVE_URL

    def _payever_get_access_token(self):
        """Return a cached or freshly-fetched OAuth2 access token.

        Tokens are stored on the provider record and refreshed 5 minutes before
        expiry to avoid race conditions at token boundaries.
        """
        self.ensure_one()
        now = fields.Datetime.now()
        if (
            self.payever_access_token
            and self.payever_token_expires_at
            and self.payever_token_expires_at > now + timedelta(minutes=5)
        ):
            return self.payever_access_token

        try:
            response = requests.post(
                f'{self._payever_get_base_url()}/oauth/v2/token',
                data={
                    'client_id': self.payever_client_id,
                    'client_secret': self.payever_client_secret,
                    'grant_type': const.OAUTH_GRANT_TYPE,
                    'scope': const.OAUTH_SCOPE,
                },
                timeout=30,
            )
            response.raise_for_status()
            token_data = response.json()
        except requests.exceptions.RequestException as exc:
            raise ValidationError(
                _('payever: Could not obtain access token. %s', str(exc))
            ) from exc

        access_token = token_data.get('access_token')
        if not access_token:
            raise ValidationError(_('payever: Empty access token in OAuth response.'))

        expiry = now + timedelta(seconds=int(token_data.get('expires_in', 86400)))
        try:
            with Registry(self._cr.dbname).cursor() as cr:
                api.Environment(cr, SUPERUSER_ID, {})['payment.provider'].browse(self.id).write({
                    'payever_access_token': access_token,
                    'payever_token_expires_at': expiry,
                })
        except psycopg2.Error as exc:
            _logger.warning('payever: could not persist access token to DB: %s', exc)
        self.payever_access_token = access_token
        self.payever_token_expires_at = expiry
        return access_token

    # -------------------------------------------------------------------------
    # GENERIC REQUEST HELPER
    # -------------------------------------------------------------------------

    def _payever_make_request(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self, endpoint, method='GET', data=None, params=None, silent_errors=False
    ):
        """Execute an authenticated request against the payever REST API.

        :param str endpoint: Path relative to the base URL, e.g. '/api/payment/abc'.
        :param str method: HTTP verb ('GET', 'POST', …).
        :param dict data: JSON request body.
        :param dict params: URL query parameters.
        :param bool silent_errors: When True, return ``{'error': msg}`` instead of raising.
        :return: Parsed JSON response.
        :rtype: dict
        :raises ValidationError: On network or HTTP errors (when silent_errors is False).
        """
        self.ensure_one()
        url = f'{self._payever_get_base_url()}{endpoint}'
        odoo_version = service.common.exp_version()['server_version']
        mod = self.env.ref(f'base.module_{const.MODULE_NAME}', raise_if_not_found=False)
        plugin_version = mod.installed_version if mod else '1.0'

        headers = {
            'Accept': 'application/json',
            'Authorization': f'Bearer {self._payever_get_access_token()}',
            'Content-Type': 'application/json',
            'User-Agent': f'Odoo/{odoo_version} payeverOdoo/{plugin_version}',
        }
        if self.payever_business_uuid:
            # Targets one business of an organisation that holds several
            # businesses under a single OAuth token.
            headers['x-payever-business'] = self.payever_business_uuid

        result = None
        error_msg = _('Could not establish connection to the payever API.')
        try:
            response = requests.request(
                method, url, params=params, json=data, headers=headers, timeout=60
            )
            if response.status_code == 204:
                return {}
            result = response.json()
            if response.status_code not in (200, 201):
                error_msg = (
                    f"payever API error [{response.status_code}]: "
                    f"{result.get('message', result)}"
                )
                _logger.error('payever API error: %s', result)
            response.raise_for_status()
        except requests.exceptions.RequestException as exc:
            if silent_errors:
                return {'error': error_msg}
            raise ValidationError(_('payever: %s', error_msg)) from exc
        finally:
            if self.payever_debug_logging:
                self._payever_log(method, url, data, result)
        return result

    def _payever_log(self, method, url, request_data, response_data):
        """Persist a debug log entry for an API call via a separate DB cursor."""
        message = json.dumps(
            {'method': method, 'url': url, 'request': request_data, 'response': response_data},
            indent=2, default=str,
        )
        try:
            with Registry(self._cr.dbname).cursor() as cr:
                api.Environment(cr, SUPERUSER_ID, {})['ir.logging'].sudo().create({
                    'name': 'payever Payments',
                    'type': 'server',
                    'level': 'DEBUG',
                    'dbname': self._cr.dbname,
                    'message': message,
                    'func': method,
                    'path': url,
                    'line': str(self.id),
                })
        except psycopg2.Error as exc:
            _logger.warning('payever: could not write debug log: %s', exc)

    # -------------------------------------------------------------------------
    # PAYEVER API CALLS
    # -------------------------------------------------------------------------

    def _payever_create_payment(self, payload):
        """POST /api/v3/payment — create a new payment session."""
        return self._payever_make_request('/api/v3/payment', method='POST', data=payload)

    def _payever_retrieve_payment(self, payment_id):
        """GET /api/payment/{payment_id} — retrieve current payment state."""
        return self._payever_make_request(f'/api/payment/{payment_id}')

    def _payever_refund(self, payment_id, amount=None):
        """POST /api/payment/refund/{payment_id} — issue a full or partial refund."""
        payload = {}
        if amount is not None:
            payload['amount'] = round(amount, 2)
        return self._payever_make_request(
            f'/api/payment/refund/{payment_id}', method='POST', data=payload
        )

    def _payever_cancel(self, payment_id):
        """POST /api/payment/cancel/{payment_id} — void an authorised payment."""
        return self._payever_make_request(
            f'/api/payment/cancel/{payment_id}', method='POST', data={}
        )

    def _payever_capture(self, payment_id, amount=None):
        """POST /api/payment/shipping-goods/{payment_id} — capture an authorised payment."""
        payload = {}
        if amount is not None:
            payload['amount'] = round(amount, 2)
        return self._payever_make_request(
            f'/api/payment/shipping-goods/{payment_id}', method='POST', data=payload
        )

    def _payever_list_payment_options(self):
        """POST /api/v2/payment/methods — list available payment options."""
        response = self._payever_make_request(
            '/api/v2/payment/methods', method='POST',
            data={'channel': 'api', 'currency': self.payever_currency_id.name or 'EUR'},
            silent_errors=True,
        )
        if response.get('error'):
            return []
        return response.get('result', [])

    # -------------------------------------------------------------------------
    # WEBHOOK SIGNATURE VERIFICATION
    # -------------------------------------------------------------------------

    def _payever_verify_notification_signature(self, payment_id, received_signature):
        """Verify the ``x-payever-signature`` header on an incoming webhook.

        The expected signature is HMAC-SHA256(client_id + payment_id, client_secret).

        :param str payment_id: payever payment ID from the notification payload.
        :param str received_signature: Value of the x-payever-signature header.
        :return: True if the signature is valid or absent, False if invalid.
        :rtype: bool
        """
        self.ensure_one()
        if not received_signature:
            return True
        try:
            expected = hmac.new(
                (self.payever_client_secret or '').encode(),
                ((self.payever_client_id or '') + payment_id).encode(),
                hashlib.sha256,
            ).hexdigest()
            return hmac.compare_digest(expected, received_signature)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            _logger.warning('payever: signature verification failed: %s', exc)
            return False
