from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional
from typing_extensions import Protocol

from omo_task_queue.state import ExecutionMode, StateMachine, Task, TaskStatus
from omo_task_queue.store import Store

logger = logging.getLogger(__name__)


class LaunchAdapter(Protocol):
    def launch(self, task: Task) -> TaskResult: ...


@dataclass
class TaskResult:
    success: bool
    output: Optional[str] = None
    error_message: Optional[str] = None
    launched: bool = False
    session_id: Optional[str] = None


class OneShotAdapter:
    def __init__(self, runtime_client: Optional[Any] = None) -> None:
        self._runtime_client = runtime_client

    def launch(self, task: Task) -> TaskResult:
        logger.info("[one-shot] Executing task %s — %s", task.id, task.title)
        if self._runtime_client is None:
            return TaskResult(
                success=False,
                error_message="No runtime client configured for one-shot execution",
            )
        try:
            session_id = self._runtime_client.send_prompt(task.prompt, title=task.title)
            return TaskResult(
                success=True,
                output=f"One-shot prompt sent for {task.id}",
                launched=True,
                session_id=session_id,
            )
        except Exception as exc:
            logger.exception("One-shot adapter failed for task %s", task.id)
            return TaskResult(success=False, error_message=str(exc))


class ULWLoopAdapter:
    def __init__(self, runtime_client: Optional[Any] = None) -> None:
        self._runtime_client = runtime_client

    def launch(self, task: Task) -> TaskResult:
        logger.info(
            "[ulw-loop] Starting ULW loop for task %s — %s", task.id, task.title
        )
        if self._runtime_client is None:
            return TaskResult(
                success=False,
                error_message="No runtime client configured for ULW loop",
            )
        try:
            session_id = self._runtime_client.send_command(
                "ulw-loop", task.prompt, title=task.title
            )
            return TaskResult(
                success=True,
                output=f"ULW loop command sent for {task.id}",
                launched=True,
                session_id=session_id,
            )
        except Exception as exc:
            logger.exception("ULW loop adapter failed for task %s", task.id)
            return TaskResult(success=False, error_message=str(exc))


class RalphLoopAdapter:
    def __init__(self, runtime_client: Optional[Any] = None) -> None:
        self._runtime_client = runtime_client

    def launch(self, task: Task) -> TaskResult:
        logger.info(
            "[ralph-loop] Starting Ralph loop for task %s — %s", task.id, task.title
        )
        if self._runtime_client is None:
            return TaskResult(
                success=False,
                error_message="No runtime client configured for Ralph loop",
            )
        try:
            session_id = self._runtime_client.send_command(
                "ralph-loop", task.prompt, title=task.title
            )
            return TaskResult(
                success=True,
                output=f"Ralph loop command sent for {task.id}",
                launched=True,
                session_id=session_id,
            )
        except Exception as exc:
            logger.exception("Ralph loop adapter failed for task %s", task.id)
            return TaskResult(success=False, error_message=str(exc))


class RetryManager:
    def handle_failure(self, task: Task, store: Store, error_message: str) -> None:
        task.retry_count += 1
        task.error_message = error_message
        if task.retry_count <= task.max_retries:
            StateMachine.transition(task, TaskStatus.RETRY_WAIT)
        else:
            logger.warning(
                "Task %s exhausted all %d retries", task.id, task.max_retries
            )
        store.update_task(task)


