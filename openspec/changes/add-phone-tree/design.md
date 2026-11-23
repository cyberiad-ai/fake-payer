# Design

## Decisions

- **A separate runner, not the bridge.** The bridge is built around the language model and speech-to-text. The tree needs neither, so `TreeRunner` walks the file with only the call's frames, the cached prompts and the fixture. A tree call costs no model or speech-recognition time.
- **Keys from Vonage's `websocket:dtmf` text frames**, which the websocket wrapper turns into events beside the audio. In-band tone detection and the NCCO `input` action were rejected: the first duplicates what Vonage already decodes, the second would take the call out of the websocket. The platform's caller sends keys with `PUT /v1/calls/{uuid}/dtmf`, which is what produces these frames. **Not yet confirmed on a live call.**
- **Speech is ignored** in the tree. A caller who speaks gets the timeout path, which is also a useful bad path.
- **Same lookup, same figures.** Every fact comes from `Fixture.lookup`; the tree only phrases it (`speech_text.py` writes numbers and dates as words). An unknown member number with any date of birth is an ordinary active member, exactly as on the conversational line; only a known member with a wrong date of birth is refused.
- **Prompts** are synthesized once and kept (memory and disk), keyed on the voice, model and the respelled text. Fixed lines are prepared at start; member answers on first use.
- **Choosing a line:** per call, at the point the call is admitted. `mixed` uses a random draw against `TREE_FRACTION`. The log records the choice, so a failed test can be matched to the line it met.
- **Hand-over:** the `transfer` node ends the tree; the server starts the bridge with a short greeting and no ringing, on the same websocket. One log entry covers both.
- **Barge-in:** a key during a menu or entry prompt stops the prompt and counts. Keys during other prompts and hold are dropped.
- **Validation at load:** unknown targets, unreachable nodes, a menu with no way out, an action with the wrong routes: the line will not start.

## Risks

- The `websocket:dtmf` frame is documented by Vonage but has not been seen from this line. If the frames do not arrive, every tree call times out; the log's `events` shows it.
- Default `mixed` changes what an existing deployment does on the next restart.
