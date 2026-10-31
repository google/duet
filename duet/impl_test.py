# Copyright 2021 The Duet Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import signal
from typing import Iterator, List

import pytest

import duet
import duet.impl as impl


class CompleteOnFlush(duet.BufferedFuture):
    def __init__(self):
        super().__init__()
        self.flushed = False

    def flush(self):
        self.flushed = True
        self.set_result(None)


def make_task(future: duet.AwaitableFuture) -> impl.Task:
    """Make a task from the given future.

    We advance the task once, which just starts the generator and yields the
    future itself.
    """
    task = impl.Task(future, impl.Scheduler(), None)
    task.advance()
    return task


class TestReadySet:
    def test_get_all_returns_all_ready_tasks(self):
        task1 = make_task(duet.completed_future(None))
        task2 = make_task(duet.completed_future(None))
        task3 = make_task(duet.AwaitableFuture())
        task4 = make_task(duet.completed_future(None))
        rs = impl.ReadySet()
        rs.register(task1)
        rs.register(task2)
        rs.register(task3)
        rs.register(task4)
        tasks = rs.get_all()
        assert tasks == [task1, task2, task4]

    def test_task_added_at_most_once(self):
        task = make_task(duet.completed_future(None))
        rs = impl.ReadySet()
        rs.register(task)
        rs.register(task)
        tasks = rs.get_all()
        assert tasks == [task]

    def test_futures_flushed_if_no_task_ready(self):
        future = CompleteOnFlush()
        task = make_task(future)
        rs = impl.ReadySet()
        rs.register(task)
        tasks = rs.get_all()
        assert tasks == [task]
        assert future.flushed

    def test_futures_not_flushed_if_tasks_ready(self):
        future = CompleteOnFlush()
        task1 = make_task(future)
        task2 = make_task(duet.completed_future(None))
        rs = impl.ReadySet()
        rs.register(task1)
        rs.register(task2)
        tasks = rs.get_all()
        assert tasks == [task2]
        assert not future.flushed


class MainTaskFirstSet(set[impl.Task]):
    """Set that yields `first` before the other tasks when iterated."""

    def __init__(self, tasks: set[impl.Task], first: impl.Task) -> None:
        super().__init__(tasks)
        self._first = first

    def __iter__(self) -> Iterator[impl.Task]:
        return iter(sorted(super().__iter__(), key=lambda task: task is not self._first))


class TestScheduler:
    def test_sigint_interrupts_interruptible_task(self) -> None:
        """SIGINT is not dropped if the first active task is not interruptible."""
        child_interrupted = False

        async def main() -> None:
            nonlocal child_interrupted
            main_task = impl.current_task()
            scheduler = main_task.scheduler
            scheduler.active_tasks = MainTaskFirstSet(scheduler.active_tasks, main_task)
            child_started: duet.AwaitableFuture[None] = duet.AwaitableFuture()

            async def child() -> None:
                nonlocal child_interrupted
                child_started.set_result(None)
                try:
                    # Finite, so that the test fails rather than hangs if the
                    # SIGINT is dropped.
                    await duet.sleep(2)
                except KeyboardInterrupt:
                    child_interrupted = True
                    raise

            with pytest.raises(KeyboardInterrupt):
                async with duet.new_scope() as scope:
                    scope.spawn(child)
                    await child_started
                    # Simulate main_task waiting for its tasks after an error,
                    # when it is not interruptible.
                    main_task.interruptible = False
                    scheduler._interrupt(signal.SIGINT, None)
                    await duet.completed_future(None)
                    main_task.interruptible = True
                    await duet.completed_future(None)

        duet.run(main)
        assert child_interrupted

    def test_keyboard_interrupt_kept_pending_if_main_task_cannot_take_it(self) -> None:
        """A task's KeyboardInterrupt is not dropped if its main task can't take it."""

        async def main() -> None:
            main_task = impl.current_task()
            scheduler = main_task.scheduler
            child_started: duet.AwaitableFuture[None] = duet.AwaitableFuture()

            async def child() -> None:
                child_started.set_result(None)
                await duet.sleep(2)

            with pytest.raises(KeyboardInterrupt):
                async with duet.new_scope() as scope:
                    scope.spawn(child)
                    await child_started
                    # Only the child can take the SIGINT, and its KeyboardInterrupt
                    # reaches main_task while main_task is not interruptible.
                    main_task.interruptible = False
                    scheduler._interrupt(signal.SIGINT, None)
                    await duet.completed_future(None)
                    await duet.completed_future(None)
                    main_task.interruptible = True
                    # Finite, so that the test fails rather than hangs if the
                    # KeyboardInterrupt is dropped.
                    await duet.sleep(2)

        duet.run(main)

    def test_sigint_pending_when_run_finishes_is_raised(self) -> None:
        """duet.run raises KeyboardInterrupt if no task could take a SIGINT in time."""

        async def main() -> str:
            main_task = impl.current_task()
            main_task.interruptible = False
            main_task.scheduler._interrupt(signal.SIGINT, None)
            await duet.completed_future(None)
            main_task.interruptible = True
            return "finished"

        with pytest.raises(KeyboardInterrupt):
            duet.run(main)

    def test_sigint_stays_pending_while_no_task_is_interruptible(self) -> None:
        """SIGINT is delivered on a later tick if no task can be interrupted now."""
        events: List[str] = []

        async def main() -> None:
            main_task = impl.current_task()
            main_task.interruptible = False
            main_task.scheduler._interrupt(signal.SIGINT, None)
            await duet.completed_future(None)  # No task can be interrupted in this tick.
            events.append("deferred")
            main_task.interruptible = True
            await duet.completed_future(None)  # The pending SIGINT is delivered now.
            events.append("not interrupted")

        with pytest.raises(KeyboardInterrupt):
            duet.run(main)
        assert events == ["deferred"]
