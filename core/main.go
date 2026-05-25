package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

const directGroup = "Direct"

const (
	maxReportBodyBytes          = 16 * 1024
	maxReportHostLength         = 253
	exePathHintTTL              = 6 * time.Hour
	exePathHintDisable          = 10 * time.Minute
	exePathHintTimeoutThreshold = 5
)

var (
	bufferPools sync.Map
)

type ListenConfig struct {
	ProxyHost string `json:"proxy_host"`
	ProxyPort int    `json:"proxy_port"`
	APIHost   string `json:"api_host"`
	APIPort   int    `json:"api_port"`
}

type FilesConfig struct {
	DomainRules  string `json:"domain_rules"`
	ProcessRules string `json:"process_rules"`
}

type LogConfig struct {
	Dir string `json:"dir"`
}

type SecurityConfig struct {
	Token string `json:"token"`
}

type BehaviorConfig struct {
	TabWaitEnabled              bool `json:"tab_wait_enabled"`
	TabWaitBrowserMS            int  `json:"tab_wait_browser_ms"`
	TabWaitUnknownMS            int  `json:"tab_wait_unknown_ms"`
	TabWaitUnknownProcessMS     int  `json:"tab_wait_unknown_process_ms"`
	NonTabTTLSeconds            int  `json:"non_tab_ttl_sec"`
	TabCapableTTLSeconds        int  `json:"tab_capable_ttl_sec"`
	BrowserRegistryTTL          int  `json:"browser_registry_ttl_sec"`
	TCPTableSnapshotMS          int  `json:"tcp_table_snapshot_ms"`
	ProcessIdentityTTLSeconds   int  `json:"process_identity_ttl_sec"`
	ConnectionPIDCacheTTLMS     int  `json:"connection_pid_cache_ttl_ms"`
	NegativeProcessCacheTTLMS   int  `json:"negative_process_cache_ttl_ms"`
	ProcessStateCleanupInterval int  `json:"process_state_cleanup_interval_sec"`
}

type TransferConfig struct {
	NormalBufferKB          int   `json:"normal_buffer_kb"`
	HighBufferKB            int   `json:"high_buffer_kb"`
	HighThroughputWindowSec int   `json:"high_throughput_window_sec"`
	HighThroughputBytes     int64 `json:"high_throughput_bytes"`
	LowThroughputWindowSec  int   `json:"low_throughput_window_sec"`
	LowThroughputBytes      int64 `json:"low_throughput_bytes"`
	WriteTimeoutSec         int   `json:"write_timeout_sec"`
	IdleTimeoutSec          int   `json:"idle_timeout_sec"`
}

type SocketConfig struct {
	TCPNoDelay       bool `json:"tcp_nodelay"`
	KeepAlive        bool `json:"keepalive"`
	KeepAliveSec     int  `json:"keepalive_sec"`
	ReadBufferBytes  int  `json:"read_buffer_bytes"`
	WriteBufferBytes int  `json:"write_buffer_bytes"`
}

type CoreConfig struct {
	Listen        ListenConfig   `json:"listen"`
	Groups        map[string]int `json:"groups"`
	Files         FilesConfig    `json:"files"`
	Logs          LogConfig      `json:"logs"`
	Security      SecurityConfig `json:"security"`
	Behavior      BehaviorConfig `json:"behavior"`
	Transfer      TransferConfig `json:"transfer"`
	Socket        SocketConfig   `json:"socket"`
	DefaultProxy  string         `json:"default_proxy"`
	Direct        string         `json:"direct"`
	SpecialGroups []string       `json:"special_groups"`
	DomainRules   []Rule         `json:"domain_rules"`
	ProcessRules  []Rule         `json:"process_rules"`
}

type Rule struct {
	Group   string `json:"group"`
	Pattern string `json:"pattern"`
}

type Report struct {
	TabHost         string    `json:"tabHost"`
	RequestHost     string    `json:"requestHost"`
	CreatedAt       time.Time `json:"createdAt"`
	ProcessName     string    `json:"processName,omitempty"`
	ProcessIdentity string    `json:"processIdentity,omitempty"`
}

type RouteDecision struct {
	Group                   string
	Source                  string
	SourceReason            string
	TabHost                 string
	MatchedPattern          string
	IsRegisteredBrowser     bool
	BrowserRegistryHit      bool
	BrowserRegistryIdentity string
	TabWaitMS               int
	TabWaitResult           string
	ReportProcessName       string
	ReportProcessIdentity   string
	ProcessLookupResult     string
	ProcessLookupRetryCount int
	ProcessIdentity         string
	ProcessIdentityKey      string
	ProcessCacheHit         bool
	ProcessRuleMatched      bool
	ProcessMatchedPattern   string
	ProcessState            string
	TabCapableUntilUnix     int64
	NonTabUntilUnix         int64
	ExePathHintHit          bool
	ExePathHintDisabled     bool
	ExePathHintTimeoutCount int
	ExePathHintReason       string
}

type CoreEvent struct {
	ID                      uint64 `json:"id"`
	BootID                  string `json:"boot_id"`
	Timestamp               string `json:"timestamp"`
	Source                  string `json:"source"`
	SourceReason            string `json:"source_reason"`
	TabHost                 string `json:"tab_host"`
	RequestHost             string `json:"request_host"`
	ProcessName             string `json:"process_name"`
	FinalGroup              string `json:"final_group"`
	MatchedPattern          string `json:"matched_pattern"`
	Target                  string `json:"target"`
	Listener                string `json:"listener"`
	Action                  string `json:"action"`
	IsRegisteredBrowser     bool   `json:"is_registered_browser"`
	BrowserRegistryHit      bool   `json:"browser_registry_hit"`
	BrowserRegistryIdentity string `json:"browser_registry_identity"`
	TabWaitMS               int    `json:"tab_wait_ms"`
	TabWaitResult           string `json:"tab_wait_result"`
	ReportProcessName       string `json:"report_process_name"`
	ReportProcessIdentity   string `json:"report_process_identity"`
	ProcessLookupResult     string `json:"process_lookup_result"`
	ProcessLookupRetryCount int    `json:"process_lookup_retry_count"`
	ProcessIdentity         string `json:"process_identity"`
	ProcessIdentityKey      string `json:"process_identity_key"`
	ProcessCacheHit         bool   `json:"process_cache_hit"`
	ProcessRuleMatched      bool   `json:"process_rule_matched"`
	ProcessMatchedPattern   string `json:"process_matched_pattern"`
	ProcessState            string `json:"process_state"`
	TabCapableUntilUnix     int64  `json:"tab_capable_until"`
	NonTabUntilUnix         int64  `json:"non_tab_until"`
	ExePathHintHit          bool   `json:"exe_path_hint_hit"`
	ExePathHintDisabled     bool   `json:"exe_path_hint_disabled"`
	ExePathHintTimeoutCount int    `json:"exe_path_hint_timeout_count"`
	ExePathHintReason       string `json:"exe_path_hint_reason"`
	FinalResult             string `json:"final_result,omitempty"`
	FinalReason             string `json:"final_reason,omitempty"`
	RelayError              string `json:"relay_error,omitempty"`
	BytesIn                 int64  `json:"bytes_in,omitempty"`
	BytesOut                int64  `json:"bytes_out,omitempty"`
	DurationMS              int64  `json:"duration_ms,omitempty"`
}

type ConnInfo struct {
	ID           uint64
	Group        string
	Host         string
	Process      string
	CreatedAt    time.Time
	LastActiveAt atomic.Int64
	FinalMu      sync.Mutex
	FinalSet     bool
	FinalReason  string
	CloseReason  atomic.Value
	Client       net.Conn
	Remote       net.Conn
	BytesIn      int64
	BytesOut     int64
	HighIn       atomic.Bool
	HighOut      atomic.Bool
	BufferIn     atomic.Int64
	BufferOut    atomic.Int64
	LargeFlow    atomic.Bool
}

type TrafficStats struct {
	RecentTotal         int    `json:"recent_total"`
	RecentFailCount     int    `json:"recent_fail_count"`
	ConsecutiveFailures int    `json:"consecutive_failures"`
	SuccessCount        int64  `json:"success_count"`
	FailureCount        int64  `json:"failure_count"`
	LastReason          string `json:"last_reason,omitempty"`
	UpdatedAtUnix       int64  `json:"updated_at"`
	window              []bool
}

type CompiledDomainRule struct {
	Group    string
	Pattern  string
	Matchers []*regexp.Regexp
	Expanded bool
}

type logEntry struct {
	fileName string
	message  string
}

type LogManager struct {
	dir              string
	ch               chan logEntry
	done             chan struct{}
	files            map[string]*os.File
	closed           atomic.Bool
	dropped          atomic.Uint64
	droppedMu        sync.Mutex
	droppedByFile    map[string]uint64
	lastDropWarnUnix atomic.Int64
}

type ProcessIdentity struct {
	Name              string
	Path              string
	PID               uint32
	StartTimeUnixNano int64
}

type ProcessLookupResult struct {
	Identity   ProcessIdentity
	Result     string
	RetryCount int
	CacheHit   bool
}

type BrowserRegistryEntry struct {
	ProcessName   string
	ExePath       string
	Identity      string
	LastSeen      time.Time
	RegisterCount int64
	ReportCount   int64
}

type ProcessStateEntry struct {
	ProcessName      string
	ExePath          string
	PID              uint32
	ProcessStartTime int64
	State            string
	LastSeenProxy    time.Time
	LastSeenReport   time.Time
	NonTabUntil      time.Time
	TabCapableUntil  time.Time
	WaitTimeoutCount int
	ReportCount      int64
}

type ProcessRuleCacheEntry struct {
	Group   string
	Pattern string
	Matched bool
}

type ExePathHintEntry struct {
	ExePath            string
	LastReportTime     time.Time
	LastTabCapableTime time.Time
	TimeoutCount       int
	DisabledUntil      time.Time
}

type GroupPauseEntry struct {
	Until time.Time
}

type contextKey string

const requestIdentityKey contextKey = "contextproxy-process-identity"

type Core struct {
	configPath   string
	mu           sync.RWMutex
	config       CoreConfig
	domainRules  []CompiledDomainRule
	processRules []Rule

	reportMu sync.RWMutex
	reports  []Report

	waiterMu sync.Mutex
	waiters  map[string][]chan struct{}

	browserMu        sync.RWMutex
	browserRegistry  map[string]*BrowserRegistryEntry
	processStateMu   sync.RWMutex
	processStates    map[string]*ProcessStateEntry
	exePathHints     map[string]*ExePathHintEntry
	processRuleMu    sync.RWMutex
	processRuleCache map[string]ProcessRuleCacheEntry
	fallbackMu       sync.Mutex
	reportFallbackAt map[string]time.Time

	connMu sync.RWMutex
	conns  map[uint64]*ConnInfo
	nextID atomic.Uint64

	statsMu             sync.RWMutex
	traffic             map[string]*TrafficStats
	failuresByGroup     map[string]map[string]int64
	failuresByProcess   map[string]map[string]int64
	failuresByHost      map[string]map[string]int64
	finalResultByReason map[string]int64

	pauseMu         sync.Mutex
	pausedGroups    map[string]GroupPauseEntry
	groupPauseCount atomic.Uint64

	eventMu     sync.RWMutex
	events      []CoreEvent
	nextEventID atomic.Uint64
	bootID      string
	startedAt   time.Time

	logger     *log.Logger
	coreLog    *os.File
	logManager *LogManager

	apiServer    *http.Server
	proxyLn      net.Listener
	shuttingDown atomic.Bool
	connWG       sync.WaitGroup

	reportProcessContextHit       atomic.Uint64
	reportProcessContextMiss      atomic.Uint64
	reportProcessFallbackLookups  atomic.Uint64
	reportInvalidCount            atomic.Uint64
	tabWaitHitCount               atomic.Uint64
	tabWaitTimeoutCount           atomic.Uint64
	tabWaitSkippedDomainHitCount  atomic.Uint64
	tabWaitSkippedNotBrowserCount atomic.Uint64
	nontabCacheHitCount           atomic.Uint64
	nontabMarkCount               atomic.Uint64
	tabCapableMarkCount           atomic.Uint64
	exePathHintHitCount           atomic.Uint64
	exePathHintTimeoutCount       atomic.Uint64
	exePathHintDisabledCount      atomic.Uint64
	exePathHintExpiredCount       atomic.Uint64
	processRestartDetectedCount   atomic.Uint64

	largeFlowActive                atomic.Int64
	largeFlowTotal                 atomic.Uint64
	highThroughputEnabledCount     atomic.Uint64
	highThroughputDisabledCount    atomic.Uint64
	activeHighThroughputDirections atomic.Int64
	transferBytesInTotal           atomic.Uint64
	transferBytesOutTotal          atomic.Uint64
	failureNoResponseCount         atomic.Uint64
	failureQuickCloseLowBytesCount atomic.Uint64
	failureWriteTimeoutCount       atomic.Uint64
	failureDirectWriteCount        atomic.Uint64
	failureListenerConnectCount    atomic.Uint64
	failureListenerWriteCount      atomic.Uint64
	failureRelayErrorCount         atomic.Uint64
	failureRelayShutdownTimeout    atomic.Uint64
	finalResultOKCount             atomic.Uint64
	finalResultFailureCount        atomic.Uint64
	duplicateResultSuppressedCount atomic.Uint64
	normalBufferPoolGet            atomic.Uint64
	normalBufferPoolPut            atomic.Uint64
	highBufferPoolGet              atomic.Uint64
	highBufferPoolPut              atomic.Uint64
	activeHighBufferCount          atomic.Int64
}

