<!-- Adapted from dashi-taskboard skills/manage-taskboard/references/cli.md (Apache-2.0) — see NOTICE. -->

# boardctl CLI

`boardctl` emits JSON for normal commands. Built-in help is the only successful stdout exception: it writes plain text, exits with code `0`, and does not request the board service.

```bash
python3 act/boardctl.py --help
python3 act/boardctl.py board --help
python3 act/boardctl.py capture --help
```

Every successful command writes one JSON object with `schemaVersion` to stdout. The current schema version is `1`. Errors write one JSON object `{"schemaVersion":1,"error":{"code","message","details"?}}` to stderr. Exit codes are `0` for success, `1` for an unexpected internal crash (catch-all `INTERNAL_ERROR` envelope, no traceback leaked — any classified error uses 2-5 instead), `2` for invalid input, `3` when the service is unavailable, `4` for API or response errors, and `5` for conflicts.

Set `ZAI_PORT` to override the default port of the local API origin, `http://127.0.0.1:47820`. The server binds 127.0.0.1 only; there is no remote origin to configure.

Card IDs must match `[A-Za-z0-9][A-Za-z0-9_-]{0,63}` (e.g. `R-101`, `MS-3`). Pass them exactly as the board returned them.

## Read the board

```bash
python3 act/boardctl.py board [--lane LANE] [--json]
```

Without `--lane`, the full board projection is returned under `"board"` (the verbatim `dashboard.json` document). With `--lane`, only that lane's rows are returned under `"cards"`.

Lanes: `needs_approval`, `running`, `needs_input`, `review`, `completed`, `debt`, `trash`, `archived`.

## Read one card

```bash
python3 act/boardctl.py card CARD_ID [--json]
```

Returns the projection row plus read-only registry fields under `"card"`: `plan`, `definition_of_done`, `sources` (citations), `notes` (comment/fold history), execution metadata, and a `lane` key naming the card's current lane (`null` for registry-only cards). A missing card is a `NOT_FOUND` error with exit code `4`.

## Capture a candidate

```bash
python3 act/boardctl.py capture --text TEXT [--image /abs/path.png ...] [--json]
python3 act/boardctl.py capture --text-file FILE [--json]
```

Exactly one of `--text` / `--text-file`. `--image` may repeat, up to 4 absolute local paths, no duplicates. Success returns `{"ok":true,"file":"capture-<uuid>.json","action":"capture","via":"agent"}` — the file name is the inbox receipt, not a card ID; the card (if any) is minted later by triage.

There is deliberately no direct-run or preset option on this channel: agent captures always go through triage. Every write self-identifies — boardctl sends `actor:"agent"` unconditionally and the server stamps the record `via:"agent"`, so the capture lands on the `agent_capture` channel and is never eligible for auto-dispatch.

## Comment on a card

```bash
python3 act/boardctl.py comment CARD_ID --body TEXT [--json]
python3 act/boardctl.py comment CARD_ID --body-file FILE [--json]
```

Exactly one of `--body` / `--body-file`; the body must be non-empty. Success returns `{"ok":true,"file":"<uuid>.json","action":"comment","via":"agent"}` (plus `steer:false` when the card is executing — agent comments are recorded on the card but never relayed into the live session). A comment does not change the card's state.

## Error codes you may see

| code | exit | meaning |
| --- | --- | --- |
| `INTERNAL_ERROR` | 1 | unexpected internal crash (catch-all; classified errors never use this) |
| `USAGE_ERROR` | 2 | bad subcommand, option, operand, or ID shape (client-side) |
| `FILE_READ_FAILED` | 2 | `--text-file` / `--body-file` unreadable |
| `SERVICE_UNAVAILABLE` | 3 | server not running / unreachable / timed out |
| `INVALID_FIELD`, `UNKNOWN_FIELD`, `NOT_FOUND` | 4 | server-side validation (passed through from the API envelope) |
| `INVALID_RESPONSE`, `HTTP_<status>` | 4 | non-JSON or unclassified server response |
| any code with HTTP 409 | 5 | conflict (reserved; the current server does not emit 409) |
