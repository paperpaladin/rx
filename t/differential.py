#!/usr/bin/env python3
"""Differential fuzzer: rx vs Python's re, on the subset where they must agree.

Python's re is the oracle. We generate a random pattern from a grammar limited
to constructs rx supports, a random subject string, and compare
`re.search(p, s) is not None` against rx's exit status.

Two deliberate restrictions keep the comparison honest rather than convenient:

  * Subjects contain no newline. Python's `$` also matches *before* a trailing
    newline and its `.` excludes newline; rx's `$` only matches at end of string.
    Rather than emulate that quirk, we keep newlines out and say so.
  * Patterns use no construct rx does not implement (no captures, no
    backreferences, no lookaround, no {m,n}). Comparing against features we
    never claimed would be measuring the wrong thing.

Any disagreement inside those bounds is a real bug in rx.
"""
import random
import re
import subprocess
import sys

ALPHABET = "abc01 _"          # small, so matches are frequent
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
            inner += rnd.choice([r"\d", r"\w", r"\s"])
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
    if rnd.random() < 0.15:
        p = "^" + p
    if rnd.random() < 0.15:
        p = p + "$"
    return p


def rand_subject(rnd):
    n = rnd.randint(0, 12)
    return "".join(rnd.choice(ALPHABET) for _ in range(n))


def rx_match(pattern, subject):
    """True/False, or None if rx rejected the pattern."""
    p = subprocess.run([RX, pattern, subject], capture_output=True)
    if p.returncode == 0:
        return True
    if p.returncode == 1:
        return False
    return None


def main():
    iters = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    rnd = random.Random(seed)

    agree = disagree = skipped = 0
    failures = []

    for _ in range(iters):
        pattern = rand_pattern(rnd)
        subject = rand_subject(rnd)

        try:
            want = re.search(pattern, subject) is not None
        except re.error:
            skipped += 1          # not valid Python regex either; not our problem
            continue

        got = rx_match(pattern, subject)
        if got is None:
            skipped += 1
            failures.append(("rejected", pattern, subject, want, None))
            continue

        if got == want:
            agree += 1
        else:
            disagree += 1
            failures.append(("mismatch", pattern, subject, want, got))

    print(f"{agree} agree, {disagree} disagree, {skipped} skipped  (seed={seed})")
    for kind, pattern, subject, want, got in failures[:25]:
        print(f"  {kind}: pattern={pattern!r} subject={subject!r} re={want} rx={got}")
    if len(failures) > 25:
        print(f"  ... and {len(failures) - 25} more")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
