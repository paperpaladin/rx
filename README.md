# rx

A regex engine that cannot be made to backtrack catastrophically, because it
never backtracks at all.

```
$ make test
  ok: rx answers (a+)+b over 40 a's (exit 1, no match), where python-re does not
  ok: python-re does not return on the same input within 10s
  ok: rx answers over a 1,000,000-character line in 34ms (exit 1, no match)
  ok: rx refuses a{4294967294} within 2s (exit 3, expansion over the bound)
  ok: rx refuses (((a{1000}){1000}){1000}) within 2s (exit 3, expansion over the bound)
  ok: rx refuses (a{4294967294}){4294967294} within 2s (exit 3, expansion over the bound)
overread: planted one-past-the-end read reported by ASan (exit 86)
2996 agree, 0 disagree, 0 skipped  (seed=0)
  3069 generated, 2996 reached the oracle, 1479 of those matched
  newline in subject: 1871 generated, 1836 compared; trailing newline: 997 generated, 975 compared
  counted repetition: 1914 generated + 40 corners, of which nested 296 + 7
  refusals: 10 refused by both engines, 0 refused by re and not by rx; 10/10 lazy and possessive cases declined
  over rx's program bound: 58 generated + 5 corners (re answers these, rx exits 3; not a divergence)

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

One restriction is left, and it is enforced by the generator: patterns only
use constructs rx implements. No captures, backreferences or lookaround.
Measuring against features never claimed would be measuring the wrong thing.

Python's refusals are part of the comparison and not an absence of one. `re`
rejects `a{2,1}`, and it rejects any repetition count at or above 4294967295;
rx has to reject both as well, and a refusal checked against nothing is just an
opinion. The three outcomes are counted apart from each other, because they are
different failures: rx compiling a pattern to the wrong program, rx declining a
pattern `re` accepts, and rx accepting one `re` rejects.

Inside that bound: 3000 random cases plus 69 named corners per seed, zero
disagreements, and zero at seeds 1 through 5 as well. The case count is a
floor now, not a printout: `differential.py` returned `1 if failures else 0`,
with `skipped` outside the decision and `agree` unbounded, so a generator
change that began emitting patterns Python's `re` rejects would have printed
`0 agree, 0 disagree, 3000 skipped` and exited 0. A green gate that compared
nothing. There are floors on the match rate too, in both directions, because
a corpus where almost nothing matches agrees perfectly while comparing one
bit over and over.

## `{m,n}`, and where the bound goes

`{m,n}` is compiled by repeating the body, which is the only way to get it
without a counter in the VM, and a counter in the VM is what would put the
linear-time bound at risk. Expansion moves the cost to compile time, where it
has to be bounded, because the program is the PRODUCT of every enclosing count:

```
$ ./rx '(((a{1000}){1000}){1000})' aaa
rx: pattern too large: expansion exceeds 10000 instructions
$ echo $?
3
```

Twenty-four characters of input asking for a billion instructions, which is a
denial of service that needs no malice. The usual mistake is to bound the
repetition count, and it does not work: each count there is 1000, so a limit of
10000 per count waves all three of them through. Bounding the pattern length
does not work either, for the same reason.

So the bound is on the number of instructions emitted, checked in `emit()`,
one comparison per instruction. Every path that can grow the program goes
through there, so no arrangement of nesting can get around it. 10000
instructions is about 480 KB of `Inst`, and it also bounds `addthread`'s
recursion depth, since a chain of 10000 epsilon transitions is 10000 nested
calls.

Over the bound rx refuses, and exits 3 rather than 2. The difference carries
information the harness needs: exit 2 says "that is not a pattern", which `re`
must agree with, and exit 3 says "that is a pattern, `re` will answer it, and
this engine will not". Folding the two together turns 26 deliberate size
refusals in one run into 26 reports of rx rejecting valid patterns.

What rx does NOT do is truncate. A shortened program matches a different
language and would answer confidently and wrongly, which is worse than
refusing. That failure is invisible over short subjects, so the corpus carries
two cases with 12000-character ones: `a{15000}` and `(a{200}){200}` both fail
to match 12000 a's, while their first 10000 instructions match them, which is
the only way a truncating engine shows up as a disagreement rather than as
agreement over a wrong answer.

The bound holding is not the same as the compiler returning. `a{4294967294}` is
a valid pattern to `re` and four billion turns of the expansion loop, every one
of them correctly refusing to emit anything. With the `prog_over` test removed
from the loop condition, rx still gives the right answer to that pattern and
takes 5.9 seconds to do it, against 1ms with the test in place.
`t/pathological.sh` asserts all three bombs come back inside two seconds.

Twenty-one mutants, twenty killed. Three needed new cases before they died.
Bounding the count rather than the product is caught by `(a{200}){200}`.
Compiling `{m,}` as `{m}` and dropping the optional copies of `{m,n}` both
survived 1500 generated cases, because under an unanchored boolean search
`a{2,}`, `a{2,4}` and `a{2}` accept exactly the same subjects: all three mean
"two consecutive a's somewhere". They need `^` and `$` at once, which the
generator produces in about 7% of patterns but rarely around a counted piece,
so they are corner cases. Removing the `prog_over` test from the expansion loop
survived the differential too, since the answers stay right, and is caught by
the two-second assertion in `t/pathological.sh`.

The survivor is equivalent rather than missed. Deleting the two lines that
refuse a second quantifier still refuses every one of the ten cases: the
leftover `*`, `+` or `?` lands at atom position on the next pass and hits
"nothing to repeat" there. The error message differs and the verdict does not.
Restoring the loop that was actually there before, which folded the second
quantifier into a node around the first, is killed by seven cases.

### What `re` does with a brace, which is not obvious

A malformed brace is not an error to `re`, it is literal text: `a{x}` matches
the string "a{x}", and so do `a{}`, `a{2`, `a{ 2}` and `a{1,2,3}`. But `{,}` IS
a quantifier, equal to `{0,}` and so to `*`, which is why `re` rejects it on its
own with "nothing to repeat".

rx had `{,}` as a literal first, and the test that let it through was true and
proved nothing: `re.search("a{,}", "a{,}")` is true, which looks like the
literal reading and is in fact `a*` matching the empty string at offset 0.
`re.fullmatch` separates them. The corner cases for literal braces now use
subjects the two readings disagree about, so `a{}` is compared against the
empty string rather than against "a{}".

Lazy and possessive quantifiers are refused rather than approximated. For a
boolean match laziness makes no difference, but `a*+` is a possessive star in
Python 3.11 and later, and possessiveness changes the language:
`re.search("a*+a", "aa")` is None where `re.search("a*a", "aa")` matches. rx
used to fold any second quantifier into a node wrapping the first, so it
answered that case with a match. Ten of those patterns are named in the suite,
which asserts that `re` accepts each one and rx declines it.

## A buffer larger than its data

`prog_cap` doubles, so a 10000-instruction program sat in a 16384-instruction
allocation, and a read one past the end of the program landed 6384 instructions
inside a live object. ASan has nothing to say about that. Measured rather than
reasoned about: with the planted read compiled in and the array left at its
doubled size, the program prints the garbage it read and exits 0 with no
diagnostic at all.

`rx_compile` reallocates the program down to its length now, and `make
overread` compiles the planted read and fails unless the sanitizer reports it.
`make test` runs it. The gate was watched both ways.

Its exit code took two goes. 86 was picked because rx itself only ever exits 0,
1, 2 or 3, so that status could only come from ASan. It can also come from
LeakSanitizer, which fires on every run because rx never frees its parse tree,
and the first version of this gate "failed" at exit 86 having seen no over-read
whatsoever. The option is `detect_leaks=0`, and the lesson is that the exit code
has to be one no other FAILURE can produce, not one no success can produce.

There is a second buffer with the same shape and it is not fixed. `getline`
grows its line buffer and never shrinks it, so after a 64-character line a
2-character line sits in at least 65 bytes, and a planted read one past the
data there produces no diagnostic either. Measured, in the same way. It is left
alone because the only reader walks to the NUL `getline` writes, and giving
every line its own exact allocation costs a malloc per line in the one path
that is about throughput. It is queued in PLAN.md rather than left as a
sentence here.

## Newline, which used to be excluded

Subjects used to contain no newline, because newline is where `$` diverged.
It is now in the alphabet, and the engine implements the rule instead:

| construct | what rx does | the matching Python mode |
|---|---|---|
| `.` | does not match newline | no `re.DOTALL` |
| `^` | start of string only | no `re.MULTILINE` |
| `$` | end of string, or before a newline that is the LAST character | no `re.MULTILINE` |
| `[^a]`, `\D`, `\W`, `\S` | DO match newline | same |

The oracle is called with no flags, deliberately and in one place. Comparing a
no-MULTILINE engine against a MULTILINE oracle produces a pile of differences
that are all the harness's fault, and they look exactly like engine bugs.

A widened alphabet that nobody counts is not widened, so the run counts. Of
3020 compared cases at seed 0, 1825 hold a newline and 986 END in one, which
is the only position the `$` rule is visible at. The run FAILS below 30% and
10%, so narrowing the alphabet back turns the suite red instead of quietly
reporting the same agreement over a smaller corpus.

That floor is load-bearing. Six defects planted in rx that only a newline can
see -- `$` back to end-of-string only, `$` before any newline, `.` matching
newline, `^` gone MULTILINE, `[^a]` dropping newline, `\D \W \S` dropping
newline -- are every one of them caught by the widened corpus and every one of
them SURVIVES 3000 cases of the old alphabet. A seventh, dropping `_` from
`\w`, is caught by both, which is what says the old-alphabet run was still
working rather than broken.

## The oracle hangs, and the harness did not notice

`re.search` was called bare. Generated case 1686 of seed 0 is a nested
quantifier, `([ a-cb-d]?_*|[c-e]1)*(...)+`, against a ten-character subject,
and Python does not return from it in 100 seconds. The suite would not have
gone red; it would have wedged, silently, forever. Both sides have a timeout
now: 2s for `re`, 10s for rx. A case `re` cannot answer is counted and named
separately, because there is no verdict to compare against, and it is not a
divergence. rx answers that same case in 1ms, which is the entire point of
the project.

A timeout on rx's side is a failure, not a skip. A linear-time engine that
does not return in ten seconds has lost the one property it exists for.

## Status

Supported: literals, `.`, `*`, `+`, `?`, `{m,n}` and its `{m}`, `{m,}`, `{,n}`
and `{,}` forms, `|`, grouping, character classes with ranges and negation,
`^`, `$`, and the escapes `\d \D \w \W \s \S \n \t \r \f \v` plus
`\<metachar>` for literals.

Exit status is 0 for a match, 1 for no match, 2 for a pattern that is not a
pattern, and 3 for a valid pattern whose expansion exceeds 10000 instructions.

Not supported, newline division: `re.MULTILINE` and `re.DOTALL`. There are no
flags at all, so there is no way to ask for them, and the differential harness
is correct only because it asks `re` for neither. `\A`, `\Z` and `\b` are
absent as well, so there is no way to spell "end of string, and not before a
trailing newline" either.

Not supported, and not planned soon. Capture groups and submatch extraction (a
boolean match needs neither), lazy and possessive quantifiers, backreferences,
lookahead and lookbehind, Unicode. Backreferences are a permanent no: they are
incompatible with the linear-time guarantee, so adding them would mean giving
up the only interesting property this engine has.

Patterns `re` accepts and rx refuses, which is a different kind of gap from the
list above and is asserted case by case rather than described: the ten lazy and
possessive forms, and anything whose expansion exceeds the program bound.

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

`I_ANYNL` in that prefix is not `I_ANY`. The implicit search has to be able to
skip over a newline even when the user's `.` must not, and when newlines were
excluded from the corpus nothing would have noticed if the two were merged.
Something does now.

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
ok: rx refuses a{4294967294} within 2s (exit 3, expansion over the bound)
```

The middle line is there because the first is vacuous without it: a case
both engines finish proves nothing about backtracking. The fourth is the
compile side of the same claim, which only exists since `{m,n}` landed.

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
