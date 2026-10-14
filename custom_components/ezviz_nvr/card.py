"""Load the dashboard card on every dashboard.

The card is registered as a Lovelace resource: the frontend asks for the
resources each time it loads a dashboard, so the card is there even when the
page was opened while Home Assistant was still starting (an extra JS URL is
only written into the page when it is served, and the companion apps keep
that page for a long time). With resources in YAML mode, an extra JS URL is
used instead.
"""
from __future__ import annotations

import logging

from homeassistant.components.frontend import add_extra_js_url, remove_extra_js_url
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def _resources(hass: HomeAssistant):
    data = hass.data.get("lovelace")
    resources = getattr(data, "resources", None)
    if getattr(data, "resource_mode", None) != "storage" or not hasattr(resources, "async_create_item"):
        return None
    return resources


async def async_register_card(hass: HomeAssistant, base_url: str, url: str) -> None:
    """Add or update the card resource (``url`` is ``base_url`` with a version query)."""
    resources = _resources(hass)
    if resources is None:
        add_extra_js_url(hass, url)
        return
    try:
        await resources.async_get_info()  # loads the collection
        mine = [item for item in resources.async_items() if item["url"].split("?")[0] == base_url]
        if not mine:
            await resources.async_create_item({"res_type": "module", "url": url})
        elif mine[0]["url"] != url:
            await resources.async_update_item(mine[0]["id"], {"res_type": "module", "url": url})
        for extra in mine[1:]:
            await resources.async_delete_item(extra["id"])
    except Exception as err:  # noqa: BLE001 - the card must load anyway
        _LOGGER.warning("Could not register the card as a dashboard resource (%s); loading it on every page instead", err)
        add_extra_js_url(hass, url)


async def async_unregister_card(hass: HomeAssistant, base_url: str, url: str) -> None:
    """Remove the card resource (when the integration is removed)."""
    remove_extra_js_url(hass, url)
    resources = _resources(hass)
    if resources is None:
        return
    await resources.async_get_info()
    for item in [i for i in resources.async_items() if i["url"].split("?")[0] == base_url]:
        await resources.async_delete_item(item["id"])
