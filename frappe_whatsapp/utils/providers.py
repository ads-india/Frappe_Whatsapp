import abc
import json
import json
import frappe
from frappe.integrations.utils import make_post_request, make_request
from frappe import _

class StandardMessageResponse:
    def __init__(self, message_id=None, status="sent", raw_response=None, error_message=None):
        self.message_id = message_id
        self.status = status
        self.raw_response = raw_response
        self.error_message = error_message

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
    def send(self, data, template_name=None) -> StandardMessageResponse:
        token = self.settings.get_password("token")
        headers = {
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
        }
        try:
            raw_response = make_post_request(
                f"{self.settings.url}/{self.settings.version}/{self.settings.phone_id}/messages",
                headers=headers,
                data=json.dumps(data),
            )
            message_id = raw_response.get("messages", [{}])[0].get("id")
            return StandardMessageResponse(message_id=message_id, status="sent", raw_response=raw_response)
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
            return StandardMessageResponse(status="failed", raw_response=error_response, error_message=error_message)


    def fetch_templates(self) -> list[dict]:
        token = self.settings.get_password("token")
        url = self.settings.url
        version = self.settings.version
        business_id = self.settings.business_id

        if not business_id:
            frappe.throw(_("Meta Business ID is not set in WhatsApp Settings. Cannot fetch templates."))

        headers = {"authorization": f"Bearer {token}", "content-type": "application/json"}

        try:
            raw_response = make_request(
                "GET",
                f"{url}/{version}/{business_id}/message_templates",
                headers=headers,
            )
            meta_templates = raw_response.get("data", [])
            normalized_templates = []

            for tpl in meta_templates:
                normalized_template = {
                    "name": tpl.get("name"),
                    "id": tpl.get("id"),
                    "status": tpl.get("status"),
                    "language_code": tpl.get("language"),
                    "category": tpl.get("category"),
                    "components": []
                }

                for component in tpl.get("components", []):
                    standard_component = {"type": component.get("type")}
                    if component["type"] == "HEADER":
                        standard_component["format"] = component.get("format")
                        if component.get("format") == "TEXT":
                            standard_component["text"] = component.get("text")
                            if component.get("example", {}).get("header_text"):
                                standard_component["example_text"] = component["example"]["header_text"]
                        elif component.get("format") in ["IMAGE", "VIDEO", "DOCUMENT"]:
                            if component.get("example", {}).get("header_handle"):
                                standard_component["example_handle"] = component["example"]["header_handle"]
                    elif component["type"] == "BODY":
                        standard_component["text"] = component.get("text")
                        if component.get("example", {}).get("body_text"):
                            standard_component["example_body_text"] = component["example"]["body_text"]
                    elif component["type"] == "FOOTER":
                        standard_component["text"] = component.get("text")
                    elif component["type"] == "BUTTONS":
                        standard_component["buttons"] = component.get("buttons")

                    normalized_template["components"].append(standard_component)
                normalized_templates.append(normalized_template)

            return normalized_templates
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
            return []


