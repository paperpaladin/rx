CFLAGS = -std=c11 -g -O2 -Wall -Wextra -ffile-prefix-map=$(CURDIR)=.

rx: rx.c
	$(CC) $(CFLAGS) -o $@ $<

# The oracle: Python's re must agree on every generated case, and the
# linear-time claim is asserted rather than printed.
#
# linearity is part of `test` now. It was only ever reachable through
# `bench`, so the commit gate never ran the one property this repo exists
# for -- and until this commit `t/pathological.sh` ended with an `echo` and
# asserted nothing at all, so `bench` printed large numbers and exited 0
# whatever the engine did.
test: rx linearity
	@python3 t/differential.py 3000 0

# The assertions only, without the comparison table: that table runs
# python-re against six subject lengths and spends most of its time waiting
# for the ones that never return, which a commit gate should not pay for.
linearity: rx
	@RX_ASSERT_ONLY=1 ./t/pathological.sh

# The same assertions with the measurement table in front of them.
bench: rx
	@./t/pathological.sh

clean:
	rm -f rx

.PHONY: test linearity bench clean
