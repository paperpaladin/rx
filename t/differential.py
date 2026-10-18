#!/usr/bin/env python3
r"""Differential fuzzer: rx vs Python's re, on the subset where they must agree.

Python's re is the oracle. We generate a random pattern from a grammar limited
to constructs rx supports, a random subject string, and compare
`re.search(p, s) is not None` against rx's exit status.

One deliberate restriction is left: patterns use no construct rx does not
implement (no captures, no backreferences, no lookaround, no {m,n}). Comparing
against features we never claimed would be measuring the wrong thing.

Newlines used to be the second restriction and are now the point. The subject
alphabet contains one, and a quarter of subjects get a trailing one on purpose,
because newline is the character that changes what several constructs MEAN:

  * `.` does not match it. rx implements that, and so compares against `re`
    WITHOUT re.DOTALL.
  * `^` matches at start of string only, `$` at end of string or just before a
    newline that is the last character. rx implements that, and so compares
    against `re` WITHOUT re.MULTILINE. Comparing a no-MULTILINE engine against
    a MULTILINE oracle would produce a pile of differences that are all the
    harness's fault, so the flags are named here and nowhere overridden.
  * `[^a]` DOES match it, and so do `\D`, `\W`, `\S`. Complements are not
    special-cased.

Any disagreement inside those bounds is a real bug in rx.

The counters exist because widening an alphabet is only real if the new
character reaches the comparison. The run prints how many compared cases hold
a newline and how many hold a TRAILING one (the only ones the `$` rule can
see), and floors on both turn a quiet renarrowing of the generator red.
"""
import random
import re
import signal
import subprocess
import sys

ALPHABET = "abc01 _\n"        # small, so matches are frequent; \n is the point
RX = "./rx"


def rand_class(rnd):
    inner = ""
    for _ in range(rnd.randint(1, 3)):
        kind = rnd.random()
        if kind < 0.25:
            lo = rnd.choice("abc01")
            hi = chr(ord(lo) + rnd.randint(0, 2))
            inner += f"{lo}-{hi}"
        elif kind < 0.4:
            inner += rnd.choice([r"\d", r"\w", r"\s", r"\n"])
        else:
            inner += rnd.choice("abc01_ ")
    neg = "^" if rnd.random() < 0.3 else ""
    return f"[{neg}{inner}]"


def rand_atom(rnd, depth):
    r = rnd.random()
    if depth > 0 and r < 0.18:
        return "(" + rand_alt(rnd, depth - 1) + ")"
    if r < 0.30:
        return rand_class(rnd)
    if r < 0.38:
        return "."
    if r < 0.46:
        return rnd.choice([r"\d", r"\w", r"\s", r"\D", r"\W", r"\S"])
    if r < 0.50:
        # Escaped metacharacter: must be treated as a literal.
        return "\\" + rnd.choice(".*+?()[]|\\^$")
    if r < 0.56:
        # A pattern that can match a newline: the escape, and the raw byte,
        # which is an ordinary literal to both engines.
        return rnd.choice(["\\n", "\n"])
    return rnd.choice(ALPHABET)


def rand_piece(rnd, depth):
    a = rand_atom(rnd, depth)
    r = rnd.random()
    if r < 0.18:
        return a + "*"
    if r < 0.30:
        return a + "+"
    if r < 0.40:
        return a + "?"
    return a


def rand_cat(rnd, depth):
    return "".join(rand_piece(rnd, depth) for _ in range(rnd.randint(1, 4)))


def rand_alt(rnd, depth):
    parts = [rand_cat(rnd, depth) for _ in range(rnd.randint(1, 3))]
    return "|".join(parts)


def rand_pattern(rnd):
    p = rand_alt(rnd, 2)
    if rnd.random() < 0.20:
        p = "^" + p
    # $ is raised from 0.15 because it is the construct whose meaning the
    # widened alphabet changes; at 0.15 the trailing-newline rule was reached
    # by about one case in forty.
    if rnd.random() < 0.35:
        p = p + "$"
    return p


def rand_subject(rnd):
    n = rnd.randint(0, 12)
    s = "".join(rnd.choice(ALPHABET) for _ in range(n))
    # A trailing newline is the only position the no-MULTILINE `$` rule can be
    # observed at, and a uniform draw puts one there too rarely to measure.
    if rnd.random() < 0.25:
        s += "\n"
    return s


def rx_match(pattern, subject):
    """True/False, None if rx rejected the pattern, "hung" if it did not return."""
    try:
        p = subprocess.run([RX, pattern, subject], capture_output=True, timeout=10)
    except subprocess.TimeoutExpired:
        return "hung"       # a linear-time engine has no excuse for this
    if p.returncode == 0:
        return True
    if p.returncode == 1:
        return False
    return None


class _Hung(Exception):
    pass


def _alarm(signum, frame):
    raise _Hung


signal.signal(signal.SIGALRM, _alarm)


def re_match(pattern, subject):
    """True/False, None if re rejected the pattern, "hung" if it did not return.

    The oracle needs a timeout for the same reason rx does not. The generator
    nests quantifiers freely, so it produces patterns like
    `([ a-cb-d]?_*|[c-e]1)*(...)` whose every partition of the subject is a
    distinct backtrack in `re`. One of those sat at generated case 1686 of
    seed 0 and did not return in 100 seconds against a ten-character subject.
    Before this, `re.search` was called bare and the suite would have hung
    forever with no output: not a red run, a wedged one. rx answers the same
    case in 1ms, which is the entire point of the project, so these are
    counted and named rather than treated as an error.
    """
    signal.setitimer(signal.ITIMER_REAL, 2.0)
    try:
        return re.search(pattern, subject) is not None
    except re.error:
        return None
    except _Hung:
        return "hung"
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


