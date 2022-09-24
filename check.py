from typing import Any, Callable, Optional, overload, TypeVar
from typing_extensions import ParamSpec

import duet

P = ParamSpec("P")
T = TypeVar("T")


@overload
async def foo_async(c: complex, x: int, y: Optional[int] = None) -> int:
    ...


@overload
async def foo_async(c: complex, x: str, y: Optional[str] = None) -> str:
    ...


@overload
async def foo_async(c: complex, x: list[T], y: list[T] | None = None) -> list[T]:
    ...


async def foo_async(c: complex, x: Any, y: Any = None) -> Any:
    return x * 2


foo = duet.sync(foo_async)


class Bar:
    async def baz_async(self, x: int, *, y: str) -> dict[int, str]:
        return {}

    baz = duet.sync(baz_async)


b = Bar()

reveal_type(duet.sync)

reveal_type(foo_async)
reveal_type(foo)

reveal_type(Bar.baz_async)
reveal_type(Bar.baz)

reveal_type(b.baz_async)
reveal_type(b.baz)


def transform(func: Callable[P, list[T]]) -> Callable[P, T]:
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        return func(*args, **kwargs)[0]

    return wrapped

@overload
def foo2(x: int) -> list[float]:
    pass

@overload
def foo2(x: str) -> list[str]:
    pass

def foo2(x: int | str) -> list[float] | list[str]:
    if isinstance(x, int):
        return [1 / x]
    return [x[::-1]]

bar2 = transform(foo2)

reveal_type(foo2)
reveal_type(bar2)
