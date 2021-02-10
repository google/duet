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

import inspect
import sys
import traceback

import pytest

import duet
import duet.impl as impl


async def mul(a, b):
    await duet.completed_future(None)
    return a * b


async def add(a, b):
    await duet.completed_future(None)
    return a + b


class Fail(Exception):
    pass


async def fail_after_await():
    await duet.completed_future(None)
    raise Fail()


async def fail_before_await():
    raise Fail()


fail_funcs = [fail_after_await, fail_before_await]


class TestAwaitableFunc:
    def test_wrap_async_func(self):
        async def async_func(a, b):
            await duet.completed_future(None)
            return a + b

        assert duet.awaitable_func(async_func) is async_func
        assert duet.run(async_func, 1, 2) == 3

    def test_wrap_sync_func(self):
        def sync_func(a, b):
            return a + b

        wrapped = duet.awaitable_func(sync_func)
        assert inspect.iscoroutinefunction(wrapped)
        assert duet.awaitable_func(wrapped) is wrapped  # Don't double-wrap
        assert duet.run(wrapped, 1, 2) == 3


class TestRun:
    def test_future(self):
        def func(value):
            return duet.completed_future(value * 2)

        assert duet.run(func, 1) == 2

    def test_function(self):
        async def func(value):
            value = await duet.completed_future(value * 2)
            return value * 3

        assert duet.run(func, 1) == 2 * 3

    def test_function_returning_none(self):
        side_effects = []

        async def func(value):
            value = await duet.completed_future(value * 2)
            value = await duet.completed_future(value * 3)
            side_effects.append(value)

        assert duet.run(func, 1) is None
        assert side_effects == [2 * 3]  # make sure func ran to completion

    def test_nested_functions(self):
        async def func(value):
            value = await sub_func(value * 2)
            return value * 3

        async def sub_func(value):
            value = await duet.completed_future(value * 5)
            return value * 7

        assert duet.run(func, 1) == 2 * 3 * 5 * 7

    def test_nested_functions_returning_none(self):
        side_effects = []

        async def func(value):
            value2 = await sub_func(value * 2)
            return value * 3, value2

        async def sub_func(value):
            value = await duet.completed_future(value * 5)
            value = await duet.completed_future(value * 7)
            side_effects.append(value)

        assert duet.run(func, 1) == (3, None)
        assert side_effects == [2 * 5 * 7]

    def test_failed_future(self):
        async def func(value):
            try:
                await duet.failed_future(Exception())
                return value * 2
            except Exception:
                return value * 3

        assert duet.run(func, 1) == 3

    def test_failed_nested_generator(self):
        side_effects = []

        async def func(value):
            try:
                await sub_func(value * 2)
                return value * 3
            except Exception:
                return value * 5

        async def sub_func(value):
            await duet.failed_future(Exception())
            side_effects.append(value * 7)

        assert duet.run(func, 1) == 5
        assert side_effects == []

    @pytest.mark.parametrize("fail_func", fail_funcs)
    def test_failure_propagates(self, fail_func):
        with pytest.raises(Fail):
            duet.run(fail_func)


class TestPmap:
    def test_ordering(self):
        """pmap results are in order, even if funcs finish out of order."""
        finished = []

        async def func(value):
            iterations = 10 - value
            for i in range(iterations):
                await duet.completed_future(i)
            finished.append(value)
            return value * 2

        results = duet.pmap(func, range(10), limit=10)
        assert results == [i * 2 for i in range(10)]
        assert finished == list(reversed(range(10)))

    @pytest.mark.parametrize("limit", [3, 10, None])
    def test_failure(self, limit):
        async def foo(i):
            if i == 7:
                raise ValueError("I do not like 7 :-(")
            return 7 * i

        with pytest.raises(ValueError):
            duet.pmap(foo, range(100), limit=limit)


class TestPstarmap:
    def test_ordering(self):
        """pstarmap results are in order, even if funcs finish out of order."""
        finished = []

        async def func(a, b):
            value = 5 * a + b
            iterations = 10 - value
            for i in range(iterations):
                await duet.completed_future(i)
            finished.append(value)
            return value * 2

        args_iter = ((a, b) for a in range(2) for b in range(5))
        results = duet.pstarmap(func, args_iter, limit=10)
        assert results == [i * 2 for i in range(10)]
        assert finished == list(reversed(range(10)))


