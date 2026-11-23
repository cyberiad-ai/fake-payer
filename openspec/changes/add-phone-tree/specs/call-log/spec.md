# Spec Delta

## MODIFIED Requirements

### Requirement: What is recorded
Each call's entry SHALL also record which line answered (`mode`: `conversation` or `tree`) and, for a tree call, the `events` of its path: each node visited, key pressed, entry made and failure, with the time. A tree call has no model turns, so its figures are not checked for grounding; a tree call handed to the representative has the representative's turns as well.

#### Scenario: A tree call
- **WHEN** a call answered by the tree ends
- **THEN** its entry says `mode: tree` and lists the nodes visited and the keys pressed
