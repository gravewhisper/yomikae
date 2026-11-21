# Translators

The server translates each page with one of three kinds of translator, chosen with `YOMIKAE_TRANSLATOR`:

- `openai`, the default: any OpenAI-compatible endpoint, such as LM Studio on your own computer or a cloud API.
- `claude`: the Claude Code command line tool, on the Claude subscription it is signed in to.
- `codex`: OpenAI's Codex command line tool, on the ChatGPT account it is signed in to, including a free one.

The cache gives a page only to the translator that made it, so switching translators translates pages again the first time you open them.

## Privacy and terms

Use a subscription only through its maker's own tool. Anthropic's terms allow a Claude subscription only in Claude Code and Claude.ai, so do not point other command line tools at one.

With a cloud API, the Japanese text of each page leaves your computer, along with the dialogue of up to two earlier pages as context. Page images never leave it. Cloud models may refuse some content, which leaves those lines in Japanese or fails the page. The server has one translator, so to get a failed page translated, restart it with `YOMIKAE_TRANSLATOR=openai` and a local model, then press Retry on the page. A failed page is not cached, so nothing has to be cleared.

## OpenAI-compatible endpoint

The server sends each page to any OpenAI-compatible `/chat/completions` endpoint that accepts `response_format` with a JSON schema. Set these environment variables before you start the server:

| Variable | Default | Meaning |
| --- | --- | --- |
| `YOMIKAE_OPENAI_BASE_URL` | `http://localhost:1234/v1` | The endpoint. For Ollama, use `http://localhost:11434/v1`. |
| `YOMIKAE_OPENAI_MODEL` | `google/gemma-4-12b` | The model name the endpoint knows. Pick a model that handles Japanese. The server translates a page again when another model or translator made its cached copy. |
| `YOMIKAE_OPENAI_API_KEY` | `lm-studio` | The bearer token, for a cloud API. |

For example, to translate with a cloud API:

```sh
YOMIKAE_OPENAI_BASE_URL=https://api.example.com/v1 \
YOMIKAE_OPENAI_MODEL=<model> \
YOMIKAE_OPENAI_API_KEY=<key> \
.venv/bin/uvicorn yomikae.app:app --host 127.0.0.1 --port 7878
```

If the endpoint rejects the `reasoning_effort` field, also set `YOMIKAE_OPENAI_REASONING_EFFORT=` to an empty string.

[models.md](models.md) suggests models for each amount of graphics memory. [server/README.md](../server/README.md#configuration) lists every setting, including lettering case, fonts, the cache size, and CPU mode.

## Claude subscription

The server can run the [Claude Code](https://claude.com/claude-code) command line tool, `claude -p`, for each page instead of calling an endpoint. It uses whatever account `claude` is signed in to, so a Claude subscription works with no API key. Start the server with `YOMIKAE_TRANSLATOR=claude`:

| Variable | Default | Meaning |
| --- | --- | --- |
| `YOMIKAE_CLAUDE_MODEL` | `claude-sonnet-5-5` | The model `claude` uses. `claude-haiku-5-5` is faster and uses less of a subscription's limit, and `claude-opus-5-5` is slower and uses more. |
| `YOMIKAE_CLAUDE_EFFORT` | `low` | How long the model thinks: `low`, `medium`, `high`, `xhigh` or `max`. |
| `YOMIKAE_CLAUDE_COMMAND` | `claude` | The command to run, if `claude` is not on `PATH`. |
| `YOMIKAE_CLAUDE_LOG_DIR` | unset | A folder where the server saves each call as JSON: the instructions, the request with the page's lines and context, and `claude`'s whole reply. Unset saves nothing. |

The page's Japanese text, the earlier pages' lines and the work's names leave your computer and go to Anthropic. The page image does not. Each call starts `claude` with no tools, no settings, no plugins and no saved session, in an empty folder, so text on a page cannot make it read files or run commands. On 2026-11-14, at low effort, a call with 8 lines took about 2.7 s with Claude Sonnet 5.5, 2.2 s with Claude Haiku 5.5 and 3.2 to 3.4 s with Claude Opus 5.5, of which about 1 s is `claude` starting. The translation quality of these models on this task was not measured.

## ChatGPT plan through Codex

The server can also run OpenAI's [Codex](https://developers.openai.com/codex) command line tool, `codex exec`, once per page, on whatever ChatGPT account `codex` is signed in to. ChatGPT's free plan includes a small Codex allowance. Run `/status` in `codex` to see how much of it is left. Start the server with `YOMIKAE_TRANSLATOR=codex`:

| Variable | Default | Meaning |
| --- | --- | --- |
| `YOMIKAE_CODEX_MODEL` | unset | The model `codex` uses. Unset uses Codex's own default. |
| `YOMIKAE_CODEX_EFFORT` | `none` | How long the model reasons: `none`, `low`, `medium`, `high`, `xhigh` or `max`. |
| `YOMIKAE_CODEX_COMMAND` | `codex` | The command to run, if `codex` is not on `PATH`. |

As with Claude, the page's text and context go to OpenAI and the page image does not. Each call runs `codex` in an empty folder with its shell tool and its other tools and plugins turned off, without your Codex settings or rules, and with the server's own instructions in place of Codex's. On 2026-11-14 a call took 3.1 to 3.2 s and about 8,600 tokens, most of them Codex's own tool definitions. Reasoning effort barely changed the count.