func main() {
	defaultConfigPath := filepath.Join("..", "config", "contextproxy_core.json")
	if exePath, err := os.Executable(); err == nil {
		defaultConfigPath = filepath.Join(filepath.Dir(exePath), "..", "config", "contextproxy_core.json")
	}
	configPath := flag.String("config", defaultConfigPath, "contextproxy core config path")
	flag.Parse()
	absConfigPath, err := filepath.Abs(*configPath)
	if err != nil {
		absConfigPath = *configPath
	}

	core := &Core{
		configPath:          absConfigPath,
		conns:               make(map[uint64]*ConnInfo),
		traffic:             make(map[string]*TrafficStats),
		failuresByGroup:     make(map[string]map[string]int64),
		failuresByProcess:   make(map[string]map[string]int64),
		failuresByHost:      make(map[string]map[string]int64),
		finalResultByReason: make(map[string]int64),
		pausedGroups:        make(map[string]GroupPauseEntry),
		waiters:             make(map[string][]chan struct{}),
		browserRegistry:     make(map[string]*BrowserRegistryEntry),
		processStates:       make(map[string]*ProcessStateEntry),
		exePathHints:        make(map[string]*ExePathHintEntry),
		processRuleCache:    make(map[string]ProcessRuleCacheEntry),
		reportFallbackAt:    make(map[string]time.Time),
		bootID:              strconv.FormatInt(time.Now().UnixNano(), 10),
		startedAt:           time.Now(),
	}
	if err := core.loadConfig(); err != nil {
		fmt.Fprintf(os.Stderr, "load config failed: %v\n", err)
		os.Exit(1)
	}
	core.setupLogger()
	defer core.closeLogs()
	core.logger.Printf("contextproxy core starting, config=%s", absConfigPath)

	cfg := core.getConfig()
	proxyAddr := net.JoinHostPort(cfg.Listen.ProxyHost, strconv.Itoa(cfg.Listen.ProxyPort))
	apiAddr := net.JoinHostPort(cfg.Listen.APIHost, strconv.Itoa(cfg.Listen.APIPort))

	proxyLn, err := net.Listen("tcp", proxyAddr)
	if err != nil {
		core.logger.Printf("proxy listen failed: %v", err)
		os.Exit(2)
	}
	core.proxyLn = proxyLn

	mux := http.NewServeMux()
	mux.HandleFunc("/health", core.handleHealth)
	mux.HandleFunc("/report", core.handleReport)
	mux.HandleFunc("/metrics", core.handleMetrics)
	mux.HandleFunc("/events", core.handleEvents)
	mux.HandleFunc("/connections", core.handleConnections)
	mux.HandleFunc("/register_browser", core.handleRegisterBrowser)
	mux.HandleFunc("/reload", core.handleReload)
	mux.HandleFunc("/close", core.handleClose)
	mux.HandleFunc("/pause_groups", core.handlePauseGroups)
	mux.HandleFunc("/resume_groups", core.handleResumeGroups)

	apiServer := &http.Server{Addr: apiAddr, Handler: mux, ConnContext: core.apiConnContext}
	core.apiServer = apiServer
	apiErr := make(chan error, 1)
	go func() {
		core.logger.Printf("api listening at %s", apiAddr)
		if err := apiServer.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			apiErr <- err
		}
		close(apiErr)
	}()

	core.logger.Printf("proxy listening at %s", proxyAddr)
	acceptDone := make(chan struct{})
	go func() {
		defer close(acceptDone)
		for {
			conn, err := proxyLn.Accept()
			if err != nil {
				if core.isShuttingDown() {
					return
				}
				core.logger.Printf("accept failed: %v", err)
				continue
			}
			core.connWG.Add(1)
			go func() {
				defer core.connWG.Done()
				core.handleProxyConn(conn)
			}()
		}
	}()

	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	select {
	case sig := <-stop:
		core.logger.Printf("shutdown signal received: %s", sig)
	case err := <-apiErr:
		if err != nil {
			core.logger.Printf("api server stopped: %v", err)
		}
	}
	core.shutdown(acceptDone)
}

func (c *Core) setupLogger() {
	cfg := c.getConfig()
	logDir := cfg.Logs.Dir
	if logDir == "" {
		logDir = "logs"
	}
	_ = os.MkdirAll(logDir, 0755)
	file, err := os.OpenFile(filepath.Join(logDir, "core.log"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644)
	if err != nil {
		c.logger = log.New(io.Discard, "", log.LstdFlags)
		return
	}
	c.coreLog = file
	c.logManager = newLogManager(logDir)
	c.logger = log.New(file, "", log.LstdFlags)
	log.SetOutput(file)
	log.SetFlags(log.LstdFlags)
}

func (c *Core) closeLogs() {
	if c.logManager != nil {
		c.logManager.close()
	}
	if c.coreLog != nil {
		_ = c.coreLog.Sync()
		_ = c.coreLog.Close()
	}
}

func newLogManager(dir string) *LogManager {
	lm := &LogManager{
		dir:           dir,
		ch:            make(chan logEntry, 2048),
		done:          make(chan struct{}),
		files:         make(map[string]*os.File),
		droppedByFile: make(map[string]uint64),
	}
	go lm.loop()
	return lm
}

func (lm *LogManager) write(fileName string, message string) {
	if lm == nil || lm.closed.Load() {
		return
	}
	select {
	case lm.ch <- logEntry{fileName: fileName, message: message}:
	default:
		lm.recordDrop(fileName)
	}
}

func (lm *LogManager) loop() {
	defer close(lm.done)
	ticker := time.NewTicker(10 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case entry, ok := <-lm.ch:
			if !ok {
				for _, file := range lm.files {
					_ = file.Sync()
					_ = file.Close()
				}
				return
			}
			file, err := lm.file(entry.fileName)
			if err != nil {
				continue
			}
			line := time.Now().Format("2006-01-02 15:04:05") + " " + entry.message + "\n"
			_, _ = file.WriteString(line)
		case <-ticker.C:
			lm.writeDropWarning()
		}
	}
}

func (lm *LogManager) recordDrop(fileName string) {
	lm.dropped.Add(1)
	lm.droppedMu.Lock()
	lm.droppedByFile[fileName]++
	lm.droppedMu.Unlock()
}

func (lm *LogManager) writeDropWarning() {
	dropped := lm.dropped.Load()
	if dropped == 0 {
		return
	}
	last := lm.lastDropWarnUnix.Load()
	now := time.Now().Unix()
	if now-last < 10 || !lm.lastDropWarnUnix.CompareAndSwap(last, now) {
		return
	}
	file, err := lm.file("core.log")
	if err != nil {
		return
	}
	line := fmt.Sprintf("%s warning: log queue dropped entries total=%d\n", time.Now().Format("2006-01-02 15:04:05"), dropped)
	_, _ = file.WriteString(line)
}

func (lm *LogManager) metrics() (uint64, map[string]uint64, int, int) {
	if lm == nil {
		return 0, map[string]uint64{}, 0, 0
	}
	lm.droppedMu.Lock()
	byFile := make(map[string]uint64, len(lm.droppedByFile))
	for name, count := range lm.droppedByFile {
		byFile[name] = count
	}
	lm.droppedMu.Unlock()
	return lm.dropped.Load(), byFile, len(lm.ch), cap(lm.ch)
}

func (lm *LogManager) file(fileName string) (*os.File, error) {
	if file := lm.files[fileName]; file != nil {
		return file, nil
	}
	if err := os.MkdirAll(lm.dir, 0755); err != nil {
		return nil, err
	}
	file, err := os.OpenFile(filepath.Join(lm.dir, fileName), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644)
	if err != nil {
		return nil, err
	}
	lm.files[fileName] = file
	return file, nil
}

func (lm *LogManager) close() {
	if lm == nil || lm.closed.Swap(true) {
		return
	}
	close(lm.ch)
	<-lm.done
}

func (c *Core) shutdown(acceptDone <-chan struct{}) {
	c.shuttingDown.Store(true)
	c.notifyAllReportWaiters()
	if c.proxyLn != nil {
		_ = c.proxyLn.Close()
	}
	if c.apiServer != nil {
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		_ = c.apiServer.Shutdown(ctx)
		cancel()
	}
	select {
	case <-acceptDone:
	case <-time.After(2 * time.Second):
	}
	done := make(chan struct{})
	go func() {
		c.connWG.Wait()
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		c.closeAllConnections()
		select {
		case <-done:
		case <-time.After(2 * time.Second):
		}
	}
}

func (c *Core) isShuttingDown() bool {
	return c.shuttingDown.Load()
}

func (c *Core) loadConfig() error {
	data, err := os.ReadFile(c.configPath)
	if err != nil {
		return err
	}
	var cfg CoreConfig
	if err := json.Unmarshal(data, &cfg); err != nil {
		return err
	}
	if cfg.Listen.ProxyHost == "" {
		cfg.Listen.ProxyHost = "127.0.0.1"
	}
	if cfg.Listen.APIHost == "" {
		cfg.Listen.APIHost = "127.0.0.1"
	}
	if cfg.DefaultProxy == "" {
		cfg.DefaultProxy = "Proxy"
	}
	if cfg.Direct == "" {
		cfg.Direct = directGroup
	}
	if !jsonHasNestedField(data, "behavior", "tab_wait_enabled") {
		cfg.Behavior.TabWaitEnabled = true
	}
	if cfg.Behavior.TabWaitBrowserMS <= 0 {
		cfg.Behavior.TabWaitBrowserMS = 250
	}
	if cfg.Behavior.TabWaitUnknownMS <= 0 {
		cfg.Behavior.TabWaitUnknownMS = 120
	}
	if cfg.Behavior.TabWaitUnknownProcessMS <= 0 {
		cfg.Behavior.TabWaitUnknownProcessMS = 80
	}
	if cfg.Behavior.NonTabTTLSeconds <= 0 {
		cfg.Behavior.NonTabTTLSeconds = 300
	}
	if cfg.Behavior.TabCapableTTLSeconds <= 0 {
		cfg.Behavior.TabCapableTTLSeconds = 1800
	}
	if cfg.Behavior.BrowserRegistryTTL <= 0 {
		cfg.Behavior.BrowserRegistryTTL = 1800
	}
	if cfg.Behavior.TCPTableSnapshotMS <= 0 {
		cfg.Behavior.TCPTableSnapshotMS = 200
	}
	if cfg.Behavior.ProcessIdentityTTLSeconds <= 0 {
		cfg.Behavior.ProcessIdentityTTLSeconds = 300
	}
	if cfg.Behavior.ConnectionPIDCacheTTLMS <= 0 {
		cfg.Behavior.ConnectionPIDCacheTTLMS = 1000
	}
	if cfg.Behavior.NegativeProcessCacheTTLMS <= 0 {
		cfg.Behavior.NegativeProcessCacheTTLMS = 75
	}
	if cfg.Behavior.ProcessStateCleanupInterval <= 0 {
		cfg.Behavior.ProcessStateCleanupInterval = 60
	}
	if cfg.Transfer.NormalBufferKB <= 0 {
		cfg.Transfer.NormalBufferKB = 64
	}
	if cfg.Transfer.HighBufferKB <= 0 {
		cfg.Transfer.HighBufferKB = 512
	}
	if cfg.Transfer.HighThroughputWindowSec <= 0 {
		cfg.Transfer.HighThroughputWindowSec = 2
	}
	if cfg.Transfer.HighThroughputBytes <= 0 {
		cfg.Transfer.HighThroughputBytes = 2 * 1024 * 1024
	}
	if cfg.Transfer.LowThroughputWindowSec <= 0 {
		cfg.Transfer.LowThroughputWindowSec = 10
	}
	if cfg.Transfer.LowThroughputBytes <= 0 {
		cfg.Transfer.LowThroughputBytes = 512 * 1024
	}
	if cfg.Transfer.WriteTimeoutSec <= 0 {
		cfg.Transfer.WriteTimeoutSec = 30
	}
	if !jsonHasNestedField(data, "socket", "tcp_nodelay") {
		cfg.Socket.TCPNoDelay = true
	}
	if !jsonHasNestedField(data, "socket", "keepalive") {
		cfg.Socket.KeepAlive = true
	}
	if cfg.Socket.KeepAliveSec <= 0 {
		cfg.Socket.KeepAliveSec = 30
	}
	if cfg.Socket.ReadBufferBytes <= 0 {
		cfg.Socket.ReadBufferBytes = 1024 * 1024
	}
	if cfg.Socket.WriteBufferBytes <= 0 {
		cfg.Socket.WriteBufferBytes = 1024 * 1024
	}
	setTCPSnapshotTTL(time.Duration(cfg.Behavior.TCPTableSnapshotMS) * time.Millisecond)
	setProcessCacheTTLs(
		time.Duration(cfg.Behavior.ConnectionPIDCacheTTLMS)*time.Millisecond,
		time.Duration(cfg.Behavior.NegativeProcessCacheTTLMS)*time.Millisecond,
		time.Duration(cfg.Behavior.ProcessIdentityTTLSeconds)*time.Second,
	)
	if cfg.Groups == nil {
		cfg.Groups = map[string]int{}
	}
	if cfg.Files.DomainRules != "" {
		cfg.DomainRules = readRuleFile(cfg.Files.DomainRules, cfg.Groups)
	}
	if cfg.Files.ProcessRules != "" {
		cfg.ProcessRules = readRuleFile(cfg.Files.ProcessRules, cfg.Groups)
	}
	compiledDomainRules := compileDomainRules(cfg.DomainRules)
	processRules := cfg.ProcessRules
	c.mu.Lock()
	c.config = cfg
	c.domainRules = compiledDomainRules
	c.processRules = processRules
	c.mu.Unlock()
	c.clearProcessRuleCache()
	return nil
}

func readRuleFile(path string, groups map[string]int) []Rule {
	file, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer file.Close()
	var rules []Rule
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := strings.TrimSpace(strings.TrimPrefix(scanner.Text(), "\ufeff"))
		if line == "" || strings.HasPrefix(line, "#") || !strings.Contains(line, ",") {
			continue
		}
		parts := strings.SplitN(line, ",", 2)
		group := strings.TrimSpace(parts[0])
		pattern := strings.ToLower(strings.TrimSpace(parts[1]))
		if group == "" || pattern == "" {
			continue
		}
		if _, ok := groups[group]; !ok {
			continue
		}
		rules = append(rules, Rule{Group: group, Pattern: pattern})
	}
	return rules
}

func jsonHasNestedField(data []byte, keys ...string) bool {
	var current map[string]json.RawMessage
	if err := json.Unmarshal(data, &current); err != nil {
		return false
	}
	for i, key := range keys {
		raw, ok := current[key]
		if !ok {
			return false
		}
		if i == len(keys)-1 {
			return true
		}
		current = map[string]json.RawMessage{}
		if err := json.Unmarshal(raw, &current); err != nil {
			return false
		}
	}
	return false
}

func (c *Core) getConfig() CoreConfig {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.config
}

func (c *Core) apiConnContext(ctx context.Context, conn net.Conn) context.Context {
	cfg := c.getConfig()
	identity := lookupProcessIdentity(conn.RemoteAddr(), cfg.Listen.APIHost, cfg.Listen.APIPort)
	if identityKey(identity) == "" {
		return ctx
	}
	return context.WithValue(ctx, requestIdentityKey, identity)
}

