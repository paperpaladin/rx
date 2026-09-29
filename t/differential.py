#!/usr/bin/env python3
r"""Differential fuzzer: rx vs Python's re, on the subset where they must agree.

Python's re is the oracle. We generate a random pattern from a grammar limited
to constructs rx supports, a random subject string, and compare
`re.search(p, s) is not None` against rx's exit status.

One deliberate restriction is left: patterns use no construct rx does not
implement (no captures, no backreferences, no lookaround). Comparing against
features we never claimed would be measuring the wrong thing.

`{m,n}` used to be in that list and is now generated, with three buckets that
are not agreement and are counted apart from it:

  * `re` REFUSES the pattern. `{2,1}` has its bounds the wrong way round and a
    count at or above 4294967295 is too large for `re`, which raises on both.
    rx must refuse those too. A refusal nobody checks against an outside
    authority is just an opinion, and rx rejecting a pattern `re` accepts is a
    separate failure from rx compiling it to the wrong program.
  * rx refuses because expanding the pattern would exceed its program bound
    (exit 3, distinct from exit 2 for a bad pattern). `re` has no such bound
    and answers these, so there is nothing to compare: counted, not agreed.
  * rx refuses a lazy or possessive quantifier, which `re` accepts. Those are
    named in UNIMPL and the run asserts rx declines each one cleanly, which is
    the opposite check: `re` must ACCEPT and rx must REFUSE.

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

The same applies to `{m,n}`, more sharply, because the counted corner cases
below would satisfy any corpus-wide count on their own and prove nothing about
the generator. So the three counts that gate the run are over GENERATED cases
only: how many contain a counted repetition, how many nest one inside another
(the shape where the expansion is a product rather than a sum, and the only
one that can blow up the compiler), and how many exceed rx's program bound. A
bound test over a corpus where nothing reaches the bound passes vacuously.
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


# The generator carries two flags alongside the text it builds: whether the
# fragment contains a counted repetition, and whether one is nested inside
# another. They are threaded through rather than recovered afterwards by
# re-parsing the finished pattern, because deciding whether the `{3}` in
# `(a{3}b){4}` is inside the `{4}`'s body means matching brackets, and a second
# parser written to check the first one is a second thing to get wrong.
def rand_atom(rnd, depth):
    """Returns (text, has_counted, has_nested)."""
    r = rnd.random()
    if depth > 0 and r < 0.18:
        inner, c, n = rand_alt(rnd, depth - 1)
        return "(" + inner + ")", c, n
    if r < 0.30:
        return rand_class(rnd), False, False
    if r < 0.38:
        return ".", False, False
    if r < 0.46:
        return rnd.choice([r"\d", r"\w", r"\s", r"\D", r"\W", r"\S"]), False, False
    if r < 0.50:
        # Escaped metacharacter: must be treated as a literal.
        return "\\" + rnd.choice(".*+?()[]|\\^$"), False, False
    if r < 0.56:
        # A pattern that can match a newline: the escape, and the raw byte,
        # which is an ordinary literal to both engines.
        return rnd.choice(["\\n", "\n"]), False, False
    if r < 0.60:
        # A brace that is NOT a quantifier. `re` treats a malformed one as
        # literal characters, so `a{x}` matches the text "a{x}", and an engine
        # that rejects it instead is wrong in a way no amount of {m,n} testing
        # would show.
        return rnd.choice(["{", "}", "{}", "{x}", "{2", "{1,2,3}", "{ 2}"]), False, False
    return rnd.choice(ALPHABET), False, False


def rand_count(rnd):
    """A `{m,n}` quantifier.

    Counts are mostly small, because small ones are what match a 13-character
    subject and agreement is measured on cases that match. One in sixteen is
    large enough that TWO of them nested cross rx's 10000-instruction bound
    while one on its own stays well under it, which is what makes the bound
    reachable by the generator rather than only by a hand-written case.
    """
    r = rnd.random()
    if r < 0.012:
        # Large enough to cross the bound on its own, so the bound is reached
        # by the generator even when the nesting draw does not come up.
        m = rnd.randint(4000, 20000)
    elif r < 0.07:
        m = rnd.randint(150, 400)
    else:
        m = rnd.randint(0, 4)
    r = rnd.random()
    if r < 0.42:
        return "{%d}" % m
    if r < 0.66:
        return "{%d,%d}" % (m, m + rnd.randint(0, 3))
    if r < 0.80:
        return "{%d,}" % m
    if r < 0.96:
        return "{,%d}" % m
    # `{,}` is `{0,}`, which is `*`. It is in here because it is the brace form
    # most easily mistaken for a literal.
    return "{,}"


def rand_piece(rnd, depth):
    """Returns (text, has_counted, has_nested). One quantifier per atom, which
    is `re`'s rule: `a*+` is a possessive star to `re`, not a star under a
    plus, and rx refuses it rather than guessing."""
    a, c, n = rand_atom(rnd, depth)
    r = rnd.random()
    if r < 0.16:
        return a + "*", c, n
    if r < 0.27:
        return a + "+", c, n
    if r < 0.36:
        return a + "?", c, n
    if r < 0.48:
        # A counted repetition OF something that may already hold one: that is
        # where the expansion becomes a product, and the whole reason the bound
        # is on the emitted program rather than on any single count.
        return a + rand_count(rnd), True, n or c
    return a, c, n


def rand_cat(rnd, depth):
    parts = [rand_piece(rnd, depth) for _ in range(rnd.randint(1, 4))]
    return ("".join(t for t, _, _ in parts),
            any(c for _, c, _ in parts),
            any(n for _, _, n in parts))


def rand_alt(rnd, depth):
    parts = [rand_cat(rnd, depth) for _ in range(rnd.randint(1, 3))]
    return ("|".join(t for t, _, _ in parts),
            any(c for _, c, _ in parts),
            any(n for _, _, n in parts))


def rand_pattern(rnd):
    """Returns (pattern, has_counted, has_nested)."""
    p, c, n = rand_alt(rnd, 2)
    if rnd.random() < 0.20:
        p = "^" + p
    # $ is raised from 0.15 because it is the construct whose meaning the
    # widened alphabet changes; at 0.15 the trailing-newline rule was reached
    # by about one case in forty.
    if rnd.random() < 0.35:
        p = p + "$"
    return p, c, n


def rand_subject(rnd):
    n = rnd.randint(0, 12)
    s = "".join(rnd.choice(ALPHABET) for _ in range(n))
    # A trailing newline is the only position the no-MULTILINE `$` rule can be
    # observed at, and a uniform draw puts one there too rarely to measure.
    if rnd.random() < 0.25:
        s += "\n"
    return s


def rx_match(pattern, subject):
    """True, False, None (rx refused: exit 2), "bound" (exit 3), or "hung".

    "bound" is its own answer and not a kind of refusal. rx expands `{m,n}` and
    stops at 10000 instructions, so there are valid patterns `re` answers and
    rx declines; exit 3 says so distinctly from exit 2, which claims the
    pattern is not a pattern. Collapsing the two would make the bound invisible
    to the one check that can tell whether it is reached at all.
    """
    try:
        p = subprocess.run([RX, pattern, subject], capture_output=True, timeout=10)
    except subprocess.TimeoutExpired:
        return "hung"       # a linear-time engine has no excuse for this
    if p.returncode == 0:
        return True
    if p.returncode == 1:
        return False
    if p.returncode == 3:
        return "bound"
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
    except OverflowError:
        # Not an re.error, and the reason this except clause exists: a
        # repetition count at or above 4294967295 raises OverflowError straight
        # out of the sre parser. With `except re.error` alone the first such
        # case killed the whole run with a traceback instead of being counted
        # as the refusal it is.
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

# The counted-repetition corners. Flags are (pattern, subject, counted, nested)
# and are written out rather than inferred, because half the point of the list
# is the patterns where a brace is NOT a repetition.
#
# `re` judges every one of these too: nothing here asserts an expected answer,
# including the refusals, which are compared as refusals.
BRACE_CORNERS = [
    ("a{2}", "aa", True, False),
    ("a{2}", "a", True, False),
    ("a{0}", "", True, False),
    ("a{2,3}", "aaa", True, False),
    ("a{2,3}", "a", True, False),
    ("a{2,}", "aaaa", True, False),
    ("a{,2}", "", True, False),
    ("a{,}", "aaa", True, False),              # `{,}` is `{0,}`, which is `*`
    ("{,}", "x", True, False),                 # so on its own it has nothing
                                               # to repeat, and `re` says so
    ("a{,}{,}", "a", True, False),             # and twice over is a multiple
                                               # repeat, not two literals
    ("(ab){3}", "ababab", True, False),
    ("(a|b){2}", "ab", True, False),
    ("[ab]{2}$", "ab\n", True, False),
    ("a{2}b{2}", "aabb", True, False),         # two counts, side by side: a sum
    ("(a{2}){2}", "aaaa", True, True),         # one inside another: a product
    ("(a{2}b){2}", "aabaab", True, True),
    ("((a{2}){2}){2}", "aaaaaaaa", True, True),
    # Anchored at BOTH ends, which is the only shape where `{m,}` and `{m,n}`
    # differ from `{m}` under a boolean search. Unanchored they do not:
    # `a{2,}` and `a{2}` both mean "two consecutive a's somewhere in the
    # subject" and accept exactly the same strings, so a mutant that compiles
    # `{m,}` as `{m}`, or drops the optional copies of `{m,n}`, agrees on every
    # unanchored case. Both survived 1500 generated cases for that reason, and
    # the generator is a poor instrument for this: it anchors at both ends in
    # about 7% of patterns, but the exact length only matters when the counted
    # piece is most of the pattern.
    ("^a{2,}$", "aaa", True, False),
    ("^a{2,}$", "a", True, False),
    ("^a{2,4}$", "aaa", True, False),
    ("^a{2,4}$", "aaaaa", True, False),      # over the upper bound
    ("^a{2,4}$", "a", True, False),
    ("^(ab){2,}$", "ababab", True, False),
    ("^a{2,}b$", "aaab", True, False),
    ("^(a{2}){2,}$", "aaaaaa", True, True),
    ("^a{,2}$", "aa", True, False),
    ("^a{,2}$", "aaa", True, False),
    # A brace that is not a quantifier. `re` reads these as literal characters.
    #
    # The subjects are chosen so that the two readings give DIFFERENT answers.
    # `("a{}", "a{}")` cannot see the bug it is for: read as a literal it
    # matches the subject, and read as `{0,0}` it matches the empty string at
    # offset 0, so it is true either way. Against "" only the wrong reading
    # matches.
    ("a{}", "", False, False),
    ("a{x}", "", False, False),
    ("a{2", "aa", False, False),
    ("a{ 2}", "aa", False, False),
    ("a{1,2,3}", "aa", False, False),
    ("a{-1}", "a", False, False),
    ("a{x}", "a{x}", False, False),            # and the positive, for the
    ("a{}", "a{}", False, False),              # engine that refuses them
    ("[a{2}]+", "{2}", False, False),          # inside a class it is never one
    # Refusals. `re` raises on each, so rx must too.
    ("a{2,1}", "a", True, False),              # bounds the wrong way round
    ("a{4294967295}", "a", True, False),       # one above what `re` accepts
    ("a{99999999999999999999}", "a", True, False),
    ("a{1,4294967296}", "a", True, False),
    ("{2}", "x", True, False),                 # nothing to repeat
    ("a{2}{3}", "a", True, False),             # multiple repeat
    ("a{1000}{1000}{1000}", "a", True, False), # the fourteen-character bomb,
                                               # which `re` rejects outright
    ("a*{2}", "a", True, False),
    # Over rx's program bound: `re` answers these and rx declines (exit 3).
    # The long subjects are deliberate. A truncating engine would answer these
    # WRONGLY rather than refusing, and over a 13-character subject truncation
    # is invisible: both a 15000-a pattern and its first 10000 instructions
    # fail to match "aaa", so the two behaviours agree. Over 12000 a's they do
    # not, which is what makes a truncation mutant detectable.
    ("a{15000}", "a" * 12000, True, False),
    ("(a{200}){200}", "a" * 12000, True, True),
    ("a{4294967294}", "a", True, False),       # accepted by `re`, far too big
    ("(a{1000}){1000}", "a", True, True),
    ("((a{30}){30}){30}", "a", True, True),
]

# The other direction, and the one easy to forget: `re` ACCEPTS each of these
# and rx must refuse cleanly rather than compile something close enough.
#
# `a*?` is a lazy star and `a*+` a possessive one (Python 3.11 and later). For
# a boolean match laziness is invisible, but possessiveness is not:
# `re.search("a*+a", "aa")` is None where `re.search("a*a", "aa")` matches. rx
# used to fold any second quantifier into a node wrapping the first, so it
# answered that case with a match. Refusing both is the only honest option
# while neither is implemented.
UNIMPL = [
    "a*?", "a+?", "a??", "a{2,3}?", "a{2}?",
    "a*+", "a++", "a?+", "a{2,3}+", "a{2}+",
]


def main():
    iters = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    rnd = random.Random(seed)

    agree = disagree = skipped = 0
    nl_subj = nl_trail = nl_cmp = nl_trail_cmp = matched = 0
    oracle_hung = 0
    refused_both = refusal_missed = 0
    bound = 0
    # Counted apart from the corners, because the corners would satisfy any
    # corpus-wide floor on their own.
    gen_counted = gen_nested = gen_bound = 0
    corner_counted = corner_nested = corner_bound = 0
    failures = []

    # (pattern, subject, generated?, counted?, nested?)
    cases = [(pat, subj, False, False, False) for pat, subj in CORNERS]
    cases += [(pat, subj, False, c, n) for pat, subj, c, n in BRACE_CORNERS]
    for _ in range(iters):
        pat, c, n = rand_pattern(rnd)
        cases.append((pat, rand_subject(rnd), True, c, n))

    for pattern, subject, generated, counted, nested in cases:
        if counted:
            if generated:
                gen_counted += 1
            else:
                corner_counted += 1
        if nested:
            if generated:
                gen_nested += 1
            else:
                corner_nested += 1
        if "\n" in subject:
            nl_subj += 1
        if subject.endswith("\n"):
            nl_trail += 1

        # No flags, deliberately: rx implements neither MULTILINE nor
        # DOTALL, so the oracle must be consulted in the same mode.
        want = re_match(pattern, subject)
        if want == "hung":
            oracle_hung += 1
            continue

        got = rx_match(pattern, subject)

        # `re` refuses. That is a verdict, not a gap, and rx has to reach the
        # same one: inverted bounds and a count at or above 4294967295 are
        # refusals in both engines or a bug in rx. Exit 3 does not count, since
        # declining on size is not the same claim as "that is not a pattern".
        if want is None:
            if got is None:
                refused_both += 1
            else:
                refusal_missed += 1
                failures.append(("re refuses, rx does not", pattern, subject,
                                 "refused", got))
            continue

        if got == "bound":
            # A valid pattern rx declines to expand. `re` answered it; there is
            # no rx verdict, so there is nothing to agree or disagree with.
            bound += 1
            if generated:
                gen_bound += 1
            else:
                corner_bound += 1
            continue
        if got is None:
            skipped += 1
            failures.append(("rx rejects a pattern re accepts", pattern, subject,
                             want, None))
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

    # The opposite assertion: `re` accepts, rx must refuse cleanly. A missing
    # feature whose failure mode is a wrong answer rather than an error message
    # is the expensive kind.
    unimpl_ok = 0
    for pattern in UNIMPL:
        if re_match(pattern, "aa") is None:
            failures.append(("UNIMPL case re also rejects", pattern, "aa",
                             "refused", None))
            continue
        got = rx_match(pattern, "aa")
        if got is None:
            unimpl_ok += 1
        else:
            failures.append(("rx should refuse this, it is not implemented",
                             pattern, "aa", "accepted", got))

    total = len(cases)
    compared = agree + disagree
    print(f"{agree} agree, {disagree} disagree, {skipped} skipped  (seed={seed})")
    print(f"  {total} generated, {compared} reached the oracle, "
          f"{matched} of those matched")
    print(f"  newline in subject: {nl_subj} generated, {nl_cmp} compared; "
          f"trailing newline: {nl_trail} generated, {nl_trail_cmp} compared")
    print(f"  counted repetition: {gen_counted} generated + {corner_counted} "
          f"corners, of which nested {gen_nested} + {corner_nested}")
    print(f"  refusals: {refused_both} refused by both engines, "
          f"{refusal_missed} refused by re and not by rx; "
          f"{unimpl_ok}/{len(UNIMPL)} lazy and possessive cases declined")
    print(f"  over rx's program bound: {gen_bound} generated + {corner_bound} "
          f"corners (re answers these, rx exits 3; not a divergence)")
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
    #
    # The denominator is `total` and not "total minus the buckets", because
    # those buckets are exactly what a drifting generator would fill: a
    # generator that emitted nothing but oversized counts would have a perfect
    # record against a denominator that excused it.
    bad = []
    if compared < total * 0.8:
        bad.append(f"only {compared} of {total} cases reached a comparison: "
                   f"{skipped} rejected by rx, {refused_both + refusal_missed} "
                   f"refused by re, {bound} over the program bound, "
                   f"{oracle_hung} unanswered by re")

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

    # The three that say the {m,n} work was exercised by the GENERATOR and not
    # only by the hand-written list below it. Each of these has been watched to
    # go red: removing the brace branch from rand_piece kills the first two,
    # and making rand_count's large draw small kills the third.
    if gen_counted == 0:
        bad.append("no generated pattern contains a counted repetition: the "
                   "{m,n} branch of the generator is not firing, and every "
                   "{m,n} result in this run came from the fixed corner list")
    if gen_nested == 0:
        bad.append("no generated pattern nests a counted repetition inside "
                   "another: the product case, which is the only one that can "
                   "blow the program up, was never generated")
    if gen_bound == 0:
        bad.append("no generated pattern reached rx's program bound: a bound "
                   "that nothing in the corpus crosses is untested, and this "
                   "run says nothing about whether it refuses or truncates")
    if refused_both == 0:
        bad.append("no case was refused by both engines: the refusal "
                   "comparison ran on nothing")
    if unimpl_ok != len(UNIMPL):
        bad.append(f"{unimpl_ok} of {len(UNIMPL)} lazy and possessive patterns "
                   f"were declined by rx; the rest were answered")

    for b in bad:
        print("FAIL " + b)
    if bad:
        return 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
