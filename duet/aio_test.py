import pytest

import duet


@pytest.mark.asyncio
async def test_run():
    async def task(i):
        return await duet.completed_future(2 * i)

    result = await duet.aio.run(duet.pmap_async, task, range(4))
    assert result == [0, 2, 4, 6]


@pytest.mark.asyncio
async def test_run_failure():
    async def task():
        await duet.completed_future(None)
        raise ValueError('oops')

    with pytest.raises(ValueError):
        await duet.aio.run(task)
