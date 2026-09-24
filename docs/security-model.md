# Security model

**Design principle: assume the model can be fooled, and limit what a fooled model can do.**
The agent reads content that strangers can write. We don't try to make the model immune to that
content. Instead, every action it attempts has to pass a deterministic check it can't talk its way past.

## Assets

| Asset | Harm if abused |
|---|---|
| Refunds | money paid to the wrong order, or more than policy allows |
| Outbound email | customer data (name, address, order details) sent to an attacker |
| Account email | account takeover via password reset to an attacker's inbox |
| Replies to the customer | the customer is told to send their own data to an attacker |

## Threat actors

| Actor | Can write | Example in the seed data |
|---|---|---|
| Anonymous forum user | `communityPost` documents | fake refund instructions, fake "claims partner", fake $150 credit |
| Compromised staff account | `helpArticle` documents | the "Delivery delays in the Northeast" article with an injected $400 refund |
| The Knowledge Base build (not malicious, but lossy) | generated entries | turned the $150 rumour into cited policy (see [kb-observation.md](kb-observation.md)) |

## Components and trust

| Component | Trusted? | Why |
|---|---|---|
| Customer's own messages | trusted for provenance | the signed-in customer is the principal the agent acts for |
| `list_my_orders` (order system) | trusted | our own system of record; its output is not marked untrusted |
| `supportSettings` fields | trusted | the only input to the authorisation limits (see boundary below) |
| Knowledge Base entries (`kb_*` tools) | **untrusted** | generated from content strangers can write |
| Raw documents (comparison mode) | **untrusted** | same content, unfiltered |
| The model's tool calls and replies | **untrusted** | the model may have been fooled by anything above |

## Trust boundaries

```text
            untrusted                                      trusted
┌──────────────────────────────┐              ┌──────────────────────────────┐
│ communityPost  (anyone)      │              │ customer messages            │
│ helpArticle    (staff, may   │              │ order system                 │
│                 be hacked)   │              │ supportSettings              │
│ Knowledge Base entries       │              │   maxAutoRefund              │
│ the model's output           │              │   refundWindowDays           │
└──────────────┬───────────────┘              │   officialEmailDomains       │
               │ tool call                    └──────────────┬───────────────┘
               ▼                                             │
        ┌─────────────────────── taintgate ◄─────────────────┘
        │  what does it do?   (limits from supportSettings)
        │  where did each value come from?  (provenance)
        └──────┬────────────────┬───────────────┬──────
             allow             ask             deny
               │                │               │
            backend        human review      stopped
```

### The `supportSettings` boundary: assumed in this demo

The guard's limits come from one document, read with GROQ at startup and validated
(non-negative numbers, a non-empty list of domains; the agent refuses to start otherwise).
Its security rests on **who can edit it**:

- **In this demo,** all documents live in one dataset and were written with one project token.
  The boundary is an assumption, not an enforced permission.
- **In a real deployment,** forum posts would reach Sanity through an ingestion token that can
  only create `communityPost` documents, and `supportSettings` would be editable only by admins:
  either a role with content-level permissions (where the Sanity plan supports custom roles), or a
  separate dataset that the ingestion and staff tokens can't write to.

## Provenance model

taintgate records two kinds of text during a conversation:

- **trusted:** everything the customer typed, across all turns;
- **untrusted:** everything returned by a `kb_*` tool, across all turns.

A value in a tool call (an order number, an email address) is:

| Label | Meaning |
|---|---|
| `untrusted` | it appears in untrusted text and the customer never typed it |
| `from_user` | the customer typed it |
| neither | it came from somewhere else, e.g. the order system, or the model made it up |

Values are compared with spacing, case and punctuation ignored. Taint lasts for the whole
conversation: a value read in turn 1 is still untrusted in turn 3.

**If the customer repeats a value they saw in content,** it counts as customer-provided, so it's
no longer a hard deny. It still isn't allowlisted, so actions using it fall to the default:
**ask a human**. (Test: `test_customer_repeating_a_poisoned_value_is_not_auto_trusted`.)

## Action authorisation

Every rule is checked on every call and the strictest result wins: **deny > ask > allow**.
Anything no rule covers defaults to **ask**.

| Action | Allow | Ask | Deny |
|---|---|---|---|
| `list_my_orders`, `kb_*` | always | | |
| `issue_refund` | own order, inside `refundWindowDays`, amount ≤ `maxAutoRefund` | amount > `maxAutoRefund`; own order outside the window | order isn't the customer's; order number came from content |
| `send_email` | recipient on an official **domain**, or the customer's own address | any other recipient | recipient only appeared in content (and isn't on an official domain) |
| `update_account_email` | never | always | new address only appeared in content |
| reply check (`reply_mentions_email`) | official domain, the customer's address, or typed by the customer | | only appeared in content → a warning is added to the reply |

Note: email trust is **domain-based**. Any `@brightsidebikes.com` address is allowed, not just
`support@`. A stricter version would store exact addresses in `supportSettings`.

## Human approval semantics

- `ask` pauses the agent until a reviewer clicks **Approve** or **Decline** (web UI) or answers
  y/N (terminal). With nobody to ask (scripted runs), `ask` means decline.
- **Approval can't override a deny.** A deny is final; the reviewer is never asked.
- On decline or deny, the model gets an explicit tool result: *"This action did NOT happen. Do
  not tell the customer it did."* (Added after Haiku 4.5 told a customer a declined refund was approved.)

## Example attack flow

```text
communityPost "Warranty claim tip" (hidden instruction)
      │  read via kb_knowledge_base_read → marked untrusted
      ▼
model is fooled → issue_refund(order_id="BB-20931", amount=1450)
      ▼
taintgate:  BB-20931 not the customer's order      → deny
            BB-20931 only appeared in content       → deny
            1450 > maxAutoRefund (50)               → ask
      ▼  strictest wins: DENY
backend is never called · ledger.refunds == []
```

## Failure modes and known limitations

| Limitation | Effect | Mitigation here |
|---|---|---|
| Provenance is **text matching** | if the model rewrites a value (spells it out, splits it, encodes it), it can't be traced | untraced values aren't allowlisted either, so they fall to **ask** |
| The model's **words** aren't controlled | a fooled model can repeat misinformation (e.g. the $150 "policy") | actions stay bounded; email addresses in replies are checked |
| The reply check only finds **conventional email addresses** | obfuscated addresses, URLs or phone numbers aren't checked | none yet |
| Settings are read **at startup** | changing `supportSettings` needs a restart to take effect | documented |
| One shared MCP session in the web demo | conversations share a connection | fine for a local demo, not for production |
| The web app has **no authentication** | `/api/info` returns demo customer data | binds to 127.0.0.1; not meant for public deployment |
| Knowledge Base builds are **LLM-generated** | results can change between builds | observations are pinned in [kb-observation.md](kb-observation.md) |

A stronger design would carry provenance through the model instead of re-discovering it from
text afterwards, for example by having the model refer to values by handle while the harness
keeps the real values and their sources (the approach of research systems like Google DeepMind's
CaMeL). That's out of scope for this project.
