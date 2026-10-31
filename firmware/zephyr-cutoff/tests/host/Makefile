# Host tests of the portable firmware core (no Zephyr SDK needed).
#   make            core + parser unit tests with ASan/UBSan, the late-copy test and its negative control
#                   (release 084), then a syntax/type
#                   check of the Zephyr glue against minimal stub headers (main.c
#                   also with CONFIG_ARCH_POSIX: the native_sim options of release 074)
#                   (zephyr_stub/: declarations only; NOT a Zephyr build)
CFLAGS ?= -std=c11 -Wall -Wextra -Werror -O1 -g -fsanitize=address,undefined
SRC := ../../src
test: test_evc_core test_evc_proto test_evc_late_copy test_evc_late_copy_neg glue
	./test_evc_core
	./test_evc_proto
	./test_evc_late_copy
	./test_evc_late_copy_neg
test_evc_core: test_evc_core.c $(SRC)/evc_core.c $(SRC)/evc_core.h
	$(CC) $(CFLAGS) -o $@ test_evc_core.c $(SRC)/evc_core.c
test_evc_proto: test_evc_proto.c $(SRC)/evc_proto.c $(SRC)/evc_core.c $(SRC)/evc_proto.h
	$(CC) $(CFLAGS) -o $@ test_evc_proto.c $(SRC)/evc_proto.c $(SRC)/evc_core.c
# release 084: late copies (after ring eviction and after a reboot) through the wire path; the _neg build is
# the negative control without the eviction rule (CONFIG_EVC_GUARD_EVICTED=n), which must execute them again;
# both builds also show that a positive or zero dedup horizon re-executes a copy older than the horizon
test_evc_late_copy: test_evc_late_copy.c $(SRC)/evc_core.c $(SRC)/evc_proto.c $(SRC)/evc_core.h $(SRC)/evc_proto.h
	$(CC) $(CFLAGS) -o $@ test_evc_late_copy.c $(SRC)/evc_core.c $(SRC)/evc_proto.c
test_evc_late_copy_neg: test_evc_late_copy.c $(SRC)/evc_core.c $(SRC)/evc_proto.c $(SRC)/evc_core.h $(SRC)/evc_proto.h
	$(CC) $(CFLAGS) -DEVC_LATE_COPY_NO_GUARD -o $@ test_evc_late_copy.c $(SRC)/evc_core.c $(SRC)/evc_proto.c
glue:
	$(CC) -std=gnu11 -Wall -Wextra -Werror -fsyntax-only -Izephyr_stub -I$(SRC) $(SRC)/main.c
	$(CC) -std=gnu11 -Wall -Wextra -Werror -fsyntax-only -Izephyr_stub -I$(SRC) -DCONFIG_ARCH_POSIX $(SRC)/main.c
	$(CC) -std=gnu11 -Wall -Wextra -Werror -fsyntax-only -Izephyr_stub -I$(SRC) $(SRC)/footprint_main.c
	@echo "zephyr glue: syntax/type check against stub headers OK (not a Zephyr build)"
clean:
	rm -f test_evc_core test_evc_proto test_evc_late_copy test_evc_late_copy_neg
.PHONY: test clean glue
