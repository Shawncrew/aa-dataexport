from allianceauth import hooks
from allianceauth.services.hooks import UrlHook

from . import urls


@hooks.register("url_hook")
def register_urls():
    # Mounted without login_required: access is decided per request by API key.
    return UrlHook(urls, "dataexport", r"^dataexport/")
