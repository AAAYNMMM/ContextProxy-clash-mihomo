//go:build windows

package main

import (
	"encoding/binary"
	"log"
	"net"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
	"unsafe"

	"golang.org/x/sys/windows"
)

const (
	afInet              = 2
	afInet6             = 23
	tcpTableOwnerPidAll = 5
)

var (
	iphlpapi                        = windows.NewLazySystemDLL("iphlpapi.dll")
	procGetExtendedTcpTable         = iphlpapi.NewProc("GetExtendedTcpTable")
	processCacheMu                  sync.Mutex
	processCache                    = map[string]processCacheEntry{}
	processNameCache                = map[string]processNameCacheEntry{}
	tcpSnapshot                     = map[string]uint32{}
	tcpSnapshotUpdatedAt            time.Time
	tcpSnapshotTTL                  = 200 * time.Millisecond
	connectionPIDCacheTTL           = 1000 * time.Millisecond
	negativeProcessCacheTTL         = 75 * time.Millisecond
	processIdentityCacheTTL         = 300 * time.Second
	tcpRefreshMu                    sync.Mutex
	lastProcessCacheCleanupAt       time.Time
	tcpTableRefreshCount            atomic.Uint64
	tcpTableForcedRefreshCount      atomic.Uint64
	tcpTableRefreshWaitCount        atomic.Uint64
	processLookupCacheHit           atomic.Uint64
	processLookupCacheMiss          atomic.Uint64
	processLookupHitCount           atomic.Uint64
	processLookupMissCount          atomic.Uint64
	processLookupZeroPIDCount       atomic.Uint64
	processLookupRetryCount         atomic.Uint64
	processLookupRetrySuccessCount  atomic.Uint64
	processLookupNegativeCacheCount atomic.Uint64
	lastProcessLookupLogAt          time.Time
	processLookupLogInterval        = 10 * time.Second
)

type processCacheEntry struct {
	pid       uint32
	identity  ProcessIdentity
	negative  bool
	updatedAt time.Time
}

type processNameCacheEntry struct {
	identity  ProcessIdentity
	negative  bool
	updatedAt time.Time
}

type mibTCPRowOwnerPID struct {
	State      uint32
	LocalAddr  uint32
	LocalPort  uint32
	RemoteAddr uint32
	RemotePort uint32
	OwningPID  uint32
}

type mibTCP6RowOwnerPID struct {
	LocalAddr     [16]byte
	LocalScopeID  uint32
	LocalPort     uint32
	RemoteAddr    [16]byte
	RemoteScopeID uint32
	RemotePort    uint32
	State         uint32
	OwningPID     uint32
}

func lookupProcessName(addr net.Addr, proxyHost string, proxyPort int) string {
	return lookupProcessIdentity(addr, proxyHost, proxyPort).Name
}

func lookupProcessIdentity(addr net.Addr, proxyHost string, proxyPort int) ProcessIdentity {
	return lookupProcessIdentityResult(addr, proxyHost, proxyPort).Identity
}

