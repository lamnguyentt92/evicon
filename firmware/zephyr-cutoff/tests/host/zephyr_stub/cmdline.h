/* Declaration stub of native_sim's command-line API (Zephyr: boards/native/common, nsi_cmdline.h),
 * for the host syntax/type check of src/main.c with CONFIG_ARCH_POSIX. Not Zephyr. */
#pragma once
#include <stdbool.h>
#include <stddef.h>
struct args_struct_t {
	bool is_mandatory;
	bool is_switch;
	char *option;
	char *name;
	char type;
	void *dest;
	void (*call_when_found)(char *argv, int offset);
	char *descript;
};
#define ARG_TABLE_ENDMARKER {false, false, NULL, NULL, 0, NULL, NULL, NULL}
void native_add_command_line_opts(struct args_struct_t *args);
