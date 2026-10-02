"""
Send mail via Microsoft Graph on behalf of a rep, and record it as a Frappe
Communication linked to the originating Lead/Deal so it shows in the CRM
Emails tab and can be threaded against later replies.

IMPORTANT: Graph's /sendMail endpoint is fire-and-forget (returns 202, no
message body) and does not let the caller set the outgoing Message-ID -
Exchange always assigns its own. To capture that real Message-ID (which is
what a recipient's reply will reference via In-Reply-To/References), we
create the message as a draft first (which returns internetMessageId), then
send that draft via /messages/{id}/send.
"""

import requests
import frappe
from frappe.utils import now_datetime

from zanaverse_mail.zanaverse_mail.graph_auth import get_access_token
from zanaverse_mail.zanaverse_mail.signature import apply_signature, embed_signature_images

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class GraphSendError(Exception):
    pass


def send_mail(
    sender_user,
    to,
    subject,
    body_html,
    reference_doctype,
    reference_name,
    cc=None,
    bcc=None,
    in_reply_to_message_id=None,
):
    account = _get_graph_mail_account(sender_user)
    mailbox_email = account.mailbox_email
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Signature goes on the outgoing email only; the Communication below
    # keeps the rep's own text so the CRM timeline stays uncluttered.
    outgoing_html = apply_signature(body_html, account)
    try:
        outgoing_html, inline_images = embed_signature_images(outgoing_html)
    except Exception:
        frappe.log_error(title="Graph Mail: embedding signature images failed")
        inline_images = []

    draft_message = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": outgoing_html},
        "attachments": inline_images,
        "toRecipients": _to_recipients(to),
    }
    if cc:
        draft_message["ccRecipients"] = _to_recipients(cc)
    if bcc:
        draft_message["bccRecipients"] = _to_recipients(bcc)

    if in_reply_to_message_id:
        draft_message["internetMessageHeaders"] = [
            {"name": "In-Reply-To", "value": in_reply_to_message_id},
            {"name": "References", "value": in_reply_to_message_id},
        ]

    create_url = f"{GRAPH_BASE}/users/{mailbox_email}/messages"
    create_resp = requests.post(create_url, json=draft_message, headers=headers, timeout=30)

    if create_resp.status_code != 201:
        frappe.log_error(
            title="Graph Mail: draft creation failed",
            message=f"Status {create_resp.status_code} creating draft for {mailbox_email}: {create_resp.text[:500]}",
        )
        raise GraphSendError(f"Failed to create Graph mail draft (HTTP {create_resp.status_code})")

    draft = create_resp.json()
    graph_message_id = draft.get("internetMessageId")
    graph_item_id = draft["id"]

    if not graph_message_id:
        frappe.log_error(
            title="Graph Mail: missing internetMessageId",
            message=f"Draft created for {mailbox_email} but response had no internetMessageId: {draft}",
        )
        raise GraphSendError("Graph draft response did not include an internetMessageId")

    send_url = f"{GRAPH_BASE}/users/{mailbox_email}/messages/{graph_item_id}/send"
    send_resp = requests.post(send_url, headers=headers, timeout=30)

    if send_resp.status_code != 202:
        frappe.log_error(
            title="Graph Mail: send failed",
            message=f"Status {send_resp.status_code} sending draft for {mailbox_email}: {send_resp.text[:500]}",
        )
        raise GraphSendError(f"Failed to send Graph mail draft (HTTP {send_resp.status_code})")

    comm = _create_communication(
        mailbox_email=mailbox_email,
        to=to,
        cc=cc,
        bcc=bcc,
        subject=subject,
        body_html=body_html,
        message_id=graph_message_id,
        reference_doctype=reference_doctype,
        reference_name=reference_name,
        in_reply_to_message_id=in_reply_to_message_id,
    )

    return comm


def _get_graph_mail_account(user):
    account_name = frappe.db.get_value(
        "Graph Mail Account", {"user": user, "enabled": 1}, "name"
    )
    if not account_name:
        frappe.throw(f"No enabled Graph Mail Account found for user {user}")
    return frappe.get_doc("Graph Mail Account", account_name)


def _to_recipients(addresses):
    return [
        {"emailAddress": {"address": addr.strip()}}
        for addr in addresses.split(",")
        if addr.strip()
    ]


def _create_communication(
    mailbox_email,
    to,
    cc,
    bcc,
    subject,
    body_html,
    message_id,
    reference_doctype,
    reference_name,
    in_reply_to_message_id,
):
    comm = frappe.new_doc("Communication")
    comm.communication_type = "Communication"
    comm.communication_medium = "Email"
    comm.sent_or_received = "Sent"
    comm.subject = subject
    comm.content = body_html
    comm.sender = mailbox_email
    comm.recipients = to
    if cc:
        comm.cc = cc
    if bcc:
        comm.bcc = bcc
    comm.reference_doctype = reference_doctype
    comm.reference_name = reference_name
    comm.message_id = message_id
    comm.communication_date = now_datetime()
    comm.insert(ignore_permissions=True)

    if in_reply_to_message_id:
        frappe.db.set_value(
            "Communication", comm.name, "custom_in_reply_to", in_reply_to_message_id
        )

    frappe.db.commit()
    return comm