func lookupProcessIdentityResult(addr net.Addr, proxyHost string, proxyPort int) ProcessLookupResult {
	cleanupProcessCaches()
	tcpAddr, ok := addr.(*net.TCPAddr)
	if !ok || tcpAddr.Port <= 0 {
		return ProcessLookupResult{Result: "miss"}
	}
	serverIP := net.ParseIP(proxyHost)
	if serverIP == nil || serverIP.IsUnspecified() {
		serverIP = net.ParseIP("127.0.0.1")
	}

	key := processCacheKey(tcpAddr.IP, tcpAddr.Port, serverIP, proxyPort)
	processCacheMu.Lock()
	entry, found := processCache[key]
	if found {
		ttl := connectionPIDCacheTTL
		if entry.negative {
			ttl = negativeProcessCacheTTL
		}
		if time.Since(entry.updatedAt) <= ttl {
			processCacheMu.Unlock()
			processLookupCacheHit.Add(1)
			if entry.negative {
				processLookupNegativeCacheCount.Add(1)
				processLookupMissCount.Add(1)
				return ProcessLookupResult{Result: "miss", CacheHit: true}
			}
			processLookupHitCount.Add(1)
			return ProcessLookupResult{Identity: entry.identity, Result: "hit", CacheHit: true}
		}
		delete(processCache, key)
	}
	processCacheMu.Unlock()
	processLookupCacheMiss.Add(1)

	pid, err := lookupPIDFromSnapshot(tcpAddr.IP, tcpAddr.Port, serverIP, proxyPort)
	if err != nil {
		logProcessLookupError(err)
		cacheProcessIdentity(key, 0, ProcessIdentity{}, true)
		processLookupMissCount.Add(1)
		return ProcessLookupResult{Result: "error"}
	}
	retryCount := 0
	if pid == 0 {
		for retryCount < 3 && pid == 0 {
			retryCount++
			processLookupRetryCount.Add(1)
			_ = forceRefreshTCPSnapshot()
			time.Sleep(time.Duration(5+retryCount*5) * time.Millisecond)
			pid, err = lookupPIDFromSnapshot(tcpAddr.IP, tcpAddr.Port, serverIP, proxyPort)
			if err != nil {
				logProcessLookupError(err)
				cacheProcessIdentity(key, 0, ProcessIdentity{}, true)
				processLookupMissCount.Add(1)
				return ProcessLookupResult{Result: "error", RetryCount: retryCount}
			}
		}
		if pid == 0 {
			processLookupZeroPIDCount.Add(1)
			processLookupMissCount.Add(1)
			return ProcessLookupResult{Result: "zero_pid", RetryCount: retryCount}
		}
		processLookupRetrySuccessCount.Add(1)
	}

	identity := cachedProcessIdentityByPID(pid)
	if identityKey(identity) == "" {
		cacheProcessIdentity(key, pid, ProcessIdentity{}, true)
		processLookupMissCount.Add(1)
		return ProcessLookupResult{Result: "miss", RetryCount: retryCount}
	}
	cacheProcessIdentity(key, pid, identity, false)
	processLookupHitCount.Add(1)
	result := "hit"
	if retryCount > 0 {
		result = "retry_hit"
	}
	return ProcessLookupResult{Identity: identity, Result: result, RetryCount: retryCount}
}

func cleanupProcessCaches() {
	now := time.Now()
	processCacheMu.Lock()
	if now.Sub(lastProcessCacheCleanupAt) < 30*time.Second {
		processCacheMu.Unlock()
		return
	}
	lastProcessCacheCleanupAt = now
	for key, entry := range processCache {
		ttl := connectionPIDCacheTTL
		if entry.negative {
			ttl = negativeProcessCacheTTL
		}
		if now.Sub(entry.updatedAt) > ttl {
			delete(processCache, key)
		}
	}
	for key, entry := range processNameCache {
		ttl := processIdentityCacheTTL
		if entry.negative {
			ttl = 5 * time.Second
		}
		if now.Sub(entry.updatedAt) > ttl {
			delete(processNameCache, key)
		}
	}
	processCacheMu.Unlock()
}

func processCacheKey(clientIP net.IP, clientPort int, serverIP net.IP, serverPort int) string {
	return clientIP.String() + ":" + strconv.Itoa(clientPort) + "->" + serverIP.String() + ":" + strconv.Itoa(serverPort)
}

func cacheProcessIdentity(key string, pid uint32, identity ProcessIdentity, negative bool) {
	if negative {
		processLookupNegativeCacheCount.Add(1)
	}
	processCacheMu.Lock()
	processCache[key] = processCacheEntry{pid: pid, identity: identity, negative: negative, updatedAt: time.Now()}
	processCacheMu.Unlock()
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
	return ProcessLookupMetrics{
		TCPTableRefreshCount:            tcpTableRefreshCount.Load(),
		TCPTableForcedRefreshCount:      tcpTableForcedRefreshCount.Load(),
		TCPTableRefreshWaitCount:        tcpTableRefreshWaitCount.Load(),
		ProcessLookupCacheHit:           processLookupCacheHit.Load(),
		ProcessLookupCacheMiss:          processLookupCacheMiss.Load(),
		ProcessLookupHitCount:           processLookupHitCount.Load(),
		ProcessLookupMissCount:          processLookupMissCount.Load(),
		ProcessLookupZeroPIDCount:       processLookupZeroPIDCount.Load(),
		ProcessLookupRetryCount:         processLookupRetryCount.Load(),
		ProcessLookupRetrySuccessCount:  processLookupRetrySuccessCount.Load(),
		ProcessLookupNegativeCacheCount: processLookupNegativeCacheCount.Load(),
	}
}

