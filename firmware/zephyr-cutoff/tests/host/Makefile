# Host tests of the portable firmware core (no Zephyr SDK needed).
#   make            core + parser unit tests with ASan/UBSan, then a syntax/type
#                   check of the Zephyr glue against minimal stub headers (main.c
#                   also with CONFIG_ARCH_POSIX: the native_sim options of release 074)
#                   (zephyr_stub/: declarations only; NOT a Zephyr build)
CFLAGS ?= -std=c11 -Wall -Wextra -Werror -O1 -g -fsanitize=address,undefined
SRC := ../../src
test: test_evc_core test_evc_proto glue
	./test_evc_core
	./test_evc_proto
test_evc_core: test_evc_core.c $(SRC)/evc_core.c $(SRC)/evc_core.h
	$(CC) $(CFLAGS) -o $@ test_evc_core.c $(SRC)/evc_core.c
test_evc_proto: test_evc_proto.c $(SRC)/evc_proto.c $(SRC)/evc_core.c $(SRC)/evc_proto.h
	$(CC) $(CFLAGS) -o $@ test_evc_proto.c $(SRC)/evc_proto.c $(SRC)/evc_core.c
glue:
	$(CC) -std=gnu11 -Wall -Wextra -Werror -fsyntax-only -Izephyr_stub -I$(SRC) $(SRC)/main.c
	$(CC) -std=gnu11 -Wall -Wextra -Werror -fsyntax-only -Izephyr_stub -I$(SRC) -DCONFIG_ARCH_POSIX $(SRC)/main.c
	$(CC) -std=gnu11 -Wall -Wextra -Werror -fsyntax-only -Izephyr_stub -I$(SRC) $(SRC)/footprint_main.c
	@echo "zephyr glue: syntax/type check against stub headers OK (not a Zephyr build)"
clean:
	rm -f test_evc_core test_evc_proto
.PHONY: test clean glue
