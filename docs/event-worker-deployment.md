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

The worker runs deterministic governance dispatch for ordinary events. Complex events
(`chairman.directive`, Chairman decisions, and task completion/failure/recovery) invoke
the persistent CEO control plane through one bounded Hermes query. The query may only
return an allowlisted structured protocol: update CEO state, create a bounded internal
task, request Chairman approval, or do nothing. It cannot run shell/file tools or directly
publish, spend, contact customers, deploy, export data, change pricing, or make a legal
commitment.

The CEO is one logical identity backed by versioned SQLite state rather than a permanently
resident chat process. It uses the Hermes default profile implicitly and invokes the
verified Hermes v0.18.2 form `hermes chat -q ... -Q --source tool -t todo --max-turns 1`.
The `tool` source keeps background sessions out of normal user session lists. This adapter
does not pass `--profile`, `--oneshot`, or `--usage-file`. Model/token fields stay null when
the supported `chat` command does not report them.

## Preflight and one-step verification

From the repository root:

```bash
python3.11 -m agent_company.cli init
python3.11 -m agent_company.cli worker-status
python3.11 -m agent_company.cli worker-step
python3.11 -m agent_company.cli worker-wake --reason "operator verification"
python3.11 -m agent_company.cli worker-step
python3.11 -m agent_company.cli ceo-status
python3.11 -m agent_company.cli ceo-step --fixture examples/ceo-actions-fixture.json --disable-external-delivery
make test
make validate
```

`worker-step` takes the same non-blocking single-instance lock as the long-running
worker and processes at most one event. Use it for CEO verification before enabling
the service. An idle step returns `{"status": "idle"}` and creates no task.
`ceo-step --fixture` creates a local complex fixture event when needed, processes it
without an LLM, and `--disable-external-delivery` prevents messaging. A fixture must be
a valid `ceo-actions/v1` JSON object.

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

The unit sets `HOME=%h` and includes `%h/.local/bin` in `PATH` so the user-installed
Hermes executable and the existing `~/.hermes` configuration are visible. Do not put
credentials in the unit file or repository. Verify `hermes --version`, `hermes chat
--help`, and `hermes send --help` as the service user before enabling it.

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

Events are claimed by descending priority and then availability/id. Retryable Hermes
timeouts and superseded reasoning atomically return their event to `pending`, with
`available_at` backoff and `last_error`. Failed approval-card delivery is also deferred,
so an available lower-priority safe event can proceed. Approval cards use the
Hermes-configured Weixin home destination exactly as `hermes send --to weixin`; no variable
name is treated as a chat ID. Delivery is idempotently recorded in SQLite.

Chairman approval blocks only its linked task. The other product or commercial lane
continues when safe, while the one-product/one-commercial WIP limit remains enforced.
The resident organization remains exactly CEO, Product Engineer, and Customer &
Revenue; reserved external decisions remain with the Chairman.
