# Host tests of the boot report (release 087; firmware/zephyr-boot). No Zephyr SDK needed.
#   make            the boot report against the frozen decision core and reply formats (ASan/UBSan), a syntax/type check
#                   of the frozen main.c with the three call redirections of ../../CMakeLists.txt against the stub
#                   headers of the cutoff firmware's host tests (NOT a Zephyr build), and the host library of the wire
#                   path with the floor that conformance/tests/test_reboot_recovery087.py loads (no sanitizers)
CFLAGS ?= -std=c11 -Wall -Wextra -Werror -O1 -g -fsanitize=address,undefined
CUT := ../../../zephyr-cutoff/src
STUB := ../../../zephyr-cutoff/tests/host/zephyr_stub
SRC := ../../src
REDIRECT := -Devc_core_init=evc_boot_core_init -Dmqtt_publish=evc_boot_mqtt_publish -Dmqtt_subscribe=evc_boot_mqtt_subscribe
test: test_evc_boot_report libevcbootshim.so glue
	./test_evc_boot_report
test_evc_boot_report: test_evc_boot_report.c $(SRC)/evc_boot_report.c $(SRC)/evc_boot_report.h $(CUT)/evc_core.c $(CUT)/evc_proto.c
	$(CC) $(CFLAGS) -I$(STUB) -I$(CUT) -I$(SRC) -o $@ test_evc_boot_report.c $(SRC)/evc_boot_report.c $(CUT)/evc_core.c $(CUT)/evc_proto.c
libevcbootshim.so: evc_boot_shim.c $(SRC)/evc_boot_report.c $(CUT)/evc_core.c $(CUT)/evc_proto.c
	$(CC) -std=c11 -Wall -Wextra -Werror -O1 -shared -fPIC -DEVC_BOOT_HOST_CORE_ONLY -I$(SRC) -I$(CUT) -o $@ evc_boot_shim.c $(SRC)/evc_boot_report.c $(CUT)/evc_core.c $(CUT)/evc_proto.c
glue:
	$(CC) -std=gnu11 -Wall -Wextra -Werror -fsyntax-only -I$(STUB) -I$(CUT) $(REDIRECT) $(CUT)/main.c
	$(CC) -std=gnu11 -Wall -Wextra -Werror -fsyntax-only -I$(STUB) -I$(CUT) $(REDIRECT) -DCONFIG_ARCH_POSIX $(CUT)/main.c
	$(CC) -std=gnu11 -Wall -Wextra -Werror -fsyntax-only -I$(STUB) -I$(CUT) -I$(SRC) $(SRC)/evc_boot_report.c
	@echo "boot report glue: main.c with the redirections, evc_boot_report.c: syntax/type check against stub headers OK (not a Zephyr build)"
clean:
	rm -f test_evc_boot_report libevcbootshim.so
.PHONY: test clean glue
