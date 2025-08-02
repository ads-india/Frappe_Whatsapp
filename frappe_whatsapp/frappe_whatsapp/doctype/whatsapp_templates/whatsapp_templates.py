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
    """Fetch templates from the configured WhatsApp provider."""

    # 1. Get WhatsApp Settings
    settings = frappe.get_doc("WhatsApp Settings", "WhatsApp Settings")

    # 2. Get the appropriate provider instance using the factory function
    # This abstracts away which specific API (Meta, Exotel, etc.) is being used.
    provider_instance = get_provider(settings)

    # 3. Use the provider's fetch_templates method
    # The provider is now responsible for making the actual API call,
    # handling its specific authentication, URLs, and initial error logging.
    # It will throw a Frappe exception if an API error occurs, which will be caught by Frappe.
    response = provider_instance.fetch_templates()

    # 4. Process the response (common logic for all providers)
    if not response or "data" not in response:
        frappe.throw(_("No templates data received from the provider."))

    for template in response["data"]:
        doc = None
        # Check if the template already exists using actual_name (Meta's template name)
        if frappe.db.exists("WhatsApp Templates", {"actual_name": template["name"]}):
            doc = frappe.get_doc("WhatsApp Templates", {"actual_name": template["name"]})
            is_new_doc = False
        else:
            doc = frappe.new_doc("WhatsApp Templates")
            doc.template_name = template["name"] # Frappe DocType name
            doc.actual_name = template["name"] # Meta's actual template name
            is_new_doc = True

        doc.status = template["status"]
        doc.language_code = template["language"]
        doc.category = template["category"]
        doc.id = template["id"] # Meta's template ID

        # Update components
        for component in template.get("components", []):
            if component["type"] == "HEADER":
                doc.header_type = component["format"]
                if component["format"] == "TEXT":
                    doc.header = component.get("text")
            elif component["type"] == "FOOTER":
                doc.footer = component.get("text")
            elif component["type"] == "BODY":
                doc.template = component.get("text")
                if component.get("example"):
                    body_text_examples = component["example"].get("body_text", [])
                    if body_text_examples and body_text_examples[0]:
                        doc.sample_values = ",".join(str(val) for val in body_text_examples[0])
                    else:
                        doc.sample_values = None
                else:
                    doc.sample_values = None

        # Save the document
        if not is_new_doc:
            doc.db_update() # Update existing document, ignoring hooks
        else:
            doc.db_insert() # Insert new document, ignoring hooks

        frappe.db.commit() # Commit after each template to ensure atomic operations

    return _("Successfully fetched templates from the configured WhatsApp provider")
