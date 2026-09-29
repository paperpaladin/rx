# rx: plan

## Contract

`rx PATTERN TEXT` exits 0 on match, 1 on no match, 2 on a bad pattern, and 3 on
a valid pattern whose `{m,n}` expansion exceeds the program bound.
`rx -g PATTERN` filters stdin like grep. Pattern syntax is a subset of Python's
`re`, and **the subset is the contract**: within it, rx must agree with `re`
exactly, including on what `re` refuses.

Exit 3 is separate from exit 2 deliberately. Exit 2 is a claim about the
pattern that the oracle has to agree with; exit 3 is a limit of this engine
that the oracle has no opinion about, since `re` has no program bound. Folding
them together reports every size refusal as rx rejecting a valid pattern.

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

   `re`'s refusals are compared as refusals, not skipped. It rejects `a{2,1}`
   and any count at or above 4294967295, and the run fails if rx accepts one
   of those. The opposite check is a named list: ten lazy and possessive
   patterns `re` accepts and rx must decline cleanly.
2. `t/pathological.sh`: the linearity claim, measured against a real
   backtracking engine, not asserted. Since 06 it also asserts the compile
   side, which is that three expansion bombs are refused inside two seconds.
3. `make overread`, which is ASan on a planted one-past-the-end read of the
   program array. Not an oracle in the sense of the other two, since the
   authority is the sanitizer rather than another implementation, but it is
   outside our control in the same way.

## Non-goals

Capture groups and submatch extraction. Lazy and possessive quantifiers, both
refused rather than approximated: laziness is invisible to a boolean match, but
`a*+` is possessive in Python 3.11 and later and possessiveness changes the
language, so there is no safe guess. Unicode.

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
- [x] 06 `{m,n}` repetition by expansion, with a bound on the expanded program
      size so a pattern cannot blow up the compiler instead of the matcher
      files: rx.c t/differential.py t/pathological.sh Makefile
      done: the bound is on instructions EMITTED, checked in `emit()`, because
            the program is the product of every enclosing count and bounding
            the counts does not bound the product:
            `(((a{1000}){1000}){1000})` has no count above 1000 and asks for a
            billion instructions. 10000 instructions, about 480 KB of `Inst`,
            which also bounds `addthread`'s recursion depth. Over it rx exits
            3 and refuses; it does not truncate, because a shortened program
            matches a different language and answers wrongly rather than not
            at all.
      proves: 3000 generated cases plus 69 corners at seeds 0 to 5, zero
            disagreements. 1914 generated patterns hold a counted repetition
            and 296 nest one inside another; 58 generated patterns exceed the
            bound. Each of those three counts is required non-zero, over
            GENERATED cases only, since the corner list would satisfy a
            corpus-wide floor on its own and say nothing about the generator.
      bug: `re` raises OverflowError, not `re.error`, for a count at or above
            4294967295. The harness caught only `re.error`, so the first such
            case killed the run with a traceback instead of counting a refusal.
      bug: `{,}` is a quantifier, equal to `{0,}`. rx had it as a literal, and
            the check that passed was vacuous: `re.search("a{,}", "a{,}")` is
            true because `a*` matches the empty string, not because the brace
            is literal. The literal-brace corners use subjects the two
            readings disagree about now.
      bug: the old `parse_repeat` loop folded a second quantifier into a node
            wrapping the first, so `a*+` compiled as `(a*)+`. `a*+` is a
            possessive star to `re` and `re.search("a*+a", "aa")` is None,
            where rx answered match. One quantifier per atom now, and the ten
            lazy and possessive forms are refused and asserted to be.
      bug: the program array doubles, so a 10000-instruction program sat in a
            16384-instruction allocation and a planted read one past the end
            produced no sanitizer diagnostic and exit 0. `rx_compile` shrinks
            it; `make overread` plants the read and fails unless ASan reports
            it. Its exit code 86 was first satisfied by LeakSanitizer rather
            than by the over-read, which is why `detect_leaks=0` is there.
      mutants: 21 planted (16 in rx.c, 5 in the harness), 20 killed. The one
            that matters is bounding the
            count rather than the product, which `(a{200}){200}` catches. Two
            survived a first run and needed corner cases anchored at both
            ends, because under an unanchored boolean search `a{2,}`, `a{2,4}`
            and `a{2}` accept exactly the same subjects. One is equivalent:
            deleting the second-quantifier refusals still refuses, because
            the leftover `*` or `?` lands at atom position and hits "nothing
            to repeat" there.
- [ ] 07 case-insensitive matching, folded at compile time into the class bitmaps
      why: zero runtime cost if done at compile time, and the fuzzer can
           compare against `re.IGNORECASE` for free
- [ ] 08 run the fuzzer under ASan and UBSan; the parser eats arbitrary bytes
- [ ] 09 shrinking in the fuzzer: on a disagreement, minimise the pattern and
      subject before printing them
      why: the first real disagreement will otherwise be a 40-char pattern
           and 06 raised the typical length, since a counted repetition can
           carry a five-digit number
- [ ] 09a give `rx -g` an exactly sized line buffer behind a build flag, and
      plant a read past the data in it
      why: `getline` grows its buffer and never shrinks it, so a 2-character
           line after a 64-character one sits in at least 65 bytes. A planted
           read one past the data there was measured to produce no diagnostic,
           which is the second instance of the shape `make overread` exists
           for. Nothing reads past the NUL today, so this is prophylactic, and
           it is here rather than done because an exact allocation per line
           costs a malloc per line in the one path that is about throughput.

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
