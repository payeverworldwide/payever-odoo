"""payever payment module initialisation and post-install hook."""
import base64
import importlib.resources as pkg_resources

from odoo.addons.payment import reset_payment_provider, setup_provider

from . import controllers
from . import models
from . import static


def post_init_hook(env):
    """Complete provider setup and set the logo on fresh install."""
    setup_provider(env, 'payever')
    providers = env['payment.provider'].with_context(active_test=False).search(
        [('code', '=', 'payever')]
    )
    if not providers:
        return

    try:
        logo_data = pkg_resources.files(static).joinpath('description/logo.png').read_bytes()
        providers.write({'image_128': base64.b64encode(logo_data)})
    except Exception:  # pylint: disable=broad-exception-caught
        pass


def uninstall_hook(env):
    """Reset the payever provider when the module is uninstalled."""
    reset_payment_provider(env, 'payever')
