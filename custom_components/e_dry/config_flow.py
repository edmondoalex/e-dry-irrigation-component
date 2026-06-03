
from __future__ import annotations
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import callback

from .const import DOMAIN


class EDry2ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for e-dry."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(
                title=user_input[CONF_NAME],
                data={CONF_NAME: user_input[CONF_NAME]},
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_NAME, default="e-dry Irrigation"): str}
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        from .options_flow import EDry2OptionsFlow
        return EDry2OptionsFlow(config_entry)
