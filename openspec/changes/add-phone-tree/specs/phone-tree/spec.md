# Spec Delta

## Purpose
A keypad phone menu that routes and answers, for testing automated callers against what real payers do before a person answers.

## ADDED Requirements

### Requirement: Which line answers
The line SHALL answer each call either as the conversational representative or as the phone tree, as `LINE_MODE` says: `conversation` (always the representative), `tree` (always the tree), `mixed` (each call the tree with probability `TREE_FRACTION`, otherwise the representative). The default SHALL be `mixed` with a fraction of one half. A value that is not one of the three modes, or a fraction outside 0 to 1, SHALL stop the line starting.

#### Scenario: Tree only
- **WHEN** `LINE_MODE` is `tree`
- **THEN** every call is answered by the phone tree and no speech-recognition or model service is used

#### Scenario: Mixed
- **WHEN** `LINE_MODE` is `mixed` and `TREE_FRACTION` is 0.5
- **THEN** about half of many calls are answered by the tree and the rest by the representative

### Requirement: Keypad input
The tree SHALL take its input from the keys the caller presses, delivered as Vonage `websocket:dtmf` events, and SHALL ignore the caller's speech. A key pressed while a menu or an entry prompt is playing SHALL stop the prompt and count as the answer.

#### Scenario: A key during a menu
- **WHEN** the caller presses 2 while the main menu is still playing
- **THEN** the menu stops and option 2 is taken

### Requirement: Answers from the fixture
Under eligibility and benefits the tree SHALL ask for a six-digit member ID and a date of birth (MMDDYYYY), and SHALL say eligibility dates, the annual maximum and deductible with what remains, coverage percentages and waiting periods, every figure from the same lookup the representative uses. For a terminated member it SHALL say the end date and give no benefit figures.

#### Scenario: An ordinary member
- **WHEN** the caller keys member 100001 and date of birth 03141985
- **THEN** the tree says Pat Sample's coverage is active, effective January first, twenty twenty-five, and offers the benefit options

#### Scenario: A terminated member
- **WHEN** the caller keys member 100003 and date of birth 11301978
- **THEN** the tree says coverage ended on June thirtieth, twenty twenty-six, and says nothing about benefits

### Requirement: Dead ends and bad paths
The tree SHALL include, on purpose: an option that says it cannot help and hangs up, an option that returns to the main menu, a menu loop with no way forward, a supervisor who never comes (hold, then the call is dropped), a refusal of a member whose details do not agree (three tries, then the call ends), an impossible date, an invalid key and silence. After three invalid or missing entries in a row at one prompt the tree SHALL say so and end the call.

#### Scenario: Silence
- **WHEN** the caller presses nothing at the main menu three times
- **THEN** the tree says it did not receive a valid entry and hangs up

### Requirement: Hand-over to the representative
Key 0 at the main menu SHALL play hold music and then hand the call to the conversational representative, who greets the caller without ringing or a second disclosure.

### Requirement: The tree is checked before use
The tree file SHALL be checked when it loads. A menu option or route to a node that does not exist, a node the start cannot reach, a menu without options, a collect with a bad length and an action with the wrong routes SHALL each stop the line starting.
