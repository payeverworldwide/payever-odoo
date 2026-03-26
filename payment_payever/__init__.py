"""payever payment module initialisation and post-install hook."""
import base64
import os

from . import controllers
from . import models


def post_init_hook(env):
    """Set the provider logo on fresh install."""
    providers = env['payment.provider'].with_context(active_test=False).search(
        [('code', '=', 'payever')]
    )
    if not providers:
        return

    logo_path = os.path.join(os.path.dirname(__file__), 'static', 'description', 'logo.png')
    if os.path.exists(logo_path):
        with open(logo_path, 'rb') as fh:
            providers.write({'image_128': base64.b64encode(fh.read())})
