#pragma once
/* the log macros take a printf format, as Zephyr's (whose arguments are format-checked too) */
void evc_stub_log(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
#define LOG_MODULE_REGISTER(a,b)
#define LOG_LEVEL_INF 3
#define LOG_INF(...) evc_stub_log(__VA_ARGS__)
#define LOG_WRN(...) evc_stub_log(__VA_ARGS__)
#define LOG_ERR(...) evc_stub_log(__VA_ARGS__)
