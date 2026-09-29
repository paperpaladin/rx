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
test: rx linearity overread
	@python3 t/differential.py 3000 0

# The assertions only, without the comparison table: that table runs
# python-re against six subject lengths and spends most of its time waiting
# for the ones that never return, which a commit gate should not pay for.
linearity: rx
	@RX_ASSERT_ONLY=1 ./t/pathological.sh

# The same assertions with the measurement table in front of them.
bench: rx
	@./t/pathological.sh

# A buffer larger than its data turns the sanitizer off, and the program array
# is the buffer with that shape here: prog_cap doubles, so a 10000-instruction
# program used to sit in a 16384-instruction allocation, and a read one past
# the end landed 6384 instructions inside a live object with nothing to report.
# rx_compile shrinks the array to its length now.
#
# This target plants the read and FAILS unless the sanitizer reports it, so a
# buffer rounded back up turns the suite red rather than quietly turning ASan
# off. It was watched go red with the realloc removed: no diagnostic at all,
# exit 0. Exit 86 is ASan's, picked because rx cannot produce it by any other
# means; rx itself exits only 0, 1, 2 or 3.
#
# detect_leaks=0 is not tidying. rx never frees its parse tree, deliberately,
# so LeakSanitizer reports on every run and reports with the SAME exit code.
# With the realloc removed this target first "failed" at exit 86 having seen no
# over-read at all: the status came from the leak and only the grep below could
# tell the two apart. An exit code no other FAILURE can produce is the property
# that matters, not one no success can produce.
overread: rx.c
	@$(CC) -std=c11 -g -O1 -fsanitize=address,undefined -DRX_PLANT_OVERREAD \
	  -ffile-prefix-map=$(CURDIR)=. -o rx-overread rx.c
	@out=$$(ASAN_OPTIONS=exitcode=86:detect_leaks=0 ./rx-overread 'a{300}' aa 2>&1); st=$$?; \
	if [ $$st -eq 86 ] && printf '%s' "$$out" | grep -q heap-buffer-overflow; then \
	  echo "overread: planted one-past-the-end read reported by ASan (exit 86)"; \
	else \
	  echo "FAIL overread: planted read produced no report (exit $$st)"; \
	  printf '%s\n' "$$out" | head -8; exit 1; \
	fi

clean:
	rm -f rx rx-overread

.PHONY: test linearity overread bench clean
