import abc
import json
import frappe
import requests # Still needed internally for providers if make_request doesn't support content directly
from frappe.integrations.utils import make_post_request, make_request
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
        """Fetches message templates from the provider's API (or a shared source).
        Returns the raw API response for templates.
        """
        pass

    @abc.abstractmethod
    def get_media_asset(self, media_id: str) -> tuple[bytes, str, str]:
        """Fetches a media asset (image, video, audio, document) by its ID.
        Returns a tuple: (file_content_bytes, mime_type, file_extension).
        Raises an exception on failure.
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
        business_id = self.settings.business_id

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

    def get_media_asset(self, media_id: str) -> tuple[bytes, str, str]:
        token = self.settings.get_password("token")
        base_url = f"{self.settings.url}/{self.settings.version}/"
        headers = {'Authorization': 'Bearer ' + token}

        # First request to get media metadata (URL, mime_type)
        try:
            # Using requests.get directly as frappe.integrations.utils.make_request
            # might not be optimized for binary content download or streaming.
            # It primarily handles JSON responses.
            meta_response = requests.get(f'{base_url}{media_id}/', headers=headers, timeout=10)
            meta_response.raise_for_status()
            media_data = meta_response.json()
        except requests.exceptions.RequestException as e:
            self._log_error_and_throw(
                template_name=None, # No specific template context here
                error_response={},
                error_message=f"Error fetching media metadata for ID {media_id}: {e}",
                error_title=_("WhatsApp Media Metadata Error")
            )
        except json.JSONDecodeError as e:
            self._log_error_and_throw(
                template_name=None,
                error_response={},
                error_message=f"Error decoding JSON for media metadata {media_id}: {e}",
                error_title=_("WhatsApp Media Metadata Error")
            )

        media_url = media_data.get("url")
        mime_type = media_data.get("mime_type")

        if not media_url or not mime_type:
            self._log_error_and_throw(
                template_name=None,
                error_response=media_data,
                error_message=f"Missing media URL or MIME type for ID {media_id}.",
                error_title=_("WhatsApp Media Data Missing")
            )

        file_extension = "bin"
        if '/' in mime_type:
            file_extension = mime_type.split('/')[-1]

        # Second request to download the actual media file
        try:
            media_content_response = requests.get(media_url, headers=headers, timeout=30)
            media_content_response.raise_for_status()
            file_data = media_content_response.content
            return file_data, mime_type, file_extension
        except requests.exceptions.RequestException as e:
            self._log_error_and_throw(
                template_name=None,
                error_response={},
                error_message=f"Error downloading media from {media_url}: {e}",
                error_title=_("WhatsApp Media Download Error")
            )


class ExotelProvider(MetaProvider): # Inherits fetch_templates from MetaProvider
    """Provider for Exotel's WhatsApp API. Reuses MetaProvider's fetch_templates."""

    def send(self, data, template_name=None):
        api_key = self.settings.get("api_key")
        api_token = self.settings.get_password("api_token")
        subdomain = self.settings.get("subdomain")
        sid = self.settings.get("sid")

        url = f"https://{subdomain}/v2/accounts/{sid}/messages"
        auth = (api_key, api_token)
        headers = {"content-type": "application/json"}

        try:
            response = make_post_request(
                url,
                auth=auth,
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

    def get_media_asset(self, media_id: str) -> tuple[bytes, str, str]:
        """Fetches a media asset by its ID from Exotel's API."""
        # Exotel's media fetching might be different.
        # For now, let's assume it also relies on a Meta-like mechanism
        # or that media assets are provided in the webhook directly.
        # If Exotel has its own media API, this method MUST be implemented
        # to call Exotel's API instead.
        # Since you explicitly want to reuse Meta's fetch_templates,
        # and if Exotel doesn't have a direct media retrieval API or if
        # you want to route all media through Meta's, then we can
        # call MetaProvider's implementation or raise an error.

        # Option 1: Raise an error if Exotel doesn't have a media retrieval API
        # frappe.throw(_("Media asset fetching is not supported directly for Exotel provider in this setup."))

        # Option 2: If Exotel's media fetching mechanism is similar enough to Meta's
        # (e.g., they proxy Meta's media or provide similar URLs/tokens),
        # you *could* call super().get_media_asset(media_id) or directly use
        # MetaProvider.get_media_asset(self, media_id)
        # However, this makes Exotel's media handling directly dependent on Meta's,
        # which might not be the case in a real-world Exotel integration.
        # Given your prior instruction for templates, let's explicitly state the limitation.
        # If Exotel *does* have a media API, you should implement it here, similar to MetaProvider.
