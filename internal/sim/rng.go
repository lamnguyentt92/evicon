package sim

import (
	"hash/fnv"
	"math"
)

// Common random numbers: every random draw is a pure function of
// (seed, episode, tag, i, j). Policies compared on the same episode therefore
// see identical arrivals, values, availability and per-attempt network fates.

func splitmix(x uint64) uint64 {
	x += 0x9e3779b97f4a7c15
	x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9
	x = (x ^ (x >> 27)) * 0x94d049bb133111eb
	return x ^ (x >> 31)
}

func tagHash(tag string) uint64 {
	h := fnv.New64a()
	_, _ = h.Write([]byte(tag))
	return h.Sum64()
}

type crn struct {
	seed    int64
	episode int
}

func (r crn) u(tag string, i, j int) float64 {
	x := splitmix(uint64(r.seed))
	x = splitmix(x ^ uint64(r.episode)*0x2545f4914f6cdd1d)
	x = splitmix(x ^ tagHash(tag))
	x = splitmix(x ^ uint64(int64(i))*0x9e3779b97f4a7c15)
	x = splitmix(x ^ uint64(int64(j))*0xd1b54a32d192ed03)
	// 53-bit mantissa in [0,1)
	return float64(x>>11) / float64(uint64(1)<<53)
}

func (r crn) exp(tag string, i, j int, mean float64) float64 {
	if mean <= 0 {
		return 0
	}
	u := r.u(tag, i, j)
	return -mean * math.Log(1-u)
}

func (r crn) intn(tag string, i, j int, lo, hi int64) int64 {
	if hi <= lo {
		return lo
	}
	span := hi - lo + 1
	v := int64(r.u(tag, i, j) * float64(span))
	if v >= span {
		v = span - 1
	}
	return lo + v
}

func ms(x float64) int64 { return int64(math.Round(x * 1000)) } // ms -> us
