"""
Scheduled poll job: for each enabled Graph Mail Account, query Microsoft Graph
for inbound mail received since the last poll (a simple receivedDateTime
time-window query, not delta - delta proved to have inconsistent behaviour
during testing, missing messages that were independently confirmed present
via a direct query), and thread any message that is a reply to a
Zanaverse-originated email back onto the correct CRM Lead/Deal.

Matching strategy (v1, deliberately strict): only thread via In-Reply-To /
References headers against Message-IDs we ourselves generated at send time.
No sender-address fallback matching - a known, deliberate scope limit.
"""

import re
import requests
import frappe
from datetime import datetime, timedelta
from frappe.utils import now_datetime, get_datetime

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

INITIAL_LOOKBACK_MINUTES = 60


def poll_all_accounts():
    settings = frappe.get_single("Graph Mail Settings")
    if not settings.enabled:
        return

    accounts = frappe.get_all("Graph Mail Account", filters={"enabled": 1}, fields=["name"])

    for acc in accounts:
        try:
            _poll_account(acc.name)
        except Exception:
            frappe.log_error(
                title="Graph Mail: poll failed",
                message=frappe.get_traceback(),
            )
            frappe.db.set_value(
                "Graph Mail Account",
                acc.name,
                {"last_sync_status": "Error", "last_error": frappe.get_traceback()[:500]},
            )
            frappe.db.commit()

    frappe.db.set_value("Graph Mail Settings", None, "last_poll_at", now_datetime())
    frappe.db.commit()


def _poll_account(account_name):
    from zanaverse_mail.zanaverse_mail.graph_auth import get_access_token

    account = frappe.get_doc("Graph Mail Account", account_name)
    mailbox_email = account.mailbox_email
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    since = _get_since_timestamp(account)
    since_iso = since.strftime("%Y-%m-%dT%H:%M:%SZ")

    url = (
        f"{GRAPH_BASE}/users/{mailbox_email}/mailFolders/inbox/messages"
        f"?$filter=receivedDateTime ge {since_iso}"
        "&$select=id,internetMessageId,subject,internetMessageHeaders,from,receivedDateTime,body,hasAttachments"
        "&$orderby=receivedDateTime asc"
        "&$top=50"
    )

    all_messages = []
    next_link = url

    while next_link:
        response = requests.get(next_link, headers=headers, timeout=30)
        if response.status_code != 200:
            frappe.throw(
                f"Graph inbox query failed for {mailbox_email} (HTTP {response.status_code}): "
                f"{response.text[:300]}"
            )
        payload = response.json()
        all_messages.extend(payload.get("value", []))
        next_link = payload.get("@odata.nextLink")

    for message in all_messages:
        _process_message(message, account)

    frappe.db.set_value(
        "Graph Mail Account",
        account_name,
        {"last_sync_status": "Success", "last_error": "", "last_poll_completed_at": now_datetime()},
    )
    frappe.db.commit()


def _get_since_timestamp(account):
    last_completed = frappe.db.get_value(
        "Graph Mail Account", account.name, "last_poll_completed_at"
    )
    if last_completed:
        # last_poll_completed_at was stored as site-local time (from
        # now_datetime()); convert it to real UTC before comparing against
        # Graph, which always expects UTC.
        local_dt = get_datetime(last_completed)
        return _local_to_utc(local_dt)
    return datetime.utcnow() - timedelta(minutes=INITIAL_LOOKBACK_MINUTES)


def _local_to_utc(local_dt):
    import pytz
    site_tz = frappe.utils.get_system_timezone()
    localized = pytz.timezone(site_tz).localize(local_dt)
    return localized.astimezone(pytz.utc).replace(tzinfo=None)


def _process_message(message, account):
    headers = {
        h["name"].lower(): h["value"] for h in message.get("internetMessageHeaders", []) or []
    }

    in_reply_to = headers.get("in-reply-to")
    references = headers.get("references")

    candidate_ids = _extract_message_ids(in_reply_to) + _extract_message_ids(references)
    if not candidate_ids:
        return

    matched_comm = None
    for msg_id in candidate_ids:
        matched_comm = frappe.db.get_value(
            "Communication",
            {"message_id": msg_id},
            ["name", "reference_doctype", "reference_name"],
            as_dict=True,
        )
        if matched_comm:
            break

    if not matched_comm:
        return

    internet_message_id = message.get("internetMessageId") or ""
    if internet_message_id and frappe.db.exists("Communication", {"message_id": internet_message_id}):
        return

    sender_address = message.get("from", {}).get("emailAddress", {}).get("address", "")
    subject = message.get("subject", "")
    body_content = message.get("body", {}).get("content") or message.get("bodyPreview", "")

    comm = frappe.new_doc("Communication")
    comm.communication_type = "Communication"
    comm.communication_medium = "Email"
    comm.sent_or_received = "Received"
    comm.subject = subject
    comm.content = body_content
    comm.sender = sender_address
    comm.recipients = account.mailbox_email
    comm.reference_doctype = matched_comm.reference_doctype
    comm.reference_name = matched_comm.reference_name
    comm.message_id = internet_message_id or None
    received = message.get("receivedDateTime")
    comm.communication_date = get_datetime(received.replace("Z", "")) if received else now_datetime()
    comm.insert(ignore_permissions=True)

    if "cid:" in (body_content or "").lower():
        try:
            _attach_inline_images(comm, message, account)
        except Exception:
            # Never lose the email over its pictures; it just shows without them.
            frappe.log_error(title="Graph Mail: inline images failed", message=frappe.get_traceback())

    frappe.db.commit()


MAX_INLINE_BYTES = 5 * 1024 * 1024


def _attach_inline_images(comm, message, account):
    """Save the email's embedded (cid:) images as private files on the
    Communication and point the HTML at them, so they display in the CRM."""
    import base64
    from zanaverse_mail.zanaverse_mail.graph_auth import get_access_token

    message_id = message.get("id")
    if not message_id:
        return

    token = get_access_token()
    url = f"{GRAPH_BASE}/users/{account.mailbox_email}/messages/{message_id}/attachments"
    response = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    if response.status_code != 200:
        frappe.throw(f"Graph attachments query failed (HTTP {response.status_code}): {response.text[:300]}")

    html = comm.content or ""
    changed = False
    for att in response.json().get("value", []):
        if att.get("@odata.type") != "#microsoft.graph.fileAttachment":
            continue
        content_id = (att.get("contentId") or "").strip("<>")
        if not content_id or not att.get("contentBytes"):
            continue
        if not re.search(re.escape(f"cid:{content_id}"), html, flags=re.IGNORECASE):
            continue
        data = base64.b64decode(att["contentBytes"])
        if len(data) > MAX_INLINE_BYTES:
            continue

        file_doc = frappe.get_doc({
            "doctype": "File",
            "file_name": att.get("name") or f"inline-{content_id}",
            "content": data,
            "is_private": 1,
            "attached_to_doctype": "Communication",
            "attached_to_name": comm.name,
        }).insert(ignore_permissions=True)

        html = re.sub(re.escape(f"cid:{content_id}"), file_doc.file_url, html, flags=re.IGNORECASE)
        changed = True

    if changed:
        frappe.db.set_value("Communication", comm.name, "content", html, update_modified=False)


def _extract_message_ids(header_value):
    if not header_value:
        return []
    return re.findall(r"<[^<>]+>", header_value)
