"""payever-specific payment method availability metadata."""

from odoo import api, fields, models
from odoo.http import request

from .. import const


class PaymentMethodPayever(models.Model):
    """Store and apply availability returned by payever's method sync."""

    _inherit = 'payment.method'

    payever_synced = fields.Boolean(
        string='Synced from payever',
        readonly=True,
        copy=False,
    )
    payever_currency_id = fields.Many2one(
        comodel_name='res.currency',
        string='payever Limit Currency',
        readonly=True,
        copy=False,
    )
    payever_minimum_amount = fields.Monetary(
        string='Minimum amount',
        currency_field='payever_currency_id',
        readonly=True,
        copy=False,
    )
    payever_maximum_amount = fields.Monetary(
        string='Maximum amount',
        currency_field='payever_currency_id',
        readonly=True,
        copy=False,
    )
    payever_country_ids = fields.Many2many(
        comodel_name='res.country',
        relation='payment_method_payever_country_rel',
        column1='payment_method_id',
        column2='country_id',
        string='Allowed countries',
        readonly=True,
        copy=False,
    )
    payever_country_label = fields.Char(
        string='Allowed countries',
        compute='_compute_payever_country_label',
    )
    payever_business_type = fields.Selection(
        selection=[
            ('b2c', 'B2C'),
            ('b2b', 'B2B'),
            ('mixed', 'Mixed'),
        ],
        string='Business type',
        default='mixed',
        readonly=True,
        copy=False,
    )
    payever_is_redirect_method = fields.Boolean(
        string='Redirect method',
        readonly=True,
        copy=False,
    )
    payever_force_redirect = fields.Boolean(
        string='Force Redirect',
        copy=False,
        help='Force payever to redirect the customer for this payment method.',
    )

    @api.depends('payever_country_ids')
    def _compute_payever_country_label(self):
        for method in self:
            codes = sorted(method.payever_country_ids.mapped('code'))
            if len(codes) == 1:
                method.payever_country_label = codes[0]
            elif codes:
                method.payever_country_label = f'Multiple ({len(codes)})'
            else:
                method.payever_country_label = ''

    @api.model
    def _get_compatible_payment_methods(
        self, provider_ids, partner_id, currency_id=None, force_tokenization=False,
        is_express_checkout=False, **kwargs
    ):
        """Filter payever methods by synced country and basket amount."""
        methods = super()._get_compatible_payment_methods(
            provider_ids,
            partner_id,
            currency_id=currency_id,
            force_tokenization=force_tokenization,
            is_express_checkout=is_express_checkout,
            **kwargs,
        )
        partner = self.env['res.partner'].browse(partner_id)
        currency = self.env['res.currency'].browse(currency_id).exists()
        amount = self._payever_get_checkout_amount(kwargs)
        is_b2b = self._payever_is_b2b_checkout(partner, kwargs)
        declined_ids = self._payever_get_declined_method_ids(kwargs.get('sale_order_id'))

        return methods.filtered(
            lambda method: method.id not in declined_ids and method._payever_is_available(
                provider_ids, partner.country_id, amount, currency, is_b2b
            )
        )

    @api.model
    def _payever_get_declined_method_ids(self, sale_order_id):
        """Return the ids of the methods payever declined for this session's order."""
        session = getattr(request, 'session', None) if request else None
        if not sale_order_id or session is None:
            return set()
        declined = session.get(const.DECLINED_METHODS_SESSION_KEY) or {}
        return set(declined.get(str(sale_order_id)) or [])

    @api.model
    def _payever_get_checkout_amount(self, kwargs):
        """Get the amount to filter, focusing on the webshop basket flow."""
        amount = kwargs.get('amount')
        if amount is not None:
            return amount

        sale_order_id = kwargs.get('sale_order_id')
        if sale_order_id and self.env.registry.get('sale.order'):
            order = self.env['sale.order'].browse(sale_order_id).exists()
            if order:
                return max(order.amount_total - order.amount_paid, 0.0)
        return None

    @api.model
    def _payever_is_b2b_checkout(self, partner, kwargs):
        """Return whether billing or shipping contains a company name."""
        partners = partner
        sale_order_id = kwargs.get('sale_order_id')
        if sale_order_id and self.env.registry.get('sale.order'):
            order = self.env['sale.order'].browse(sale_order_id).exists()
            if order:
                partners |= order.partner_invoice_id | order.partner_shipping_id
        return any(self._payever_partner_has_company(record) for record in partners)

    @api.model
    def _payever_partner_has_company(self, partner):
        """Return whether a partner represents or names a company."""
        return bool(
            partner
            and (
                partner.is_company
                or partner.company_name
                or (partner.parent_id and partner.parent_id.is_company)
            )
        )

    def _payever_is_available(self, provider_ids, country, amount, currency, is_b2b=None):
        """Return whether this method is available through a relevant provider."""
        self.ensure_one()
        relevant_providers = self.provider_ids.filtered(lambda p: p.id in provider_ids)
        payever_providers = relevant_providers.filtered(lambda p: p.code == 'payever')
        other_providers = relevant_providers - payever_providers

        # Do not restrict a shared method that remains available through another provider.
        if not self.payever_synced or not payever_providers or other_providers:
            return True
        business_type = self.payever_business_type or 'mixed'
        if is_b2b is True and business_type == 'b2c':
            return False
        if is_b2b is False and business_type == 'b2b':
            return False
        if country and self.payever_country_ids and country not in self.payever_country_ids:
            return False
        if amount is None or not currency or not self.payever_currency_id:
            return True

        limit_amount = (
            currency._convert(
                amount,
                self.payever_currency_id,
                payever_providers[:1].company_id,
                fields.Date.context_today(self),
            )
            if currency != self.payever_currency_id
            else amount
        )
        if (
            self.payever_minimum_amount
            and self.payever_currency_id.compare_amounts(
                limit_amount, self.payever_minimum_amount
            ) < 0
        ):
            return False
        if (
            self.payever_maximum_amount
            and self.payever_currency_id.compare_amounts(
                limit_amount, self.payever_maximum_amount
            ) > 0
        ):
            return False
        return True
