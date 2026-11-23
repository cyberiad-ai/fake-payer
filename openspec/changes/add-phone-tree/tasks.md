# Tasks

- [x] 1. `speech_text.py`: numbers, money, percent, months, dates and digits as words. Verify: tests.
- [x] 2. `prompts.py`: spoken lines synthesized once, kept in memory and on disk, respelled for the voice, silence on failure. Verify: tests.
- [x] 3. `audio.hold_music_pcm`. Verify: length and not silent.
- [x] 4. `tree.py` and `tree.yaml`: load and check the file; `TreeRunner` with menu, collect, action, hold, transfer, hangup; barge-in; retry and give-up; the member actions. Verify: a test for every dead end and bad path and for the answers.
- [x] 5. Server: `websocket:dtmf` frames, `LINE_MODE` / `TREE_FRACTION`, the choice per call, the hand-over, the call log's `mode` and `events`. Verify: tests over a real websocket.
- [x] 6. README, `.env.example`, packaging of `tree.yaml`.
- [ ] 7. On a real call: confirm Vonage delivers the keys as `websocket:dtmf` frames, that the platform's caller can get through the tree, and that prompts sound right in the voice.
