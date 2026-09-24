"""
Build the taintgate policy from structured Sanity content.

The limits come from the `supportSettings` document's number and list fields
(maxAutoRefund, officialEmailDomains), never from article or forum text, so
a poisoned page can't loosen the rules it is trying to get around.
"""

import json
import urllib.parse
import urllib.request

from taintgate import Policy

from . import config
from .backend import CUSTOMER, ORDERS

SETTINGS_QUERY = '*[_id == "supportSettings"][0]{maxAutoRefund, refundWindowDays, officialEmailDomains}'


def fetch_settings():
    url = (f"https://{config.PROJECT_ID}.api.sanity.io/v2025-02-19/data/query/{config.DATASET}"
           f"?query={urllib.parse.quote(SETTINGS_QUERY)}")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {config.secret('project_token')}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        settings = json.load(r)["result"]
    if not settings:
        raise RuntimeError("supportSettings document not found in Sanity")
    return validate(settings)


def validate(settings):
    """The structured fields are the security boundary, so refuse to start on bad values."""
    problems = []
    for field in ("maxAutoRefund", "refundWindowDays"):
        value = settings.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            problems.append(f"{field} must be a non-negative number, got {value!r}")
    domains = settings.get("officialEmailDomains")
    if not isinstance(domains, list) or not domains or not all(
            isinstance(d, str) and "." in d and "@" not in d for d in domains):
        problems.append(f"officialEmailDomains must be a non-empty list of domains, got {domains!r}")
    if problems:
        raise ValueError("invalid supportSettings: " + "; ".join(problems))
    return settings


def build_policy(settings):
    max_refund = settings["maxAutoRefund"]
    window = settings["refundWindowDays"]
    domains = settings["officialEmailDomains"]
    my_orders = [oid for oid, o in ORDERS.items() if o["customer"] == CUSTOMER["id"]]
    # Not yet delivered, or delivered within the refund window from Sanity.
    in_window = [oid for oid in my_orders
                 if ORDERS[oid]["delivered_days_ago"] is None or ORDERS[oid]["delivered_days_ago"] <= window]
    return Policy.from_dict({
        "default": "ask",
        # Only knowledge-base reads can carry injected text. The order system is ours.
        "untrusted_sources": ["kb_*"],
        "rules": [
            {"tool": ["kb_*", "list_my_orders"], "action": "allow"},

            # Refunds: small, on the customer's own order, inside the refund window -> fine.
            {"tool": "issue_refund", "action": "allow",
             "when": {"order_id": {"in": in_window}, "amount": {"max": max_refund}},
             "reason": f"refund of at most ${max_refund} on the customer's own order"},
            {"tool": "issue_refund", "action": "ask",
             "when": {"order_id": {"in": my_orders, "not_in": in_window}},
             "reason": f"order is outside the {window}-day refund window"},
            {"tool": "issue_refund", "action": "ask",
             "when": {"amount": {"min": max_refund + 0.01}},
             "reason": f"refund above the ${max_refund} automatic limit"},
            {"tool": "issue_refund", "action": "deny",
             "when": {"order_id": {"not_in": my_orders}},
             "reason": "order does not belong to this customer"},
            {"tool": "issue_refund", "action": "deny",
             "when": {"order_id": {"untrusted": True}},
             "reason": "order number came from help-center content, not the customer"},

            # Email: our own domains or the customer's address are fine;
            # an address that only appeared in content is never used.
            {"tool": "send_email", "action": "allow",
             "when": {"to": {"host_in": domains}},
             "reason": "official Brightside address"},
            {"tool": "send_email", "action": "allow",
             "when": {"to": {"in": [CUSTOMER["email"]]}},
             "reason": "the customer's own address"},
            {"tool": "send_email", "action": "deny",
             "when": {"to": {"untrusted": True, "host_not_in": domains}},
             "reason": "recipient address came from help-center content, not the customer"},

            # Replies: an email address the agent tells the customer to use must be
            # official, or one the customer gave us. Checked on every final reply.
            {"tool": "reply_mentions_email", "action": "allow",
             "when": {"address": {"host_in": domains}}},
            {"tool": "reply_mentions_email", "action": "allow",
             "when": {"address": {"from_user": True}}},
            {"tool": "reply_mentions_email", "action": "allow",
             "when": {"address": {"in": [CUSTOMER["email"]]}}},
            {"tool": "reply_mentions_email", "action": "deny",
             "when": {"address": {"untrusted": True, "host_not_in": domains}},
             "reason": "address came from forum or help-center content and is not an official Brightside address"},

            # Account email: always confirm, and never to an address from content.
            {"tool": "update_account_email", "action": "ask",
             "reason": "account email changes need the customer's confirmation"},
            {"tool": "update_account_email", "action": "deny",
             "when": {"new_email": {"untrusted": True}},
             "reason": "new email address came from help-center content, not the customer"},
        ],
    })
