"""payever payment module initialisation and post-install hook."""
import base64
import importlib.resources as pkg_resources

from . import controllers
from . import models
from . import static


def post_init_hook(env):
    """Set the provider logo on fresh install."""
    providers = env['payment.provider'].with_context(active_test=False).search(
        [('code', '=', 'payever')]
    )
    if not providers:
        return

    try:
        logo_data = pkg_resources.files(static).joinpath('description/logo.png').read_bytes()
        providers.write({'image_128': base64.b64encode(logo_data)})
    except Exception:
        pass
