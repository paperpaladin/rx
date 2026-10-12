#!/bin/sh
# The claim: rx is linear in the input, so the inputs that make backtracking
# engines explode are unremarkable here.
#
# (a+)+b against a run of a's with no b is the classic case. A backtracking
# engine tries every way to partition the a's among the nested quantifiers,
# which is exponential. rx has one thread list per position, so it is linear.
cd "$(dirname "$0")/.." || exit 1

PATTERN='(a+)+b'

# RX_ASSERT_ONLY skips the comparison table and runs only the assertions at
# the bottom, so `make test` can gate on the linearity claim without paying
# for six python-re runs that are expected not to return.
if [ -z "${RX_ASSERT_ONLY:-}" ]; then
printf 'pattern: %s   subject: N a'"'"'s, no b (never matches)\n\n' "$PATTERN"
printf '%4s  %12s  %12s\n' N rx python-re

for n in 10 16 20 24 26 28; do
  subject=$(printf 'a%.0s' $(seq 1 $n))

  start=$(date +%s%N)
  ./rx "$PATTERN" "$subject" >/dev/null 2>&1
  rx_ms=$(( ($(date +%s%N) - start) / 1000000 ))

  # Cap Python at 5s; past n=26 it is not coming back.
  start=$(date +%s%N)
  if timeout 5 python3 -c "
import re,sys
re.search(r'(a+)+b', 'a'*$n)
" >/dev/null 2>&1; then
    py_ms=$(( ($(date +%s%N) - start) / 1000000 ))
    py="${py_ms}ms"
  else
    py=">5000ms"
  fi

  printf '%4s  %10sms  %12s\n' "$n" "$rx_ms" "$py"
done
fi

echo
echo "Linearity check: 1MB subject, pattern that cannot match."
#
# Through a FILE and `rx -g`, not through argv. Linux caps a single argv
# entry at MAX_ARG_STRLEN -- 32 pages, 131072 bytes -- independently of
# ARG_MAX, so `./rx "$PATTERN" "$(1MB of a's)"` fails to exec with E2BIG
# and exits 126. This script used to do exactly that and time it: the
# "1MB subject" line was measuring the cost of a failed exec, about 6ms,
# and reporting it as the engine's speed. The subject never reached rx.
big=$(mktemp "${TMPDIR:-/tmp}/rx-big.XXXXXX")
trap 'rm -f "$big"' EXIT INT TERM
python3 -c "print('a'*1000000)" > "$big"
start=$(date +%s%N)
./rx -g "$PATTERN" < "$big" >/dev/null 2>&1
echo "  rx on 1,000,000 a's: $(( ($(date +%s%N) - start) / 1000000 ))ms"

# ---------------------------------------------------------------- assertions
#
# Everything above is measurement. Until now that was ALL of it: this script
# printed a table and ended with an echo, so if rx regressed to exponential
# `make bench` would print large numbers and exit 0. The claim the whole
# repo exists for -- "cannot be made to backtrack catastrophically" -- was
# backed by a printout, and the script was wired to `bench` rather than
# `test`, so the commit gate never ran it either.
#
# What is asserted is COMPLETION UNDER A TIMEOUT, not a time. A wall-clock
# threshold is a claim about this CPU under this load, and prof's suite in
# this directory already demonstrates how that goes. "Finishes a case a
# backtracking engine cannot finish" is the actual property, it is machine-
# tolerant, and it is what fails the moment the thread-list goes exponential.
fail=0

# n = 40 is far past where Python's re stops returning: each extra 'a'
# doubles its work, and it was already over 5s at n=28 in the table above.
# The status is compared to an exact set, not tested with `if cmd`.
# (a+)+b never matches a run of a's, so rx ANSWERS with exit 1 -- and the
# first version of this assertion was written `if timeout 10 ./rx ...` and
# read that correct answer as a failure. `if cmd` is true only on 0, which
# is the same defect being fixed in four other repos this session, written
# fresh here within the hour of fixing them.
#
# 0 is "matched", 1 is "did not match", and both are answers. 124 is
# timeout's verdict and is the only outcome that means the engine did not
# come back.
subject=$(printf 'a%.0s' $(seq 1 40))
timeout 10 ./rx "$PATTERN" "$subject" >/dev/null 2>&1
rc=$?
if [ "$rc" -eq 0 ] || [ "$rc" -eq 1 ]; then
  echo "  ok: rx answers (a+)+b over 40 a's (exit $rc), where python-re does not"
else
  echo "  FAIL: rx did not answer (a+)+b over 40 a's within 10s (exit $rc) --"
  echo "        the thread list is not linear any more"
  fail=1
fi

# And python-re really must fail it, or the comparison above is vacuous:
# a case both engines finish proves nothing about backtracking.
if timeout 10 python3 -c "
import re
re.search(r'(a+)+b', 'a'*40)
" >/dev/null 2>&1; then
  echo "  FAIL: python-re finished 40 a's, so this is no longer a"
  echo "        pathological case and the assertion above is vacuous"
  fail=1
else
  echo "  ok: python-re does not return on the same input within 10s"
fi

# The 1MB case has to finish too. One second per megabyte is 30x slower
# than measured and still impossible for anything super-linear.
start=$(date +%s%N)
timeout 30 ./rx -g "$PATTERN" < "$big" >/dev/null 2>&1
rc=$?
ms=$(( ($(date +%s%N) - start) / 1000000 ))
if [ "$rc" -eq 0 ] || [ "$rc" -eq 1 ]; then
  echo "  ok: rx answers over a 1,000,000-character line in ${ms}ms (exit $rc)"
else
  echo "  FAIL: rx did not answer over 1,000,000 a's within 30s (exit $rc)"
  fail=1
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "linearity: asserted, not just measured"
else
  echo "linearity: FAILED"
fi
exit "$fail"
