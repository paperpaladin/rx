# rx: plan

## Contract

`rx PATTERN TEXT` exits 0 on match, 1 on no match, 2 on a bad pattern.
`rx -g PATTERN` filters stdin like grep. Pattern syntax is a subset of Python's
`re`, and **the subset is the contract**: within it, rx must agree with `re`
exactly.

## Hard invariant

A program counter enters a thread list at most once per input position.

That is the entire linear-time guarantee. It bounds total work at
`O(len(text) * len(program))` and makes catastrophic backtracking structurally
impossible, not merely unlikely. Every change has to preserve it; the
first bug in this project was a change that quietly did not.

## Oracle

1. `t/differential.py`, Python's `re` on randomly generated patterns and
   subjects, with no flags: rx implements neither MULTILINE nor DOTALL, so
   the oracle must be consulted in the same mode or every difference is the
   harness's. `make test` runs 20 named newline corners plus 3000 random
   cases at seed 0, zero disagreements.

   The 15,500-across-6-seeds figure that used to be here was hand-run in one
   sitting and is reproducible from nothing in the repo. There is no seed
   loop and no multi-seed target. README.md always said 3000 per seed; this
   file was the one overstating it.
2. `t/pathological.sh`: the linearity claim, measured against a real
   backtracking engine, not asserted.

## Non-goals

Capture groups and submatch extraction. `{m,n}`. Lazy quantifiers. Unicode.

Backreferences are a permanent non-goal, not a deferral. They are
incompatible with the linear-time guarantee. Adding them would mean giving up the
only interesting property this engine has, so the answer is no.

## Commit sequence

- [x] 00 tree parser, program compiler, Pike VM; literals through alternation
      files: rx.c Makefile
      proves: 13 smoke cases on exit status
- [x] 01 character classes with ranges, negation, and `\d \w \s` families
- [x] 02 differential fuzzer against Python `re`
      files: t/differential.py
      proves: 3000 cases, 0 disagreements
- [x] 03 fix: move the unanchored prefix into the program
      why: injecting a start thread per position collided with the generation
           marks, breaking the dedup that *is* the linear-time guarantee
      proves: still 0 disagreements, and `^` cases still correct
- [x] 04 pathological benchmark vs python-re
      proves: 1ms flat where python goes 20ms -> 1240ms -> >5s; 1MB in 4ms

### Milestone 1: make the oracle harder to satisfy

- [x] 05 widen the generator's alphabet to include newline, and implement
      Python's `$`-before-trailing-newline rule so the restriction can be dropped
      why: the current restriction is honest but it is still a restriction
      done: one line of engine change (`$` matches at end of string or before a
            newline that is the last character, which is Python without
            MULTILINE) and a generator that puts a newline in 60% of subjects
            and a TRAILING newline in 33%. 20 named corner cases cover the
            constructs whose meaning newline changes; `re` judges those too.
            3020 cases, 0 disagreements, and 0 at seeds 1-5 as well.
            Counted, because a widening nobody counts is not a widening: the
            run prints newline-bearing and trailing-newline counts and fails
            below 30% / 10% of the compared total. Six mutations that only a
            newline can see (`$` reverted, `$` before any newline, `.` matching
            newline, `^` gone MULTILINE, `[^a]` dropping newline, `\D \W \S`
            dropping newline) are all caught by the widened corpus and all
            SURVIVE the old alphabet over 3000 cases.
      bug: the oracle itself hangs. `re.search` was called bare, and generated
           case 1686 of seed 0 is a nested-quantifier pattern Python does not
           answer in 100s against a 10-character subject. The suite would have
           wedged with no output, which is worse than red. Both sides have a
           timeout now and unanswerable cases are counted separately. rx
           answers that case in 1ms.
- [ ] 06 `{m,n}` repetition by expansion, with a bound on the expanded program
      size so a pattern cannot blow up the compiler instead of the matcher
- [ ] 07 case-insensitive matching, folded at compile time into the class bitmaps
      why: zero runtime cost if done at compile time, and the fuzzer can
           compare against `re.IGNORECASE` for free
- [ ] 08 run the fuzzer under ASan and UBSan; the parser eats arbitrary bytes
- [ ] 09 shrinking in the fuzzer: on a disagreement, minimise the pattern and
      subject before printing them
      why: the first real disagreement will otherwise be a 40-char pattern

### Milestone 2: submatches, carefully

- [ ] 10 leftmost-longest match position reporting (start and end offsets), still
      without captures
- [ ] 11 thread priority ordering, the prerequisite for greediness
      having any meaning
- [ ] 12 capture groups, with the fuzzer comparing `.group(n)` not just
      match/no-match
      note: this is where the project stops being easy. Greediness and submatch
            semantics are where most engines diverge from each other, and the
            differential harness will start finding real disagreements here.

### Milestone 3

- [ ] 13 lazy DFA cache on top of the NFA for the common case, keeping the NFA as
      the fallback so the worst-case bound survives
- [ ] 14 benchmark against RE2 and PCRE2 on a real corpus, reporting where we lose
- [ ] 15 a `rx`-backed `grep` and a comparison of throughput on a large file

## Open questions

- Whether to report leftmost-longest or leftmost-first once offsets land. Python
  is leftmost-first (as are PCRE and most backtrackers); POSIX and RE2's default
  are leftmost-longest. Differential testing against `re` forces leftmost-first,
  which is the less natural fit for an NFA simulation. Decide before 10.
- The lazy DFA in 13 is the only change that could threaten the invariant, since
  a cache with a bounded size has to be able to evict and fall back mid-match.
  That needs the fuzzer green and the benchmark in place first.
