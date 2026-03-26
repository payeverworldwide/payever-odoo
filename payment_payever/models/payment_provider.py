# -*- coding: utf-8 -*-

import json
import logging
import hmac
import hashlib
import psycopg2
from datetime import datetime, timedelta

import requests
from werkzeug import urls

from odoo import _, api, fields, models, service, SUPERUSER_ID
from odoo.exceptions import ValidationError
from odoo.modules.registry import Registry

from odoo.addons.payment_payever import const

_logger = logging.getLogger(__name__)


class PaymentProviderPayever(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('payever', 'payever')],
        ondelete={'payever': 'set default'},
    )

    # Credentials
    payever_client_id = fields.Char(
        string='Client ID',
        required_if_provider='payever',
        groups='base.group_system',
        help='32-character hexadecimal Client ID from your payever account.',
    )
    payever_client_secret = fields.Char(
        string='Client Secret',
        required_if_provider='payever',
        groups='base.group_system',
        help='256-bit Client Secret associated with your Client ID.',
    )
    payever_business_uuid = fields.Char(
        string='Business UUID',
        groups='base.group_user',
        help='Required only for the "List payment options" endpoint.',
    )

    # Cached OAuth token (internal use only)
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

    # Debug / UX
    payever_debug_logging = fields.Boolean(
        string='Debug Logging',
        help='Log every payever API request and response for troubleshooting.',
    )

    def toggle_payever_debug(self):
        for provider in self:
            provider.payever_debug_logging = not provider.payever_debug_logging

    # ─────────────────────────────────────────────
    # PAYMENT FEATURES
    # ─────────────────────────────────────────────

    def _compute_feature_support_fields(self):
        super()._compute_feature_support_fields()
        self.filtered(lambda p: p.code == 'payever').update({
            'support_refund': 'partial',
            'support_manual_capture': 'partial',
        })

    # ─────────────────────────────────────────────
    # REDIRECT FORM
    # ─────────────────────────────────────────────

    def _get_redirect_form_view(self, is_validation=False):
        if self.code != 'payever':
            return super()._get_redirect_form_view(is_validation)
        return self.env.ref('payment_payever.payever_redirect_form')

    # ─────────────────────────────────────────────
    # ACTION METHODS
    # ─────────────────────────────────────────────

    def action_sync_payever_methods(self):
        """Fetch available payment methods from payever and update payment.method records."""
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
            return

        for method_info in methods_data:
            code = method_info.get('payment_method', '')
            name = method_info.get('name', code)
            if not code:
                continue
            existing = self.env['payment.method'].with_context(active_test=False).search(
                [('code', '=', code)], limit=1
            )
            if not existing:
                self.env['payment.method'].create({
                    'name': name,
                    'code': code,
                    'active': True,
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

    # ─────────────────────────────────────────────
    # OAUTH TOKEN MANAGEMENT
    # ─────────────────────────────────────────────

    def _payever_get_base_url(self):
        """Return the correct API base URL for sandbox or live mode."""
        self.ensure_one()
        if self.state == 'test':
            return const.SANDBOX_URL
        return const.LIVE_URL

    def _payever_get_access_token(self):
        """Return a valid OAuth2 access token, refreshing if necessary.

        Tokens are cached on the provider record. The payever token TTL is
        86 400 s (24 h); we refresh 5 minutes early to avoid edge cases.
        """
        self.ensure_one()
        now = fields.Datetime.now()
        if (
            self.payever_access_token
            and self.payever_token_expires_at
            and self.payever_token_expires_at > now + timedelta(minutes=5)
        ):
            return self.payever_access_token

        base_url = self._payever_get_base_url()
        token_url = f'{base_url}/oauth/v2/token'

        try:
            response = requests.post(
                token_url,
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
            )

        access_token = token_data.get('access_token')
        expires_in = token_data.get('expires_in', 86400)
        if not access_token:
            raise ValidationError(_('payever: Empty access token in OAuth response.'))

        expiry = now + timedelta(seconds=int(expires_in))
        # Write in a separate cursor so the token survives even if the outer
        # transaction is rolled back (e.g., on a failed payment creation).
        db_name = self._cr.dbname
        try:
            with Registry(db_name).cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                env['payment.provider'].browse(self.id).write({
                    'payever_access_token': access_token,
                    'payever_token_expires_at': expiry,
                })
        except psycopg2.Error:
            pass
        self.payever_access_token = access_token
        self.payever_token_expires_at = expiry
        return access_token

    # ─────────────────────────────────────────────
    # GENERIC REQUEST HELPER
    # ─────────────────────────────────────────────

    def _payever_make_request(self, endpoint, method='GET', data=None, params=None, silent_errors=False):
        """Execute an authenticated request against the payever API.

        :param str endpoint: Path relative to the base URL (e.g. '/api/payment/abc').
        :param str method: HTTP verb – 'GET', 'POST', etc.
        :param dict data: JSON body (for POST/PATCH requests).
        :param dict params: Query-string parameters.
        :param bool silent_errors: If True, return dict with 'error' key instead of raising.
        :return: Parsed JSON response dict.
        :rtype: dict
        :raise ValidationError: On HTTP or API errors when silent_errors=False.
        """
        self.ensure_one()
        access_token = self._payever_get_access_token()
        base_url = self._payever_get_base_url()
        url = f'{base_url}{endpoint}'

        odoo_version = service.common.exp_version()['server_version']
        module = self.env.ref('base.module_payment_payever', raise_if_not_found=False)
        plugin_version = module.installed_version if module else '1.0'

        headers = {
            'Accept': 'application/json',
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
            'User-Agent': f'Odoo/{odoo_version} payeverOdoo/{plugin_version}',
        }

        result = None
        error_msg = _('Could not establish connection to the payever API.')
        try:
            response = requests.request(
                method,
                url,
                params=params,
                json=data,
                headers=headers,
                timeout=60,
            )
            if response.status_code == 204:
                return {}
            result = response.json()
            if response.status_code not in (200, 201):
                error_msg = (
                    f"payever API Error [{response.status_code}]: "
                    f"{result.get('message', result)}"
                )
                _logger.error('payever API error: %s', result)
            response.raise_for_status()
        except requests.exceptions.RequestException:
            if silent_errors:
                return {'error': error_msg}
            raise ValidationError('payever: ' + str(error_msg))
        finally:
            if self.payever_debug_logging:
                self._payever_log(method, url, data, result)
        return result

    def _payever_log(self, method, url, request_data, response_data):
        """Write a debug log entry using a separate cursor."""
        db_name = self._cr.dbname
        message = json.dumps({
            'method': method,
            'url': url,
            'request': request_data,
            'response': response_data,
        }, indent=2, default=str)
        try:
            with Registry(db_name).cursor() as cr:
                env = api.Environment(cr, SUPERUSER_ID, {})
                env['ir.logging'].sudo().create({
                    'name': 'payever Payments',
                    'type': 'server',
                    'level': 'DEBUG',
                    'dbname': db_name,
                    'message': message,
                    'func': method,
                    'path': url,
                    'line': str(self.id),
                })
        except psycopg2.Error:
            pass

    # ─────────────────────────────────────────────
    # PUBLIC API METHODS
    # ─────────────────────────────────────────────

    def _payever_create_payment(self, payment_data):
        """Create a new payment via POST /api/v3/payment.

        :param dict payment_data: Full request payload as per payever docs.
        :return: API response containing 'redirect_url' and 'call'.
        :rtype: dict
        """
        return self._payever_make_request('/api/v3/payment', method='POST', data=payment_data)

    def _payever_retrieve_payment(self, payment_id):
        """Retrieve payment details via GET /api/payment/{payment_id}.

        :param str payment_id: The payever payment ID.
        :return: API response with 'result' containing payment details.
        :rtype: dict
        """
        return self._payever_make_request(f'/api/payment/{payment_id}', method='GET')

    def _payever_refund(self, payment_id, amount=None):
        """Issue a (partial) refund via POST /api/payment/refund/{payment_id}.

        :param str payment_id: The payever payment ID.
        :param float amount: Amount to refund. Pass None for a full refund.
        :return: API response.
        :rtype: dict
        """
        payload = {}
        if amount is not None:
            payload['amount'] = round(amount, 2)
        return self._payever_make_request(
            f'/api/payment/refund/{payment_id}', method='POST', data=payload
        )

    def _payever_cancel(self, payment_id, amount=None):
        """Cancel a payment via POST /api/payment/cancel/{payment_id}.

        :param str payment_id: The payever payment ID.
        :param float amount: Amount to cancel. Pass None for a full cancel.
        :return: API response.
        :rtype: dict
        """
        payload = {}
        if amount is not None:
            payload['amount'] = round(amount, 2)
        return self._payever_make_request(
            f'/api/payment/cancel/{payment_id}', method='POST', data=payload
        )

    def _payever_capture(self, payment_id, amount=None):
        """Capture (ship goods) via POST /api/payment/shipping-goods/{payment_id}.

        :param str payment_id: The payever payment ID.
        :param float amount: Amount to capture. Pass None for full capture.
        :return: API response.
        :rtype: dict
        """
        payload = {}
        if amount is not None:
            payload['amount'] = round(amount, 2)
        return self._payever_make_request(
            f'/api/payment/shipping-goods/{payment_id}', method='POST', data=payload
        )

    def _payever_list_payment_options(self):
        """Return available payment options via POST /api/v2/payment/methods.

        :return: List of payment method dicts.
        :rtype: list
        """
        payload = {
            'channel': 'api',
            'currency': 'EUR',
        }
        response = self._payever_make_request(
            '/api/v2/payment/methods', method='POST', data=payload, silent_errors=True
        )
        if response.get('error'):
            return []
        return response.get('result', [])

    # ─────────────────────────────────────────────
    # SIGNATURE VERIFICATION
    # ─────────────────────────────────────────────

    def _payever_verify_notification_signature(self, payment_id, received_signature):
        """Verify the x-payever-signature header from an incoming notification.

        Signature = HMAC-SHA256( client_id + payment_id, client_secret )

        :param str payment_id: payever payment ID extracted from notification payload.
        :param str received_signature: Value of the x-payever-signature header.
        :return: True if valid, False otherwise.
        :rtype: bool
        """
        self.ensure_one()
        if not received_signature:
            return True  # signature header is optional; treat absent as valid for now
        try:
            expected = hmac.new(
                (self.payever_client_secret or '').encode('utf-8'),
                ((self.payever_client_id or '') + payment_id).encode('utf-8'),
                hashlib.sha256,
            ).hexdigest()  # Python's hmac.new() is an alias for hmac.HMAC()
            return hmac.compare_digest(expected, received_signature)
        except Exception:
            return False
