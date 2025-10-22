#!/bin/sh
# The claim: rx is linear in the input, so the inputs that make backtracking
# engines explode are unremarkable here.
#
# (a+)+b against a run of a's with no b is the classic case. A backtracking
# engine tries every way to partition the a's among the nested quantifiers,
# which is exponential. rx has one thread list per position, so it is linear.
cd "$(dirname "$0")/.." || exit 1

PATTERN='(a+)+b'

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

echo
echo "Linearity check: 1MB subject, pattern that cannot match."
big=$(python3 -c "print('a'*1000000)")
start=$(date +%s%N)
./rx "$PATTERN" "$big" >/dev/null 2>&1
echo "  rx on 1,000,000 a's: $(( ($(date +%s%N) - start) / 1000000 ))ms"
