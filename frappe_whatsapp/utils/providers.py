import abc
import json
import frappe
from frappe.integrations.utils import make_post_request, make_request # Ensure both are imported
from frappe import _

class BaseProvider(abc.ABC):
    """Abstract base class for WhatsApp providers."""

    def __init__(self, settings):
        self.settings = settings

    @abc.abstractmethod
    def send(self, data, template_name=None):
        """Sends the message and returns the full API response JSON."""
        pass

    @abc.abstractmethod
    def fetch_templates(self):
        """Fetches message templates from the provider's API.
        Returns the raw API response for templates.
        """
        pass

    def _log_error_and_throw(self, template_name, error_response, error_message, error_title="Error"):
        """Logs the error and throws a Frappe exception."""
        frappe.get_doc({
            "doctype": "WhatsApp Notification Log",
            "template": template_name or "Text Message",
            "meta_data": json.dumps(error_response),
        }).insert(ignore_permissions=True)
        frappe.throw(msg=error_message, title=error_title)


class MetaProvider(BaseProvider):
    """Provider for Meta's official WhatsApp Cloud API."""

    def send(self, data, template_name=None):
        token = self.settings.get_password("token")
        headers = {
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
        }
        try:
            response = make_post_request(
                f"{self.settings.url}/{self.settings.version}/{self.settings.phone_id}/messages",
                headers=headers,
                data=json.dumps(data),
            )
            return response
        except Exception as e:
            error_message = str(e)
            error_title = "Error"
            error_response = {}
            if hasattr(frappe.flags, "integration_request") and frappe.flags.integration_request:
                try:
                    error_response = frappe.flags.integration_request.json()
                    res = error_response.get("error", {})
                    error_message = res.get("Error", res.get("message", error_message))
                    error_title = res.get("error_user_title", error_title)
                except (json.JSONDecodeError, AttributeError):
                    pass
            self._log_error_and_throw(template_name, error_response, error_message, error_title)

    def fetch_templates(self):
        token = self.settings.get_password("token")
        url = self.settings.url
        version = self.settings.version
        business_id = self.settings.business_id # Assuming business_id is available in settings for Meta

        if not business_id:
            frappe.throw(_("Meta Business ID is not set in WhatsApp Settings. Cannot fetch templates."))

        headers = {"authorization": f"Bearer {token}", "content-type": "application/json"}

        try:
            response = make_request(
                "GET",
                f"{url}/{version}/{business_id}/message_templates",
                headers=headers,
            )
            return response
        except Exception as e:
            error_message = str(e)
            error_title = "Error"
            error_response = {}
            if hasattr(frappe.flags, "integration_request") and frappe.flags.integration_request:
                try:
                    error_response = frappe.flags.integration_request.json()
                    res = error_response.get("error", {})
                    error_message = res.get("error_user_msg", res.get("message", error_message))
                    error_title = res.get("error_user_title", error_title)
                except (json.JSONDecodeError, AttributeError):
                    pass
            self._log_error_and_throw("Template Fetch", error_response, error_message, error_title)


class ExotelProvider(BaseProvider): # Inherit from BaseProvider, NOT MetaProvider
    """Provider for Exotel's WhatsApp API."""

    def send(self, data, template_name=None):
        api_key = self.settings.get("api_key")
        api_token = self.settings.get_password("api_token")
        subdomain = self.settings.get("subdomain")
        sid = self.settings.get("sid")

        url = f"https://{subdomain}/v2/accounts/{sid}/messages" # Check Exotel docs for v1 or v2
        auth = (api_key, api_token) # Exotel typically uses Basic Auth with key:token
        headers = {"content-type": "application/json"}

        try:
            response = make_post_request(
                url,
                auth=auth, # Use basic auth
                headers=headers,
                data=json.dumps(data),
            )
            return response
        except Exception as e:
            error_message = str(e)
            error_response = {}
            if hasattr(frappe.flags, "integration_request") and frappe.flags.integration_request:
                try:
                    error_response = frappe.flags.integration_request.json()
                    error_message = error_response.get("message", error_message)
                except (json.JSONDecodeError, AttributeError):
                    pass
            self._log_error_and_throw(template_name, error_response, error_message)

    def fetch_templates(self):
        """Fetches message templates from Exotel's API."""


PROVIDER_MAP = {
    "Meta": MetaProvider,
    "Exotel": ExotelProvider,
}


def get_provider(settings):
    """Factory function to get the configured WhatsApp provider."""
    provider_name = settings.get("provider", "Meta")
    provider_class = PROVIDER_MAP.get(provider_name)
    if not provider_class:
        frappe.throw(f"Unknown WhatsApp provider: {provider_name}")
    return provider_class(settings)
