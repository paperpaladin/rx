# rx

A regex engine that cannot be made to backtrack catastrophically, because it
never backtracks at all.

```
$ make test
3000 agree, 0 disagree, 0 skipped  (seed=0)

$ make bench
pattern: (a+)+b   subject: N a's, no b (never matches)

   N            rx     python-re
  10           1ms          20ms
  16           1ms          26ms
  20           1ms          98ms
  24           1ms        1240ms
  26           1ms       >5000ms
  28           1ms       >5000ms

Linearity check: 1MB subject, pattern that cannot match.
  rx on 1,000,000 a's: 4ms
```

## How

Parse to a small tree, compile the tree to a program, run the program as a Pike
VM: every thread advances together over the input, one character at a time, and
a program counter may appear in the thread list **at most once per input
position**. That caps total work at `O(len(text) * len(program))`.

That one invariant: a pc enters a list once per position, is the whole
guarantee. `(a+)+b` is exponential in a backtracking engine because it tries
every way to partition the `a`s among the nested quantifiers. Here those
partitions all collapse into the same set of program counters, so there is
nothing to enumerate.

## The oracle

`t/differential.py` generates random patterns and subjects and compares
`re.search(p, s) is not None` against rx's exit status. Python's `re` is the
authority. Any disagreement is a bug in rx.

Two restrictions keep that comparison honest, not convenient, and both
are enforced by the generator:

- Subjects contain no newline. Python's `$` also matches *before* a trailing
  newline, and its `.` excludes newline. rx's `$` only matches at end of string.
  Rather than emulate the quirk, newlines are excluded and the limitation is
  stated.
- Patterns only use constructs rx implements. No captures, backreferences,
  lookaround, or `{m,n}`. Measuring against features never claimed would be
  measuring the wrong thing.

Inside those bounds: 3000 cases per seed, zero disagreements. The case
count is a floor now, not a printout: `differential.py` returned
`1 if failures else 0`, with `skipped` outside the decision and `agree`
unbounded, so a generator change that began emitting patterns Python's `re`
rejects would have printed `0 agree, 0 disagree, 3000 skipped` and exited 0.
A green gate that compared nothing.

## Status

Supported: literals, `.`, `*`, `+`, `?`, `|`, grouping, character classes with
ranges and negation, `^`, `$`, and the escapes `\d \D \w \W \s \S \n \t \r \f \v`
plus `\<metachar>` for literals.

Not supported, and not planned soon: capture groups and submatch extraction
(a boolean match needs neither), `{m,n}` repetition, lazy quantifiers,
backreferences, lookahead and lookbehind, Unicode. Backreferences are not merely
unimplemented. They are incompatible with the linear-time guarantee, so adding
them would mean giving up the only interesting property this engine has.

Greediness is not modelled at all, which is sound here precisely because the
answer is a boolean. The moment submatch extraction lands, thread priority
ordering has to come with it.

## Writing this found one real bug

The first version injected a fresh start thread into the current thread list at
every input position, to get unanchored search. That collides with the
generation marks the list was already built under, and that dedup *is* the
linear-time guarantee, so the bug quietly broke the one property the project
exists for. The fix moves the unanchored prefix into the compiled program:

```
0: SPLIT 1, user_start
1: ANYNL
2: JMP 0
```

`^` still works, because the `I_BOL` instruction rejects any thread whose
position has moved from the start of the text.

## The linear-time claim is asserted now, not printed

This repo exists because `rx` cannot be made to backtrack catastrophically,
and until this commit that claim was backed by a table. `t/pathological.sh`
printed timings and ended with an `echo`: no comparison, no threshold, no
exit status. If `rx` regressed to exponential, `make bench` would have
printed large numbers and succeeded. It was also wired to `bench` rather
than `test`, so the commit gate never ran it.

What is asserted is completion, not a time. A wall-clock threshold is a
claim about this CPU under this load. "Answers a case a backtracking engine
cannot answer" is the actual property, it is machine-tolerant, and it fails
the moment the thread list stops being linear:

```
ok: rx answers (a+)+b over 40 a's (exit 1), where python-re does not
ok: python-re does not return on the same input within 10s
ok: rx answers over a 1,000,000-character line in 40ms (exit 1)
```

The middle line is there because the first is vacuous without it: a case
both engines finish proves nothing about backtracking.

Two things had to be fixed in the assertions themselves before they worked.

`(a+)+b` never matches a run of `a`s, so `rx` answers with exit 1,
and the first version was written `if timeout 10 ./rx ...`, true only on
exit 0, so it read that correct answer as a failure. The status is compared
to the exact set {0, 1} now, with 124 being `timeout`'s verdict and the only
outcome meaning the engine did not come back.

And the 1MB subject **was never reaching `rx` at all**. Linux caps a single
`argv` entry at `MAX_ARG_STRLEN`, 32 pages or 131072 bytes, independently of
`ARG_MAX`, so `./rx "$PATTERN" "$(a million a's)"` fails to `exec` with
`E2BIG` and exits 126. The old "1MB subject" line timed that failed exec at
about 6ms and reported it as the engine's speed. It goes through a file and
`rx -g` now, and takes 40ms for a line that actually exists.

## License

GPL-3.0-or-later.
