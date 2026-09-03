"""
Override for frappe.core.doctype.communication.email.make.

The CRM frontend's Send an Email button calls this core Frappe whitelisted
method directly. If the sending user has an enabled Graph Mail Account,
route the send through Microsoft Graph and build our own Communication
record; otherwise fall through to Frappe's native implementation unchanged
so every other caller of this method is unaffected.
"""

import frappe
from frappe.core.doctype.communication.email import make as native_make

from zanaverse_mail.zanaverse_mail.graph_send import send_mail as graph_send_mail


@frappe.whitelist()
def make(
    doctype=None,
    name=None,
    content=None,
    subject=None,
    sent_or_received="Sent",
    sender=None,
    sender_full_name=None,
    recipients=None,
    communication_medium="Email",
    send_email=False,
    print_html=None,
    print_format=None,
    attachments=None,
    send_me_a_copy=False,
    cc=None,
    bcc=None,
    read_receipt=None,
    print_letterhead=True,
    email_template=None,
    communication_type=None,
    send_after=None,
    print_language=None,
    now=False,
    raw_html=False,
    add_css=True,
    in_reply_to=None,
    **kwargs,
):
    current_user = frappe.session.user

    has_graph_account = frappe.db.exists(
        "Graph Mail Account", {"user": current_user, "enabled": 1}
    )

    if has_graph_account and send_email and doctype and name and recipients:
        comm = graph_send_mail(
            sender_user=current_user,
            to=recipients,
            subject=subject or "",
            body_html=content or "",
            reference_doctype=doctype,
            reference_name=name,
            cc=cc,
            bcc=bcc,
            in_reply_to_message_id=in_reply_to,
        )
        return {"name": comm.name, "emails_not_sent_to": ""}

    return native_make(
        doctype=doctype,
        name=name,
        content=content,
        subject=subject,
        sent_or_received=sent_or_received,
        sender=sender,
        sender_full_name=sender_full_name,
        recipients=recipients,
        communication_medium=communication_medium,
        send_email=send_email,
        print_html=print_html,
        print_format=print_format,
        attachments=attachments,
        send_me_a_copy=send_me_a_copy,
        cc=cc,
        bcc=bcc,
        read_receipt=read_receipt,
        print_letterhead=print_letterhead,
        email_template=email_template,
        communication_type=communication_type,
        send_after=send_after,
        print_language=print_language,
        now=now,
        raw_html=raw_html,
        add_css=add_css,
        in_reply_to=in_reply_to,
        **kwargs,
    )
