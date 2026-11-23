# Spec Delta

## MODIFIED Requirements

### Requirement: Answers from the fixture
The tree SHALL be able to give every answer the conversational line can, from the same fixture and lookup: for a verified member, eligibility dates, the annual maximum and deductible with what remains, coverage percentages, waiting periods, the services in each category with their frequency limits and how much is used, and a check of one procedure code (covered now or not, percentage, deductible, limit, the reason); for a valid group number alone, the plan's benefits without member figures; and every entry of the fixture's form answers, with no member needed. For a terminated member it SHALL say the end date and give no benefit figures.

#### Scenario: A form question
- **WHEN** the caller chooses plan questions, a topic and a question's key
- **THEN** the tree says that question's answer exactly as the fixture gives it

#### Scenario: Every question is reachable
- **WHEN** the caller walks every topic's pages
- **THEN** every one of the fixture's form questions has been offered and answered

#### Scenario: A procedure check
- **WHEN** a member whose cleanings are used up for the year asks about code D1110
- **THEN** the tree says it is not covered now because the frequency limit has been reached

### Requirement: The tree and the fixture agree
Every topic the fixture's form answers have SHALL be reached by a form menu, and every form menu's topic SHALL have questions; otherwise the line SHALL NOT start.

## ADDED Requirements

### Requirement: Reference number
The tree SHALL say, on request, that it is the automated line, its I D, and the call's reference number digit by digit; the reference SHALL be the one in the call log.

### Requirement: The line takes calls while the tree's lines are prepared
The tree's fixed spoken lines SHALL be prepared in the background; the line SHALL accept calls before they are ready, and a line not ready SHALL be made when first needed.