func identityFromRequest(r *http.Request) ProcessIdentity {
	if identity, ok := r.Context().Value(requestIdentityKey).(ProcessIdentity); ok {
		return identity
	}
	return ProcessIdentity{}
}

func identityKey(identity ProcessIdentity) string {
	path := strings.ToLower(strings.TrimSpace(identity.Path))
	if path != "" {
		return path
	}
	return strings.ToLower(strings.TrimSpace(identity.Name))
}

func processInstanceKey(identity ProcessIdentity) string {
	path := strings.ToLower(strings.TrimSpace(identity.Path))
	name := strings.ToLower(strings.TrimSpace(identity.Name))
	if path != "" && identity.PID != 0 && identity.StartTimeUnixNano != 0 {
		return fmt.Sprintf("%s#%d#%d", path, identity.PID, identity.StartTimeUnixNano)
	}
	if path != "" && identity.PID != 0 {
		return fmt.Sprintf("%s#%d", path, identity.PID)
	}
	if path != "" {
		return path
	}
	return name
}

func (c *Core) fallbackRequestIdentity(r *http.Request) ProcessIdentity {
	remoteHost, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil || remoteHost == "" {
		return ProcessIdentity{}
	}
	c.fallbackMu.Lock()
	last := c.reportFallbackAt[r.RemoteAddr]
	if time.Since(last) < 30*time.Second {
		c.fallbackMu.Unlock()
		return ProcessIdentity{}
	}
	c.reportFallbackAt[r.RemoteAddr] = time.Now()
	c.fallbackMu.Unlock()
	cfg := c.getConfig()
	c.reportProcessFallbackLookups.Add(1)
	return lookupProcessIdentity(&net.TCPAddr{IP: net.ParseIP(remoteHost), Port: remotePort(r.RemoteAddr)}, cfg.Listen.APIHost, cfg.Listen.APIPort)
}

func remotePort(remoteAddr string) int {
	_, portText, err := net.SplitHostPort(remoteAddr)
	if err != nil {
		return 0
	}
	port, _ := strconv.Atoi(portText)
	return port
}

func (c *Core) refreshBrowserRegistry(identity ProcessIdentity, register bool, report bool) {
	key := identityKey(identity)
	if key == "" {
		return
	}
	now := time.Now()
	c.browserMu.Lock()
	entry := c.browserRegistry[key]
	if entry == nil {
		entry = &BrowserRegistryEntry{Identity: key}
		c.browserRegistry[key] = entry
	}
	entry.ProcessName = identity.Name
	entry.ExePath = identity.Path
	entry.LastSeen = now
	if register {
		entry.RegisterCount++
	}
	if report {
		entry.ReportCount++
	}
	c.trimBrowserRegistryLocked(now)
	c.browserMu.Unlock()
	if report {
		c.refreshExePathHint(identity)
		c.markProcessTabCapable(identity)
	}
}

func (c *Core) isRegisteredBrowser(identity ProcessIdentity) bool {
	key := identityKey(identity)
	if key == "" {
		return false
	}
	now := time.Now()
	c.browserMu.Lock()
	defer c.browserMu.Unlock()
	c.trimBrowserRegistryLocked(now)
	entry := c.browserRegistry[key]
	if entry == nil {
		return false
	}
	return now.Sub(entry.LastSeen) <= c.browserRegistryTTL()
}

func (c *Core) trimBrowserRegistryLocked(now time.Time) {
	ttl := c.browserRegistryTTL()
	for key, entry := range c.browserRegistry {
		if now.Sub(entry.LastSeen) > ttl {
			delete(c.browserRegistry, key)
		}
	}
}

func (c *Core) browserRegistryTTL() time.Duration {
	cfg := c.getConfig()
	seconds := cfg.Behavior.BrowserRegistryTTL
	if seconds <= 0 {
		seconds = 1800
	}
	return time.Duration(seconds) * time.Second
}

func (c *Core) nonTabTTL() time.Duration {
	cfg := c.getConfig()
	seconds := cfg.Behavior.NonTabTTLSeconds
	if seconds <= 0 {
		seconds = 300
	}
	return time.Duration(seconds) * time.Second
}

func (c *Core) tabCapableTTL() time.Duration {
	cfg := c.getConfig()
	seconds := cfg.Behavior.TabCapableTTLSeconds
	if seconds <= 0 {
		seconds = 1800
	}
	return time.Duration(seconds) * time.Second
}

func (c *Core) tabWaitUnknownMS() int {
	cfg := c.getConfig()
	if cfg.Behavior.TabWaitUnknownMS <= 0 {
		return 120
	}
	return cfg.Behavior.TabWaitUnknownMS
}

func (c *Core) tabWaitUnknownProcessMS() int {
	cfg := c.getConfig()
	if cfg.Behavior.TabWaitUnknownProcessMS <= 0 {
		return 80
	}
	return cfg.Behavior.TabWaitUnknownProcessMS
}

func (c *Core) browserRegistrySize() int {
	c.browserMu.Lock()
	defer c.browserMu.Unlock()
	c.trimBrowserRegistryLocked(time.Now())
	return len(c.browserRegistry)
}

func normalizedExePath(path string) string {
	return strings.ToLower(strings.TrimSpace(path))
}

func (c *Core) refreshExePathHint(identity ProcessIdentity) {
	key := normalizedExePath(identity.Path)
	if key == "" {
		return
	}
	now := time.Now()
	c.processStateMu.Lock()
	defer c.processStateMu.Unlock()
	c.trimExePathHintsLocked(now)
	entry := c.exePathHints[key]
	if entry == nil {
		entry = &ExePathHintEntry{ExePath: identity.Path}
		c.exePathHints[key] = entry
	}
	entry.ExePath = identity.Path
	entry.LastReportTime = now
	entry.LastTabCapableTime = now
	entry.TimeoutCount = 0
	entry.DisabledUntil = time.Time{}
}

func (c *Core) exePathHintStatus(identity ProcessIdentity) (bool, bool, int, string) {
	key := normalizedExePath(identity.Path)
	if key == "" {
		return false, false, 0, "no_exe_path"
	}
	now := time.Now()
	c.processStateMu.Lock()
	defer c.processStateMu.Unlock()
	c.trimExePathHintsLocked(now)
	entry := c.exePathHints[key]
	if entry == nil {
		return false, false, 0, "no_hint"
	}
	if !entry.DisabledUntil.IsZero() && entry.DisabledUntil.After(now) {
		c.exePathHintDisabledCount.Add(1)
		return false, true, entry.TimeoutCount, "disabled"
	}
	if now.Sub(entry.LastReportTime) > exePathHintTTL {
		delete(c.exePathHints, key)
		c.exePathHintExpiredCount.Add(1)
		return false, false, entry.TimeoutCount, "expired"
	}
	return true, false, entry.TimeoutCount, "hit"
}

func (c *Core) recordExePathHintTimeout(identity ProcessIdentity) int {
	key := normalizedExePath(identity.Path)
	if key == "" {
		return 0
	}
	now := time.Now()
	c.processStateMu.Lock()
	defer c.processStateMu.Unlock()
	c.trimExePathHintsLocked(now)
	entry := c.exePathHints[key]
	if entry == nil {
		return 0
	}
	entry.TimeoutCount++
	c.exePathHintTimeoutCount.Add(1)
	if entry.TimeoutCount >= exePathHintTimeoutThreshold {
		entry.DisabledUntil = now.Add(exePathHintDisable)
		c.exePathHintDisabledCount.Add(1)
	}
	return entry.TimeoutCount
}

func (c *Core) trimExePathHintsLocked(now time.Time) {
	for key, entry := range c.exePathHints {
		if now.Sub(entry.LastReportTime) > exePathHintTTL {
			delete(c.exePathHints, key)
			c.exePathHintExpiredCount.Add(1)
		}
	}
}

func (c *Core) exePathHintSize() int {
	now := time.Now()
	c.processStateMu.Lock()
	defer c.processStateMu.Unlock()
	c.trimExePathHintsLocked(now)
	return len(c.exePathHints)
}

func (c *Core) processStateCounts() map[string]int {
	now := time.Now()
	result := map[string]int{
		"Unknown":         0,
		"ProbableNonTab":  0,
		"TabCapable":      0,
		"ConfirmedNonTab": 0,
	}
	c.processStateMu.Lock()
	defer c.processStateMu.Unlock()
	c.trimProcessStatesLocked(now)
	for _, entry := range c.processStates {
		state := c.effectiveProcessStateLocked(entry, now)
		result[state]++
	}
	return result
}

func (c *Core) getOrCreateProcessState(identity ProcessIdentity) (*ProcessStateEntry, string) {
	key := processInstanceKey(identity)
	if key == "" {
		return nil, "Unknown"
	}
	now := time.Now()
	c.processStateMu.Lock()
	defer c.processStateMu.Unlock()
	c.trimProcessStatesLocked(now)
	entry := c.processStates[key]
	if entry == nil {
		entry = &ProcessStateEntry{
			ProcessName:      identity.Name,
			ExePath:          identity.Path,
			PID:              identity.PID,
			ProcessStartTime: identity.StartTimeUnixNano,
			State:            "Unknown",
		}
		c.processStates[key] = entry
	} else if entry.PID != 0 && identity.PID != 0 && (entry.PID != identity.PID || entry.ProcessStartTime != identity.StartTimeUnixNano) {
		c.processRestartDetectedCount.Add(1)
	}
	entry.ProcessName = identity.Name
	entry.ExePath = identity.Path
	entry.PID = identity.PID
	entry.ProcessStartTime = identity.StartTimeUnixNano
	entry.LastSeenProxy = now
	return entry, c.effectiveProcessStateLocked(entry, now)
}

func (c *Core) markProcessTabCapable(identity ProcessIdentity) {
	key := processInstanceKey(identity)
	if key == "" {
		return
	}
	now := time.Now()
	c.processStateMu.Lock()
	entry := c.processStates[key]
	if entry == nil {
		entry = &ProcessStateEntry{State: "Unknown"}
		c.processStates[key] = entry
	}
	entry.ProcessName = identity.Name
	entry.ExePath = identity.Path
	entry.PID = identity.PID
	entry.ProcessStartTime = identity.StartTimeUnixNano
	entry.State = "TabCapable"
	entry.LastSeenReport = now
	entry.TabCapableUntil = now.Add(c.tabCapableTTL())
	entry.ReportCount++
	c.processStateMu.Unlock()
	c.tabCapableMarkCount.Add(1)
}

func (c *Core) markProcessProbableNonTab(identity ProcessIdentity) (time.Time, string) {
	key := processInstanceKey(identity)
	if key == "" {
		return time.Time{}, "Unknown"
	}
	now := time.Now()
	c.processStateMu.Lock()
	defer c.processStateMu.Unlock()
	entry := c.processStates[key]
	if entry == nil {
		entry = &ProcessStateEntry{State: "Unknown"}
		c.processStates[key] = entry
	}
	entry.ProcessName = identity.Name
	entry.ExePath = identity.Path
	entry.PID = identity.PID
	entry.ProcessStartTime = identity.StartTimeUnixNano
	entry.State = "ProbableNonTab"
	entry.WaitTimeoutCount++
	entry.NonTabUntil = now.Add(c.nonTabTTL())
	c.nontabMarkCount.Add(1)
	return entry.NonTabUntil, entry.State
}

func (c *Core) processStateSnapshot(identity ProcessIdentity) (string, time.Time, time.Time) {
	key := processInstanceKey(identity)
	if key == "" {
		return "Unknown", time.Time{}, time.Time{}
	}
	now := time.Now()
	c.processStateMu.Lock()
	defer c.processStateMu.Unlock()
	c.trimProcessStatesLocked(now)
	entry := c.processStates[key]
	if entry == nil {
		return "Unknown", time.Time{}, time.Time{}
	}
	return c.effectiveProcessStateLocked(entry, now), entry.NonTabUntil, entry.TabCapableUntil
}

func (c *Core) effectiveProcessStateLocked(entry *ProcessStateEntry, now time.Time) string {
	switch entry.State {
	case "TabCapable":
		if entry.TabCapableUntil.IsZero() || now.Before(entry.TabCapableUntil) {
			return "TabCapable"
		}
		entry.State = "Unknown"
	case "ProbableNonTab":
		if !entry.NonTabUntil.IsZero() && now.Before(entry.NonTabUntil) {
			return "ProbableNonTab"
		}
		entry.State = "Unknown"
	case "ConfirmedNonTab":
		return "ConfirmedNonTab"
	}
	return "Unknown"
}

func (c *Core) trimProcessStatesLocked(now time.Time) {
	for key, entry := range c.processStates {
		state := c.effectiveProcessStateLocked(entry, now)
		if state == "Unknown" && !entry.LastSeenProxy.IsZero() && now.Sub(entry.LastSeenProxy) > 30*time.Minute && now.Sub(entry.LastSeenReport) > 30*time.Minute {
			delete(c.processStates, key)
		}
	}
}

func (c *Core) tabWaitActiveCount() int {
	c.waiterMu.Lock()
	defer c.waiterMu.Unlock()
	count := 0
	for _, waiters := range c.waiters {
		count += len(waiters)
	}
	return count
}

func (c *Core) handleHealth(w http.ResponseWriter, r *http.Request) {
	if !requireMethod(w, r, http.MethodGet) {
		return
	}
	cfg := c.getConfig()
	_ = json.NewEncoder(w).Encode(map[string]any{
		"ok":          true,
		"boot_id":     c.bootID,
		"started_at":  c.startedAt.Format(time.RFC3339Nano),
		"proxyListen": net.JoinHostPort(cfg.Listen.ProxyHost, strconv.Itoa(cfg.Listen.ProxyPort)),
		"apiListen":   net.JoinHostPort(cfg.Listen.APIHost, strconv.Itoa(cfg.Listen.APIPort)),
	})
}