class TestPmapAsync:
    @duet.sync
    async def test_ordering(self):
        """pmap_async results in order, even if funcs finish out of order."""
        finished = []

        async def func(value):
            iterations = 10 - value
            for i in range(iterations):
                await duet.completed_future(i)
            finished.append(value)
            return value * 2

        results = await duet.pmap_async(func, range(10), limit=10)
        assert results == [i * 2 for i in range(10)]
        assert finished == list(reversed(range(10)))

    @duet.sync
    async def test_laziness(self):
        live = set()

        async def func(i):
            num_live = len(live)
            live.add(i)
            await duet.completed_future(i)
            live.remove(i)
            return num_live

        num_lives = await duet.pmap_async(func, range(100), limit=10)
        assert all(num_live <= 10 for num_live in num_lives)


class TestPstarmapAsync:
    @duet.sync
    async def test_ordering(self):
        """pstarmap_async results in order, even if funcs finish out of order."""
        finished = []

        async def func(a, b):
            value = 5 * a + b
            iterations = 10 - value
            for i in range(iterations):
                await duet.completed_future(i)
            finished.append(value)
            return value * 2

        args_iter = ((a, b) for a in range(2) for b in range(5))
        results = await duet.pstarmap_async(func, args_iter, limit=10)
        assert results == [i * 2 for i in range(10)]
        assert finished == list(reversed(range(10)))


class TestLimiter:
    @duet.sync
    async def test_ordering(self):
        """Check that waiting coroutines acquire limiter in order."""
        limiter = duet.Limiter(1)
        acquired = []

        async def func(i):
            async with limiter:
                acquired.append(i)
                await duet.completed_future(None)

        async with duet.new_scope() as scope:
            for i in range(10):
                scope.spawn(func, i)

        assert acquired == sorted(acquired)


class TestScope:
    @duet.sync
    async def test_run_all(self):
        results = {}

        async def func(a, b):
            results[a, b] = await mul(a, b)

        async with duet.new_scope() as scope:
            for a in range(10):
                for b in range(10):
                    scope.spawn(func, a, b)
        assert results == {(a, b): a * b for a in range(10) for b in range(10)}

    @pytest.mark.parametrize("fail_func", fail_funcs)
    @duet.sync
    async def test_failure_in_spawned_task(self, fail_func):
        after_fail = False
        with pytest.raises(Fail):
            async with duet.new_scope() as scope:
                for a in range(10):
                    scope.spawn(mul, a, a)
                scope.spawn(fail_func)
                after_fail = True  # This should still run.
        assert after_fail

    @duet.sync
    async def test_sync_failure_in_main_task(self):
        # pylint: disable=unreachable
        after_await = False
        with pytest.raises(Fail):
            async with duet.new_scope() as scope:
                scope.spawn(mul, 2, 3)
                raise Fail()
                after_await = True  # This should not run.
        assert not after_await
        # pyline: enable=unreachable

    @duet.sync
    async def test_async_failure_in_main_task(self):
        after_await = False
        with pytest.raises(Fail):
            async with duet.new_scope() as scope:
                scope.spawn(mul, 2, 3)
                await duet.failed_future(Fail())
                after_await = True  # This should not run.
        assert not after_await

    def test_interrupt_not_included_in_stack_trace(self):
        async def func():
            async with duet.new_scope() as scope:
                f = duet.AwaitableFuture()
                scope.spawn(lambda: f)
                f.set_exception(ValueError("oops!"))
                await duet.AwaitableFuture()

        with pytest.raises(ValueError, match="oops!") as exc_info:
            duet.run(func)

        stack_trace = "".join(
            traceback.format_exception(exc_info.type, exc_info.value, exc_info.tb)
        )
        assert "Interrupt" not in stack_trace
        assert isinstance(exc_info.value.__context__, impl.Interrupt)
        assert exc_info.value.__suppress_context__


@pytest.mark.skipif(
    sys.version_info >= (3, 8), reason="inapplicable for python 3.8+ (can be removed)"
)
@duet.sync
async def test_multiple_calls_to_future_set_result():
    """This checks a scenario that caused deadlocks in earlier versions."""

    async def set_results(*fs):
        for f in fs:
            await duet.completed_future(None)
            f.set_result(None)

    async with duet.new_scope() as scope:
        f0 = duet.AwaitableFuture()
        f1 = duet.AwaitableFuture()

        scope.spawn(set_results, f0)
        await f0

        # Calling f0.set_result again should not mark this main task as ready.
        # If it does, then the duet scheduler will try to advance the task and
        # will block on getting the result of f1. This prevents the background
        # `set_results` task from advancing and actually calling f1.set_result,
        # so we would deadlock.

        scope.spawn(set_results, f0, f1)
        await f1
