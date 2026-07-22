"""Durable event-driven company dispatcher with an idle blocking wait."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import select
import socket
import stat
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import CompanyConfig
from .db import Store, utcnow
from .ops import CompanyOS


class WorkerAlreadyRunning(RuntimeError):
    pass


class EventEngine:
    def __init__(self, config: CompanyConfig, ceo_runtime=None):
        from .ceo_runtime import CEORuntime

        self.config = config
        self.osys = CompanyOS(config)
        self.store = Store(config.db_path)
        self.ceo_runtime = ceo_runtime or CEORuntime(config)
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"

    def init(self) -> None:
        self.osys.init()
        self._ensure_wake_fifo()
        self._ensure_strategic_review()

    def _ensure_strategic_review(self) -> bool:
        """Persist one CEO review when an active phase has no executable work."""
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            phase = conn.execute(
                "SELECT id FROM strategic_phases WHERE status='active' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if phase is None:
                return False
            active_work = conn.execute(
                "SELECT 1 FROM tasks WHERE status IN ('open', 'in_progress', 'blocked') LIMIT 1"
            ).fetchone()
            if active_work is not None:
                return False
            existing = conn.execute(
                """SELECT 1 FROM execution_events
                   WHERE event_type IN ('ceo.strategic_review', 'ceo.business_stall_review')
                     AND status IN ('pending', 'processing') LIMIT 1"""
            ).fetchone()
            if existing is not None:
                return True
            event_id = self.store.enqueue_event(
                conn,
                "ceo.strategic_review",
                "strategic_phase",
                int(phase["id"]),
                {"reason": "active strategic phase has no executable work"},
                priority=80,
            )
            self.store.audit(
                conn, "system", "schedule_strategic_review", "execution_event", event_id,
                {"phase_id": int(phase["id"]), "reason": "active phase work exhausted"},
            )
        self.store.notify_worker()
        return True

    def _ensure_wake_fifo(self) -> Path:
        path = self.store.worker_wake_path
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            mode = path.stat().st_mode
        except FileNotFoundError:
            os.mkfifo(path, 0o600)
        else:
            if not stat.S_ISFIFO(mode):
                raise RuntimeError(f"worker wake path is not a FIFO: {path}")
        return path

    @contextlib.contextmanager
    def worker_lock(self) -> Iterator[None]:
        self.init()
        path = self.store.worker_lock_path
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        acquired = False
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                with self.store.connect() as conn:
                    self.store.audit(
                        conn,
                        "system",
                        "worker_lock_rejected",
                        "event_worker",
                        self.worker_id,
                        {"lock_path": str(path)},
                    )
                raise WorkerAlreadyRunning("event worker is already running") from exc
            acquired = True
            os.ftruncate(fd, 0)
            os.write(fd, f"{self.worker_id}\n".encode("ascii"))
            with self.store.connect() as conn:
                self.store.audit(
                    conn,
                    "system",
                    "worker_lock_acquired",
                    "event_worker",
                    self.worker_id,
                    {"lock_path": str(path)},
                )
            yield
        finally:
            if acquired:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)
                with self.store.connect() as conn:
                    self.store.audit(
                        conn,
                        "system",
                        "worker_lock_released",
                        "event_worker",
                        self.worker_id,
                        {"lock_path": str(path)},
                    )
            else:
                os.close(fd)

    def wake(self, reason: str, actor: str = "operator") -> dict[str, Any]:
        self.init()
        if not reason.strip():
            raise ValueError("wake reason must not be empty")
        with self.store.connect() as conn:
            event_id = self.store.enqueue_event(
                conn,
                "worker.wake",
                "event_worker",
                None,
                {"actor": actor, "reason": reason.strip()},
            )
            self.store.audit(
                conn,
                actor,
                "wake_event_worker",
                "execution_event",
                event_id,
                {"reason": reason.strip()},
            )
        self.store.notify_worker()
        return {"event_id": event_id, "event_type": "worker.wake", "status": "pending"}

    def wait_for_wake(self, timeout: float | None = None) -> bool:
        """Block in the kernel; this path does not run cycles or call a backend."""
        self.init()
        if self._pending_count():
            return True
        fd = os.open(self.store.worker_wake_path, os.O_RDWR | os.O_NONBLOCK)
        try:
            # Opening before the second queue check closes the enqueue/wait race.
            if self._pending_count():
                return True
            wait_timeout = self._next_wake_timeout(timeout)
            readable, _, _ = select.select([fd], [], [], wait_timeout)
            if not readable:
                return self._enqueue_recovery_wake_if_due()
            try:
                os.read(fd, 4096)
            except BlockingIOError:
                pass
            return True
        finally:
            os.close(fd)

    def step(self) -> dict[str, Any]:
        with self.worker_lock():
            recovered = self._recover_processing_events()
            return self._process_one(recovered)

    def run(self) -> None:
        with self.worker_lock():
            recovered = self._recover_processing_events()
            self._set_worker_state("running", recovered=recovered)
            try:
                while True:
                    result = self._process_one(0)
                    if result["status"] == "idle":
                        self._set_worker_state("waiting")
                        self.wait_for_wake()
                        self._set_worker_state("running")
            except BaseException as exc:
                self._set_worker_state("stopped", error=None if isinstance(exc, KeyboardInterrupt) else str(exc))
                raise

    def _recover_processing_events(self) -> int:
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT id, worker_id FROM execution_events WHERE status='processing' ORDER BY id"
            ).fetchall()
            if not rows:
                return 0
            now = utcnow()
            conn.execute(
                """UPDATE execution_events
                   SET status='pending', claimed_at=NULL, worker_id=NULL,
                       last_error='worker restarted before acknowledgement'
                   WHERE status='processing'"""
            )
            details = {"event_ids": [row["id"] for row in rows], "recovered_count": len(rows)}
            self.store.audit(
                conn,
                "system",
                "recover_processing_events",
                "event_worker",
                self.worker_id,
                details,
            )
            recovered_count = len(rows)
        self.store.notify_worker()
        return recovered_count

    def _claim_one(self):
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT * FROM execution_events
                   WHERE status='pending' AND available_at <= ?
                   ORDER BY priority DESC, available_at, id LIMIT 1""",
                (utcnow(),),
            ).fetchone()
            if row is None:
                return None
            claimed_at = utcnow()
            conn.execute(
                """UPDATE execution_events
                   SET status='processing', claimed_at=?, worker_id=?,
                       attempts=attempts + 1, last_error=NULL
                   WHERE id=? AND status='pending'""",
                (claimed_at, self.worker_id, row["id"]),
            )
            claimed = conn.execute("SELECT * FROM execution_events WHERE id=?", (row["id"],)).fetchone()
            self.store.audit(
                conn,
                "system",
                "claim_execution_event",
                "execution_event",
                row["id"],
                {"event_type": row["event_type"], "worker_id": self.worker_id},
            )
            return dict(claimed)

    def _process_one(self, recovered: int) -> dict[str, Any]:
        event = self._claim_one()
        if event is None and self._enqueue_recovery_wake_if_due():
            event = self._claim_one()
        if event is None:
            return {"status": "idle", "recovered_events": recovered}
        try:
            ceo = self.ceo_runtime.process_event(event)
            if ceo["classification"] == "deterministic_dispatch":
                dispatch = self.osys.run_cycle()
            else:
                dispatch = None
            dispatch_outcome = self._enforce_task_dispatch_outcome(event, ceo, dispatch, recovered)
            if dispatch_outcome is not None:
                return dispatch_outcome
        except Exception as exc:
            with self.store.connect() as conn:
                conn.execute(
                    """UPDATE execution_events
                       SET status='pending', worker_id=NULL, claimed_at=NULL,
                           last_error=? WHERE id=?""",
                    (str(exc), event["id"]),
                )
                self.store.audit(
                    conn,
                    "system",
                    "fail_execution_event",
                    "execution_event",
                    event["id"],
                    {"error": str(exc), "event_type": event["event_type"]},
                )
            raise
        if ceo["status"] in {
            "retry_scheduled",
            "superseded",
            "delivery_retry_scheduled",
            "delivery_disabled",
        }:
            return self._defer_event(event, ceo, recovered)
        if ceo["status"] == "protocol_rejected":
            return self._dead_letter_event(event, ceo, recovered)
        if event["event_type"] in {"ceo.strategic_review", "ceo.business_stall_review"}:
            self._schedule_followup_review(event)
        with self.store.connect() as conn:
            processed_at = utcnow()
            conn.execute(
                """UPDATE execution_events
                   SET status='processed', processed_at=?, last_error=NULL
                   WHERE id=? AND worker_id=?""",
                (processed_at, event["id"], self.worker_id),
            )
            self.store.audit(
                conn,
                "system",
                "process_execution_event",
                "execution_event",
                event["id"],
                {"event_type": event["event_type"], "dispatch": dispatch, "ceo": ceo},
            )
            conn.execute(
                """UPDATE event_worker_state
                   SET heartbeat_at=?, events_processed=events_processed + 1,
                       last_error=NULL WHERE singleton=1""",
                (processed_at,),
            )
        return {
            "event_id": event["id"],
            "event_type": event["event_type"],
            "entity_type": event["entity_type"],
            "entity_id": event["entity_id"],
            "status": "processed",
            "recovered_events": recovered,
            "dispatch": dispatch,
            "ceo": ceo,
        }

    def _enforce_task_dispatch_outcome(
        self,
        event: dict[str, Any],
        ceo: dict[str, Any],
        dispatch: dict[str, Any] | None,
        recovered: int,
    ) -> dict[str, Any] | None:
        if event["event_type"] != "task.created" or not event.get("entity_id"):
            return None
        task_id = int(event["entity_id"])
        with self.store.connect() as conn:
            task = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
            execution = conn.execute(
                "SELECT id FROM task_executions WHERE task_id=? ORDER BY id DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        if task is None or task["status"] in {"blocked", "cancelled", "done"} or execution is not None:
            return None
        error = f"no executor claimed task {task_id} after deterministic dispatch"
        if int(event["attempts"]) >= 3:
            with self.store.connect() as conn:
                now = utcnow()
                reason = "No healthy executor claimed task after 3 dispatch attempts"
                conn.execute(
                    "UPDATE tasks SET status='blocked', updated_at=?, blocked_reason=? WHERE id=? AND status='open'",
                    (now, reason, task_id),
                )
                self.store.audit(
                    conn, "CEO", "block_unclaimed_task", "task", task_id,
                    {"attempts": event["attempts"], "reason": reason},
                )
            return None
        available_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).replace(microsecond=0).isoformat()
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """UPDATE execution_events SET status='pending', available_at=?, worker_id=NULL,
                          claimed_at=NULL, processed_at=NULL, last_error=?
                   WHERE id=? AND status='processing' AND worker_id=?""",
                (available_at, error, event["id"], self.worker_id),
            )
            self.store.audit(
                conn, "CEO", "defer_unclaimed_task", "task", task_id,
                {"available_at": available_at, "attempt": event["attempts"], "reason": error},
            )
        return {
            "event_id": event["id"], "event_type": event["event_type"],
            "entity_type": event["entity_type"], "entity_id": event["entity_id"],
            "status": "deferred", "recovered_events": recovered,
            "dispatch": dispatch, "ceo": ceo,
        }

    def _schedule_followup_review(self, event: dict[str, Any]) -> None:
        due = (datetime.now(timezone.utc) + timedelta(hours=24)).replace(microsecond=0).isoformat()
        with self.store.connect() as conn:
            existing = conn.execute(
                """SELECT 1 FROM execution_events
                   WHERE event_type='ceo.business_stall_review'
                     AND status IN ('pending', 'processing') LIMIT 1"""
            ).fetchone()
            if existing is not None:
                return
            event_id = self.store.enqueue_event(
                conn, "ceo.business_stall_review", "strategic_phase", event["entity_id"],
                {"reason": "verify material business progress after strategic review"},
                priority=70, available_at=due,
            )
            self.store.audit(
                conn, "CEO", "schedule_business_stall_review", "execution_event", event_id,
                {"available_at": due, "phase_id": event["entity_id"]},
            )
        self.store.notify_worker()

    def _defer_event(self, event: dict[str, Any], ceo: dict[str, Any], recovered: int) -> dict[str, Any]:
        error = ceo.get("error") or (
            "CEO result superseded by a newer state or Chairman directive"
            if ceo["status"] == "superseded"
            else ceo["status"]
        )
        available_at = None
        if ceo["status"] == "delivery_retry_scheduled":
            delivery = self.store.fetch_one(
                "SELECT available_at FROM approval_deliveries WHERE approval_id=?",
                (event["entity_id"],),
            )
            available_at = delivery["available_at"] if delivery else None
        if available_at is None:
            from .ceo_runtime import _retry_at

            available_at = _retry_at(int(event["attempts"]))
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """UPDATE execution_events
                   SET status='pending', available_at=?, worker_id=NULL,
                       claimed_at=NULL, processed_at=NULL, last_error=?
                   WHERE id=? AND status='processing' AND worker_id=?""",
                (available_at, error, event["id"], self.worker_id),
            )
            self.store.audit(
                conn,
                "system",
                "defer_execution_event",
                "execution_event",
                event["id"],
                {
                    "event_type": event["event_type"],
                    "ceo_status": ceo["status"],
                    "available_at": available_at,
                    "error": error,
                },
            )
        return {
            "event_id": event["id"],
            "event_type": event["event_type"],
            "entity_type": event["entity_type"],
            "entity_id": event["entity_id"],
            "status": "deferred",
            "recovered_events": recovered,
            "dispatch": None,
            "ceo": ceo,
        }

    def _dead_letter_event(self, event: dict[str, Any], ceo: dict[str, Any], recovered: int) -> dict[str, Any]:
        error = ceo.get("error") or ceo["status"]
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """UPDATE execution_events
                   SET status='failed', worker_id=NULL, claimed_at=NULL,
                       processed_at=?, last_error=?
                   WHERE id=? AND status='processing' AND worker_id=?""",
                (utcnow(), error, event["id"], self.worker_id),
            )
            self.store.audit(
                conn,
                "system",
                "dead_letter_execution_event",
                "execution_event",
                event["id"],
                {
                    "event_type": event["event_type"],
                    "attempts": event["attempts"],
                    "error": error,
                },
            )
        return {
            "event_id": event["id"],
            "event_type": event["event_type"],
            "entity_type": event["entity_type"],
            "entity_id": event["entity_id"],
            "status": "failed",
            "recovered_events": recovered,
            "dispatch": None,
            "ceo": ceo,
        }

    def _set_worker_state(self, status: str, error: str | None = None, recovered: int = 0) -> None:
        now = utcnow()
        with self.store.connect() as conn:
            conn.execute(
                """UPDATE event_worker_state SET
                       status=?, worker_id=?, process_id=?,
                       started_at=CASE WHEN ?='running' AND status='stopped' THEN ? ELSE started_at END,
                       heartbeat_at=?, stopped_at=CASE WHEN ?='stopped' THEN ? ELSE NULL END,
                       last_error=?
                   WHERE singleton=1""",
                (status, self.worker_id, os.getpid(), status, now, now, status, now, error),
            )
            self.store.audit(
                conn,
                "system",
                f"worker_{status}",
                "event_worker",
                self.worker_id,
                {"error": error, "recovered_events": recovered},
            )

    def _pending_count(self) -> int:
        row = self.store.fetch_one(
            "SELECT COUNT(*) AS count FROM execution_events WHERE status='pending' AND available_at <= ?",
            (utcnow(),),
        )
        return int(row["count"])

    def _next_wake_timeout(self, requested: float | None) -> float | None:
        recovery_timeout = self._recovery_timeout(requested)
        row = self.store.fetch_one(
            "SELECT MIN(available_at) AS due_at FROM execution_events WHERE status='pending'"
        )
        if row is None or row["due_at"] is None:
            return recovery_timeout
        try:
            due = datetime.fromisoformat(str(row["due_at"]))
        except ValueError:
            due_seconds = 0.0
        else:
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            due_seconds = max(0.0, (due - datetime.now(timezone.utc)).total_seconds())
        return due_seconds if recovery_timeout is None else min(recovery_timeout, due_seconds)

    def _recovery_timeout(self, requested: float | None) -> float | None:
        row = self.store.fetch_one(
            """SELECT MIN(lease_expires_at) AS due_at
               FROM task_executions WHERE recovery_status IN ('running', 'failed')"""
        )
        if row is None or row["due_at"] is None:
            return requested
        try:
            due = datetime.fromisoformat(str(row["due_at"]))
        except ValueError:
            due_seconds = 0.0
        else:
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            due_seconds = max(0.0, (due - datetime.now(timezone.utc)).total_seconds())
        return due_seconds if requested is None else min(requested, due_seconds)

    def _enqueue_recovery_wake_if_due(self) -> bool:
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            due = conn.execute(
                """SELECT 1 FROM task_executions
                   WHERE recovery_status='failed' OR (
                       recovery_status='running' AND lease_expires_at <= ?
                   ) LIMIT 1""",
                (utcnow(),),
            ).fetchone()
            if due is None:
                return False
            existing = conn.execute(
                """SELECT 1 FROM execution_events
                   WHERE event_type='worker.wake'
                     AND status IN ('pending', 'processing')
                     AND payload=? LIMIT 1""",
                (json.dumps({"reason": "task execution recovery due"}, sort_keys=True),),
            ).fetchone()
            if existing is not None:
                return True
            event_id = self.store.enqueue_event(
                conn,
                "worker.wake",
                "task_execution",
                None,
                {"reason": "task execution recovery due"},
            )
            self.store.audit(
                conn,
                "system",
                "schedule_task_recovery",
                "execution_event",
                event_id,
                {"reason": "task execution recovery due"},
            )
        return True

    def status(self) -> dict[str, Any]:
        self.init()
        state = self.store.fetch_one("SELECT * FROM event_worker_state WHERE singleton=1")
        counts = {
            row["status"]: int(row["count"])
            for row in self.store.fetch_all(
                "SELECT status, COUNT(*) AS count FROM execution_events GROUP BY status"
            )
        }
        lock_held = self._lock_held()
        last_event = self.store.fetch_one(
            "SELECT id, event_type, processed_at, last_error FROM execution_events ORDER BY id DESC LIMIT 1"
        )
        state_dict = dict(state)
        stale_active_state = state_dict["status"] in {"running", "waiting"} and not lock_held
        interrupted_processing = counts.get("processing", 0) and not lock_held
        health = "degraded" if stale_active_state or interrupted_processing else "healthy"
        return {
            "health": health,
            "worker_status": state_dict["status"],
            "worker_id": state_dict["worker_id"],
            "process_id": state_dict["process_id"],
            "lock_held": lock_held,
            "pending_events": counts.get("pending", 0),
            "processing_events": counts.get("processing", 0),
            "processed_events": counts.get("processed", 0),
            "events_processed": state_dict["events_processed"],
            "heartbeat_at": state_dict["heartbeat_at"],
            "last_error": state_dict["last_error"],
            "last_event": dict(last_event) if last_event else None,
        }

    def _lock_held(self) -> bool:
        fd = os.open(self.store.worker_lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        finally:
            os.close(fd)
