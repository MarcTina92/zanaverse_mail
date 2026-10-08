"""
Each person may open and edit only their own Graph Mail Account, and only its
Email Signature section. Mailbox, user and sync fields are permlevel 1, which
only System Manager can change. System Manager keeps full access to all accounts.
"""

import frappe


def _is_admin(user):
	return user == "Administrator" or "System Manager" in frappe.get_roles(user)


def graph_mail_account_query(user=None):
	user = user or frappe.session.user
	if _is_admin(user):
		return ""
	return f"`tabGraph Mail Account`.`user` = {frappe.db.escape(user)}"


def graph_mail_account_has_permission(doc, ptype=None, user=None):
	user = user or frappe.session.user
	if _is_admin(user):
		return True
	if ptype in ("create", "delete", "submit", "cancel", "amend", "share"):
		return False
	return doc.user == user


@frappe.whitelist()
def my_signature_url():
	"""Link straight to the caller's own account, for a 'My Email Signature' shortcut."""
	name = frappe.db.get_value("Graph Mail Account", {"user": frappe.session.user}, "name")
	if not name:
		frappe.throw("You don't have a mail account set up yet. Please ask an administrator.")
	return f"/app/graph-mail-account/{name}"
