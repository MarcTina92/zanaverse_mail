import frappe


def execute():
    """Fill the new Name column on existing Graph Mail Accounts from their linked User."""
    for acc in frappe.get_all("Graph Mail Account", fields=["name", "user"]):
        if acc.user:
            full_name = frappe.db.get_value("User", acc.user, "full_name")
            frappe.db.set_value("Graph Mail Account", acc.name, "full_name", full_name, update_modified=False)