class ExotelProvider(BaseProvider):
    def send(self, data, template_name=None) -> StandardMessageResponse:
        api_key = self.settings.get("api_key")
        api_token = self.settings.get_password("api_token")
        subdomain = self.settings.get("subdomain")
        sid = self.settings.get("sid")
        from_number = self.settings.get("from_number")

        if not from_number:
            frappe.throw(_("Exotel 'From' number is not set in WhatsApp Settings. Cannot send message."))

        to_number = data.pop("to")
        content = data

        exotel_payload = {
            "whatsapp": {
                "messages": [
                    {"from": from_number, "to": to_number, "content": content}
                ]
            }
        }

        status_callback_url = self.settings.get("status_callback_url")
        if status_callback_url:
            exotel_payload["status_callback"] = status_callback_url

        url = f"https://{api_key}:{api_token}@{subdomain}/v2/accounts/{sid}/messages"
        headers = {"content-Type": "application/json"}
        try:
            raw_response = make_post_request(
                url,
                headers=headers,
                data=json.dumps(exotel_payload),
            )
            message_id = raw_response.get("response", {}).get("whatsapp", {}).get("messages", [{}])[0].get("data", {}).get("sid")
            return StandardMessageResponse(message_id=message_id, status="sent", raw_response=raw_response)
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
            return StandardMessageResponse(status="failed", raw_response=error_response, error_message=error_message)

    def fetch_templates(self) -> list[dict]:
        api_key = self.settings.get("api_key")
        api_token = self.settings.get_password("api_token")
        subdomain = self.settings.get("subdomain")
        sid = self.settings.get("sid")
        waba_id = self.settings.get("waba_id")

        if not waba_id:
            frappe.throw(_("Exotel WhatsApp Business Account ID (WABA ID) is not set in WhatsApp Settings. Cannot fetch templates."))

        url = f"https://{api_key}:{api_token}@{subdomain}/v2/accounts/{sid}/templates?waba_id={waba_id}"
        headers = {"content-type": "application/json"}

        try:
            raw_response = make_request(
                "GET",
                url,
                headers=headers,
            )
            exotel_templates = raw_response.get("data", [])
            normalized_templates = []

            for tpl in exotel_templates:
                normalized_template = {
                    "name": tpl.get("name"),
                    "id": tpl.get("template_id"),
                    "status": tpl.get("status"),
                    "language_code": tpl.get("language"),
                    "category": tpl.get("category"),
                    "components": []
                }

                for component in tpl.get("components", []):
                    standard_component = {"type": component.get("type")}
                    if component["type"] == "HEADER":
                        standard_component["format"] = component.get("format")
                        if component.get("format") == "TEXT":
                            standard_component["text"] = component.get("text")
                            if component.get("example", {}).get("header_text"):
                                standard_component["example_text"] = component["example"]["header_text"]
                        elif component.get("format") in ["IMAGE", "VIDEO", "DOCUMENT"]:
                            if component.get("example", {}).get("header_handle"):
                                standard_component["example_handle"] = component["example"]["header_handle"]
                    elif component["type"] == "BODY":
                        standard_component["text"] = component.get("text")
                        if component.get("example", {}).get("body_text"):
                            standard_component["example_body_text"] = component["example"]["body_text"]
                    elif component["type"] == "FOOTER":
                        standard_component["text"] = component.get("text")
                    elif component["type"] == "BUTTONS":
                        standard_component["buttons"] = component.get("buttons")

                    normalized_template["components"].append(standard_component)
                normalized_templates.append(normalized_template)

            return normalized_templates
        except Exception as e:
            error_message = str(e)
            error_response = {}
            if hasattr(frappe.flags, "integration_request") and frappe.flags.integration_request:
                try:
                    error_response = frappe.flags.integration_request.json()
                    error_message = error_response.get("message", error_message)
                except (json.JSONDecodeError, AttributeError):
                    pass
            self._log_error_and_throw("Template Fetch", error_response, error_message)
            return [] # Return empty list on failure after logging



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

def get_message_id_from_provider_response(response: dict) -> str | None:
    """
    Parses the raw API response from a WhatsApp provider to extract the message ID.
    Handles different response structures for Meta and Exotel.

    Args:
        response (dict): The raw dictionary response from the WhatsApp provider's API.

    Returns:
        str | None: The extracted message ID if successful, otherwise None.
                    Raises a Frappe error if parsing fails for a known provider.
    """
    provider_name = frappe.db.get_value("WhatsApp Settings", None, "provider")
    message_id = None # Initialize message_id

    if provider_name == "Exotel":
        try:
            # For Exotel, the message ID (sid) is nested deeper
            message_id = response["response"]["whatsapp"]["messages"][0]["data"]["sid"]
        except (KeyError, IndexError) as e:
            # Log the error with the full response for debugging
            frappe.log_error(
                f"Exotel response missing expected keys for message ID: {e} | Response: {response}",
                "Exotel Response Parsing Error"
            )
            # Re-throw as a Frappe error to stop execution and inform the user
            frappe.throw(_(f"Failed to parse Exotel response for message ID. Please check server logs for details. Raw Response: {json.dumps(response)}"))
    elif provider_name == "Meta":
        try:
            # For Meta, the message ID is at the top level
            message_id = response["messages"][0]["id"]
        except (KeyError, IndexError) as e:
            # Log the error with the full response for debugging
            frappe.log_error(
                f"Meta response missing expected keys for message ID: {e} | Response: {response}",
                "Meta Response Parsing Error"
            )
            # Re-throw as a Frappe error
            frappe.throw(_(f"Failed to parse Meta response for message ID. Please check server logs for details. Raw Response: {json.dumps(response)}"))
    else:
        # Handle cases where provider is not 'Exotel' or 'Meta', or is not set
        frappe.log_warn(
            f"Unknown provider '{provider_name}' or unhandled response format for message_id extraction. Response: {response}",
            "WhatsApp Message ID Parsing Warning"
        )
        # Optionally, you could try a generic guess here if desired, e.g.,
        # try:
        #     message_id = response.get("messages", [{}])[0].get("id")
        # except Exception:
        #     pass # Keep message_id as None

    return message_id