# The newline corners, named rather than left to chance. Each is a pair whose
# answer differs depending on which newline semantics the engine implements, so
# a run that reached none of them has not tested the widening. `re` still judges
# every one: nothing below asserts an expected answer.
CORNERS = [
    ("a$", "a\n"),        # $ before a trailing newline: matches
    ("a$", "a\nb"),       # ... but not before any other newline (no MULTILINE)
    ("a$", "a\n\n"),      # ... and only before the LAST one
    ("$", "\n"),
    ("^a", "b\na"),       # ^ is start of string only (no MULTILINE)
    ("^$", "\n"),
    ("a.b", "a\nb"),      # . excludes newline (no DOTALL)
    ("a.*b", "a\nb"),
    ("[^a]", "\n"),       # a complemented class DOES match newline
    ("[^\n]", "\n"),
    (r"\D", "\n"),        # and so do \D \W \S
    (r"\W", "\n"),
    (r"\S", "\n"),
    (r"\s", "\n"),
    (r"\n", "\n"),
    ("\n", "\n"),
    ("a\nb", "a\nb"),
    ("a*$", "aa\n"),
    ("(a|b)$", "b\n"),
    ("[ab]+$", "ab\n"),
]


def main():
    iters = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    rnd = random.Random(seed)

    agree = disagree = skipped = 0
    nl_subj = nl_trail = nl_cmp = nl_trail_cmp = matched = 0
    oracle_hung = 0
    failures = []

    cases = list(CORNERS)
    cases += [(rand_pattern(rnd), rand_subject(rnd)) for _ in range(iters)]

    for pattern, subject in cases:
        if "\n" in subject:
            nl_subj += 1
        if subject.endswith("\n"):
            nl_trail += 1

        # No flags, deliberately: rx implements neither MULTILINE nor
        # DOTALL, so the oracle must be consulted in the same mode.
        want = re_match(pattern, subject)
        if want is None:
            skipped += 1          # not valid Python regex either; not our problem
            continue
        if want == "hung":
            oracle_hung += 1
            continue

        got = rx_match(pattern, subject)
        if got is None:
            skipped += 1
            failures.append(("rejected", pattern, subject, want, None))
            continue
        if got == "hung":
            disagree += 1
            failures.append(("rx did not return", pattern, subject, want, got))
            continue

        if "\n" in subject:
            nl_cmp += 1
        if subject.endswith("\n"):
            nl_trail_cmp += 1
        if want:
            matched += 1

        if got == want:
            agree += 1
        else:
            disagree += 1
            failures.append(("mismatch", pattern, subject, want, got))

    total = len(cases)
    compared = agree + disagree
    print(f"{agree} agree, {disagree} disagree, {skipped} skipped  (seed={seed})")
    print(f"  {total} generated, {compared} reached the oracle, "
          f"{matched} of those matched")
    print(f"  newline in subject: {nl_subj} generated, {nl_cmp} compared; "
          f"trailing newline: {nl_trail} generated, {nl_trail_cmp} compared")
    if oracle_hung:
        print(f"  {oracle_hung} cases dropped: python-re did not return within "
              f"2s (rx did). Not a divergence; there is no verdict to compare.")
    for kind, pattern, subject, want, got in failures[:25]:
        print(f"  {kind}: pattern={pattern!r} subject={subject!r} re={want} rx={got}")
    if len(failures) > 25:
        print(f"  ... and {len(failures) - 25} more")

    # A floor on the denominator. `skipped` never entered this decision and
    # `agree` had none, so a generator change that started emitting patterns
    # Python's re rejects would print "0 agree, 0 disagree, 3000 skipped" and
    # exit 0 -- a green gate that compared nothing. The oracle is only
    # consulted on cases that survive the skip, and nothing bounded how few
    # that could be.
    bad = []
    if agree < total * 0.9:
        bad.append(f"only {agree} of {total} cases reached the oracle "
                   f"({skipped} skipped): the generator is producing patterns "
                   f"Python rejects, so this run compared almost nothing")

    # Floors on the widening itself. A generator that emits a newline once in a
    # thousand has not been widened, and nothing else here would notice: the
    # agreement count reads the same either way. Measured rates at seed 0 are
    # about 61% and 29%, so these are not tight.
    if nl_cmp < compared * 0.3:
        bad.append(f"only {nl_cmp} of {compared} compared cases contain a "
                   f"newline: the alphabet has been narrowed back")
    if nl_trail_cmp < compared * 0.1:
        bad.append(f"only {nl_trail_cmp} of {compared} compared cases end in a "
                   f"newline, which is the only position the $ rule is visible "
                   f"at")

    # And a floor on both answers. A corpus where almost nothing matches agrees
    # perfectly while comparing one bit over and over.
    if not (compared * 0.2 < matched < compared * 0.8):
        bad.append(f"{matched} of {compared} compared cases matched: the corpus "
                   f"is lopsided enough that agreement means little")

    for b in bad:
        print("FAIL " + b)
    if bad:
        return 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
