CFLAGS = -std=c11 -g -O2 -Wall -Wextra

rx: rx.c
	$(CC) $(CFLAGS) -o $@ $<

# The oracle: Python's re must agree on every generated case.
test: rx
	@python3 t/differential.py 3000 0

# Prove the linear-time claim against a backtracking engine.
bench: rx
	@./t/pathological.sh

clean:
	rm -f rx

.PHONY: test bench clean
