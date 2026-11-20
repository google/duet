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

import threading
import time

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


class FakeClockScheduler(impl.Scheduler):
    """Scheduler with a manually controllable clock for deterministic testing."""

    def __init__(self, now: float = 0.0) -> None:
        super().__init__()
        self.now = now

    def time(self) -> float:
        return self.now


class TestSchedulerDeadlines:
    def test_child_task_close_invalidates_inherited_deadline(self) -> None:
        scheduler = FakeClockScheduler(now=100.0)

        async def child() -> None:
            await duet.completed_future(None)

        async def main() -> None:
            async with duet.new_scope(timeout=10.0) as scope:
                scope.spawn(child)
                scope.spawn(child)

        scheduler.spawn(main())
        while scheduler.active_tasks:
            scheduler.tick()

        assert scheduler.get_next_deadline() is None
        assert not scheduler._deadlines
        assert scheduler._valid_deadlines == 0

    def test_multiple_distinct_expired_deadlines_fire_in_single_tick(self) -> None:
        scheduler = FakeClockScheduler(now=100.0)
        timed_out = []

        async def waiter(name: str, deadline: float) -> None:
            try:
                async with duet.deadline_scope(deadline):
                    await duet.AwaitableFuture()
            except TimeoutError:
                timed_out.append(name)

        scheduler.spawn(waiter("a", 101.0))
        scheduler.spawn(waiter("b", 102.0))
        scheduler.spawn(waiter("c", 110.0))

        # Advance initial step so all tasks register their deadlines and wait.
        scheduler.tick()
        assert timed_out == []

        # Jump past the first two distinct deadlines (101.0 and 102.0).
        scheduler.now = 105.0
        scheduler.tick()
        assert timed_out == ["a", "b"]

        # Jump past the third deadline.
        scheduler.now = 115.0
        scheduler.tick()
        assert timed_out == ["a", "b", "c"]
        assert not scheduler.active_tasks

    def test_uninterruptible_expired_deadlines_do_not_block_later_deadline(self) -> None:
        scheduler = FakeClockScheduler(now=100.0)
        timed_out = []
        futures = []

        async def waiter(name: str, deadline: float) -> None:
            f: duet.AwaitableFuture[None] = duet.AwaitableFuture()
            futures.append(f)
            try:
                async with duet.deadline_scope(deadline):
                    await f
            except TimeoutError:
                timed_out.append(name)

        t1 = scheduler.spawn(waiter("uninterruptible_1", 101.0))
        t2 = scheduler.spawn(waiter("uninterruptible_2", 102.0))
        t3 = scheduler.spawn(waiter("interruptible", 105.0))

        scheduler.tick()
        t1.interruptible = False
        t2.interruptible = False

        # Jump past all three deadlines: t1 and t2 are no-ops, t3 must still wake.
        scheduler.now = 106.0
        scheduler.tick()
        assert timed_out == ["interruptible"]
        assert t3.done

        # Clean up remaining uninterruptible tasks.
        for f in futures:
            f.try_set_result(None)
        while scheduler.active_tasks:
            scheduler.tick()
        assert not scheduler._deadlines
        assert scheduler._valid_deadlines == 0

    def test_uninterruptible_expired_deadline_continues_waiting_for_future_deadline(self) -> None:
        scheduler = impl.Scheduler()
        timed_out = []
        futures = []

        async def waiter(name: str, timeout: float) -> None:
            f: duet.AwaitableFuture[None] = duet.AwaitableFuture()
            futures.append(f)
            try:
                async with duet.timeout_scope(timeout):
                    await f
            except TimeoutError:
                timed_out.append(name)

        t1 = scheduler.spawn(waiter("uninterruptible_1", 0.02))
        t2 = scheduler.spawn(waiter("uninterruptible_2", 0.04))
        t3 = scheduler.spawn(waiter("interruptible", 0.08))

        scheduler.tick()
        t1.interruptible = False
        t2.interruptible = False

        # One tick should drain t1 and t2 as they expire, then keep waiting until t3 expires.
        start = time.time()
        scheduler.tick()
        elapsed = time.time() - start
        assert timed_out == ["interruptible"]
        assert t3.done
        assert elapsed < 1.0

        # Clean up remaining tasks.
        for f in futures:
            f.try_set_result(None)
        while scheduler.active_tasks:
            scheduler.tick()
        assert not scheduler._deadlines
        assert scheduler._valid_deadlines == 0

    def test_invalidated_deadlines_are_compacted(self) -> None:
        scheduler = FakeClockScheduler(now=100.0)
        keep_open: duet.AwaitableFuture[None] = duet.AwaitableFuture()

        async def long_waiter() -> None:
            async with duet.deadline_scope(200.0):
                await keep_open

        async def churn() -> None:
            for i in range(500):
                # Deadlines later than 200.0 sit below long_waiter in the min-heap.
                async with duet.deadline_scope(300.0 + i):
                    await duet.completed_future(None)
            # Heap should have been compacted rather than holding all 500 invalid entries.
            assert len(scheduler._deadlines) <= 64
            assert scheduler._valid_deadlines == 1
            keep_open.set_result(None)

        scheduler.spawn(long_waiter())
        scheduler.spawn(churn())
        while scheduler.active_tasks:
            scheduler.tick()
        assert not scheduler._deadlines
        assert scheduler._valid_deadlines == 0

    def test_interrupt_signal_while_waiting_on_deadline_does_not_timeout(self) -> None:
        scheduler = impl.Scheduler()

        async def waiter() -> None:
            async with duet.timeout_scope(10.0):
                await duet.AwaitableFuture()

        task = scheduler.spawn(waiter())
        scheduler.tick()  # Start task and register 10s deadline.

        def send_interrupt() -> None:
            time.sleep(0.05)
            scheduler._interrupted = True
            scheduler._ready_tasks.interrupt()

        thread = threading.Thread(target=send_interrupt)
        thread.start()
        try:
            with pytest.raises(KeyboardInterrupt):
                scheduler.tick()
        finally:
            thread.join()

        assert task.done
        assert not scheduler._deadlines
