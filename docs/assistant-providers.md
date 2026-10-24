# Assistant providers

The AI assistant is optional: every modelling and simulation workflow works without it. It talks to a provider
through one interface, so the provider is a deployment choice. Two are implemented, a scripted one is used by the
tests, and when nothing is configured the assistant reports itself unavailable.

| `PARALLEL_ASSISTANT_PROVIDER` | Needs | Model setting |
| --- | --- | --- |
| `anthropic` (default) | `PARALLEL_ANTHROPIC_API_KEY` | `PARALLEL_ASSISTANT_MODEL` (default `claude-opus-5-5`), `PARALLEL_ASSISTANT_EFFORT` |
| `gemini` | `PARALLEL_GEMINI_API_KEY` (or `GEMINI_API_KEY` / `GOOGLE_API_KEY` in the environment) | `PARALLEL_GEMINI_MODEL` (default `gemini-2.5-pro`) |
| `disabled` | nothing | the assistant reports that the server has it switched off |

## Using Gemini

1. Create an API key in [Google AI Studio](https://aistudio.google.com/apikey).
2. In the server's `.env` (or the container environment) set:

   ```
   PARALLEL_ASSISTANT_PROVIDER=gemini
   PARALLEL_GEMINI_API_KEY=<your key>
   PARALLEL_GEMINI_MODEL=<a current Gemini model id>
   ```

   The default model, `gemini-2.5-pro`, is only a starting point: Google retires and adds models, so choose the id
   you want from Google's current model list. Models that do not support function calling cannot be used.
3. Restart the API (`docker compose up -d`). The key is read at start-up.
4. Check **Organisation settings**: *Allow project data to be sent to the configured language-model provider*
   must be on for the assistant to be available (it is on by default for new organisations; an administrator can
   switch it off, which makes the assistant unavailable for that organisation). The assistant panel then shows the
   provider as `gemini` and the model.

`PARALLEL_GEMINI_BASE_URL` (default `https://generativelanguage.googleapis.com`) can point at a proxy or another
endpoint that speaks the same REST interface.

## What does not change with the provider

The rules are enforced by the service and the tools, not by the model:

- the assistant works as the signed-in user and only through server-side tools, each authorised exactly as the same
  action would be for that user (case-scoped records stay hidden from users who are not assigned to the case);
- scenario drafts and report narratives are `ai_suggested` until a person adopts them, and runs refuse unadopted AI
  content;
- numbers it reports come from recorded run results with metric and run identifiers; unavailable values are said
  to be unavailable;
- CrashLens interpretations never carry a probability that a hypothesis is true and never determine fault;
- CrashLens case records are sent only if the organisation allows it (*Allow CrashLens case records to be sent to the
  provider*, off by default), and media files are never sent, whatever the provider.

Each assistant message records which provider and model served it.

## What is sent, and what to know before choosing a key

Each turn sends the conversation, the system prompt, the tool definitions, and the results of the tools the model
calls (project, model and run data the user is allowed to see). Check the data terms of the key you use:

- **Gemini free tier / Google AI Studio keys:** Google's terms for the free tier have allowed prompts and responses
  to be used to improve its products. Do not use such a key for confidential projects or CrashLens cases.
  Paid-tier and Vertex AI terms differ; read the current terms before relying on them.
- **Anthropic:** see Anthropic's current API data terms.

If the terms of a key do not fit a project, leave the assistant switched off for that organisation. Everything else
works without it.

## How the Gemini provider works

It calls Gemini's `generateContent` REST interface with function calling (`AUTO` mode). Stored turns use the same
block form for every provider, so a conversation is replayed unchanged on each request. A function call is stored
as a `tool_use` block together with Gemini's `thoughtSignature`, which later Gemini models require to be sent back
with the call; thought summaries are not stored or shown. The tool schemas are converted to the OpenAPI subset
Gemini accepts (`additionalProperties` is dropped, because each tool validates its own input; a nullable field uses
`nullable`; a tool with no arguments declares none). Errors are reported without the key, and a blocked prompt or a
safety stop is shown as the model declining, not as a failure.

## What has and has not been verified

The request and response contract, the schema conversion of all fifteen tools, the replay of stored turns and the
whole assistant loop (tools run under the user's permissions, results fed back, signatures replayed) are tested
against a stub transport (`services/api/tests/test_gemini_provider.py`). **The provider has not been run against the
live Gemini service**: there was no key in the verification environment. The first live conversation is the real test;
if Google rejects a request, the assistant shows the provider's message (for example an unsupported model id) rather
than failing silently.
