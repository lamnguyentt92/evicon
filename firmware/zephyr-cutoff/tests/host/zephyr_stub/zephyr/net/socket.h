#pragma once
#include <stdint.h>
#include <sys/types.h>
#include <netinet/in.h>
#include <sys/socket.h>
struct zsock_pollfd { int fd; short events; short revents; };
#define ZSOCK_POLLIN 1
int zsock_inet_pton(int af, const char *s, void *dst);
int zsock_socket(int f, int t, int p);
int zsock_connect(int s, const struct sockaddr *a, socklen_t l);
int zsock_close(int s);
ssize_t zsock_send(int s, const void *b, size_t n, int f);
ssize_t zsock_recv(int s, void *b, size_t n, int f);
int zsock_poll(struct zsock_pollfd *f, int n, int t);
