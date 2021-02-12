import asyncio
import functools

import pytest

import duet
import duet.interop.asyncio as aio_compat


def asyncio_test(func):
    """Decorator to make it easier to test asyncio code.

    Add this decorator to an async function that uses asyncio. The code will get
    executed using the asyncio event loop and the test framework will block on
    the result.
    """

    @functools.wraps(func)
    def wrapped(*args, **kw):
        return asyncio.get_event_loop().run_until_complete(func(*args, **kw))

    return wrapped


@asyncio_test
async def test_run():
    async def task(i):
        return await duet.completed_future(2 * i)

    result = await aio_compat.run(duet.pmap_async, task, range(4))
    assert result == [0, 2, 4, 6]


@asyncio_test
async def test_run_failure():
    async def task():
        await duet.completed_future(None)
        raise ValueError('oops')

    with pytest.raises(ValueError):
        await aio_compat.run(task)
