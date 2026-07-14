# Event Worker Deployment

The company execution engine is event-driven. SQLite is the durable event and wake
queue; a local FIFO provides an edge notification so an idle worker sleeps in the
kernel instead of polling. New tasks, completion, cancellation, execution failure,
recovery, Chairman decisions, and explicit operator wakes are persisted before they
can cause another dispatch pass.

Active execution leases add a one-shot deadline to that blocking wait. At the nearest
lease expiry the worker persists an internal wake and runs recovery; this is not a
fixed-interval pulse. Existing active tasks are backfilled into the event queue during
the idempotent SQLite migration.

The worker performs governance dispatch only. It does not invoke an LLM, launch Codex,
create replacement work, publish, spend, contact customers, deploy, or perform another
external action. Executors remain separately bounded and must attach reviewable
evidence through the existing task lifecycle.

## Preflight and one-step verification

From the repository root:

```bash
python3.11 -m agent_company.cli init
python3.11 -m agent_company.cli worker-status
python3.11 -m agent_company.cli worker-step
python3.11 -m agent_company.cli worker-wake --reason "operator verification"
python3.11 -m agent_company.cli worker-step
make test
make validate
```

`worker-step` takes the same non-blocking single-instance lock as the long-running
worker and processes at most one event. Use it for CEO verification before enabling
the service. An idle step returns `{"status": "idle"}` and creates no task.

## Install the user service

The checked-in template assumes the repository is at `%h/agent-company`. If it is
elsewhere, copy the file and change `WorkingDirectory` before enabling it.

```bash
mkdir -p ~/.config/systemd/user
cp deploy/agent-company-worker.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now agent-company-worker.service
```

Do not run the final command until CEO verification is complete. This repository
change deliberately does not start or enable the infinite worker.

## Operations and recovery

```bash
python3.11 -m agent_company.cli worker-status
python3.11 -m agent_company.cli worker-wake --reason "review durable queue"
systemctl --user status agent-company-worker.service
journalctl --user -u agent-company-worker.service
systemctl --user stop agent-company-worker.service
```

Only one worker can hold the file lock for a database. On restart, events left in
`processing` are returned to `pending` and audited before dispatch resumes. SQLite is
authoritative if a FIFO notification is lost. Health is `degraded` when a processing
event exists without a live lock holder; queue counts, worker heartbeat, last error,
and last event are exposed by `worker-status`.

Chairman approval blocks only its linked task. The other product or commercial lane
continues when safe, while the one-product/one-commercial WIP limit remains enforced.
The resident organization remains exactly CEO, Product Engineer, and Customer &
Revenue; reserved external decisions remain with the Chairman.
