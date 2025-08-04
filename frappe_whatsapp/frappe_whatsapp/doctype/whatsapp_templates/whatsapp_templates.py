"""Create whatsapp template."""

# Copyright (c) 2022, Shridhar Patil and contributors
# For license information, please see license.txt
import os
import json
import frappe
from frappe_whatsapp.utils.providers import get_provider
import magic
from frappe.model.document import Document
from frappe.integrations.utils import make_post_request, make_request
from frappe.desk.form.utils import get_pdf_link


class WhatsAppTemplates(Document):
    """Create whatsapp template."""

    def validate(self):
        if not self.language_code or self.has_value_changed("language"):
            lang_code = frappe.db.get_value("Language", self.language) or "en"
            self.language_code = lang_code.replace("-", "_")

        if self.header_type in ["IMAGE", "DOCUMENT"] and self.sample:
            self.get_session_id()
            self.get_media_id()

        if not self.is_new():
            self.update_template()


    def get_session_id(self):
        """Upload media."""
        self.get_settings()
        file_path = self.get_absolute_path(self.sample)
        mime = magic.Magic(mime=True)
        file_type = mime.from_file(file_path)

        payload = {
            'file_length': os.path.getsize(file_path),
            'file_type': file_type,
            'messaging_product': 'whatsapp'
        }

        response = make_post_request(
            f"{self._url}/{self._version}/{self._app_id}/uploads",
            headers=self._headers,
            data=json.loads(json.dumps(payload))
        )
        self._session_id = response['id']

    def get_media_id(self):
        self.get_settings()

        headers = {
                "authorization": f"OAuth {self._token}"
            }
        file_name = self.get_absolute_path(self.sample)
        with open(file_name, mode='rb') as file: # b is important -> binary
            file_content = file.read()

        payload = file_content
        response = make_post_request(
            f"{self._url}/{self._version}/{self._session_id}",
            headers=headers,
            data=payload
        )

        self._media_id = response['h']

    def get_absolute_path(self, file_name):
        if(file_name.startswith('/files/')):
            file_path = f'{frappe.utils.get_bench_path()}/sites/{frappe.utils.get_site_base_path()[2:]}/public{file_name}'
        if(file_name.startswith('/private/')):
            file_path = f'{frappe.utils.get_bench_path()}/sites/{frappe.utils.get_site_base_path()[2:]}{file_name}'
        return file_path


    def after_insert(self):
        if self.template_name:
            self.actual_name = self.template_name.lower().replace(" ", "_")

        self.get_settings()
        data = {
            "name": self.actual_name,
            "language": self.language_code,
            "category": self.category,
            "components": [],
        }

        body = {
            "type": "BODY",
            "text": self.template,
        }
        if self.sample_values:
            body.update({"example": {"body_text": [self.sample_values.split(",")]}})

        data["components"].append(body)
        if self.header_type:
            data["components"].append(self.get_header())

        # add footer
        if self.footer:
            data["components"].append({"type": "FOOTER", "text": self.footer})

        try:
            response = make_post_request(
                f"{self._url}/{self._version}/{self._business_id}/message_templates",
                headers=self._headers,
                data=json.dumps(data),
            )
            self.id = response["id"]
            self.status = response["status"]
            self.db_update()
        except Exception as e:
            res = frappe.flags.integration_request.json()["error"]
            error_message = res.get("error_user_msg", res.get("message"))
            frappe.throw(
                msg=error_message,
                title=res.get("error_user_title", "Error"),
            )

    def update_template(self):
        """Update template to meta."""
        self.get_settings()
        data = {"components": []}

        body = {
            "type": "BODY",
            "text": self.template,
        }
        if self.sample_values:
            body.update({"example": {"body_text": [self.sample_values.split(",")]}})
        data["components"].append(body)
        if self.header_type:
            data["components"].append(self.get_header())
        if self.footer:
            data["components"].append({"type": "FOOTER", "text": self.footer})
        try:
            # post template to meta for update
            make_post_request(
                f"{self._url}/{self._version}/{self.id}",
                headers=self._headers,
                data=json.dumps(data),
            )
        except Exception as e:
            raise e
            # res = frappe.flags.integration_request.json()['error']
            # frappe.throw(
            #     msg=res.get('error_user_msg', res.get("message")),
            #     title=res.get("error_user_title", "Error"),
            # )

    def get_settings(self):
        """Get whatsapp settings."""
        settings = frappe.get_doc("WhatsApp Settings", "WhatsApp Settings")
        self._token = settings.get_password("token")
        self._url = settings.url
        self._version = settings.version
        self._business_id = settings.business_id
        self._app_id = settings.app_id

        self._headers = {
            "authorization": f"Bearer {self._token}",
            "content-type": "application/json",
        }

    def on_trash(self):
        self.get_settings()
        url = f"{self._url}/{self._version}/{self._business_id}/message_templates?name={self.actual_name}"
        try:
            make_request("DELETE", url, headers=self._headers)
        except Exception:
            res = frappe.flags.integration_request.json()["error"]
            if res.get("error_user_title") == "Message Template Not Found":
                frappe.msgprint(
                    "Deleted locally", res.get("error_user_title", "Error"), alert=True
                )
            else:
                frappe.throw(
                    msg=res.get("error_user_msg"),
                    title=res.get("error_user_title", "Error"),
                )

    def get_header(self):
        """Get header format."""
        header = {"type": "header", "format": self.header_type}
        if self.header_type == "TEXT":
            header["text"] = self.header
            if self.sample:
                samples = self.sample.split(", ")
                header.update({"example": {"header_text": samples}})
        else:
            pdf_link = ''
            if not self.sample:
                key = frappe.get_doc(self.doctype, self.name).get_document_share_key()
                link = get_pdf_link(self.doctype, self.name)
                pdf_link = f"{frappe.utils.get_url()}{link}&key={key}"
            header.update({"example": {"header_handle": [self._media_id]}})

        return header


