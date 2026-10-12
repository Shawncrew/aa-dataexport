from allianceauth import hooks
from allianceauth.services.hooks import UrlHook

from . import urls


@hooks.register("url_hook")
def register_urls():
    # AA wraps hooked views in login_required unless excluded. These are called by
    # other servers, not logged-in users: access is decided per request by API key.
    return UrlHook(
        urls,
        "dataexport",
        r"^dataexport/",
        excluded_views=["dataexport.views.index", "dataexport.views.endpoint"],
    )
