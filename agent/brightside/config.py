"""Settings and secrets. Secrets are read from sanity.env (`label: value` or `NAME=value` lines)."""

import os
import re
from pathlib import Path

PROJECT_ID = "ggoz5yx2"
DATASET = "production"
ORG_ID = "o8LGSaCu5"

# Filled in once the Knowledge Base and MCP endpoint exist in the Sanity Context app.
MCP_ENDPOINT_NAME = os.environ.get("SANITY_MCP_ENDPOINT", "")
KNOWLEDGE_BASE_ID = os.environ.get("SANITY_KB_ID", "")

MODEL = os.environ.get("AGENT_MODEL", "claude-opus-5")

ENV_FILE = Path(os.environ.get("SANITY_ENV_FILE", Path(__file__).resolve().parents[3] / "sanity.env"))


# Upper-case names for hosts whose secret settings expect them (e.g. Hugging Face Spaces).
ALIASES = {"project_token": "SANITY_PROJECT_TOKEN", "org_token": "SANITY_ORG_TOKEN"}


def secret(name):
    for key in (name, ALIASES.get(name)):
        if key and os.environ.get(key):
            return os.environ[key]
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            m = re.match(r"\s*([A-Za-z_][\w]*)\s*[:=]\s*(.+?)\s*$", line)
            if m and m.group(1) == name:
                return m.group(2).strip("'\"")
    raise RuntimeError(f"{name} not set (env var or {ENV_FILE})")


def mcp_url():
    if not MCP_ENDPOINT_NAME or not KNOWLEDGE_BASE_ID:
        raise RuntimeError("set SANITY_MCP_ENDPOINT and SANITY_KB_ID")
    return (f"https://api.sanity.io/v1/context/organizations/{ORG_ID}/mcp/{MCP_ENDPOINT_NAME}"
            f"?mode=knowledge_base&knowledgeBases={KNOWLEDGE_BASE_ID}")
