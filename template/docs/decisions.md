<!-- SPDX-License-Identifier: Apache-2.0 -->
# Decision log: {{project_name}}

Append-only. One entry per owner decision, written the same day. Add a matching row to the STATUS.md
"Locked decisions" table. To reverse a decision, add a new entry that supersedes the old one. Do not edit
the old entry.

## Template
```
## D-001: <short title>   (YYYY-MM-DD, phase N)
- Decided by: <owner>
- Decision: <what was chosen>
- Options considered: <A, B, C, with one line each>
- Why: <reasons, with links to sources>
- Consequences: <what this locks, what it rules out>
- PR / ref: <PR number or commit>
- Supersedes: <D-xxx or none>
```

## D-000: Use OhmAI {{ohmai_version}} workflow   ({{date}}, phase 0)
- Decided by: owner (project created)
- Decision: phases and gates as in `.ohmai/phases/`; main = approved state only; PR = gate.
- Supersedes: none
