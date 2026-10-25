#pragma once
#include <stddef.h>
#include <sys/types.h>
typedef ssize_t (*settings_read_cb)(void *cb_arg, void *data, size_t len);
#define SETTINGS_STATIC_HANDLER_DEFINE(_hname, _tree, _get, _set, _commit, _export) \
  static int (*_hname##_set_ptr)(const char *, size_t, settings_read_cb, void *) __attribute__((unused)) = _set;
int settings_save_one(const char *name, const void *value, size_t val_len);
int settings_subsys_init(void);
int settings_load(void);
