# Playbooks

## Run Cycle

1. Run `python3.11 -m agent_company.cli run-cycle`.
2. Review output for progressed and escalated tasks.
3. If approvals are pending, run `chairman-inbox`.
4. Chairman decides with `decide`.
5. Run `report` for the operating snapshot.

## Reserved Action

1. Agent detects a reserved action.
2. CEO writes an inbox approval file.
3. Task becomes blocked.
4. Chairman approves or denies.
5. Approved tasks return to open state; denied tasks close without external action.
