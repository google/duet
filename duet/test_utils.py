import functools

import duet


def duet_live(func):
    """Decorator for running an async function with duet."""

    @functools.wraps(func)
    def wrapped(*args, **kwds):
        return duet.run(func, *args, **kwds)

    return wrapped
