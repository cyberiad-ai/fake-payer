# Proposal

## Why

The line only ever answers as a conversational representative. Real payers often answer with a keypad menu first, and an automated caller has to get through one: choose the right option, key a member ID and a date of birth, and recognise a dead end instead of going round it. The line cannot rehearse that today.

## What Changes

- A **phone tree** answers some calls: a keypad menu that routes and answers. The tree is data (`tree.yaml`), checked at load.
- `LINE_MODE` chooses who answers: `conversation`, `tree`, or `mixed` (each call at random, `TREE_FRACTION` of them the tree, 0.5 by default). Either line can be made the only one by setting it.
- The tree **answers**, not only routes: under eligibility the caller keys a member ID and date of birth, and the tree reads back eligibility dates, annual maximum and deductible, coverage percentages and waiting periods from the same fixture lookup the representative uses.
- **Dead ends and bad paths on purpose:** a hang-up after "not available", a menu that loops, a supervisor who never comes, wrong or impossible entries, silence.
- Key 0 holds and then hands the call to the representative.
- Keys arrive as Vonage's `websocket:dtmf` frames. The call log says which line answered and records the tree's path.

## Capabilities

### New Capabilities

- `phone-tree`: the keypad menu, its answers, its dead ends, and the choice of line per call.

### Modified Capabilities

- `call-log`: each entry records `mode` and, for a tree call, the `events` of its path.

## Impact

New: `tree.py`, `tree.yaml`, `prompts.py`, `speech_text.py`, hold music in `audio.py`. Changed: `server.py` (the choice, keys, the hand-over), `config.py`, `calllog.py`. The bridge is untouched. The default mode is `mixed`, so a deployment that does nothing starts answering about half its calls with the tree.
