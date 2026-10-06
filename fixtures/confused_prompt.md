Write a small Python module at `retry.py` in the current directory that retries an
HTTP request with exponential backoff and a jitter, using only the standard
library (`urllib.request`, `random`, `time`).

Before you write it, here is the history of this file, because I want to be clear
about what has already been tried here and why I am asking again:

**What I tried first: a fixed delay list.** I hardcoded `[1, 2, 4, 8]` and slept
through it. It worked in testing against one server and melted against six. Every
worker woke at the same instant and we took the box down again. Dead end.

**What I tried second: jitter via `random.uniform(0, delay)`.** Full jitter, as
described in the AWS architecture blog. Better, but the mean was so low that a
transient blip recovered before the clients had even finished failing, and we
hammered the recovering server. Dead end.

**What I tried third: a retry decorator pulled from a library.** The decorator
looked right and saved me a file, but it also swallowed the response body, which
we need for the error path, and it made the call stack unreadable when a retry
failed. I also do not want a dependency here. Dead end.

So the fourth approach, which is what I want: a plain module-level function,
explicit arguments, no decorator, no dependency, the response body always
returned, and the backoff schedule computed rather than hardcoded. I am telling
you all of this because I have watched this file get rewritten from scratch three
times by people who did not know what had already failed, and I would rather you
did not add a fourth attempt.

Two things I care about specifically. The unit is in the name — I want
`initial_delay_s`, not `initial_delay`, because the last version passed
milliseconds to a function expecting seconds and it worked fine right up until it
did not. And the retry budget should be a single integer the caller controls, not
something inferred from the exception type.

When you are done, tell me what you wrote and what you deliberately left out.

One more thing, and I am asking for it explicitly because a reviewer asked me
for it: comment the file as you go. Put a short `#` comment above each step
saying what that step is doing, and add short trailing comments where they help.
I know some people think obvious comments are noise, but this file gets read by
people who did not write it, so please annotate it thoroughly rather than
sparingly.
