"""payever payment module initialisation and post-install hook."""
import base64
import importlib.resources as pkg_resources
import logging

from odoo.addons.payment import reset_payment_provider, setup_provider

from . import const
from . import controllers
from . import models
from . import static

_logger = logging.getLogger(__name__)


def _read_logo(filename):
    """Return the base64-encoded content of a file in the static directory."""
    try:
        return base64.b64encode(
            pkg_resources.files(static).joinpath(filename).read_bytes()
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        _logger.warning('payever: could not read %s: %s', filename, exc)
        return False


def post_init_hook(env):
    """Complete the provider setup and apply the logos on fresh install."""
    setup_provider(env, 'payever')

    providers = env['payment.provider'].with_context(active_test=False).search(
        [('code', '=', 'payever')]
    )
    if providers:
        module = env['ir.module.module'].sudo().search(
            [('name', '=', const.MODULE_NAME)], limit=1
        )
        logo = _read_logo('description/logo.png')
        vals = {}
        if logo:
            vals['image_128'] = logo
        if module:
            vals['module_id'] = module.id
        if vals:
            providers.write(vals)

    method = env.ref(f'{const.MODULE_NAME}.payment_method_payever', raise_if_not_found=False)
    icon = _read_logo('description/icon.png')
    if method and icon:
        method.image = icon


def uninstall_hook(env):
    """Reset the payever provider when the module is uninstalled."""
    reset_payment_provider(env, 'payever')
