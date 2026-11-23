# Proposal

## Why

The phone tree (`add-phone-tree`) answered only four things about a member. The platform's caller asks the representative a whole verification form, so a call that met the tree ended with most questions unanswered. The tree should answer whatever the conversational line can.

## What Changes

- Every question of the fixture's `benefit_answers` (128) is reachable by keypad under plan questions (6), grouped into topics (`topic` on each entry), seven to a page.
- A member's covered services with their frequency limits and usage, and a procedure-code check, are added under eligibility.
- Plan benefits by group number alone (7), and the reference number and I D (8).
- The tree is checked against the fixture at start: a topic without a menu, or a menu without questions, stops the line starting.
- The tree's spoken lines are prepared in the background, a few at a time, so the line takes calls at once instead of after the first start's synthesis. The tree waits twelve seconds for a key (it was eight) so a slower caller does not hear the menu again.

## Capabilities

### Modified Capabilities

- `phone-tree`: answers from the whole fixture, not only the four member figures.

## Impact

`fixture.yaml` gains a `topic` on each benefit answer (the conversational line ignores it; its tool result is unchanged). `tree.py`, `tree.yaml`, `prompts.py`, `__main__.py` change.
