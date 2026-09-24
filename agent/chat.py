"""
Chat with the Brightside support agent in the terminal, with a live trace of
every knowledge-base read and every security decision.

    python chat.py                       # interactive
    python chat.py "Does the warranty cover my battery?"   # one question
    python chat.py --raw ...             # read raw documents instead of the Knowledge Base
"""

import asyncio
import sys

from brightside.agent import SupportAgent
from brightside.guard import build_policy, fetch_settings
from brightside.kb import KnowledgeBase
from brightside.raw import RawContent

ICONS = {"allow": "✅", "ask": "✋", "deny": "⛔"}


def show(event):
    kind = event["type"]
    if kind == "kb_read":
        print(f"   📚 {event['tool']}({_short(event['args'])}) -> {event['chars']} chars")
    elif kind == "gate":
        why = "; ".join(event["reasons"]) or "default policy"
        print(f"   {ICONS[event['action']]} {event['action'].upper()} {event['tool']}({_short(event['args'])}): {why}")
    elif kind == "action":
        print(f"   ⚙️  {event['result']}")
    elif kind == "approval":
        print(f"   🙋 human {'approved' if event['approved'] else 'declined'}")
    elif kind == "refusal":
        print(f"   🚫 model refused (category: {event['category']})")


def _short(args):
    text = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return text if len(text) < 90 else text[:87] + "..."


async def ask_human(decision, args):
    if not sys.stdin.isatty():   # scripted run: nobody to ask, so decline
        print(f"   🙋 {decision} (no human available: declined)")
        return False
    answer = await asyncio.to_thread(input, f"   🙋 {decision}\n      approve? [y/N] ")
    return answer.strip().lower() in ("y", "yes")


async def main():
    policy = build_policy(fetch_settings())
    raw = "--raw" in sys.argv
    async with (RawContent() if raw else KnowledgeBase()) as kb:
        print(f"mode: {'raw documents (no Knowledge Base)' if raw else 'Sanity Knowledge Base'}")
        agent = SupportAgent(kb, policy, approve=ask_human, on_event=show)
        questions = [a for a in sys.argv[1:] if a != "--raw"]
        if questions:
            for q in questions:
                print(f"\n👤 {q}")
                print(f"\n🤖 {await agent.send(q)}")
            return
        print("Brightside Bikes support (Ctrl-C to quit)")
        while True:
            q = (await asyncio.to_thread(input, "\n👤 ")).strip()
            if q:
                print(f"\n🤖 {await agent.send(q)}")


if __name__ == "__main__":
    asyncio.run(main())
