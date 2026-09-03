"""
Microsoft Graph authentication - client credentials flow with token caching.

Best practice: cache the access token in-memory per process, keyed by tenant_id,
and refresh proactively before expiry rather than fetching a fresh token on every
call. Tokens are short-lived secrets and are never persisted to disk or database.
"""

import time
import requests
import frappe

_token_cache = {}
EXPIRY_BUFFER_SECONDS = 300
GRAPH_SCOPE = "https://graph.microsoft.com/.default"


class GraphAuthError(Exception):
    pass


def get_access_token(force_refresh=False):
    settings = frappe.get_single("Graph Mail Settings")

    if not settings.enabled:
        frappe.throw("Graph Mail Settings is not enabled.")

    tenant_id = settings.tenant_id
    cached = _token_cache.get(tenant_id)

    if not force_refresh and cached and cached["expires_at"] - time.time() > EXPIRY_BUFFER_SECONDS:
        return cached["access_token"]

    return _fetch_new_token(settings)


def _fetch_new_token(settings):
    tenant_id = settings.tenant_id
    client_id = settings.client_id
    client_secret = settings.get_password("client_secret")

    if not (tenant_id and client_id and client_secret):
        frappe.throw("Graph Mail Settings is missing Tenant ID, Client ID, or Client Secret.")

    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": GRAPH_SCOPE,
        "grant_type": "client_credentials",
    }

    response = requests.post(url, data=data, timeout=30)

    if response.status_code != 200:
        frappe.log_error(
            title="Graph Mail: token fetch failed",
            message=f"Status {response.status_code} fetching token for tenant {tenant_id}",
        )
        raise GraphAuthError(f"Failed to obtain Graph access token (HTTP {response.status_code})")

    payload = response.json()
    access_token = payload["access_token"]
    expires_in = payload.get("expires_in", 3600)

    _token_cache[tenant_id] = {
        "access_token": access_token,
        "expires_at": time.time() + int(expires_in),
    }

    return access_token