class Dispatcher:
    def __init__(
        self,
        store: Store,
        retry_manager: Optional[RetryManager] = None,
        adapters: Optional[dict[ExecutionMode, LaunchAdapter]] = None,
        client: Optional[Any] = None,
        launch_callback: Optional[Callable[[Task, TaskResult], None]] = None,
        project_path: str = "",
    ) -> None:
        self._store = store
        self._retry_manager = retry_manager or RetryManager()
        self._adapters = adapters or self._default_adapters(client)
        self._currently_running: Optional[str] = None
        self._launch_callback = launch_callback
        self._project_path = project_path

    @staticmethod
    def _default_adapters(
        client: Optional[Any] = None,
    ) -> dict[ExecutionMode, LaunchAdapter]:
        from omo_task_queue.runtime_client import RuntimeClient

        runtime_client = RuntimeClient(client) if client is not None else None
        return {
            ExecutionMode.ONE_SHOT: OneShotAdapter(runtime_client),
            ExecutionMode.ULW_LOOP: ULWLoopAdapter(runtime_client),
            ExecutionMode.RALPH_LOOP: RalphLoopAdapter(runtime_client),
        }

    def dispatch(self, task: Task) -> TaskResult:
        if self._currently_running is not None:
            logger.error(
                "Dispatcher busy with task %s; cannot dispatch %s",
                self._currently_running,
                task.id,
            )
            return TaskResult(
                success=False,
                error_message=(
                    f"Dispatcher busy with task {self._currently_running}; "
                    f"cannot dispatch {task.id}"
                ),
            )

        adapter = self._adapters.get(task.mode)
        if adapter is None:
            logger.error("No adapter registered for mode %s", task.mode.value)
            return TaskResult(
                success=False,
                error_message=f"No adapter registered for mode {task.mode.value}",
            )

        self._currently_running = task.id
        try:
            result = adapter.launch(task)
        except Exception as exc:
            logger.exception(
                "Adapter %s raised for task %s", type(adapter).__name__, task.id
            )
            result = TaskResult(success=False, error_message=str(exc))
        finally:
            self._currently_running = None

        self.on_task_launched(task, result)
        return result

    def on_task_launched(self, task: Task, result: TaskResult) -> None:
        if result.success:
            logger.info("Task %s launched successfully", task.id)
            task.updated_at = datetime.utcnow()
            self._store.update_task(task)
            if self._launch_callback is not None:
                self._launch_callback(task, result)
            return

        logger.warning(
            "Task %s failed to launch: %s",
            task.id,
            result.error_message or "unknown error",
        )
        self._retry_manager.handle_failure(
            task, self._store, result.error_message or "unknown error"
        )

    def mark_task_completed(self, task_id: str) -> Optional[Task]:
        task = self._store.get_task(task_id, project_path=self._project_path)
        if task is None:
            logger.warning("Completion event for unknown task %s", task_id)
            return None
        if task.status is not TaskStatus.RUNNING:
            logger.info(
                "Ignoring completion for task %s in status %s",
                task.id,
                task.status.value,
            )
            return task
        logger.info("Task %s completed successfully", task.id)
        StateMachine.transition(task, TaskStatus.DONE)
        self._store.update_task(task)

        next_task = self._store.claim_next(project_path=self._project_path)
        if next_task is not None:
            logger.info("Auto-advancing to next task %s", next_task.id)
            self.dispatch(next_task)
        else:
            logger.info("No pending tasks — queue idle")
        return task

    def mark_task_failed(self, task_id: str, error_message: str) -> Optional[Task]:
        task = self._store.get_task(task_id, project_path=self._project_path)
        if task is None:
            logger.warning("Failure event for unknown task %s", task_id)
            return None
        if task.status is not TaskStatus.RUNNING:
            logger.info(
                "Ignoring failure for task %s in status %s",
                task.id,
                task.status.value,
            )
            return task
        logger.warning("Task %s failed: %s", task.id, error_message or "unknown error")
        self._retry_manager.handle_failure(
            task, self._store, error_message or "unknown error"
        )
        return task

    def start_next_pending(self) -> Optional[TaskResult]:
        if self._store.get_running_task(project_path=self._project_path) is not None:
            return None
        next_task = self._store.claim_next(project_path=self._project_path)
        if next_task is None:
            return None
        return self.dispatch(next_task)

    def on_task_completed(self, task: Task, result: TaskResult) -> None:
        if result.success:
            self.mark_task_completed(task.id)
        else:
            self.mark_task_failed(task.id, result.error_message or "unknown error")

    @property
    def currently_running(self) -> Optional[str]:
        return self._currently_running
