//go:build !windows

package main

import (
	"net"
	"time"
)

func lookupProcessName(addr net.Addr, proxyHost string, proxyPort int) string {
	_, _, _ = addr, proxyHost, proxyPort
	return ""
}

func lookupProcessIdentity(addr net.Addr, proxyHost string, proxyPort int) ProcessIdentity {
	_, _, _ = addr, proxyHost, proxyPort
	return ProcessIdentity{}
}

func lookupProcessIdentityResult(addr net.Addr, proxyHost string, proxyPort int) ProcessLookupResult {
	_, _, _ = addr, proxyHost, proxyPort
	return ProcessLookupResult{Result: "miss"}
}

type ProcessLookupMetrics struct {
	TCPTableRefreshCount            uint64
	TCPTableForcedRefreshCount      uint64
	TCPTableRefreshWaitCount        uint64
	ProcessLookupCacheHit           uint64
	ProcessLookupCacheMiss          uint64
	ProcessLookupHitCount           uint64
	ProcessLookupMissCount          uint64
	ProcessLookupZeroPIDCount       uint64
	ProcessLookupRetryCount         uint64
	ProcessLookupRetrySuccessCount  uint64
	ProcessLookupNegativeCacheCount uint64
}

func getProcessLookupMetrics() ProcessLookupMetrics {
	return ProcessLookupMetrics{}
}

func setTCPSnapshotTTL(_ time.Duration) {}

func setProcessCacheTTLs(_, _, _ time.Duration) {}