func (c *Core) handleReport(w http.ResponseWriter, r *http.Request) {
	if !requireMethod(w, r, http.MethodPost) {
		return
	}
	var payload Report
	if r.Body == nil {
		c.reportInvalidCount.Add(1)
		http.Error(w, "empty body", http.StatusBadRequest)
		return
	}
	reader := io.LimitReader(r.Body, maxReportBodyBytes)
	if err := json.NewDecoder(reader).Decode(&payload); err != nil {
		c.reportInvalidCount.Add(1)
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	payload.TabHost = normalizeHost(payload.TabHost)
	payload.RequestHost = normalizeHost(payload.RequestHost)
	if !validReportHost(payload.TabHost) || !validReportHost(payload.RequestHost) {
		c.reportInvalidCount.Add(1)
		http.Error(w, "invalid report", http.StatusBadRequest)
		return
	}

	identity := identityFromRequest(r)
	if identityKey(identity) != "" {
		c.reportProcessContextHit.Add(1)
		c.refreshBrowserRegistry(identity, false, true)
	} else {
		c.reportProcessContextMiss.Add(1)
		identity = c.fallbackRequestIdentity(r)
		if identityKey(identity) != "" {
			c.refreshBrowserRegistry(identity, false, true)
		}
	}

	payload.ProcessName = identity.Name
	payload.ProcessIdentity = identityKey(identity)
	payload.CreatedAt = time.Now()

	c.reportMu.Lock()
	c.reports = append(c.reports, payload)
	c.trimReportsLocked(time.Now())
	c.reportMu.Unlock()
	c.notifyReportWaiters(payload.RequestHost)
	_ = json.NewEncoder(w).Encode(map[string]any{"ok": true})
}

func (c *Core) handleRegisterBrowser(w http.ResponseWriter, r *http.Request) {
	if !requireMethod(w, r, http.MethodPost) {
		return
	}
	// This API is bound to the local core API listener. Registration is only a
	// hint that the caller can provide Tab reports; management APIs with side
	// effects, such as /reload and /close, remain token protected.
	identity := identityFromRequest(r)
	if identityKey(identity) == "" {
		identity = c.fallbackRequestIdentity(r)
	}
	registered := false
	if identityKey(identity) != "" {
		c.refreshBrowserRegistry(identity, true, false)
		registered = true
	}
	_ = json.NewEncoder(w).Encode(map[string]any{
		"ok":           true,
		"registered":   registered,
		"process_name": identity.Name,
		"exe_path":     identity.Path,
	})
}

func (c *Core) handleMetrics(w http.ResponseWriter, r *http.Request) {
	if !requireMethod(w, r, http.MethodGet) {
		return
	}
	c.connMu.RLock()
	activeByGroup := map[string]int{}
	idleConnectionCount := 0
	cfgForIdle := c.getConfig()
	idleThreshold := time.Duration(cfgForIdle.Transfer.IdleTimeoutSec) * time.Second
	nowForIdle := time.Now()
	for _, info := range c.conns {
		activeByGroup[info.Group]++
		if idleThreshold > 0 {
			lastActive := info.LastActiveAt.Load()
			if lastActive > 0 && nowForIdle.Sub(time.Unix(0, lastActive)) >= idleThreshold {
				idleConnectionCount++
			}
		}
	}
	activeCount := len(c.conns)
	c.connMu.RUnlock()
	c.statsMu.RLock()
	traffic := map[string]TrafficStats{}
	for group, stats := range c.traffic {
		copyStats := *stats
		copyStats.window = nil
		traffic[group] = copyStats
	}
	failuresByGroup := cloneNestedCounterMap(c.failuresByGroup)
	failuresByProcess := cloneNestedCounterMap(c.failuresByProcess)
	failuresByHost := cloneNestedCounterMap(c.failuresByHost)
	finalResultByReason := make(map[string]int64, len(c.finalResultByReason))
	for reason, count := range c.finalResultByReason {
		finalResultByReason[reason] = count
	}
	c.statsMu.RUnlock()
	processMetrics := getProcessLookupMetrics()
	stateCounts := c.processStateCounts()
	droppedLogCount, droppedLogByFile, logQueueLen, logQueueCap := c.logManager.metrics()
	_ = json.NewEncoder(w).Encode(map[string]any{
		"ok":                                            true,
		"active":                                        activeCount,
		"activeByGroup":                                 activeByGroup,
		"traffic":                                       traffic,
		"tcp_table_refresh_count":                       processMetrics.TCPTableRefreshCount,
		"tcp_table_forced_refresh_count":                processMetrics.TCPTableForcedRefreshCount,
		"tcp_table_refresh_wait_count":                  processMetrics.TCPTableRefreshWaitCount,
		"process_lookup_cache_hit":                      processMetrics.ProcessLookupCacheHit,
		"process_lookup_cache_miss":                     processMetrics.ProcessLookupCacheMiss,
		"process_lookup_hit_count":                      processMetrics.ProcessLookupHitCount,
		"process_lookup_miss_count":                     processMetrics.ProcessLookupMissCount,
		"report_process_context_hit":                    c.reportProcessContextHit.Load(),
		"report_process_context_miss":                   c.reportProcessContextMiss.Load(),
		"report_process_fallback_lookup_count":          c.reportProcessFallbackLookups.Load(),
		"report_invalid_count":                          c.reportInvalidCount.Load(),
		"browser_registry_size":                         c.browserRegistrySize(),
		"tab_wait_active":                               c.tabWaitActiveCount(),
		"tab_wait_hit_count":                            c.tabWaitHitCount.Load(),
		"tab_wait_timeout_count":                        c.tabWaitTimeoutCount.Load(),
		"tab_wait_skipped_domain_hit_count":             c.tabWaitSkippedDomainHitCount.Load(),
		"tab_wait_skipped_not_registered_browser_count": c.tabWaitSkippedNotBrowserCount.Load(),
		"process_lookup_zero_pid_count":                 processMetrics.ProcessLookupZeroPIDCount,
		"process_lookup_retry_count":                    processMetrics.ProcessLookupRetryCount,
		"process_lookup_retry_success_count":            processMetrics.ProcessLookupRetrySuccessCount,
		"process_lookup_negative_cache_count":           processMetrics.ProcessLookupNegativeCacheCount,
		"process_state_unknown_count":                   stateCounts["Unknown"],
		"process_state_probable_nontab_count":           stateCounts["ProbableNonTab"],
		"process_state_tab_capable_count":               stateCounts["TabCapable"],
		"process_state_confirmed_nontab_count":          stateCounts["ConfirmedNonTab"],
		"nontab_cache_hit_count":                        c.nontabCacheHitCount.Load(),
		"nontab_mark_count":                             c.nontabMarkCount.Load(),
		"tab_capable_mark_count":                        c.tabCapableMarkCount.Load(),
		"exe_path_hint_size":                            c.exePathHintSize(),
		"exe_path_hint_hit_count":                       c.exePathHintHitCount.Load(),
		"exe_path_hint_timeout_count":                   c.exePathHintTimeoutCount.Load(),
		"exe_path_hint_disabled_count":                  c.exePathHintDisabledCount.Load(),
		"exe_path_hint_expired_count":                   c.exePathHintExpiredCount.Load(),
		"process_restart_detected_count":                c.processRestartDetectedCount.Load(),
		"large_flow_active":                             c.largeFlowActive.Load(),
		"large_flow_total":                              c.largeFlowTotal.Load(),
		"high_throughput_enabled_count":                 c.highThroughputEnabledCount.Load(),
		"high_throughput_disabled_count":                c.highThroughputDisabledCount.Load(),
		"active_high_throughput_directions":             c.activeHighThroughputDirections.Load(),
		"transfer_bytes_in_total":                       c.transferBytesInTotal.Load(),
		"transfer_bytes_out_total":                      c.transferBytesOutTotal.Load(),
		"failure_no_response_count":                     c.failureNoResponseCount.Load(),
		"failure_quick_close_low_bytes_count":           c.failureQuickCloseLowBytesCount.Load(),
		"failure_write_timeout_count":                   c.failureWriteTimeoutCount.Load(),
		"failure_direct_write_count":                    c.failureDirectWriteCount.Load(),
		"failure_listener_connect_count":                c.failureListenerConnectCount.Load(),
		"failure_listener_write_count":                  c.failureListenerWriteCount.Load(),
		"failure_relay_error_count":                     c.failureRelayErrorCount.Load(),
		"failure_relay_shutdown_timeout_count":          c.failureRelayShutdownTimeout.Load(),
		"final_result_ok_count":                         c.finalResultOKCount.Load(),
		"final_result_failure_count":                    c.finalResultFailureCount.Load(),
		"final_result_by_reason":                        finalResultByReason,
		"duplicate_result_suppressed_count":             c.duplicateResultSuppressedCount.Load(),
		"failures_by_group":                             failuresByGroup,
		"failures_by_process":                           failuresByProcess,
		"failures_by_host":                              failuresByHost,
		"paused_groups":                                 c.pausedGroupNames(),
		"group_pause_count":                             c.groupPauseCount.Load(),
		"idle_connection_count":                         idleConnectionCount,
		"dropped_log_count":                             droppedLogCount,
		"dropped_log_by_file":                           droppedLogByFile,
		"log_queue_len":                                 logQueueLen,
		"log_queue_capacity":                            logQueueCap,
		"normal_buffer_pool_get":                        c.normalBufferPoolGet.Load(),
		"normal_buffer_pool_put":                        c.normalBufferPoolPut.Load(),
		"high_buffer_pool_get":                          c.highBufferPoolGet.Load(),
		"high_buffer_pool_put":                          c.highBufferPoolPut.Load(),
		"active_high_buffer_count":                      c.activeHighBufferCount.Load(),
	})
}

func (c *Core) handleEvents(w http.ResponseWriter, r *http.Request) {
	if !requireMethod(w, r, http.MethodGet) {
		return
	}
	afterID, _ := strconv.ParseUint(r.URL.Query().Get("after_id"), 10, 64)
	limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
	if limit <= 0 || limit > 200 {
		limit = 100
	}
	c.eventMu.RLock()
	items := make([]CoreEvent, 0, len(c.events))
	for _, event := range c.events {
		if event.ID > afterID {
			items = append(items, event)
		}
	}
	c.eventMu.RUnlock()
	if len(items) > limit {
		items = items[len(items)-limit:]
	}
	_ = json.NewEncoder(w).Encode(map[string]any{
		"ok":         true,
		"boot_id":    c.bootID,
		"started_at": c.startedAt.Format(time.RFC3339Nano),
		"events":     items,
	})
}

func (c *Core) handleConnections(w http.ResponseWriter, r *http.Request) {
	if !requireMethod(w, r, http.MethodGet) {
		return
	}
	c.connMu.RLock()
	items := make([]map[string]any, 0, len(c.conns))
	now := time.Now()
	for _, info := range c.conns {
		duration := now.Sub(info.CreatedAt).Seconds()
		if duration <= 0 {
			duration = 1
		}
		bytesIn := atomic.LoadInt64(&info.BytesIn)
		bytesOut := atomic.LoadInt64(&info.BytesOut)
		lastActiveUnix := info.LastActiveAt.Load()
		idleSeconds := int64(0)
		if lastActiveUnix > 0 {
			idleSeconds = int64(now.Sub(time.Unix(0, lastActiveUnix)).Seconds())
		}
		items = append(items, map[string]any{
			"id":                  info.ID,
			"group":               info.Group,
			"host":                info.Host,
			"process":             info.Process,
			"created_at":          info.CreatedAt.Format(time.RFC3339),
			"last_active_at":      time.Unix(0, lastActiveUnix).Format(time.RFC3339),
			"idle_seconds":        idleSeconds,
			"duration":            int(duration),
			"bytes_in":            bytesIn,
			"bytes_out":           bytesOut,
			"high_throughput_in":  info.HighIn.Load(),
			"high_throughput_out": info.HighOut.Load(),
			"current_buffer_in":   info.BufferIn.Load(),
			"current_buffer_out":  info.BufferOut.Load(),
			"throughput_in_bps":   int64(float64(bytesIn) / duration),
			"throughput_out_bps":  int64(float64(bytesOut) / duration),
		})
	}
	c.connMu.RUnlock()
	_ = json.NewEncoder(w).Encode(map[string]any{
		"ok":          true,
		"connections": items,
	})
}

func (c *Core) handleReload(w http.ResponseWriter, r *http.Request) {
	if !requireMethod(w, r, http.MethodPost) || !c.requireToken(w, r) {
		return
	}
	if err := c.loadConfig(); err != nil {
		c.logger.Printf("reload config failed: %v", err)
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	c.clearReports()
	c.logger.Printf("config reloaded")
	c.addEvent(CoreEvent{Source: "core", SourceReason: "config_reloaded", Action: "reload"})
	_ = json.NewEncoder(w).Encode(map[string]any{"ok": true})
}

func (c *Core) handleClose(w http.ResponseWriter, r *http.Request) {
	if !requireMethod(w, r, http.MethodPost) || !c.requireToken(w, r) {
		return
	}
	var req struct {
		Groups    []string `json:"groups"`
		Hosts     []string `json:"hosts"`
		Processes []string `json:"processes"`
	}
	if r.Body != nil {
		_ = json.NewDecoder(r.Body).Decode(&req)
	}
	closed := c.closeConnections(req.Groups, req.Hosts, req.Processes)
	_ = json.NewEncoder(w).Encode(map[string]any{"ok": true, "closed": closed})
}

func (c *Core) handlePauseGroups(w http.ResponseWriter, r *http.Request) {
	if !requireMethod(w, r, http.MethodPost) || !c.requireToken(w, r) {
		return
	}
	var req struct {
		Groups []string `json:"groups"`
		HoldMS int      `json:"hold_ms"`
	}
	_ = json.NewDecoder(r.Body).Decode(&req)
	if req.HoldMS <= 0 {
		req.HoldMS = 800
	}
	until := time.Now().Add(time.Duration(req.HoldMS) * time.Millisecond)
	paused := c.pauseGroups(req.Groups, until)
	_ = json.NewEncoder(w).Encode(map[string]any{"ok": true, "paused": paused})
}

func (c *Core) handleResumeGroups(w http.ResponseWriter, r *http.Request) {
	if !requireMethod(w, r, http.MethodPost) || !c.requireToken(w, r) {
		return
	}
	var req struct {
		Groups []string `json:"groups"`
	}
	_ = json.NewDecoder(r.Body).Decode(&req)
	resumed := c.resumeGroups(req.Groups)
	_ = json.NewEncoder(w).Encode(map[string]any{"ok": true, "resumed": resumed})
}

func requireMethod(w http.ResponseWriter, r *http.Request, method string) bool {
	if r.Method == method {
		return true
	}
	w.Header().Set("Allow", method)
	http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	return false
}

func (c *Core) requireToken(w http.ResponseWriter, r *http.Request) bool {
	cfg := c.getConfig()
	token := strings.TrimSpace(cfg.Security.Token)
	if token == "" {
		http.Error(w, "core token is not configured", http.StatusForbidden)
		return false
	}
	given := strings.TrimSpace(r.Header.Get("X-ContextProxy-Token"))
	if c.validateToken(given) {
		return true
	}
	http.Error(w, "forbidden", http.StatusForbidden)
	return false
}

func (c *Core) validateToken(given string) bool {
	cfg := c.getConfig()
	token := strings.TrimSpace(cfg.Security.Token)
	if token == "" || strings.TrimSpace(given) == "" {
		return false
	}
	return subtle.ConstantTimeCompare([]byte(strings.TrimSpace(given)), []byte(token)) == 1
}

func (c *Core) handleProxyConn(client net.Conn) {
	defer client.Close()
	c.tuneSocket(client)
	_ = client.SetReadDeadline(time.Now().Add(5 * time.Second))
	reader := bufio.NewReaderSize(client, 64*1024)
	header, firstLine, headers, err := readProxyHeader(reader)
	_ = client.SetReadDeadline(time.Time{})
	if err != nil {
		c.writeLog("tcp.log", fmt.Sprintf("read request failed: %v", err))
		return
	}
	host, port, isConnect := parseTarget(firstLine, headers)
	if host == "" || port <= 0 {
		return
	}
	if isLocalAddress(host) {
		decision := RouteDecision{Group: directGroup, Source: "direct", SourceReason: "local_address_direct", TabWaitResult: "skipped_local_address"}
		id := c.registerConn(client, host, directGroup, "")
		defer c.unregisterConn(id)
		c.writeLog("routing.log", fmt.Sprintf("route host=%s process= group=%s source=direct reason=local_address_direct tab_wait_result=skipped_local_address", host, directGroup))
		c.addEvent(c.eventFromDecision(decision, host, "", directGroup, net.JoinHostPort(host, strconv.Itoa(port)), "", "route"))
		if isConnect {
			c.handleDirectConnect(client, reader, host, port, id)
		} else {
			c.handleDirectHTTP(client, reader, header, firstLine, headers, host, port, id)
		}
		return
	}
	cfg := c.getConfig()
	processLookup := lookupProcessIdentityResult(client.RemoteAddr(), cfg.Listen.ProxyHost, cfg.Listen.ProxyPort)
	processIdentity := processLookup.Identity
	processName := processIdentity.Name
	decision := c.decideGroup(host, processLookup)
	group := decision.Group
	id := c.registerConn(client, host, group, processName)
	defer c.unregisterConn(id)
	c.logger.Printf("route host=%s process=%s group=%s source=%s reason=%s tab=%s tab_wait=%s/%dms", host, processName, group, decision.Source, decision.SourceReason, decision.TabHost, decision.TabWaitResult, decision.TabWaitMS)
	c.writeLog("routing.log", fmt.Sprintf("route host=%s process=%s process_identity=%s process_identity_key=%s process_lookup_result=%s process_lookup_retry_count=%d process_cache_hit=%t process_state=%s group=%s source=%s reason=%s tab=%s matched_pattern=%s process_rule_matched=%t process_matched_pattern=%s is_registered_browser=%t browser_registry_hit=%t browser_registry_identity=%s tab_wait_ms=%d tab_wait_result=%s report_process_name=%s report_process_identity=%s exe_path_hint_hit=%t exe_path_hint_disabled=%t exe_path_hint_timeout_count=%d exe_path_hint_reason=%s", host, processName, decision.ProcessIdentity, decision.ProcessIdentityKey, decision.ProcessLookupResult, decision.ProcessLookupRetryCount, decision.ProcessCacheHit, decision.ProcessState, group, decision.Source, decision.SourceReason, decision.TabHost, decision.MatchedPattern, decision.ProcessRuleMatched, decision.ProcessMatchedPattern, decision.IsRegisteredBrowser, decision.BrowserRegistryHit, decision.BrowserRegistryIdentity, decision.TabWaitMS, decision.TabWaitResult, decision.ReportProcessName, decision.ReportProcessIdentity, decision.ExePathHintHit, decision.ExePathHintDisabled, decision.ExePathHintTimeoutCount, decision.ExePathHintReason))

	if group == directGroup {
		c.addEvent(c.eventFromDecision(decision, host, processName, group, net.JoinHostPort(host, strconv.Itoa(port)), "", "route"))
		if isConnect {
			c.handleDirectConnect(client, reader, host, port, id)
		} else {
			c.handleDirectHTTP(client, reader, header, firstLine, headers, host, port, id)
		}
		return
	}

	groupPort, ok := cfg.Groups[group]
	if !ok || groupPort <= 0 {
		c.logger.Printf("missing listener port for group=%s host=%s", group, host)
		errorDecision := decision
		errorDecision.SourceReason = "missing_listener"
		c.addEvent(c.eventFromDecision(errorDecision, host, processName, group, net.JoinHostPort(host, strconv.Itoa(port)), "", "error"))
		return
	}
	listener := net.JoinHostPort("127.0.0.1", strconv.Itoa(groupPort))
	c.addEvent(c.eventFromDecision(decision, host, processName, group, net.JoinHostPort(host, strconv.Itoa(port)), listener, "route"))
	c.handleGroupForward(client, reader, header, group, groupPort, id)
}

func readProxyHeader(reader *bufio.Reader) ([]byte, string, map[string]string, error) {
	var buf bytes.Buffer
	for {
		line, err := reader.ReadString('\n')
		if err != nil {
			return nil, "", nil, err
		}
		buf.WriteString(line)
		if buf.Len() > 128*1024 {
			return nil, "", nil, errors.New("request header too large")
		}
		if line == "\r\n" || line == "\n" {
			break
		}
	}
	raw := buf.Bytes()
	text := strings.ReplaceAll(string(raw), "\r\n", "\n")
	lines := strings.Split(text, "\n")
	firstLine := strings.TrimSpace(lines[0])
	headers := map[string]string{}
	for _, line := range lines[1:] {
		line = strings.TrimSpace(line)
		if line == "" || !strings.Contains(line, ":") {
			continue
		}
		parts := strings.SplitN(line, ":", 2)
		headers[strings.ToLower(strings.TrimSpace(parts[0]))] = strings.TrimSpace(parts[1])
	}
	return raw, firstLine, headers, nil
}

func parseTarget(firstLine string, headers map[string]string) (string, int, bool) {
	parts := strings.Fields(firstLine)
	if len(parts) < 2 {
		return "", 0, false
	}
	if strings.EqualFold(parts[0], "CONNECT") {
		host, port := splitHostPortDefault(parts[1], 443)
		return normalizeHost(host), port, true
	}
	target := parts[1]
	if strings.HasPrefix(target, "http://") || strings.HasPrefix(target, "https://") {
		parsed, err := url.Parse(target)
		if err == nil && parsed.Hostname() != "" {
			port := parsed.Port()
			if port == "" {
				if parsed.Scheme == "https" {
					return normalizeHost(parsed.Hostname()), 443, false
				}
				return normalizeHost(parsed.Hostname()), 80, false
			}
			p, _ := strconv.Atoi(port)
			return normalizeHost(parsed.Hostname()), p, false
		}
	}
	hostHeader := headers["host"]
	host, port := splitHostPortDefault(hostHeader, 80)
	return normalizeHost(host), port, false
}

func splitHostPortDefault(value string, defaultPort int) (string, int) {
	value = strings.TrimSpace(value)
	if h, p, err := net.SplitHostPort(value); err == nil {
		port, _ := strconv.Atoi(p)
		return h, port
	}
	if strings.Contains(value, ":") {
		parts := strings.Split(value, ":")
		last := parts[len(parts)-1]
		if port, err := strconv.Atoi(last); err == nil {
			return strings.Join(parts[:len(parts)-1], ":"), port
		}
	}
	return value, defaultPort
}

func normalizeHost(host string) string {
	return normalizeDomainInput(host)
}

func validReportHost(host string) bool {
	if host == "" || len(host) > maxReportHostLength {
		return false
	}
	if strings.ContainsAny(host, " \t\r\n/\\?#") {
		return false
	}
	return true
}

func isLocalAddress(host string) bool {
	host = normalizeHost(host)
	if host == "localhost" {
		return true
	}
	ip := net.ParseIP(host)
	if ip == nil {
		return false
	}
	return ip.IsLoopback() || ip.IsUnspecified()
}

func normalizeDomainInput(value string) string {
	value = strings.ToLower(strings.TrimSpace(value))
	if value == "" {
		return ""
	}
	if strings.Contains(value, "://") {
		if parsed, err := url.Parse(value); err == nil && parsed.Hostname() != "" {
			value = parsed.Hostname()
		}
	} else {
		for _, sep := range []string{"/", "?", "#"} {
			if idx := strings.Index(value, sep); idx >= 0 {
				value = value[:idx]
			}
		}
		if host, _, err := net.SplitHostPort(value); err == nil {
			value = host
		} else if strings.Count(value, ":") == 1 {
			host, port, found := strings.Cut(value, ":")
			if found {
				if _, err := strconv.Atoi(port); err == nil {
					value = host
				}
			}
		}
	}
	value = strings.Trim(value, "[]")
	value = strings.TrimSuffix(value, ".")
	return value
}

func stripWWW(value string) string {
	value = normalizeDomainInput(value)
	return strings.TrimPrefix(value, "www.")
}

func (c *Core) decideGroup(requestHost string, lookup ProcessLookupResult) RouteDecision {
	cfg := c.getConfig()
	processIdentity := lookup.Identity
	browserIdentity := identityKey(processIdentity)
	browserHit := c.isRegisteredBrowser(processIdentity)
	base := c.baseDecisionForLookup(lookup)
	base.IsRegisteredBrowser = browserHit
	base.BrowserRegistryHit = browserHit
	base.BrowserRegistryIdentity = browserIdentity
	if group, pattern, reason := c.matchDomainRule(requestHost); group != "" {
		if _, ok := cfg.Groups[group]; ok {
			c.tabWaitSkippedDomainHitCount.Add(1)
			base.Group = group
			base.Source = "domain"
			base.SourceReason = reason
			base.MatchedPattern = pattern
			base.TabWaitResult = "skipped_domain_hit"
			return base
		}
	}

	if report, ok := c.matchRecentTabReport(requestHost); ok {
		if decision, matched := c.decisionFromTabReport(requestHost, report, base, "hit_existing_report", 0); matched {
			c.tabWaitHitCount.Add(1)
			return decision
		}
		base.TabHost = report.TabHost
		base.ReportProcessName = report.ProcessName
		base.ReportProcessIdentity = report.ProcessIdentity
		base.TabWaitResult = "report_no_rule"
	}

	if c.tabWaitEnabled() {
		waitMS, state, nonTabUntil, tabCapableUntil, exeHint, exeHintDisabled, exeHintTimeoutCount, exeHintReason := c.tabWaitPlan(processIdentity, browserHit)
		base.ProcessState = state
		base.NonTabUntilUnix = unixOrZero(nonTabUntil)
		base.TabCapableUntilUnix = unixOrZero(tabCapableUntil)
		base.ExePathHintHit = exeHint
		base.ExePathHintDisabled = exeHintDisabled
		base.ExePathHintTimeoutCount = exeHintTimeoutCount
		base.ExePathHintReason = exeHintReason
		if waitMS > 0 {
			tabHost, waitResult, elapsedMS, report := c.waitForTabReport(requestHost, time.Duration(waitMS)*time.Millisecond)
			if tabHost != "" {
				hitResult := "hit_unknown_probe"
				switch state {
				case "TabCapable":
					hitResult = "hit_tab_capable"
				case "UnknownProcess":
					hitResult = "hit_unknown_process"
				}
				if exeHint {
					hitResult = "hit_exe_path_hint"
				}
				if decision, matched := c.decisionFromTabReport(requestHost, report, base, hitResult, elapsedMS); matched {
					c.tabWaitHitCount.Add(1)
					return decision
				}
				waitResult = "report_no_rule"
			}
			base.TabWaitMS = elapsedMS
			base.TabHost = tabHost
			base.ReportProcessName = report.ProcessName
			base.ReportProcessIdentity = report.ProcessIdentity
			base.TabWaitResult = c.timeoutResultForState(waitResult, state, processIdentity, exeHint)
			if strings.HasPrefix(base.TabWaitResult, "timeout") {
				c.tabWaitTimeoutCount.Add(1)
			}
			return c.decideAfterTabWait(requestHost, processIdentity, base)
		}
		base.TabWaitResult = c.skipResultForState(state)
		if base.ExePathHintDisabled {
			base.TabWaitResult = "skipped_exe_path_hint_disabled"
		}
	} else {
		base.TabWaitResult = "skipped_disabled"
	}
	if base.TabWaitResult == "skipped_probable_nontab_cache" {
		c.nontabCacheHitCount.Add(1)
	}
	if base.TabWaitResult == "skipped_not_registered_browser" {
		c.tabWaitSkippedNotBrowserCount.Add(1)
	}

	return c.decideAfterTabWait(requestHost, processIdentity, base)
}

func (c *Core) baseDecisionForLookup(lookup ProcessLookupResult) RouteDecision {
	identity := lookup.Identity
	return RouteDecision{
		ProcessLookupResult:     lookup.Result,
		ProcessLookupRetryCount: lookup.RetryCount,
		ProcessIdentity:         identityKey(identity),
		ProcessIdentityKey:      processInstanceKey(identity),
		ProcessCacheHit:         lookup.CacheHit,
	}
}

func (c *Core) decisionFromTabReport(requestHost string, report Report, base RouteDecision, waitResult string, elapsedMS int) (RouteDecision, bool) {
	cfg := c.getConfig()
	if report.TabHost == "" {
		return base, false
	}
	if group, pattern, _ := c.matchDomainRule(report.TabHost); group != "" {
		if _, ok := cfg.Groups[group]; ok {
			base.Group = group
			base.Source = "tab"
			base.SourceReason = "tab_hit"
			base.TabHost = report.TabHost
			base.MatchedPattern = pattern
			base.TabWaitMS = elapsedMS
			base.TabWaitResult = waitResult
			base.ReportProcessName = report.ProcessName
			base.ReportProcessIdentity = report.ProcessIdentity
			return base, true
		}
	}
	_ = requestHost
	return base, false
}

func (c *Core) decideAfterTabWait(requestHost string, processIdentity ProcessIdentity, base RouteDecision) RouteDecision {
	cfg := c.getConfig()
	if group, pattern, matched := c.matchProcessRuleForIdentity(processIdentity); matched {
		if _, ok := cfg.Groups[group]; ok {
			base.Group = group
			base.Source = "process"
			base.SourceReason = "process_hit"
			base.MatchedPattern = pattern
			base.ProcessRuleMatched = true
			base.ProcessMatchedPattern = pattern
			if strings.HasPrefix(base.TabWaitResult, "timeout") {
				base.TabWaitResult = "timeout_then_process"
			}
			return base
		}
	}

	reason := "direct_default"
	if base.TabWaitResult == "report_no_rule" {
		reason = "tab_report_no_rule"
	}
	base.Group = directGroup
	base.Source = "direct"
	base.SourceReason = reason
	if strings.HasPrefix(base.TabWaitResult, "timeout") {
		base.TabWaitResult = "timeout_then_direct"
	}
	return base
}

func (c *Core) matchRecentTabReport(requestHost string) (Report, bool) {
	now := time.Now()
	c.reportMu.Lock()
	defer c.reportMu.Unlock()
	c.trimReportsLocked(now)
	for i := len(c.reports) - 1; i >= 0; i-- {
		report := c.reports[i]
		if report.RequestHost == requestHost && now.Sub(report.CreatedAt) <= 3*time.Second {
			return report, true
		}
	}
	return Report{}, false
}

func (c *Core) trimReportsLocked(now time.Time) {
	cutoff := now.Add(-15 * time.Second)
	keep := c.reports[:0]
	for _, report := range c.reports {
		if report.CreatedAt.After(cutoff) {
			keep = append(keep, report)
		}
	}
	if len(keep) > 500 {
		keep = keep[len(keep)-500:]
	}
	c.reports = keep
}

func (c *Core) clearReports() {
	c.reportMu.Lock()
	c.reports = nil
	c.reportMu.Unlock()
	c.notifyAllReportWaiters()
}

func (c *Core) waitForTabReport(requestHost string, timeout time.Duration) (string, string, int, Report) {
	start := time.Now()
	if report, ok := c.matchRecentTabReport(requestHost); ok {
		return report.TabHost, "hit", int(time.Since(start).Milliseconds()), report
	}

	if timeout <= 0 {
		return "", "timeout", 0, Report{}
	}

	ch := make(chan struct{}, 1)
	key := normalizeHost(requestHost)
	c.waiterMu.Lock()
	c.waiters[key] = append(c.waiters[key], ch)
	c.waiterMu.Unlock()
	defer c.removeReportWaiter(key, ch)

	timer := time.NewTimer(timeout)
	defer timer.Stop()
	select {
	case <-ch:
		if report, ok := c.matchRecentTabReport(requestHost); ok {
			return report.TabHost, "hit", int(time.Since(start).Milliseconds()), report
		}
		return "", "timeout", int(time.Since(start).Milliseconds()), Report{}
	case <-timer.C:
		return "", "timeout", int(time.Since(start).Milliseconds()), Report{}
	}
}

func (c *Core) tabWaitPlan(identity ProcessIdentity, browserHit bool) (int, string, time.Time, time.Time, bool, bool, int, string) {
	if identityKey(identity) == "" {
		return c.tabWaitUnknownProcessMS(), "UnknownProcess", time.Time{}, time.Time{}, false, false, 0, "no_identity"
	}
	state, nonTabUntil, tabCapableUntil := c.processStateSnapshot(identity)
	if browserHit && state != "TabCapable" {
		state = "TabCapable"
		tabCapableUntil = time.Now().Add(c.tabCapableTTL())
	}
	switch state {
	case "TabCapable":
		return c.tabWaitBrowserMS(), state, nonTabUntil, tabCapableUntil, false, false, 0, "process_tab_capable"
	case "ProbableNonTab":
		return 0, state, nonTabUntil, tabCapableUntil, false, false, 0, "probable_nontab"
	case "ConfirmedNonTab":
		return 0, state, nonTabUntil, tabCapableUntil, false, false, 0, "confirmed_nontab"
	default:
		hit, disabled, timeoutCount, reason := c.exePathHintStatus(identity)
		if hit {
			c.exePathHintHitCount.Add(1)
			return c.tabWaitUnknownMS(), "Unknown", nonTabUntil, tabCapableUntil, true, false, timeoutCount, reason
		}
		return c.tabWaitUnknownMS(), "Unknown", nonTabUntil, tabCapableUntil, false, disabled, timeoutCount, reason
	}
}

func (c *Core) timeoutResultForState(waitResult string, state string, identity ProcessIdentity, exeHint bool) string {
	if waitResult == "report_no_rule" {
		return waitResult
	}
	switch state {
	case "TabCapable":
		return "timeout_tab_capable"
	case "UnknownProcess":
		return "timeout_then_direct"
	case "Unknown":
		nonTabUntil, newState := c.markProcessProbableNonTab(identity)
		_ = nonTabUntil
		_ = newState
		if exeHint {
			c.recordExePathHintTimeout(identity)
			return "timeout_exe_path_hint"
		}
		return "timeout_unknown_mark_probable_nontab"
	default:
		return "timeout"
	}
}

func (c *Core) skipResultForState(state string) string {
	switch state {
	case "ProbableNonTab":
		return "skipped_probable_nontab_cache"
	case "ConfirmedNonTab":
		return "skipped_confirmed_nontab"
	default:
		return "skipped_not_registered_browser"
	}
}

func unixOrZero(value time.Time) int64 {
	if value.IsZero() {
		return 0
	}
	return value.Unix()
}

func (c *Core) notifyReportWaiters(requestHost string) {
	key := normalizeHost(requestHost)
	c.waiterMu.Lock()
	waiters := append([]chan struct{}(nil), c.waiters[key]...)
	c.waiterMu.Unlock()
	for _, ch := range waiters {
		select {
		case ch <- struct{}{}:
		default:
		}
	}
}

func (c *Core) notifyAllReportWaiters() {
	c.waiterMu.Lock()
	var waiters []chan struct{}
	for key, items := range c.waiters {
		waiters = append(waiters, items...)
		delete(c.waiters, key)
	}
	c.waiterMu.Unlock()
	for _, ch := range waiters {
		select {
		case ch <- struct{}{}:
		default:
		}
	}
}

func (c *Core) removeReportWaiter(key string, ch chan struct{}) {
	c.waiterMu.Lock()
	defer c.waiterMu.Unlock()
	items := c.waiters[key]
	for i, item := range items {
		if item == ch {
			items = append(items[:i], items[i+1:]...)
			break
		}
	}
	if len(items) == 0 {
		delete(c.waiters, key)
		return
	}
	c.waiters[key] = items
}

func (c *Core) tabWaitEnabled() bool {
	cfg := c.getConfig()
	return cfg.Behavior.TabWaitEnabled
}

func (c *Core) tabWaitBrowserMS() int {
	cfg := c.getConfig()
	if cfg.Behavior.TabWaitBrowserMS <= 0 {
		return 250
	}
	return cfg.Behavior.TabWaitBrowserMS
}

func (c *Core) matchDomainRule(host string) (string, string, string) {
	if group, pattern, wwwNormalized := c.matchCompiledDomainRules(host); group != "" {
		reason := "domain_hit"
		if wwwNormalized {
			reason = "domain_hit_www_normalized"
		}
		return group, pattern, reason
	}
	return "", "", ""
}

func (c *Core) matchProcessRule(value string) (string, string) {
	value = strings.ToLower(strings.TrimSpace(value))
	c.mu.RLock()
	rules := append([]Rule(nil), c.processRules...)
	c.mu.RUnlock()
	for _, rule := range rules {
		if matchPattern(value, rule.Pattern) {
			return rule.Group, rule.Pattern
		}
	}
	return "", ""
}

func (c *Core) clearProcessRuleCache() {
	c.processRuleMu.Lock()
	c.processRuleCache = make(map[string]ProcessRuleCacheEntry)
	c.processRuleMu.Unlock()
}

func (c *Core) matchProcessRuleForIdentity(identity ProcessIdentity) (string, string, bool) {
	key := processRuleCacheKey(identity)
	if key == "" {
		return "", "", false
	}
	c.processRuleMu.RLock()
	if entry, ok := c.processRuleCache[key]; ok {
		c.processRuleMu.RUnlock()
		return entry.Group, entry.Pattern, entry.Matched
	}
	c.processRuleMu.RUnlock()

	candidates := []string{
		strings.ToLower(strings.TrimSpace(identity.Path)),
		strings.ToLower(strings.TrimSpace(identity.Name)),
		strings.ToLower(filepath.Base(identity.Path)),
	}
	candidates = uniquePlainStrings(candidates)
	group, pattern, matched := "", "", false
	for _, candidate := range candidates {
		if candidate == "" {
			continue
		}
		if g, p := c.matchProcessRule(candidate); g != "" {
			group, pattern, matched = g, p, true
			break
		}
	}

	c.processRuleMu.Lock()
	c.processRuleCache[key] = ProcessRuleCacheEntry{Group: group, Pattern: pattern, Matched: matched}
	c.processRuleMu.Unlock()
	return group, pattern, matched
}

func processRuleCacheKey(identity ProcessIdentity) string {
	path := strings.ToLower(strings.TrimSpace(identity.Path))
	if path != "" {
		return "path:" + path
	}
	name := strings.ToLower(strings.TrimSpace(identity.Name))
	if name != "" {
		return "name:" + name
	}
	return ""
}

func (c *Core) matchCompiledDomainRules(host string) (string, string, bool) {
	host = normalizeDomainInput(host)
	if host == "" {
		return "", "", false
	}
	hosts := []string{host}
	hostNoWWW := stripWWW(host)
	if hostNoWWW != host {
		hosts = append(hosts, hostNoWWW)
	}
	hosts = uniqueStrings(hosts)

	c.mu.RLock()
	rules := append([]CompiledDomainRule(nil), c.domainRules...)
	c.mu.RUnlock()
	for _, rule := range rules {
		for _, candidate := range hosts {
			for _, matcher := range rule.Matchers {
				if matcher.MatchString(candidate) {
					return rule.Group, rule.Pattern, rule.Expanded || candidate != host
				}
			}
		}
	}
	return "", "", false
}

func expandPatternCandidates(pattern string) []string {
	pattern = normalizeDomainInput(pattern)
	if pattern == "" {
		return nil
	}
	candidates := []string{pattern}
	if strings.HasPrefix(pattern, "*.") {
		withoutLeadingWildcard := strings.TrimPrefix(pattern, "*.")
		if withoutLeadingWildcard != "" {
			candidates = append(candidates, withoutLeadingWildcard)
		}
	}
	return candidates
}

func compileDomainRules(rules []Rule) []CompiledDomainRule {
	compiled := make([]CompiledDomainRule, 0, len(rules))
	for _, rule := range rules {
		pattern := normalizeDomainInput(rule.Pattern)
		if rule.Group == "" || pattern == "" {
			continue
		}

		patterns := expandPatternCandidates(pattern)
		patternNoWWW := stripWWW(pattern)
		expanded := false
		if patternNoWWW != pattern {
			expanded = true
			patterns = append(patterns, expandPatternCandidates(patternNoWWW)...)
		}

		var matchers []*regexp.Regexp
		for _, candidate := range uniqueStrings(patterns) {
			re, err := compileDomainPattern(candidate)
			if err != nil {
				continue
			}
			if candidate != pattern {
				expanded = true
			}
			matchers = append(matchers, re)
		}
		if len(matchers) == 0 {
			continue
		}
		compiled = append(compiled, CompiledDomainRule{
			Group:    rule.Group,
			Pattern:  rule.Pattern,
			Matchers: matchers,
			Expanded: expanded,
		})
	}
	return compiled
}

func compileDomainPattern(pattern string) (*regexp.Regexp, error) {
	pattern = normalizeDomainInput(pattern)
	if pattern == "" {
		return nil, errors.New("empty domain pattern")
	}
	if !strings.Contains(pattern, "*") {
		return regexp.Compile("^(?:.*\\.)?" + regexp.QuoteMeta(pattern) + "$")
	}
	regexText := "^" + regexp.QuoteMeta(pattern) + "$"
	regexText = strings.ReplaceAll(regexText, "\\*", ".*")
	return regexp.Compile(regexText)
}

func uniqueStrings(values []string) []string {
	seen := map[string]bool{}
	result := make([]string, 0, len(values))
	for _, value := range values {
		value = normalizeDomainInput(value)
		if value == "" || seen[value] {
			continue
		}
		seen[value] = true
		result = append(result, value)
	}
	return result
}

func uniquePlainStrings(values []string) []string {
	seen := map[string]bool{}
	result := make([]string, 0, len(values))
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value == "" || seen[value] {
			continue
		}
		seen[value] = true
		result = append(result, value)
	}
	return result
}

func matchPattern(value string, pattern string) bool {
	pattern = strings.ToLower(strings.TrimSpace(pattern))
	if pattern == "" {
		return false
	}
	if ok, _ := filepath.Match(pattern, value); ok {
		return true
	}
	if strings.HasPrefix(pattern, "*.") {
		root := strings.TrimPrefix(pattern, "*.")
		return value == root || strings.HasSuffix(value, "."+root)
	}
	return value == pattern
}

func (c *Core) pauseGroups(groups []string, until time.Time) []string {
	if until.Before(time.Now()) {
		return nil
	}
	c.pauseMu.Lock()
	defer c.pauseMu.Unlock()
	var paused []string
	for _, group := range groups {
		group = strings.TrimSpace(group)
		if group == "" || group == directGroup {
			continue
		}
		c.pausedGroups[group] = GroupPauseEntry{Until: until}
		c.groupPauseCount.Add(1)
		paused = append(paused, group)
		c.addEvent(CoreEvent{Source: "core", SourceReason: "group_paused", FinalGroup: group, Action: "pause"})
	}
	return paused
}

func (c *Core) resumeGroups(groups []string) []string {
	c.pauseMu.Lock()
	defer c.pauseMu.Unlock()
	var resumed []string
	if len(groups) == 0 {
		for group := range c.pausedGroups {
			delete(c.pausedGroups, group)
			resumed = append(resumed, group)
			c.addEvent(CoreEvent{Source: "core", SourceReason: "group_resumed", FinalGroup: group, Action: "resume"})
		}
		return resumed
	}
	for _, group := range groups {
		group = strings.TrimSpace(group)
		if group == "" {
			continue
		}
		if _, ok := c.pausedGroups[group]; ok {
			delete(c.pausedGroups, group)
			resumed = append(resumed, group)
			c.addEvent(CoreEvent{Source: "core", SourceReason: "group_resumed", FinalGroup: group, Action: "resume"})
		}
	}
	return resumed
}

func (c *Core) pausedGroupNames() []string {
	now := time.Now()
	c.pauseMu.Lock()
	defer c.pauseMu.Unlock()
	var groups []string
	for group, entry := range c.pausedGroups {
		if now.After(entry.Until) {
			delete(c.pausedGroups, group)
			continue
		}
		groups = append(groups, group)
	}
	return groups
}

func (c *Core) waitIfGroupPaused(group string) {
	if group == "" || group == directGroup {
		return
	}
	c.pauseMu.Lock()
	entry, ok := c.pausedGroups[group]
	if ok && time.Now().After(entry.Until) {
		delete(c.pausedGroups, group)
		ok = false
	}
	c.pauseMu.Unlock()
	if !ok {
		return
	}
	delay := time.Until(entry.Until)
	if delay <= 0 {
		return
	}
	if delay > time.Second {
		delay = time.Second
	}
	time.Sleep(delay)
}

func (c *Core) tuneSocket(conn net.Conn) {
	tcpConn, ok := conn.(*net.TCPConn)
	if !ok {
		return
	}
	cfg := c.getConfig()
	if cfg.Socket.TCPNoDelay {
		_ = tcpConn.SetNoDelay(true)
	}
	if cfg.Socket.KeepAlive {
		_ = tcpConn.SetKeepAlive(true)
		if cfg.Socket.KeepAliveSec > 0 {
			_ = tcpConn.SetKeepAlivePeriod(time.Duration(cfg.Socket.KeepAliveSec) * time.Second)
		}
	}
	if cfg.Socket.ReadBufferBytes > 0 {
		_ = tcpConn.SetReadBuffer(cfg.Socket.ReadBufferBytes)
	}
	if cfg.Socket.WriteBufferBytes > 0 {
		_ = tcpConn.SetWriteBuffer(cfg.Socket.WriteBufferBytes)
	}
}

func (c *Core) handleDirectConnect(client net.Conn, reader *bufio.Reader, host string, port int, id uint64) {
	remote, err := net.DialTimeout("tcp", net.JoinHostPort(host, strconv.Itoa(port)), 10*time.Second)
	if err != nil {
		c.writeLog("tcp.log", fmt.Sprintf("direct dial failed host=%s:%d err=%v", host, port, err))
		return
	}
	defer remote.Close()
	c.tuneSocket(remote)
	c.setRemote(id, remote)
	if err := writeFull(client, []byte("HTTP/1.1 200 Connection Established\r\n\r\n"), c.transferSettings().writeTimeout); err != nil {
		reason := "direct_write_fail"
		if netErr, ok := err.(net.Error); ok && netErr.Timeout() {
			reason = "direct_write_timeout"
		}
		c.recordFinalResult(id, reason)
		return
	}
	result := c.tunnel(client, reader, remote, id)
	c.recordFinalResult(id, c.finalReasonFromTunnel(id, result))
}

func (c *Core) handleDirectHTTP(client net.Conn, reader *bufio.Reader, header []byte, firstLine string, headers map[string]string, host string, port int, id uint64) {
	remote, err := net.DialTimeout("tcp", net.JoinHostPort(host, strconv.Itoa(port)), 10*time.Second)
	if err != nil {
		c.writeLog("tcp.log", fmt.Sprintf("direct http dial failed host=%s:%d err=%v", host, port, err))
		return
	}
	defer remote.Close()
	c.tuneSocket(remote)
	c.setRemote(id, remote)
	header = rewriteHTTPHeader(header, firstLine, headers)
	if err := writeFull(remote, header, c.transferSettings().writeTimeout); err != nil {
		reason := "direct_write_fail"
		if netErr, ok := err.(net.Error); ok && netErr.Timeout() {
			reason = "direct_write_timeout"
		}
		c.recordFinalResult(id, reason)
		return
	}
	result := c.tunnel(client, reader, remote, id)
	c.recordFinalResult(id, c.finalReasonFromTunnel(id, result))
}

func (c *Core) handleGroupForward(client net.Conn, reader *bufio.Reader, header []byte, group string, port int, id uint64) {
	c.waitIfGroupPaused(group)
	remote, err := net.DialTimeout("tcp", net.JoinHostPort("127.0.0.1", strconv.Itoa(port)), 6*time.Second)
	if err != nil {
		c.writeLog("tcp.log", fmt.Sprintf("listener dial failed group=%s port=%d err=%v", group, port, err))
		c.recordFinalResult(id, "listener_connect_fail")
		return
	}
	defer remote.Close()
	c.tuneSocket(remote)
	c.setRemote(id, remote)
	if err := writeFull(remote, header, c.transferSettings().writeTimeout); err != nil {
		c.recordFinalResult(id, "listener_write_fail")
		return
	}
	result := c.tunnel(client, reader, remote, id)
	c.recordFinalResult(id, c.finalReasonFromTunnel(id, result))
}

func rewriteHTTPHeader(header []byte, firstLine string, headers map[string]string) []byte {
	parts := strings.Fields(firstLine)
	if len(parts) < 3 || !(strings.HasPrefix(parts[1], "http://") || strings.HasPrefix(parts[1], "https://")) {
		return header
	}
	parsed, err := url.Parse(parts[1])
	if err != nil {
		return header
	}
	path := parsed.RequestURI()
	if path == "" {
		path = "/"
	}
	newFirst := parts[0] + " " + path + " " + parts[2]
	text := strings.ReplaceAll(string(header), "\r\n", "\n")
	lines := strings.Split(text, "\n")
	if len(lines) > 0 {
		lines[0] = newFirst
	}
	_ = headers
	return []byte(strings.ReplaceAll(strings.Join(lines, "\n"), "\n", "\r\n"))
}

type tunnelResult struct {
	Reason     string
	RelayError string
	BytesIn    int64
	BytesOut   int64
	DurationMS int64
}

type relayResult struct {
	err error
	dir string
}

func (c *Core) tunnel(client net.Conn, clientReader *bufio.Reader, remote net.Conn, id uint64) tunnelResult {
	startedAt := time.Now()
	result := tunnelResult{Reason: "ok"}
	done := make(chan relayResult, 2)

	go func() {
		if err := c.adaptiveRelay(remote, clientReader, id, true); err != nil {
			done <- relayResult{err: err, dir: "in"}
			return
		}
		if tcpConn, ok := remote.(*net.TCPConn); ok {
			_ = tcpConn.CloseWrite()
		}
		done <- relayResult{dir: "in"}
	}()

	go func() {
		if err := c.adaptiveRelay(client, remote, id, false); err != nil {
			done <- relayResult{err: err, dir: "out"}
			return
		}
		if tcpConn, ok := client.(*net.TCPConn); ok {
			_ = tcpConn.CloseWrite()
		}
		done <- relayResult{dir: "out"}
	}()

	first := <-done
	firstClean := first.err == nil || isExpectedRelayCloseError(first.err)
	if !firstClean {
		result.Reason = relayErrorReason(first.err)
		result.RelayError = first.err.Error()
	}

	// One direction finishing normally is enough to start tunnel shutdown. Closing
	// both sockets is intentional here; errors caused by this close in the other
	// relay direction are normal tunnel teardown, not real relay failures.
	_ = client.Close()
	_ = remote.Close()

	select {
	case second := <-done:
		// If the first direction finished cleanly, the second direction often exits
		// with a read/write error only because we deliberately closed both sockets to
		// finish the tunnel. Do not turn successful HTTP/CONNECT transfers into
		// relay_error because of that expected teardown.
		if !firstClean && result.Reason == "ok" && second.err != nil && !isExpectedRelayCloseError(second.err) {
			result.Reason = relayErrorReason(second.err)
			result.RelayError = second.err.Error()
		}
	case <-time.After(2 * time.Second):
		result.Reason = higherPriorityReason(result.Reason, "relay_shutdown_timeout")
	}

	info := c.getConn(id)
	result.BytesIn = atomic.LoadInt64(&info.BytesIn)
	result.BytesOut = atomic.LoadInt64(&info.BytesOut)
	result.DurationMS = time.Since(startedAt).Milliseconds()
	return result
}

type transferSettings struct {
	normalBufferSize int
	highBufferSize   int
	highWindow       time.Duration
	highBytes        int64
	lowWindow        time.Duration
	lowBytes         int64
	writeTimeout     time.Duration
	idleTimeout      time.Duration
}

func (c *Core) transferSettings() transferSettings {
	cfg := c.getConfig()
	normalKB := cfg.Transfer.NormalBufferKB
	if normalKB <= 0 {
		normalKB = 64
	}
	highKB := cfg.Transfer.HighBufferKB
	if highKB <= 0 {
		highKB = 512
	}
	highWindow := cfg.Transfer.HighThroughputWindowSec
	if highWindow <= 0 {
		highWindow = 2
	}
	lowWindow := cfg.Transfer.LowThroughputWindowSec
	if lowWindow <= 0 {
		lowWindow = 10
	}
	highBytes := cfg.Transfer.HighThroughputBytes
	if highBytes <= 0 {
		highBytes = 2 * 1024 * 1024
	}
	lowBytes := cfg.Transfer.LowThroughputBytes
	if lowBytes <= 0 {
		lowBytes = 512 * 1024
	}
	writeTimeout := cfg.Transfer.WriteTimeoutSec
	if writeTimeout <= 0 {
		writeTimeout = 30
	}
	return transferSettings{
		normalBufferSize: normalKB * 1024,
		highBufferSize:   highKB * 1024,
		highWindow:       time.Duration(highWindow) * time.Second,
		highBytes:        highBytes,
		lowWindow:        time.Duration(lowWindow) * time.Second,
		lowBytes:         lowBytes,
		writeTimeout:     time.Duration(writeTimeout) * time.Second,
		idleTimeout:      time.Duration(cfg.Transfer.IdleTimeoutSec) * time.Second,
	}
}

func bufferPoolForSize(size int) *sync.Pool {
	if size <= 0 {
		size = 64 * 1024
	}
	if pool, ok := bufferPools.Load(size); ok {
		return pool.(*sync.Pool)
	}
	pool := &sync.Pool{New: func() any { return make([]byte, size) }}
	actual, _ := bufferPools.LoadOrStore(size, pool)
	return actual.(*sync.Pool)
}

func (c *Core) getRelayBuffer(size int, high bool) []byte {
	buf := bufferPoolForSize(size).Get().([]byte)
	if cap(buf) < size {
		buf = make([]byte, size)
	}
	if high {
		c.highBufferPoolGet.Add(1)
		c.activeHighBufferCount.Add(1)
	} else {
		c.normalBufferPoolGet.Add(1)
	}
	return buf[:size]
}

func (c *Core) putRelayBuffer(buf []byte, high bool) {
	if buf == nil {
		return
	}
	size := cap(buf)
	if size <= 0 {
		return
	}
	bufferPoolForSize(size).Put(buf[:size])
	if high {
		c.highBufferPoolPut.Add(1)
		c.activeHighBufferCount.Add(-1)
	} else {
		c.normalBufferPoolPut.Add(1)
	}
}

func (c *Core) adaptiveRelay(dst net.Conn, src io.Reader, id uint64, inDirection bool) error {
	settings := c.transferSettings()
	high := false
	buf := c.getRelayBuffer(settings.normalBufferSize, false)
	defer func() {
		if high {
			c.highThroughputDisabledCount.Add(1)
			c.activeHighThroughputDirections.Add(-1)
		}
		c.putRelayBuffer(buf, high)
		c.setConnTransferState(id, inDirection, false, 0)
	}()
	c.setConnTransferState(id, inDirection, false, len(buf))

	windowStart := time.Now()
	windowBytes := int64(0)
	for {
		n, readErr := src.Read(buf)
		if n > 0 {
			data := buf[:n]
			if err := writeFull(dst, data, settings.writeTimeout); err != nil {
				return err
			}
			if inDirection {
				c.addBytes(id, int64(n), 0)
			} else {
				c.addBytes(id, 0, int64(n))
			}
			windowBytes += int64(n)
			now := time.Now()
			if !high && now.Sub(windowStart) <= settings.highWindow && windowBytes >= settings.highBytes {
				c.putRelayBuffer(buf, false)
				buf = c.getRelayBuffer(settings.highBufferSize, true)
				high = true
				windowStart = now
				windowBytes = 0
				c.setConnTransferState(id, inDirection, true, len(buf))
				c.markConnLargeFlow(id)
				c.highThroughputEnabledCount.Add(1)
				c.activeHighThroughputDirections.Add(1)
				c.writeLog("tcp.log", fmt.Sprintf("high-throughput enabled conn=%d dir=%s buffer=%d", id, relayDirectionName(inDirection), len(buf)))
			} else if high && now.Sub(windowStart) >= settings.lowWindow {
				if windowBytes < settings.lowBytes {
					c.putRelayBuffer(buf, true)
					buf = c.getRelayBuffer(settings.normalBufferSize, false)
					high = false
					c.setConnTransferState(id, inDirection, false, len(buf))
					c.highThroughputDisabledCount.Add(1)
					c.activeHighThroughputDirections.Add(-1)
					c.writeLog("tcp.log", fmt.Sprintf("high-throughput disabled conn=%d dir=%s buffer=%d", id, relayDirectionName(inDirection), len(buf)))
				}
				windowStart = now
				windowBytes = 0
			} else if !high && now.Sub(windowStart) > settings.highWindow {
				windowStart = now
				windowBytes = 0
			}
		}
		if readErr != nil {
			if errors.Is(readErr, io.EOF) {
				return nil
			}
			return readErr
		}
	}
}

func writeFull(dst net.Conn, data []byte, timeout time.Duration) error {
	if timeout > 0 {
		_ = dst.SetWriteDeadline(time.Now().Add(timeout))
		defer dst.SetWriteDeadline(time.Time{})
	}
	for len(data) > 0 {
		n, err := dst.Write(data)
		if err != nil {
			return err
		}
		if n == 0 {
			return io.ErrShortWrite
		}
		data = data[n:]
	}
	return nil
}

func relayDirectionName(inDirection bool) string {
	if inDirection {
		return "in"
	}
	return "out"
}

func isExpectedRelayCloseError(err error) bool {
	if err == nil || errors.Is(err, io.EOF) || errors.Is(err, net.ErrClosed) || errors.Is(err, os.ErrClosed) {
		return true
	}
	message := strings.ToLower(err.Error())
	return strings.Contains(message, "use of closed network connection") ||
		strings.Contains(message, "closed network connection") ||
		strings.Contains(message, "closed pipe") ||
		strings.Contains(message, "operation on closed")
}

func relayErrorReason(err error) string {
	if err == nil || errors.Is(err, io.EOF) || isExpectedRelayCloseError(err) {
		return "ok"
	}
	if netErr, ok := err.(net.Error); ok && netErr.Timeout() {
		return "write_timeout"
	}
	return "relay_error"
}

func finalReasonPriority(reason string) int {
	switch reason {
	case "listener_connect_fail":
		return 100
	case "listener_write_fail":
		return 90
	case "direct_write_timeout":
		return 80
	case "direct_write_fail":
		return 75
	case "write_timeout":
		return 70
	case "relay_shutdown_timeout":
		return 60
	case "relay_error":
		return 50
	case "no_response":
		return 40
	case "quick_close_low_bytes":
		return 30
	case "closed", "canceled":
		return 10
	case "ok":
		return 0
	default:
		if reason == "" {
			return 0
		}
		return 20
	}
}

func higherPriorityReason(current string, candidate string) string {
	if finalReasonPriority(candidate) > finalReasonPriority(current) {
		return candidate
	}
	if current == "" {
		return candidate
	}
	return current
}

func isFailureFinalReason(reason string) bool {
	return reason != "" && reason != "ok" && reason != "closed" && reason != "canceled"
}

func (c *Core) finalReasonFromTunnel(id uint64, result tunnelResult) string {
	info := c.getConn(id)
	if c.isShuttingDown() {
		return "closed"
	}
	if closeReason := c.connCloseReason(id); closeReason != "" {
		return closeReason
	}
	reason := result.Reason
	if reason == "" {
		reason = "ok"
	}
	if reason != "ok" {
		return reason
	}
	bytesIn := result.BytesIn
	bytesOut := result.BytesOut
	if bytesIn == 0 && bytesOut == 0 {
		bytesIn = atomic.LoadInt64(&info.BytesIn)
		bytesOut = atomic.LoadInt64(&info.BytesOut)
	}
	duration := time.Since(info.CreatedAt)
	if result.DurationMS > 0 {
		duration = time.Duration(result.DurationMS) * time.Millisecond
	}
	if bytesOut > 0 {
		return "ok"
	}
	if duration < 750*time.Millisecond && bytesIn > 0 && bytesIn < 1024 {
		return "quick_close_low_bytes"
	}
	return "no_response"
}

func (c *Core) registerConn(client net.Conn, host string, group string, process string) uint64 {
	id := c.nextID.Add(1)
	c.connMu.Lock()
	info := &ConnInfo{
		ID:        id,
		Group:     group,
		Host:      host,
		Process:   strings.ToLower(process),
		CreatedAt: time.Now(),
		Client:    client,
	}
	info.LastActiveAt.Store(time.Now().UnixNano())
	info.BufferIn.Store(int64(c.transferSettings().normalBufferSize))
	info.BufferOut.Store(int64(c.transferSettings().normalBufferSize))
	c.conns[id] = info
	c.connMu.Unlock()
	return id
}

func (c *Core) setRemote(id uint64, remote net.Conn) {
	c.connMu.Lock()
	if info := c.conns[id]; info != nil {
		info.Remote = remote
	}
	c.connMu.Unlock()
}

func (c *Core) getConn(id uint64) *ConnInfo {
	c.connMu.RLock()
	info := c.conns[id]
	c.connMu.RUnlock()
	if info == nil {
		return &ConnInfo{}
	}
	return info
}

func (c *Core) markConnClosed(id uint64, reason string) {
	if reason == "" {
		reason = "closed"
	}
	c.connMu.RLock()
	info := c.conns[id]
	c.connMu.RUnlock()
	if info == nil {
		return
	}
	info.CloseReason.Store(reason)
}

func (c *Core) connCloseReason(id uint64) string {
	c.connMu.RLock()
	info := c.conns[id]
	c.connMu.RUnlock()
	if info == nil {
		return ""
	}
	value := info.CloseReason.Load()
	if value == nil {
		return ""
	}
	reason, _ := value.(string)
	return reason
}

func (c *Core) addBytes(id uint64, in int64, out int64) {
	c.connMu.RLock()
	info := c.conns[id]
	c.connMu.RUnlock()
	if info == nil {
		return
	}
	if in != 0 {
		atomic.AddInt64(&info.BytesIn, in)
		c.transferBytesInTotal.Add(uint64(in))
	}
	if out != 0 {
		atomic.AddInt64(&info.BytesOut, out)
		c.transferBytesOutTotal.Add(uint64(out))
	}
	if in != 0 || out != 0 {
		info.LastActiveAt.Store(time.Now().UnixNano())
	}
}

func (c *Core) recordFinalResult(id uint64, reason string) {
	if reason == "" {
		reason = "ok"
	}

	c.connMu.RLock()
	info := c.conns[id]
	c.connMu.RUnlock()
	if info == nil {
		return
	}

	info.FinalMu.Lock()
	if info.FinalSet {
		info.FinalMu.Unlock()
		c.duplicateResultSuppressedCount.Add(1)
		return
	}
	info.FinalSet = true
	info.FinalReason = reason
	info.FinalMu.Unlock()

	bytesIn := atomic.LoadInt64(&info.BytesIn)
	bytesOut := atomic.LoadInt64(&info.BytesOut)
	durationMS := time.Since(info.CreatedAt).Milliseconds()
	failed := isFailureFinalReason(reason)
	countTraffic := reason == "ok" || failed

	c.statsMu.Lock()
	c.finalResultByReason[reason]++
	if reason == "ok" {
		c.finalResultOKCount.Add(1)
	} else if failed {
		c.finalResultFailureCount.Add(1)
	}

	if countTraffic && info.Group != "" && info.Group != directGroup {
		stats := c.traffic[info.Group]
		if stats == nil {
			stats = &TrafficStats{}
			c.traffic[info.Group] = stats
		}
		success := reason == "ok"
		stats.window = append(stats.window, success)
		if len(stats.window) > 20 {
			stats.window = stats.window[len(stats.window)-20:]
		}
		stats.RecentTotal = len(stats.window)
		stats.RecentFailCount = 0
		for _, item := range stats.window {
			if !item {
				stats.RecentFailCount++
			}
		}
		if success {
			stats.SuccessCount++
			stats.ConsecutiveFailures = 0
		} else {
			stats.FailureCount++
			stats.ConsecutiveFailures++
			stats.LastReason = reason
			c.incrementFailureCounterLocked(info.Group, info.Host, info.Process, reason)
		}
		stats.UpdatedAtUnix = time.Now().Unix()
	} else if failed {
		c.incrementFailureCounterLocked(info.Group, info.Host, info.Process, reason)
	}
	c.statsMu.Unlock()

	finalResult := "ok"
	if failed {
		finalResult = "failure"
	} else if reason != "ok" {
		finalResult = reason
	}
	c.writeLog("tcp.log", fmt.Sprintf("conn final conn=%d group=%s host=%s process=%s final_result=%s final_reason=%s bytes_in=%d bytes_out=%d duration_ms=%d",
		id, info.Group, info.Host, info.Process, finalResult, reason, bytesIn, bytesOut, durationMS))

	if failed {
		c.addEvent(CoreEvent{
			Source:       "core",
			SourceReason: reason,
			RequestHost:  info.Host,
			ProcessName:  info.Process,
			FinalGroup:   info.Group,
			Action:       "error",
			FinalResult:  finalResult,
			FinalReason:  reason,
			BytesIn:      bytesIn,
			BytesOut:     bytesOut,
			DurationMS:   durationMS,
		})
	}
}

func (c *Core) setConnTransferState(id uint64, inDirection bool, high bool, bufferSize int) {
	c.connMu.RLock()
	info := c.conns[id]
	c.connMu.RUnlock()
	if info == nil {
		return
	}
	if inDirection {
		info.HighIn.Store(high)
		info.BufferIn.Store(int64(bufferSize))
		return
	}
	info.HighOut.Store(high)
	info.BufferOut.Store(int64(bufferSize))
}

func (c *Core) markConnLargeFlow(id uint64) {
	c.connMu.RLock()
	info := c.conns[id]
	c.connMu.RUnlock()
	if info == nil || info.LargeFlow.Swap(true) {
		return
	}
	c.largeFlowTotal.Add(1)
	c.largeFlowActive.Add(1)
}

func (c *Core) unregisterConn(id uint64) {
	c.connMu.Lock()
	info := c.conns[id]
	delete(c.conns, id)
	c.connMu.Unlock()
	if info != nil && info.LargeFlow.Load() {
		c.largeFlowActive.Add(-1)
	}
}

func (c *Core) addEvent(event CoreEvent) {
	event.ID = c.nextEventID.Add(1)
	event.BootID = c.bootID
	event.Timestamp = time.Now().Format(time.RFC3339Nano)
	c.eventMu.Lock()
	c.events = append(c.events, event)
	if len(c.events) > 500 {
		c.events = c.events[len(c.events)-500:]
	}
	c.eventMu.Unlock()
}

func (c *Core) eventFromDecision(decision RouteDecision, host string, processName string, group string, target string, listener string, action string) CoreEvent {
	return CoreEvent{
		Source:                  decision.Source,
		SourceReason:            decision.SourceReason,
		TabHost:                 decision.TabHost,
		RequestHost:             host,
		ProcessName:             processName,
		FinalGroup:              group,
		MatchedPattern:          decision.MatchedPattern,
		Target:                  target,
		Listener:                listener,
		Action:                  action,
		IsRegisteredBrowser:     decision.IsRegisteredBrowser,
		BrowserRegistryHit:      decision.BrowserRegistryHit,
		BrowserRegistryIdentity: decision.BrowserRegistryIdentity,
		TabWaitMS:               decision.TabWaitMS,
		TabWaitResult:           decision.TabWaitResult,
		ReportProcessName:       decision.ReportProcessName,
		ReportProcessIdentity:   decision.ReportProcessIdentity,
		ProcessLookupResult:     decision.ProcessLookupResult,
		ProcessLookupRetryCount: decision.ProcessLookupRetryCount,
		ProcessIdentity:         decision.ProcessIdentity,
		ProcessIdentityKey:      decision.ProcessIdentityKey,
		ProcessCacheHit:         decision.ProcessCacheHit,
		ProcessRuleMatched:      decision.ProcessRuleMatched,
		ProcessMatchedPattern:   decision.ProcessMatchedPattern,
		ProcessState:            decision.ProcessState,
		TabCapableUntilUnix:     decision.TabCapableUntilUnix,
		NonTabUntilUnix:         decision.NonTabUntilUnix,
		ExePathHintHit:          decision.ExePathHintHit,
		ExePathHintDisabled:     decision.ExePathHintDisabled,
		ExePathHintTimeoutCount: decision.ExePathHintTimeoutCount,
		ExePathHintReason:       decision.ExePathHintReason,
	}
}

func (c *Core) closeConnections(groups []string, hosts []string, processes []string) int {
	groupSet := exactSet(groups)
	hostSet := lowerSet(hosts)
	processSet := stringSet(processes)
	if len(groupSet) == 0 && len(hostSet) == 0 && len(processSet) == 0 {
		return 0
	}
	closed := 0
	c.connMu.RLock()
	var targets []*ConnInfo
	for _, info := range c.conns {
		if (len(groupSet) == 0 || groupSet[info.Group]) &&
			(len(hostSet) == 0 || hostSet[strings.ToLower(info.Host)]) &&
			(len(processSet) == 0 || processSet[info.Process]) {
			targets = append(targets, info)
		}
	}
	c.connMu.RUnlock()
	for _, info := range targets {
		c.markConnClosed(info.ID, "closed")
		_ = info.Client.Close()
		if info.Remote != nil {
			_ = info.Remote.Close()
		}
		closed++
	}
	return closed
}

func (c *Core) closeAllConnections() int {
	closed := 0
	c.connMu.RLock()
	var targets []*ConnInfo
	for _, info := range c.conns {
		targets = append(targets, info)
	}
	c.connMu.RUnlock()
	for _, info := range targets {
		c.markConnClosed(info.ID, "closed")
		_ = info.Client.Close()
		if info.Remote != nil {
			_ = info.Remote.Close()
		}
		closed++
	}
	return closed
}

func (c *Core) incrementFailureCounterLocked(group string, host string, process string, reason string) {
	switch reason {
	case "no_response":
		c.failureNoResponseCount.Add(1)
	case "quick_close_low_bytes":
		c.failureQuickCloseLowBytesCount.Add(1)
	case "write_timeout":
		c.failureWriteTimeoutCount.Add(1)
	case "direct_write_fail", "direct_write_timeout":
		c.failureDirectWriteCount.Add(1)
	case "listener_connect_fail":
		c.failureListenerConnectCount.Add(1)
	case "listener_write_fail":
		c.failureListenerWriteCount.Add(1)
	case "relay_error":
		c.failureRelayErrorCount.Add(1)
	case "relay_shutdown_timeout":
		c.failureRelayShutdownTimeout.Add(1)
	}
	incrementNestedCounter(c.failuresByGroup, group, reason)
	incrementNestedCounter(c.failuresByProcess, process, reason)
	incrementNestedCounter(c.failuresByHost, host, reason)
}

func incrementNestedCounter(values map[string]map[string]int64, key string, reason string) {
	key = strings.ToLower(strings.TrimSpace(key))
	if key == "" {
		key = "-"
	}
	if reason == "" {
		reason = "unknown"
	}
	reasons := values[key]
	if reasons == nil {
		reasons = map[string]int64{}
		values[key] = reasons
	}
	reasons[reason]++
}

func cloneNestedCounterMap(values map[string]map[string]int64) map[string]map[string]int64 {
	result := make(map[string]map[string]int64, len(values))
	for key, nested := range values {
		copyNested := make(map[string]int64, len(nested))
		for reason, count := range nested {
			copyNested[reason] = count
		}
		result[key] = copyNested
	}
	return result
}

func (c *Core) writeLog(fileName string, message string) {
	if c.logManager == nil {
		return
	}
	c.logManager.write(fileName, message)
}

func stringSet(values []string) map[string]bool {
	result := map[string]bool{}
	for _, value := range values {
		value = strings.ToLower(strings.TrimSpace(value))
		if value != "" {
			result[value] = true
		}
	}
	return result
}

func exactSet(values []string) map[string]bool {
	result := map[string]bool{}
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value != "" {
			result[value] = true
		}
	}
	return result
}

func lowerSet(values []string) map[string]bool {
	return stringSet(values)
}
