"""
Comparison mode: search the raw Sanity documents directly with GROQ, the way
a typical "chat with your CMS" bot works. No Knowledge Base build in between,
so whatever text is in a document reaches the model verbatim.

Same interface as KnowledgeBase (`tools`, `call`), so the agent doesn't care.
"""

import json
import urllib.parse
import urllib.request

from . import config

FIELDS = "[title, name, description, pt::text(body)]"
PROJECTION = """{_type, "title": coalesce(title, name), author, "text": coalesce(pt::text(body), description)}"""
STOPWORDS = {"the", "and", "for", "can", "you", "how", "what", "does", "any", "is", "my", "me", "it", "are", "was", "there", "this", "that", "with", "about"}


def search_query(n_terms):
    """Match documents containing ANY term, ranked by how many terms they hit."""
    any_term = " || ".join(f"{FIELDS} match $t{i}" for i in range(n_terms))
    boosts = ", ".join(f"{FIELDS} match $t{i}" for i in range(n_terms))
    return (f'*[_type in ["product", "helpArticle", "communityPost"] && ({any_term})]'
            f" | score({boosts}) | order(_score desc)[0...6]{PROJECTION}")


class RawContent:
    tools = [{
        "name": "kb_search_help_center",
        "description": "Search Brightside help articles, product pages and community forum posts. "
                       "Returns the full text of the best matches.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "A few keywords"}},
            "required": ["query"],
        },
    }]
    instructions = "Use kb_search_help_center to look things up in the help center."

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def call(self, tool_name, arguments):
        words = [w.strip(".,?!'\"").lower() for w in arguments["query"].split()]
        terms = [w for w in words if len(w) > 2 and w not in STOPWORDS][:8] or words[:1]
        params = {"query": search_query(len(terms)), **{f"$t{i}": json.dumps(t + "*") for i, t in enumerate(terms)}}
        url = (f"https://{config.PROJECT_ID}.api.sanity.io/v2025-02-19/data/query/{config.DATASET}"
               f"?{urllib.parse.urlencode(params)}")
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {config.secret('project_token')}"})
        with urllib.request.urlopen(req, timeout=20) as r:
            docs = json.load(r)["result"]
        if not docs:
            return "No matching articles.", False
        return "\n\n---\n\n".join(
            f"[{d['_type']}] {d['title']}" + (f" (posted by {d['author']})" if d.get("author") else "")
            + f"\n{d['text']}" for d in docs), False
