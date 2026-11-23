# fake-payer

A **fictional dental insurance phone line**, for rehearsing automated calls to payers.

You call a number. After a couple of rings a made-up insurance representative ("Adrian") answers, listens, and gives coverage answers for made-up members. The answers come from a fixed data file, so the same question always gets the same answer and a test can check exactly what a caller was told.

**This is a test fixture, not a product.** It uses synthetic data only. It does not imitate any real insurer: no real payer's name, number, plan terms or member data belongs in this repository. A check (`python -m fake_payer.real_data_check`, run in CI) guards against real data creeping in.

## Two ways a call is answered

`LINE_MODE` says who answers:

- `conversation`: the representative described below answers every call.
- `tree`: a **phone tree** answers every call: a keypad menu that routes and answers (see "The phone tree").
- `mixed` (the default): each call is the phone tree with probability `TREE_FRACTION` (0.5, so about half and half) and the representative otherwise. Each call log entry says which (`mode`).

To make one of them the only line, set `LINE_MODE` and restart; to test one without the other, nothing else is needed.

## How a call goes (the representative)

1. The line rings (`RING_COUNT` rings of ringback tone), waits a moment (`ANSWER_DELAY_SECONDS`), then greets the caller and says the call is fictional and recorded.
2. The representative asks for what it needs to find the patient (member number, date of birth and name, group number, NPIs, tax ID digits). Any patient the caller names is accepted as an ordinary active member; the named fixture members keep their scenarios (see below). Name pronunciation need not be exact.
3. It answers **only what is asked**, in a sentence or two, and does not ask "anything else?" after each answer. Every fact comes from a lookup in the fixture, done *before* it starts speaking, so there are no silent gaps that make a caller think the turn is over.
4. It **keeps the floor**: while it is audibly speaking it ignores the caller talking over it (`KEEP_THE_FLOOR=0` lets the caller cut it off).
5. It waits as long as the caller has questions. There is no call-length limit by default.
6. It says goodbye, and hangs up, only after the caller has said goodbye: if the model tries to say one earlier, the words of the farewell are taken out of what is spoken and logged. "Thank you" or "that is all" does not end the call: it stays on the line, gives its name, ID and the call reference if the caller has not had them, and waits. When the caller says goodbye it says goodbye back and hangs up (the end-call tool refuses until the caller's last words contain a goodbye). After either side hangs up the line plays a three-second dial tone.

## The phone tree

The tree is [`fake_payer/tree.yaml`](fake_payer/tree.yaml); the node types are described at its top. A tree call rings and greets like the other line (same disclosure), then plays a menu. The caller answers with the keypad: Vonage sends each key to the call's websocket as a `websocket:dtmf` frame. Speech is ignored. A key pressed while a menu or entry prompt is playing cuts it short, as on a real line.

- **Routes:** 1 eligibility and benefits, 2 claim status, 3 fax numbers and addresses, 4 provider enrollment, 5 supervisor, 6 plan questions, 7 plan benefits by group number, 8 reference number for this call, 0 representative, 9 repeat.
- **It answers what the representative can,** from the same fixture, so a figure is the same on either line:
  - **Under 1** the caller keys the six-digit member ID (without the M) and the date of birth (MMDDYYYY). Then: eligibility dates; annual maximum and deductible with what remains; coverage percentages; waiting periods; covered services with their frequency limits and how much the member has used (by category); a procedure-code check (four digits, without the D): covered or not now, percentage, deductible, limit used and remaining, and the reason. A terminated member gets the end date and nothing else.
  - **Under 7** a valid group number (five digits, without the G) gives the plan's benefits with no member, as the representative does.
  - **Under 6** every question on the practice's verification form (all 128 of the fixture's `benefit_answers`: network, claims, maximums, each procedure's coverage and limits, downgrades, waiting periods, orthodontics, history on file) is read out by topic, seven to a page (9 for more, star to go back), and a question's key says its answer. No member is needed, as with the representative. The topics are the `topic` of each `benefit_answers` entry in `fixture.yaml`; the tree and the fixture must agree on them (checked at start).
  - **Under 8** the line says it is automated, its I D (the fixture's employee ID) and the call's reference number, digit by digit.
  - Lines about a member are synthesized the first time they are spoken and kept.
- **Dead ends and bad paths, on purpose:** claim status says it cannot help and hangs up; the fax option goes round to the main menu; provider enrollment is a loop with no way forward; a supervisor never comes (hold music, then the call is dropped); a wrong date of birth is refused (three tries, then the call ends); an invalid key, a missing key or an impossible date is said so and asked again, and three in a row end the call.
- **Representative:** key 0 plays hold music and then hands the call to the representative, who greets the caller again without ringing. Both parts of that call are in one log entry.
- **Editing it:** menu text, options and routes are data; a new node type or action needs code (`fake_payer/tree.py`). The file is checked when it loads (unknown targets, unreachable nodes, a menu with no way out) and the line will not start with a faulty one.
- **Hold music** is a short synthesized tune (`audio.py`); the prompts are in the configured voice and kept in `AUDIO_CACHE_DIR`. The couple of hundred fixed lines are made in the background, three at a time, while the line already takes calls; a line not ready when a caller needs it is made then (a pause of a few seconds on the first start after a change to the tree or the fixture, never after).

## What it knows

Everything is in [`fake_payer/fixture.yaml`](fake_payer/fixture.yaml) (all invented):

- **Plans**: `GOLD` (annual maximum, deductible, tiered percentages and waiting periods, orthodontics with a lifetime maximum and age limit) and `SILVER` (no orthodontic benefit).
- **Members** (each has a `note` saying what it is for): `M100001` Pat Sample, the ordinary case; `M100002` Robin Example, frequency limits used up; `M100003` Casey Placeholder, terminated coverage; `M100004`, annual maximum nearly used.
- **Procedures**: a handful of codes with category and frequency limit. A caller does not need to give a code; "what is covered for fillings" works.
- **`standing_answers`**: answers given whoever the patient is (employee ID, fee schedule, once-per-lifetime, missing tooth clause, prior carrier, treatment in progress).
- **`benefit_answers`**: a plausible answer for every question in the platform's insurance benefit catalog, found by `lookup_benefit_answer`.

Adding a member or an answer needs no code change; edit the fixture. Tests check the fixture's shape, and `grounding.py` checks that what the representative said matches what the lookup returned.

## Running it

Python 3.11+.

```
pip install -e ".[dev]"
pytest
python -m fake_payer          # reads configuration from the environment; a .env file in the working directory fills in anything not set
```

It will not start if a required variable is missing, and no credential is ever printed or logged.

Required: `SONIOX_API_KEY`, `ANTHROPIC_API_KEY`, `FAKE_PAYER_NUMBER`, `ANSWER_KEY` (a secret of 16+ characters that goes in the answer URL).

Optional (defaults in parentheses; see [`.env.example`](.env.example) and `fake_payer/config.py`):

| Variable | Meaning |
| --- | --- |
| `MAX_CONCURRENT_CALLS` (3), `MAX_CALLS_PER_NUMBER_PER_HOUR` (120) | Volume limits |
| `MAX_CALL_SECONDS` (0) | Call-length limit; 0 means none |
| `DAILY_SPEND_CEILING_USD` (5), `COST_PER_MINUTE_USD` (0.15) | Spend estimate ceiling |
| `RING_COUNT` (2), `ANSWER_DELAY_SECONDS` (2), `REPLY_DELAY_SECONDS` (2) | Pacing: rings before answering, pause after the last ring, least time from the caller finishing to the first sound of the reply |
| `KEEP_THE_FLOOR` (1) | Carry on speaking when talked over |
| `LINE_MODE` (mixed), `TREE_FRACTION` (0.5) | Who answers: `conversation`, `tree` or `mixed`; in `mixed`, the share of calls the phone tree gets (0 to 1) |
| `TTS_VOICE` (Adrian), `SONIOX_STT_MODEL`, `SONIOX_TTS_MODEL` | Speech settings |
| `LISTEN_HOST` (127.0.0.1), `PORT` (8080) | Where it listens |
| `CALL_LOG_DIR` (calls), `LOG_RETENTION_DAYS` (7), `AUDIO_CACHE_DIR` | Call log and cached spoken refusals |

## Telephony

[Vonage](https://www.vonage.com) delivers the number. Vonage requests `GET|POST /answer?key=<ANSWER_KEY>` and the reply is an NCCO that connects the call to this program's websocket (audio/l16, 8 kHz). Only requests carrying the key are answered; every other path is refused. There are no pages and no user interface.

A Vonage number links to **one application**. If another app (for example a voice-agent app in another product) is linked to the number, calls stop reaching this line; relink the number to this line's application.

Speech is [Soniox](https://soniox.com) in and out; the conversation is a language model (Anthropic) with two tools, `lookup_coverage` / `lookup_benefit_answer` and `end_call`.

## Call log

Each call is written to a JSON file in `CALL_LOG_DIR` (which line answered, the turns, for a tree call its path of nodes, keys and entries, how it ended, duration, caller's last four digits and the call reference), and files older than `LOG_RETENTION_DAYS` are removed. The call reference the representative gives is derived from the call, so a caller's saved reference can be matched to its log entry.

## Deploying

The `Dockerfile` builds an image that keeps the call log and cached audio in the `/data` volume and listens on port 8080 behind a host reverse proxy. The hosting that runs it (VM, DNS, secrets, number linking and redeploys) is scripted in a separate infrastructure repository, not here. Redeploy only when no call is in progress.

## Not in this repository

Credentials, the telephone number, real insurer names and real data. Configuration comes from the environment; only an example with fictional values is kept here.

## Specification

The original design is in [`openspec/changes/add-fake-payer-phone-line/`](openspec/changes/add-fake-payer-phone-line/) (proposal, design, specs, tasks). Later behaviour (ringing, keeping the floor, ending the call, the dial tone, answers for the whole benefit catalog, no time limit) was added after it, so where they differ, the code and this README are current. The phone tree was added by [`openspec/changes/add-phone-tree/`](openspec/changes/add-phone-tree/). Identity questions that need more than a member number, refusals, a representative who demands a human and conflicting answers are still to come.
