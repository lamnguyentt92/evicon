/* Host test of evc_auth: FIPS 180-4 SHA-256 and RFC 4231 HMAC-SHA256 vectors.
 *   cc -O2 -fsanitize=address,undefined -I../src test_auth.c ../src/evc_auth.c -o test_auth && ./test_auth */
#include <stdio.h>
#include <string.h>

#include "evc_auth.h"

static int check(const char *what, const uint8_t mac[32], const char *want)
{
	char hex[65];
	evc_hex(mac, hex);
	int ok = strcmp(hex, want) == 0;
	printf("%s %s\n", ok ? "ok  " : "FAIL", what);
	return ok ? 0 : 1;
}

int main(void)
{
	int bad = 0;
	uint8_t out[32];
	struct evc_sha256 s;
	evc_sha256_init(&s);
	evc_sha256_update(&s, "abc", 3);
	evc_sha256_final(&s, out);
	bad += check("sha256(abc)", out, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
	evc_sha256_init(&s);
	const char *m2 = "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq";
	for (size_t i = 0; i < strlen(m2); i++) {
		evc_sha256_update(&s, m2 + i, 1); /* byte by byte: the buffering */
	}
	evc_sha256_final(&s, out);
	bad += check("sha256(448-bit message)", out, "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
	uint8_t k1[20];
	memset(k1, 0x0b, sizeof(k1));
	evc_hmac_sha256(k1, sizeof(k1), "Hi There", 8, out);
	bad += check("RFC 4231 case 1", out, "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7");
	evc_hmac_sha256((const uint8_t *)"Jefe", 4, "what do ya want for nothing?", 28, out);
	bad += check("RFC 4231 case 2", out, "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843");
	uint8_t k3[20], d3[50];
	memset(k3, 0xaa, sizeof(k3));
	memset(d3, 0xdd, sizeof(d3));
	evc_hmac_sha256(k3, sizeof(k3), d3, sizeof(d3), out);
	bad += check("RFC 4231 case 3", out, "773ea91e36800e46854db8ebd09181a72959098b3ef8c122d9635514ced565fe");
	uint8_t k6[131];
	memset(k6, 0xaa, sizeof(k6));
	const char *m6 = "Test Using Larger Than Block-Size Key - Hash Key First";
	evc_hmac_sha256(k6, sizeof(k6), m6, strlen(m6), out);
	bad += check("RFC 4231 case 6 (key longer than a block)", out, "60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54");
	uint8_t kb[32];
	bad += evc_unhex("00112233445566778899aabbccddeeff00112233445566778899AABBCCDDEEFF", kb, 32) == 0 && kb[31] == 0xff ? 0 : 1;
	bad += evc_unhex("0011", kb, 32) != 0 ? 0 : 1; /* wrong length */
	bad += evc_unhex("zz112233445566778899aabbccddeeff00112233445566778899aabbccddeeff", kb, 32) != 0 ? 0 : 1;
	char a[65], b[65];
	evc_hex(out, a);
	memcpy(b, a, sizeof(a));
	bad += evc_mac_equal(a, b) ? 0 : 1;
	b[63] = b[63] == '0' ? '1' : '0';
	bad += evc_mac_equal(a, b) ? 1 : 0;
	printf("%s\n", bad ? "FAILED" : "ALL OK");
	return bad ? 1 : 0;
}