func setTCPSnapshotTTL(ttl time.Duration) {
	if ttl <= 0 {
		return
	}
	processCacheMu.Lock()
	tcpSnapshotTTL = ttl
	processCacheMu.Unlock()
}

func setProcessCacheTTLs(connectionTTL time.Duration, negativeTTL time.Duration, identityTTL time.Duration) {
	processCacheMu.Lock()
	if connectionTTL > 0 {
		connectionPIDCacheTTL = connectionTTL
	}
	if negativeTTL > 0 {
		negativeProcessCacheTTL = negativeTTL
	}
	if identityTTL > 0 {
		processIdentityCacheTTL = identityTTL
	}
	processCacheMu.Unlock()
}

func logProcessLookupError(err error) {
	processCacheMu.Lock()
	defer processCacheMu.Unlock()
	if time.Since(lastProcessLookupLogAt) < processLookupLogInterval {
		return
	}
	lastProcessLookupLogAt = time.Now()
	log.Printf("process lookup failed: %v", err)
}

func lookupPIDFromSnapshot(clientIP net.IP, clientPort int, serverIP net.IP, serverPort int) (uint32, error) {
	key := processCacheKey(clientIP, clientPort, serverIP, serverPort)
	processCacheMu.Lock()
	if time.Since(tcpSnapshotUpdatedAt) > tcpSnapshotTTL {
		processCacheMu.Unlock()
		if err := refreshTCPSnapshot(false); err != nil {
			return 0, err
		}
		processCacheMu.Lock()
	}
	pid := tcpSnapshot[key]
	processCacheMu.Unlock()
	return pid, nil
}

func forceRefreshTCPSnapshot() error {
	return refreshTCPSnapshot(true)
}

func refreshTCPSnapshot(force bool) error {
	if !tcpRefreshMu.TryLock() {
		tcpTableRefreshWaitCount.Add(1)
		tcpRefreshMu.Lock()
		tcpRefreshMu.Unlock()
		return nil
	}
	defer tcpRefreshMu.Unlock()
	processCacheMu.Lock()
	if !force && time.Since(tcpSnapshotUpdatedAt) <= tcpSnapshotTTL {
		processCacheMu.Unlock()
		return nil
	}
	if force && time.Since(tcpSnapshotUpdatedAt) < 20*time.Millisecond {
		processCacheMu.Unlock()
		return nil
	}
	processCacheMu.Unlock()
	snapshot, err := buildTCPSnapshot()
	if err != nil {
		return err
	}
	processCacheMu.Lock()
	tcpSnapshot = snapshot
	tcpSnapshotUpdatedAt = time.Now()
	tcpTableRefreshCount.Add(1)
	if force {
		tcpTableForcedRefreshCount.Add(1)
	}
	processCacheMu.Unlock()
	return nil
}

func buildTCPSnapshot() (map[string]uint32, error) {
	result := map[string]uint32{}
	if err := addIPv4Snapshot(result); err != nil {
		return result, err
	}
	if err := addIPv6Snapshot(result); err != nil {
		return result, err
	}
	return result, nil
}

func addIPv4Snapshot(result map[string]uint32) error {
	table, err := getExtendedTCPTable(afInet)
	if err != nil {
		return err
	}
	if len(table) < 4 {
		return nil
	}
	count := int(*(*uint32)(unsafe.Pointer(&table[0])))
	offset := uintptr(4)
	rowSize := unsafe.Sizeof(mibTCPRowOwnerPID{})
	for i := 0; i < count; i++ {
		row := (*mibTCPRowOwnerPID)(unsafe.Pointer(&table[offset+uintptr(i)*rowSize]))
		localIP := uint32ToIPv4(row.LocalAddr)
		remoteIP := uint32ToIPv4(row.RemoteAddr)
		key := processCacheKey(localIP, networkPort(row.LocalPort), remoteIP, networkPort(row.RemotePort))
		if row.OwningPID != 0 {
			result[key] = row.OwningPID
		}
	}
	return nil
}

