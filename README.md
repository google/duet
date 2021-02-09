# duet
A simple future-based async library for python

Duet takes inspiration from the amazing [trio](https://trio.readthedocs.io/en/stable/)
library and the [structured concurrency](https://vorpus.org/blog/notes-on-structured-concurrency-or-go-statement-considered-harmful/#:~:text=Notes%20on%20structured%20concurrency%2C%20or%3A%20Go%20statement%20considered%20harmful,-Wed%2025%20April&text=There%20are%20lots%20of%20variations,parent%20can%20do%20other%20things.)
approach to async programming that it uses.
However, duet differs from trio in two major ways:

- Instead of a full-blown implementation of asynchronous IO, duet relies
  on `Future`s for parallelism, and provides a way to run async/await
  coroutines around those `Future`s. This is useful if you are using
  an API that returns futures, such as RPC libraries like gRPC. The
  standard `Future` interface does not implement `__await__` directly,
  so `Future`s must be wrapped in `duet.AwaitableFuture`.

- duet is re-entrant. At the top level, you run async code by calling
  `duet.run(foo)`. Inside `foo` suppose you call a function that has
  not yet been fully refactored to be asynchronous, but itself calls
  `duet.run(bar)`. Most async libraries, including `trio` and `asyncio`,
  will raise an exception if you try to "re-enter" the event loop in
  this way, but duet allows it. We have found that this can simplify
  the process of refactoring code to be asynchronous because  you
  don't have to completely separate the sync and async parts of your
  codebase all at once.
  
duet is not an official Google project.