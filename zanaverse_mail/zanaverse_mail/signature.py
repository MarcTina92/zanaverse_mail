"""
Per-sender email signature for mail sent through Microsoft Graph.

The signature is rendered from templates/emails/zv_signature.html using the
sender's Graph Mail Account fields, falling back to their Employee / User
record for name and title. It is added only to the HTML that goes to Graph;
the CRM Communication keeps the rep's own text, so the timeline stays clean.
"""

import re

import frappe
from frappe.utils import escape_html, get_url

SIG_MARKER = 'data-zv-sig="1"'
TEMPLATE_PATH = "zanaverse_mail/templates/emails/zv_signature.html"

# Where a reply/forward's quoted thread starts. The signature goes just above it.
QUOTE_PATTERNS = [
    r"<blockquote",
    r'<div[^>]*class="[^"]*gmail_quote',
    r'<div[^>]*id="(?:divRplyFwdMsg|mail-editor-reference-message-container)"',
    r"<p[^>]*>\s*On [^<]{5,200}wrote:",
    r"On [^<]{5,200}wrote:",
]


def apply_signature(body_html, account):
    """Return body_html with the sender's signature inserted. Safe to call twice."""
    body_html = body_html or ""
    if not account.get("include_signature"):
        return body_html
    if SIG_MARKER in body_html:
        return body_html

    sig = render_signature(account)
    if not sig:
        return body_html

    block = f'<div {SIG_MARKER} style="margin-top:16px">{sig}</div>'

    cut = _quote_start(body_html)
    if cut is not None:
        return body_html[:cut] + block + "<br>" + body_html[cut:]
    return body_html + block


def render_signature(account):
    try:
        ctx = _signature_context(account)
        if not ctx.get("full_name") or not ctx.get("email"):
            return ""
        return frappe.render_template(TEMPLATE_PATH, ctx)
    except Exception:
        # Never block an email because the signature failed to render.
        frappe.log_error(title="Graph Mail: signature render failed")
        return ""


def _signature_context(account):
    settings = frappe.get_cached_doc("Graph Mail Settings")
    user = account.user

    emp = frappe.db.get_value(
        "Employee",
        {"user_id": user, "status": "Active"},
        ["employee_name", "designation"],
        as_dict=True,
    ) or {}
    user_full_name = frappe.db.get_value("User", user, "full_name") if user else None

    asset_base = (settings.get("signature_asset_base") or f"{get_url()}/files").rstrip("/")
    booking_name = (account.get("sig_full_name") or emp.get("employee_name") or user_full_name or "").split(" ")[0]

    ctx = {
        "full_name": account.get("sig_full_name") or emp.get("employee_name") or user_full_name,
        "designation": account.get("sig_designation") or emp.get("designation"),
        "entity_line": account.get("sig_entity_line"),
        "phone": account.get("sig_phone"),
        "email": account.mailbox_email,
        "website": account.get("sig_website") or settings.get("signature_default_website"),
        "location": account.get("sig_location"),
        "booking_url": account.get("sig_booking_url"),
        "booking_label": account.get("sig_booking_label")
        or (f"{booking_name}\u2019s Booking Page" if booking_name else None),
    }
    # Escape everything that came from a record; asset_base is admin config.
    ctx = {k: escape_html(v) if isinstance(v, str) else v for k, v in ctx.items()}
    ctx["asset_base"] = asset_base
    return ctx


def _quote_start(html):
    starts = []
    for pat in QUOTE_PATTERNS:
        m = re.search(pat, html, flags=re.IGNORECASE)
        if m:
            starts.append(m.start())
    return min(starts) if starts else None