func addIPv6Snapshot(result map[string]uint32) error {
	table, err := getExtendedTCPTable(afInet6)
	if err != nil {
		return err
	}
	if len(table) < 4 {
		return nil
	}
	count := int(*(*uint32)(unsafe.Pointer(&table[0])))
	offset := uintptr(4)
	rowSize := unsafe.Sizeof(mibTCP6RowOwnerPID{})
	for i := 0; i < count; i++ {
		row := (*mibTCP6RowOwnerPID)(unsafe.Pointer(&table[offset+uintptr(i)*rowSize]))
		localIP := net.IP(row.LocalAddr[:])
		remoteIP := net.IP(row.RemoteAddr[:])
		key := processCacheKey(localIP, networkPort(row.LocalPort), remoteIP, networkPort(row.RemotePort))
		if row.OwningPID != 0 {
			result[key] = row.OwningPID
		}
	}
	return nil
}

func getExtendedTCPTable(addressFamily uint32) ([]byte, error) {
	var size uint32
	r1, _, err := procGetExtendedTcpTable.Call(
		0,
		uintptr(unsafe.Pointer(&size)),
		1,
		uintptr(addressFamily),
		tcpTableOwnerPidAll,
		0,
	)
	if r1 != uintptr(windows.ERROR_INSUFFICIENT_BUFFER) && r1 != 0 {
		return nil, err
	}
	buffer := make([]byte, size)
	r1, _, err = procGetExtendedTcpTable.Call(
		uintptr(unsafe.Pointer(&buffer[0])),
		uintptr(unsafe.Pointer(&size)),
		1,
		uintptr(addressFamily),
		tcpTableOwnerPidAll,
		0,
	)
	if r1 != 0 {
		return nil, err
	}
	return buffer[:size], nil
}

func networkPort(port uint32) int {
	return int(windows.Ntohs(uint16(port)))
}

func uint32ToIPv4(value uint32) net.IP {
	ip := make(net.IP, 4)
	binary.LittleEndian.PutUint32(ip, value)
	return ip
}

func cachedProcessIdentityByPID(pid uint32) ProcessIdentity {
	// Query once to obtain the creation time; cache lookup then uses PID plus
	// start time so PID reuse cannot inherit the previous process identity.
	identity := processIdentityByPID(pid)
	if identityKey(identity) == "" {
		return ProcessIdentity{}
	}
	key := processIdentityCacheKey(identity)
	processCacheMu.Lock()
	entry, found := processNameCache[key]
	if found {
		ttl := processIdentityCacheTTL
		if entry.negative {
			ttl = 5 * time.Second
		}
		if time.Since(entry.updatedAt) <= ttl {
			processCacheMu.Unlock()
			if entry.negative {
				return ProcessIdentity{}
			}
			return entry.identity
		}
		delete(processNameCache, key)
	}
	processNameCache[key] = processNameCacheEntry{
		identity:  identity,
		negative:  identityKey(identity) == "",
		updatedAt: time.Now(),
	}
	processCacheMu.Unlock()
	return identity
}

func processIdentityCacheKey(identity ProcessIdentity) string {
	if identity.PID == 0 {
		return ""
	}
	return strconv.FormatUint(uint64(identity.PID), 10) + "#" + strconv.FormatInt(identity.StartTimeUnixNano, 10)
}

func processIdentityByPID(pid uint32) ProcessIdentity {
	handle, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, pid)
	if err != nil {
		return ProcessIdentity{}
	}
	defer windows.CloseHandle(handle)

	buffer := make([]uint16, windows.MAX_PATH)
	size := uint32(len(buffer))
	if err := windows.QueryFullProcessImageName(handle, 0, &buffer[0], &size); err != nil {
		return ProcessIdentity{}
	}
	path := windows.UTF16ToString(buffer[:size])
	name := filepath.Base(path)
	var createdAt int64
	var creationTime, exitTime, kernelTime, userTime windows.Filetime
	if err := windows.GetProcessTimes(handle, &creationTime, &exitTime, &kernelTime, &userTime); err == nil {
		createdAt = creationTime.Nanoseconds()
	}
	return ProcessIdentity{
		Name:              strings.TrimSpace(name),
		Path:              strings.TrimSpace(path),
		PID:               pid,
		StartTimeUnixNano: createdAt,
	}
}
