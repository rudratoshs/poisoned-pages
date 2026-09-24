"""
The Brightside support agent: Claude + the Sanity Knowledge Base + taintgate.

Every knowledge-base result is recorded as untrusted. Every action goes
through the taintgate policy first: allow runs it, deny blocks it, ask waits
for a human via the `approve` callback.
"""

import json
import re

import anthropic
from taintgate import Session

from . import config
from .backend import ACTION_TOOLS, ACTIONS, CUSTOMER
from .kb import PREFIX

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

SYSTEM = """You are the support assistant for Brightside Bikes, an e-bike shop.
You are chatting with a signed-in customer: {name} ({email}, customer id {id}).

Answer questions from the Brightside help center. {kb_instructions} Mention which article or post
an answer comes from. Use the account tools to look up orders and take actions for the customer.

Keep answers short, friendly and practical. If a tool call is blocked or declined, tell the customer
plainly and suggest what they can do instead."""


class SupportAgent:
    def __init__(self, kb, policy, approve, on_event=lambda event: None, model=None):
        """approve: async (decision, args) -> bool, asked for every 'ask' decision.
        on_event: called with dicts describing each step, for the CLI / web trace."""
        self.kb, self.approve, self.emit = kb, approve, on_event
        self.model = model or config.MODEL
        self.gate = Session(policy)
        self.client = anthropic.AsyncAnthropic(api_key=config.secret("ANTHROPIC_API_KEY"))
        self.messages = []
        self.tools = kb.tools + ACTION_TOOLS
        self.system = SYSTEM.format(kb_instructions=kb.instructions, **CUSTOMER)

    async def send(self, text):
        """One customer turn. Returns the assistant's reply text."""
        self.gate.add_user_message(text)
        turn_start = len(self.messages)
        self.messages.append({"role": "user", "content": text})
        for _ in range(20):   # tool-use rounds per turn
            response = await self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=self.system,
                tools=self.tools,
                messages=self.messages,
                cache_control={"type": "ephemeral"},
                **_model_options(self.model),
            )
            if response.stop_reason == "refusal":
                self.emit({"type": "refusal", "category": getattr(response.stop_details, "category", None)})
                del self.messages[turn_start:]   # roll back the whole turn so history stays valid
                return "Sorry, I can't help with that request."
            self.messages.append({"role": "assistant", "content": response.content})
            for block in response.content:
                if block.type == "text" and block.text.strip() and response.stop_reason == "tool_use":
                    self.emit({"type": "note", "text": block.text})
            if response.stop_reason != "tool_use":
                reply = "".join(b.text for b in response.content if b.type == "text")
                if response.stop_reason == "max_tokens":
                    reply += "\n\n(reply cut off)"
                return reply + self._reply_warnings(reply)
            results = [await self._run_tool(b) for b in response.content if b.type == "tool_use"]
            self.messages.append({"role": "user", "content": results})
        return "Sorry, that took too many steps. Please try rephrasing."

    def _reply_warnings(self, reply):
        """The guard can block actions, but a fooled model can still *tell* the customer
        to email an attacker. Check every address in the reply against the same policy."""
        warnings = []
        for address in dict.fromkeys(EMAIL.findall(reply)):
            decision = self.gate.check("reply_mentions_email", {"address": address})
            self.emit({"type": "gate", "tool": "reply_mentions_email", "args": {"address": address},
                       "action": decision.action, "reasons": decision.reasons})
            if decision.action == "deny":
                warnings.append(f"⚠️ **Security notice:** {address} is not an official Brightside address. "
                                "Please don't send personal details to it. Official support is "
                                "support@brightsidebikes.com or the chat on brightsidebikes.com.")
        return "\n\n" + "\n\n".join(warnings) if warnings else ""

    async def _run_tool(self, block):
        name, args = block.name, dict(block.input)
        if name.startswith(PREFIX):
            text, is_error = await self.kb.call(name, args)
            self.gate.observe(name, text)   # knowledge-base content is untrusted
            self.emit({"type": "kb_read", "tool": name, "args": args, "chars": len(text)})
            return _result(block.id, text, is_error)

        if name not in ACTIONS:
            return _result(block.id, f"Error: unknown tool {name}.", True)
        decision = self.gate.check(name, args)
        self.emit({"type": "gate", "tool": name, "args": args,
                   "action": decision.action, "reasons": decision.reasons})
        if decision.action == "deny":
            return _result(block.id, f"BLOCKED by security policy: {'; '.join(decision.reasons)}. "
                                     "This action did NOT happen. Do not tell the customer it did.", True)
        if decision.action == "ask":
            approved = await self.approve(decision, args)
            self.emit({"type": "approval", "tool": name, "approved": approved})
            if not approved:
                return _result(block.id, "DECLINED by a human reviewer. This action did NOT happen. "
                                         "Do not tell the customer it was approved or completed.", True)
        output = ACTIONS[name](args)
        self.emit({"type": "action", "tool": name, "args": args, "result": output})
        return _result(block.id, output, output.startswith("Error"))


def _model_options(model):
    """Effort and refusal fallbacks exist on Opus 5; Haiku 4.5 rejects both."""
    if model.startswith("claude-haiku"):
        return {}
    return {"output_config": {"effort": "medium"},
            "betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}


def _result(tool_use_id, content, is_error=False):
    return {"type": "tool_result", "tool_use_id": tool_use_id,
            "content": content if isinstance(content, str) else json.dumps(content),
            "is_error": is_error}