@frappe.whitelist()
def fetch():
    settings = frappe.get_doc("WhatsApp Settings", "WhatsApp Settings")
    provider_instance = get_provider(settings)

    # provider_instance.fetch_templates() now returns a list of standardized template dicts
    # We explicitly name it 'normalized_templates' for clarity.
    normalized_templates = provider_instance.fetch_templates()

    # The provider's fetch_templates method should handle logging/throwing errors
    # if the API call itself fails. This check is for an empty list, which could mean no templates.
    if not normalized_templates:
        frappe.msgprint(_("No templates found or an error occurred while fetching templates."), indicator="orange", alert=True)
        return [] # Return an empty list if no templates or error

    for template_data in normalized_templates: # Iterate through the standardized dictionaries
        doc = None
        # Use 'name' for checking existence as it's the consistent unique identifier
        if frappe.db.exists("WhatsApp Templates", {"actual_name": template_data["name"]}):
            doc = frappe.get_doc("WhatsApp Templates", {"actual_name": template_data["name"]})
            is_new_doc = False
        else:
            doc = frappe.new_doc("WhatsApp Templates")
            doc.template_name = template_data["name"] # Frappe DocType name
            doc.actual_name = template_data["name"] # Provider's actual template name
            is_new_doc = True

        # Assign values from the standardized 'template_data' dictionary
        doc.status = template_data.get("status")
        doc.language_code = template_data.get("language_code") # Now consistently 'language_code'
        doc.category = template_data.get("category")
        doc.id = template_data.get("id") # Provider-specific template ID

        # Reset components before populating to ensure consistency if a component is removed remotely
        doc.header_type = None
        doc.header = None
        doc.footer = None
        doc.template = None
        doc.sample_values = None
        doc.sample = None # Assuming 'sample' is for media path, reset it

        # Update components based on the standardized component structure
        for component in template_data.get("components", []):
            if component.get("type") == "HEADER":
                doc.header_type = component.get("format")
                if component.get("format") == "TEXT":
                    doc.header = component.get("text")
                    # 'example_text' is the standardized key for header text examples
                    if component.get("example_text"):
                        doc.sample = ", ".join(str(val) for val in component["example_text"])
                elif component.get("format") in ["IMAGE", "VIDEO", "DOCUMENT"]:
                    # How you handle doc.sample for media headers depends on your DocType.
                    # 'example_handle' might contain media IDs or URLs.
                    # You might need further logic here to fetch/store the media or just store its ID/URL.
                    pass # Keep header_type set, but sample might remain None or require a custom field.

            elif component.get("type") == "FOOTER":
                doc.footer = component.get("text")
            elif component.get("type") == "BODY":
                doc.template = component.get("text")
                # 'example_body_text' is the standardized key for body text examples
                if component.get("example_body_text"):
                    # The example is a list of lists, take the first inner list
                    if component["example_body_text"] and component["example_body_text"][0]:
                        doc.sample_values = ",".join(str(val) for val in component["example_body_text"][0])
                    else:
                        doc.sample_values = None
                else:
                    doc.sample_values = None

            # Add more component types like BUTTONS here if your DocType supports them
            # elif component.get("type") == "BUTTONS":
            #     doc.buttons_json = json.dumps(component.get("buttons")) # Example for storing buttons as JSON

        # Save the document
        if not is_new_doc:
            doc.db_update()
        else:
            doc.db_insert()

        frappe.db.commit()

    return _("Successfully fetched templates from the configured WhatsApp provider")
