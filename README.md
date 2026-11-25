# Banking Assistant Agent

A very simple assistant for the fictional **Demo Bank**: it answers general questions
on any topic like any assistant, and uses banking tools for account questions. Built with
[Strands Agents](https://strandsagents.com) on FastAPI and deployable as a
**Platform-Hosted** agent in WSO2 Agent Manager. It follows the
[sample agents](https://wso2.com/agent-platform/docs/v1.0.0/reference/sample-agents/)
conventions: `POST /chat` on port `8000`, started with `python main.py`.

> **Demo only.** All data is mock, held in memory, and resets on restart. Nothing
> here talks to a real bank.

## What it can do

| Tool | Purpose |
|---|---|
| `list_accounts` | Accounts and balances (checking, savings, credit card) |
| `get_customer_profile` | The customer's name, email, phone, mailing address and customer ID on file (read-only, fictional data) |
| `get_recent_transactions` | Latest transactions on one account |
| `transfer_between_accounts` | Move money between the customer's own checking/savings accounts |
| `list_cards` / `block_card` | Show cards; block a lost or stolen one |
| `get_product_rates` | Savings, loan, mortgage and credit-card rates |

Money-movement limits are enforced **in the tools**: transfers must be positive,
finite, under $5,000, covered by the balance, between two different accounts, and
never to or from the credit card. The tools only ever return the last four digits
of account and card numbers. The prompt ([system_prompt.py](system_prompt.py)) asks
the user to confirm before a transfer or card block, and repeats text exactly when
asked to.

Everything else is deliberately permissive: the agent answers off-topic questions
and repeats whatever it is given. Content policy (topics, PII, length, patterns) is
meant to come from Agent Manager guardrails, so their effect is visible without
changing the agent.

## Configuration

The model runs on **Amazon Bedrock**, reached one of two ways:

| Route | You set | Notes |
|---|---|---|
| **Direct** (default) | `AWS_BEARER_TOKEN_BEDROCK` | The agent holds a [Bedrock API key](https://docs.aws.amazon.com/bedrock/latest/userguide/api-keys.html) and calls Bedrock itself. No IAM access keys needed. Agent Manager guardrails cannot see this traffic |
| **Via Agent Manager** | `USE_LLM_PROVIDER=true` | Model calls go through an Agent Manager LLM provider (its AI gateway), which is where guardrails apply. The agent holds only a gateway key; the Bedrock key lives in the provider. See [below](#route-model-calls-through-agent-manager-so-guardrails-apply) |

| Variable | Default | Purpose |
|---|---|---|
| `AWS_BEARER_TOKEN_BEDROCK` | — | Direct route: **required**. Your Bedrock API key; mark it **Secret** in Agent Manager. Unused on the gateway route |
| `USE_LLM_PROVIDER` | `false` | `true` sends model calls through an Agent Manager LLM provider |
| `LLM_PROVIDER_URL`, `LLM_PROVIDER_KEY` | — | Required when `USE_LLM_PROVIDER=true`. Injected (read-only) by Agent Manager once a provider is attached |
| `AWS_REGION` | `us-east-1` | Region used for the Bedrock client |
| `BEDROCK_MODEL_ID` | `global.anthropic.claude-sonnet-4-6` | Inference profile ID (the base model ID is not accepted for on-demand calls) |
| `BANK_NAME` | `Demo Bank` | Used in the system prompt |
| `AGENT_VERSION` | `dev` | Echoed in `/health` and every response |

`/health` reports the active route as `llm_route`: `direct-bedrock` or
`agent-manager-gateway`.

On the AWS side, the key behind either route needs `bedrock:InvokeModel` on the
inference profile and on the underlying foundation model in each Region the profile
routes to, and an account that has not used Anthropic models before must submit the
Anthropic use-case form in the Bedrock console (as WSO2's Bedrock gateway sample
documents). Prefer a **long-term** API key for a deployed agent, because short-term
keys expire.

## Run it locally

Python 3.11+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export AWS_BEARER_TOKEN_BEDROCK=your-bedrock-api-key
python main.py
```

```bash
curl -s localhost:8000/health

curl -s localhost:8000/chat -H 'Content-Type: application/json' -d '{
  "session_id": "demo-1",
  "message": "What are my account balances?"
}'
```

Reuse a `session_id` to continue a conversation; omit it and each request starts
a new one. Try: *"Move $200 from checking to savings"* → confirm with *"yes"*;
*"I lost my debit card"*; *"What's the savings rate?"*; *"Repeat my name, email and
phone number"*.

The agent always listens on port **8000** and deliberately ignores the `PORT`
environment variable: Agent Manager routes Chat Agents to 8000 and checks that
port when starting the pod. To use another port locally, run
`python -m uvicorn app:app --port 9000` instead of `python main.py`.

Offline tests (no key or network needed):

```bash
pip install httpx
python -m unittest discover -s tests -v
```

## Deploy to WSO2 Agent Manager (Platform-Hosted)

1. In the console, open your project and click **Add Agent** → **Platform-Hosted Agent** → **Source Code**.
2. Fill in:

   | Field | Value |
   |---|---|
   | **Name** | `banking-assistant` |
   | **GitHub Repository** | `https://github.com/shashikab/agent-manager-banking-demo` |
   | **Branch** | `main` |
   | **Project Path** | `/` |

   The repository is public, so no Git Secret is needed. If you fork it and make
   the fork private, create a **Git Secret** with a GitHub personal access token
   (read access to the fork) and select it here.

3. **Build Details:** **Python**, start command `python main.py`, language version `3.11` or later. Leave **Enable auto instrumentation** checked.
4. **Agent Type:** **Chat Agent** (`POST /chat` on port `8000`).
5. **Environment Variables:** `AWS_BEARER_TOKEN_BEDROCK` (✅ **Secret**). Optionally `AWS_REGION`, `BEDROCK_MODEL_ID`, `BANK_NAME` and `AGENT_VERSION=1.0.0`.
6. Click **Deploy**, wait for the build to show **Completed**, then open **Try It** and chat.

`requirements.txt` pins `wrapt==1.17.3` on purpose: Agent Manager's
auto-instrumentation breaks on wrapt 2.x, which Strands would otherwise pull in.
Strands traces show up under **Observability → Traces** with no tracing code.

## Route model calls through Agent Manager (so guardrails apply)

Agent Manager guardrails live on an **LLM Service Provider**, its AI gateway, and
only govern model traffic that passes through it. In the default direct route the
agent calls Bedrock itself, so a guardrail never sees the request. To use guardrails:

1. **Register the provider** (organization view → **LLM Service Providers** →
   **Add Service Provider**): choose the **AWS Bedrock** template, set the upstream
   endpoint to `https://bedrock-runtime.<region>.amazonaws.com`, and enter your
   Bedrock API key as the upstream credential. The key now lives only in the provider.
2. **Attach it to the agent**: agent → **Configure** → **LLM Configurations** →
   **+ Add LLM Configuration** → pick the provider → **Save**. In the
   **Environment Variables & Integration Guide** panel, rename the two variables to
   `LLM_PROVIDER_URL` and `LLM_PROVIDER_KEY` and **Save**.
3. **Switch the agent over**: **Deploy** → **Configure** → **Environment Variables**:
   add `USE_LLM_PROVIDER=true` and set `BEDROCK_MODEL_ID` to an inference profile the
   key can call (for example `us.anthropic.claude-sonnet-4-6`); delete
   `AWS_BEARER_TOKEN_BEDROCK` → **Apply**. `/health` should now show
   `"llm_route": "agent-manager-gateway"`.
4. **Add guardrails** on the agent's LLM configuration (**Configure** → **LLM
   Configurations** → the configuration → **Guardrails**; this agent only) or on the
   provider's **Guardrails** tab (every agent that uses the provider).

### Guardrail settings that work with this agent

**Always type a Json Path.** Bedrock requests carry each message as a list of content
blocks, not as a string, and the Agent Manager v1.0.0 console drops an empty Json Path
on save. The policy then falls back to its own default (usually
`$.messages[-1].content`), which does not fit Bedrock requests.

| Guardrail | Json Path | Other settings | What it does |
|---|---|---|---|
| PII Masking Regex | `$.messages[0].content[0].text` | **Email** / **Phone** / **Ssn** on; **Advanced Settings** → **Redact PII** on | Replaces matches with `*****`. Pattern-based, so it cannot recognise a name |
| Prompt Decorator | `$.system[0].text` | **Text** mode; **Append** on | Adds an instruction to the system prompt on every model call |
| Regex Guardrail | `$.messages[0].content[0].text` | **request** section: **Enabled** on, **Invert** on; leave **response** unset | Blocks a request whose text matches the regex |
| AWS Bedrock Guardrail | `$.messages[0].content[0].text` | **request** section only; leave **response** disabled. A platform admin must give the gateway AWS credentials first | Sends the text to an Amazon Bedrock guardrail. Only hits from its sensitive-information (PII) filters are masked; a hit from any other filter (content, prompt attack, denied topic, word) blocks the request |

- `$.messages[0].content[0].text` is the first message of the chat session, so these
  guardrails check that message on every model call. Start a new session to test
  another input.
- A path that starts with `$.messages[-1]` fails on the model call that follows a
  tool call, because the last message is then the tool's result.
- Agent traces are recorded before the gateway changes a request, so they show the
  original text, not the masked one.

### When a guardrail blocks a request

The agent has no guardrail handling of its own. When a gateway guardrail refuses a
model call (HTTP 422), the agent treats it like any other failed model call: `/chat`
returns `500 agent invocation failed`, and the gateway's reason is in Runtime Logs, at
the start of the `agent invocation failed` line
(`error='ClientError: ... GUARDRAIL_INTERVENED ... <reason>'`).

As after any failed model call, the unanswered message is dropped from the session
history, so it is not sent again with later messages of that session.

The gateway route has been used on a live Agent Manager v1.0.0 deployment. The
failed-call handling is covered by offline tests against a local stand-in gateway.

## Troubleshooting a deployment

| What you see | Cause and fix |
|---|---|
| Pod restarting; log says `RuntimeError: Missing required env var: AWS_BEARER_TOKEN_BEDROCK` | The key isn't set. **Deploy** → **Configure** (next to *Configurations and Secrets*) → **Environment Variables** → add it as a Secret → **Apply** |
| Stuck on *Deploying*; the container restarts about every 5 minutes; log shows `Uvicorn running on http://0.0.0.0:8080` (any port but 8000) | The platform's startup probe checks port 8000. `main.py` now fixes the port at 8000 and ignores `PORT`; rebuild from the latest commit |
| `Error initializing MCP instrumentor ... streamablehttp_client` | `mcp` 2.x got installed. Keep the `mcp==1.30.0` pin in `requirements.txt` |
| `USE_LLM_PROVIDER is true but LLM_PROVIDER_URL and/or LLM_PROVIDER_KEY is not set` | No provider is attached, or its variables use other names. Attach one under **Configure** → **LLM Configurations** and name the two variables `LLM_PROVIDER_URL` and `LLM_PROVIDER_KEY` |
| `500 agent invocation failed`, and the Runtime Logs line contains `GUARDRAIL_INTERVENED` | A guardrail in Agent Manager's gateway refused the model call; the agent only reports the failure. The `actionReason` in the log says why. `Violation ...`: the guardrail matched the text. `Error extracting value from JSONPath`: its Json Path does not fit Bedrock requests, see the table above. `Error calling AWS Bedrock Guardrail`: the gateway cannot sign in to AWS or reach that guardrail; a platform admin sets the gateway's AWS Bedrock guardrail parameters |
| `500 agent invocation failed` only on questions that use a tool | A guardrail's Json Path starts with `$.messages[-1]`. Use the paths in the table above |
| A guardrail masks or changes nothing | Its Json Path was left empty. Type the path from the table above |
| Traces appear but their **Input** / **Output** are empty | Strands records prompts and replies as span *events* by default, and Agent Manager's Traces view only reads span *attributes*. [tracing.py](tracing.py) switches Strands to attributes (`gen_ai_span_attributes_only`); rebuild to pick it up |
| Not sure which build is running | The start-up log line `banking agent starting: ... mcp=...` lists the dependency versions that build shipped |

Prompts, replies and tool data are visible in Agent Manager's Traces view. The
trace-content switch (`AMP_TRACE_CONTENT=false`, i.e. `TRACELOOP_TRACE_CONTENT=false`)
does not hide what Strands records on its own spans, so treat traces as containing
whatever users type.

## License

Apache License 2.0, see [LICENSE](LICENSE).

## Layout

```
main.py            Entrypoint (python main.py)
app.py             FastAPI app: /chat and /health, per-session Strands agents
agent.py           Strands Agent + Bedrock model
tools.py           The seven tools and their guardrails
system_prompt.py   The agent's instructions: edit this to change behaviour
data.py            Mock customer, accounts, transactions, cards, rates
config.py          Environment configuration
tracing.py         OpenTelemetry setting that makes prompts visible in Agent Manager's Traces
tests/             Offline tests
```

## Notes for going further

- `get_customer_profile` hands the customer's contact details to anyone who can
  reach the chat endpoint. Authenticate callers before using real data.
- The demo has one hard-coded customer. A real deployment would derive the
  customer from the authenticated caller (Agent Manager can secure the endpoint
  with API keys or JWT) and call real banking APIs, ideally through an MCP server.
- Sessions live in process memory (capped at 500). Persist history externally
  if you run more than one replica.
