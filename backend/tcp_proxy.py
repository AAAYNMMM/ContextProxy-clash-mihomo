import asyncio
import fnmatch
import socket
import threading
import time
from urllib.parse import urlparse

import psutil

from backend.config import TCP_LISTEN_HOST, TCP_LISTEN_PORT, DEFAULT_PROXY, DIRECT
from backend.runtime_config import get_group_port_map
from backend.batch_processor import REQUEST_GROUP_MAP
from backend.app_process_rules import match_process_group
from backend.group_runtime_state import is_group_restarting
from backend.process_cache import cache_process_lookup_miss, get_process_lookup_from_cache
from backend.activity_bus import emit_activity, emit_routing_event, write_log


tcp_server = None

# conn_id -> connection info
ACTIVE_CONNECTIONS = {}
ACTIVE_CONNECTIONS_LOCK = threading.RLock()

NORMAL_BUFFER_SIZE = 64 * 1024
NORMAL_DRAIN_THRESHOLD = 256 * 1024
HIGH_BUFFER_SIZE = 512 * 1024
HIGH_DRAIN_THRESHOLD = 2 * 1024 * 1024
STREAM_LIMIT = 2 * 1024 * 1024
HIGH_THROUGHPUT_WINDOW_SECONDS = 2.0
HIGH_THROUGHPUT_BYTES = 2 * 1024 * 1024
LOW_THROUGHPUT_WINDOW_SECONDS = 10.0
LOW_THROUGHPUT_BYTES = 512 * 1024

GROUP_WAIT_TIMEOUT = 0.08
GROUP_WAIT_STEP = 0.005
BROWSER_GROUP_WAIT_TIMEOUT = 0.30
BROWSER_PROCESS_NAMES = {
    "chrome.exe",
    "msedge.exe",
    "brave.exe",
    "opera.exe",
    "vivaldi.exe",
    "firefox.exe",
    "browser.exe",
}

# requestHost 鍒嗙粍缁撴灉鍙 1 绉掑唴鐨勬柊缁撴灉
# 杩欐牱鍙互閬垮厤鏃?Tab 鍒嗙粍褰卞搷鏃?Tab 鐨?App 璇锋眰
REQUEST_GROUP_CACHE_TTL = 1.0


def force_close_writer(writer):
    """
    灏介噺寮哄埗鍏抽棴 StreamWriter銆?
    writer.close() 鏄俯鍜屽叧闂紱
    transport.abort() 鏄珛鍗虫柇寮€搴曞眰杩炴帴銆?    """
    if not writer:
        return

    try:
        writer.close()
    except Exception:
        pass

    try:
        transport = getattr(writer, "transport", None)

        if not transport:
            transport = getattr(writer, "_transport", None)

        if transport:
            transport.abort()
    except Exception:
        pass


def tune_socket(writer: asyncio.StreamWriter):
    sock = writer.get_extra_info("socket") if writer else None
    if not sock:
        return

    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except Exception:
        pass

    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    except Exception:
        pass

    for option in (socket.SO_RCVBUF, socket.SO_SNDBUF):
        try:
            sock.setsockopt(socket.SOL_SOCKET, option, 1024 * 1024)
        except Exception:
            pass


def is_normal_socket_error(e: Exception) -> bool:
    """
    Windows 涓嬪父瑙佺殑姝ｅ父鏂繛/瓒呮椂锛?    10054 = 杩滅▼涓绘満寮鸿揩鍏抽棴杩炴帴
    10053 = 杞欢涓杩炴帴
    121   = 淇″彿鐏秴鏃舵椂闂村凡鍒?    """
    if isinstance(e, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
        return True

    if isinstance(e, OSError):
        return getattr(e, "winerror", None) in {121, 10053, 10054}

    return False


def parse_connect_host(first_line: str):
    """
    瑙ｆ瀽 HTTPS 浠ｇ悊璇锋眰锛?    CONNECT chatgpt.com:443 HTTP/1.1
    """
    parts = first_line.split()

    if len(parts) < 2:
        return None, None

    target = parts[1]

    if ":" in target:
        host, port_str = target.rsplit(":", 1)

        try:
            return host.lower(), int(port_str)
        except ValueError:
            return host.lower(), 443

    return target.lower(), 443


def parse_http_host(first_line: str, headers_text: str):
    """
    瑙ｆ瀽鏅€?HTTP 浠ｇ悊璇锋眰锛?
    GET http://example.com/path HTTP/1.1

    鎴栬€呬粠 Host 澶撮噷鍙栧煙鍚嶃€?    """
    parts = first_line.split()

    if len(parts) >= 2:
        url = parts[1]

        if url.startswith("http://") or url.startswith("https://"):
            parsed = urlparse(url)

            if parsed.hostname:
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                return parsed.hostname.lower(), port

    for line in headers_text.splitlines():
        if line.lower().startswith("host:"):
            host_value = line.split(":", 1)[1].strip()

            if ":" in host_value:
                host, port_str = host_value.rsplit(":", 1)

                try:
                    return host.lower(), int(port_str)
                except ValueError:
                    return host.lower(), 80

            return host_value.lower(), 80

    return None, None


def rewrite_http_request_for_direct(data: bytes):
    """
    娴忚鍣ㄥ彂缁?HTTP 浠ｇ悊鏃讹紝鏅€?HTTP 璇锋眰閫氬父鏄細

    GET http://example.com/path HTTP/1.1

    鐩磋繛鐩爣鏈嶅姟鍣ㄦ椂锛岄渶瑕佹敼鎴愶細

    GET /path HTTP/1.1
    """
    try:
        text = data.decode("iso-8859-1")
        header_end = text.find("\r\n\r\n")

        if header_end == -1:
            return data

        header_text = text[:header_end]
        body = text[header_end + 4:]

        lines = header_text.split("\r\n")
        first = lines[0]
        parts = first.split()

        if len(parts) >= 3:
            method, url, version = parts[0], parts[1], parts[2]

            if url.startswith("http://") or url.startswith("https://"):
                parsed = urlparse(url)
                path = parsed.path or "/"

                if parsed.query:
                    path += "?" + parsed.query

                lines[0] = f"{method} {path} {version}"

        new_text = "\r\n".join(lines) + "\r\n\r\n" + body
        return new_text.encode("iso-8859-1")

    except Exception:
        return data


def _extract_cached_group(request_host: str):
    entry = REQUEST_GROUP_MAP.get(request_host)

    if not entry:
        return None

    # 鍏煎鏃ф牸寮忥細REQUEST_GROUP_MAP[host] = "AI"
    if isinstance(entry, str):
        return entry

    # 褰撳墠鏍煎紡锛歊EQUEST_GROUP_MAP[host] = {"group": "AI", "created_at": time.monotonic()}
    if isinstance(entry, dict):
        group = entry.get("group")
        created_at = entry.get("created_at", 0)

        if not group:
            return None

        if time.monotonic() - created_at <= REQUEST_GROUP_CACHE_TTL:
            return group

        try:
            REQUEST_GROUP_MAP.pop(request_host, None)
        except Exception:
            pass

    return None


async def wait_for_group(request_host: str, timeout: float | None = None):
    """
    绛夊緟 batch_processor 鍐欏叆 requestHost -> final_group銆?
    鏈?Tab 涓婃姤锛?        杩斿洖 AI / Media / Proxy / Direct

    娌℃湁 Tab 涓婃姤锛?        杩斿洖 None锛屽悗缁啀璧?App 瑙勫垯
    """
    waited = 0.0
    max_wait = GROUP_WAIT_TIMEOUT if timeout is None else timeout

    while waited < max_wait:
        group = _extract_cached_group(request_host)

        if group:
            return group

        await asyncio.sleep(GROUP_WAIT_STEP)
        waited += GROUP_WAIT_STEP

    return None


def is_browser_process(process_name: str | None) -> bool:
    return (process_name or "").lower().strip() in BROWSER_PROCESS_NAMES


def group_wait_timeout_for_process(process_name: str | None) -> float:
    if is_browser_process(process_name):
        return BROWSER_GROUP_WAIT_TIMEOUT
    return GROUP_WAIT_TIMEOUT


def _get_addr_ip_port(addr):
    if not addr:
        return None, None

    if hasattr(addr, "ip") and hasattr(addr, "port"):
        return addr.ip, addr.port

    try:
        return addr[0], addr[1]
    except Exception:
        return None, None


def get_process_name_by_client_writer(client_writer: asyncio.StreamWriter):
    """
    鏍规嵁杩炴帴鍒?Python 鍓嶇疆浠ｇ悊鐨勬湰鍦版簮绔彛锛屽弽鏌ヨ繘绋嬪悕銆?
    App/chrome.exe -> 127.0.0.1:18000
    Python 鍙互鐪嬪埌 peername 閲岀殑涓存椂绔彛锛?    鍐嶇敤 psutil 鏌ヨ繖涓鍙ｅ睘浜庡摢涓?PID銆?    """
    peer = client_writer.get_extra_info("peername")

    if not peer:
        return None

    client_ip = peer[0]
    client_port = peer[1]

    cache_hit, cached_process_name = get_process_lookup_from_cache(client_ip, client_port)
    if cache_hit:
        return cached_process_name

    try:
        for conn in psutil.net_connections(kind="tcp"):
            if not conn.pid:
                continue

            _laddr_ip, laddr_port = _get_addr_ip_port(conn.laddr)
            _raddr_ip, raddr_port = _get_addr_ip_port(conn.raddr)

            if not laddr_port or not raddr_port:
                continue

            if laddr_port == client_port and raddr_port == TCP_LISTEN_PORT:
                try:
                    return psutil.Process(conn.pid).name()
                except Exception:
                    cache_process_lookup_miss(client_ip, client_port)
                    return None

    except Exception as e:
        write_log("tcp", f"process lookup failed: {e}", "WARN")

    cache_process_lookup_miss(client_ip, client_port)
    return None


def register_active_connection(
    client_writer: asyncio.StreamWriter,
    request_host: str,
    request_port: int,
    final_group: str,
    process_name: str | None = None,
):
    conn_id = id(client_writer)

    with ACTIVE_CONNECTIONS_LOCK:
        ACTIVE_CONNECTIONS[conn_id] = {
            "writer": client_writer,
            "remote_writer": None,
            "request_host": request_host,
            "request_port": request_port,
            "final_group": final_group,
            "process_name": (process_name or "").lower().strip(),
            "created_at": time.monotonic(),
        }

    return conn_id


def unregister_active_connection(conn_id):
    if conn_id is not None:
        with ACTIVE_CONNECTIONS_LOCK:
            ACTIVE_CONNECTIONS.pop(conn_id, None)


def set_remote_writer(conn_id, remote_writer):
    """
    璁板綍 Python -> mihomo / Python -> 鐩爣缃戠珯 杩欎竴渚ц繛鎺ャ€?    """
    if conn_id is None:
        return

    with ACTIVE_CONNECTIONS_LOCK:
        if conn_id in ACTIVE_CONNECTIONS:
            ACTIVE_CONNECTIONS[conn_id]["remote_writer"] = remote_writer


def get_active_connection_count() -> int:
    try:
        with ACTIVE_CONNECTIONS_LOCK:
            return len(ACTIVE_CONNECTIONS)
    except Exception:
        return 0


def get_active_connection_snapshot() -> list[dict]:
    try:
        now = time.monotonic()
        with ACTIVE_CONNECTIONS_LOCK:
            items = list(ACTIVE_CONNECTIONS.items())
    except Exception:
        return []

    snapshot = []
    for _conn_id, info in items:
        created_at = info.get("created_at")
        try:
            duration = int(now - float(created_at))
        except Exception:
            duration = 0

        snapshot.append(
            {
                "host": str(info.get("request_host") or ""),
                "port": info.get("request_port"),
                "group": str(info.get("final_group") or ""),
                "process": str(info.get("process_name") or ""),
                "duration": max(0, duration),
            }
        )

    return snapshot


def close_connection_info(conn_id, info):
    """
    鍚屾椂鍏抽棴娴忚鍣ㄤ晶鍜岃繙绔晶銆?    """
    client_writer = info.get("writer")
    remote_writer = info.get("remote_writer")

    force_close_writer(client_writer)
    force_close_writer(remote_writer)

    with ACTIVE_CONNECTIONS_LOCK:
        ACTIVE_CONNECTIONS.pop(conn_id, None)


def close_connections_by_group(group_name: str):
    closed = 0

    with ACTIVE_CONNECTIONS_LOCK:
        items = list(ACTIVE_CONNECTIONS.items())

    for conn_id, info in items:
        if info.get("final_group") != group_name:
            continue

        close_connection_info(conn_id, info)
        closed += 1

    emit_activity(f"已断开分组连接：{group_name}，{closed} 条", "INFO", key=f"close-group:{group_name}", ttl=2)


def close_connections_by_groups(group_names: set[str] | list[str]):
    total = 0
    group_set = set(group_names)

    with ACTIVE_CONNECTIONS_LOCK:
        items = list(ACTIVE_CONNECTIONS.items())

    for conn_id, info in items:
        if info.get("final_group") not in group_set:
            continue

        close_connection_info(conn_id, info)
        total += 1

    if total:
        emit_activity(f"已断开受影响分组连接：{', '.join(sorted(group_set))}，{total} 条", "INFO", key=f"close-groups:{sorted(group_set)}", ttl=2)




def _host_matches_pattern(host: str, pattern: str) -> bool:
    host = (host or "").lower().strip()
    pattern = (pattern or "").lower().strip()

    if not host or not pattern:
        return False

    if fnmatch.fnmatch(host, pattern):
        return True

    if pattern.startswith("*."):
        root_pattern = pattern[2:]
        if fnmatch.fnmatch(host, root_pattern):
            return True

    return False


def close_connections_by_domain_patterns(patterns: set[str] | list[str]):
    pattern_set = {str(pattern).lower().strip() for pattern in patterns if str(pattern).strip()}
    if not pattern_set:
        return 0

    closed = 0
    with ACTIVE_CONNECTIONS_LOCK:
        items = list(ACTIVE_CONNECTIONS.items())

    for conn_id, info in items:
        request_host = str(info.get("request_host") or "").lower().strip()
        if not request_host:
            continue

        if not any(_host_matches_pattern(request_host, pattern) for pattern in pattern_set):
            continue

        close_connection_info(conn_id, info)
        closed += 1

    if closed:
        emit_activity(f"已断开域名规则相关连接：{closed} 条", "INFO", key=f"close-domain:{sorted(pattern_set)}", ttl=2)
    return closed


def close_connections_by_process_patterns(patterns: set[str] | list[str]):
    pattern_set = {str(pattern).lower().strip() for pattern in patterns if str(pattern).strip()}
    if not pattern_set:
        return 0

    closed = 0
    with ACTIVE_CONNECTIONS_LOCK:
        items = list(ACTIVE_CONNECTIONS.items())

    for conn_id, info in items:
        process_name = str(info.get("process_name") or "").lower().strip()
        if not process_name:
            continue

        if not any(fnmatch.fnmatch(process_name, pattern) for pattern in pattern_set):
            continue

        close_connection_info(conn_id, info)
        closed += 1

    if closed:
        emit_activity(f"已断开进程规则相关连接：{closed} 条", "INFO", key=f"close-process:{sorted(pattern_set)}", ttl=2)
    return closed


def close_connections_by_host(request_host: str):
    request_host = request_host.lower().strip()
    closed = 0

    with ACTIVE_CONNECTIONS_LOCK:
        items = list(ACTIVE_CONNECTIONS.items())

    for conn_id, info in items:
        if info.get("request_host") != request_host:
            continue

        close_connection_info(conn_id, info)
        closed += 1

    if closed:
        emit_activity(f"已断开主机连接：{request_host}，{closed} 条", "INFO", key=f"close-host:{request_host}", ttl=2)


def close_all_active_connections():
    closed = 0

    with ACTIVE_CONNECTIONS_LOCK:
        items = list(ACTIVE_CONNECTIONS.items())

    for conn_id, info in items:
        close_connection_info(conn_id, info)
        closed += 1

    write_log("tcp", f"closed all active connections: {closed}")


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, label: str = ""):
    """
    鍗曞悜杞彂鏁版嵁銆?    """
    total_bytes = 0
    window_bytes = 0
    window_start_time = time.monotonic()
    high_throughput = False
    low_window_bytes = 0
    low_window_start_time = window_start_time
    pending_write_bytes = 0

    try:
        while True:
            buffer_size = HIGH_BUFFER_SIZE if high_throughput else NORMAL_BUFFER_SIZE
            drain_threshold = HIGH_DRAIN_THRESHOLD if high_throughput else NORMAL_DRAIN_THRESHOLD
            data = await reader.read(buffer_size)

            if not data:
                break

            data_size = len(data)
            now = time.monotonic()
            total_bytes += data_size
            window_bytes += data_size

            if not high_throughput:
                if now - window_start_time <= HIGH_THROUGHPUT_WINDOW_SECONDS and window_bytes >= HIGH_THROUGHPUT_BYTES:
                    high_throughput = True
                    low_window_start_time = now
                    low_window_bytes = 0
                    write_log("tcp", f"relay high-throughput enabled: {label or '-'} total={total_bytes}", "DEBUG")
                elif now - window_start_time >= HIGH_THROUGHPUT_WINDOW_SECONDS:
                    window_start_time = now
                    window_bytes = 0
            else:
                low_window_bytes += data_size
                if now - low_window_start_time >= LOW_THROUGHPUT_WINDOW_SECONDS:
                    if low_window_bytes < LOW_THROUGHPUT_BYTES:
                        high_throughput = False
                        window_start_time = now
                        window_bytes = 0
                        write_log("tcp", f"relay high-throughput disabled: {label or '-'} total={total_bytes}", "DEBUG")
                    low_window_start_time = now
                    low_window_bytes = 0

            writer.write(data)
            pending_write_bytes += data_size
            if pending_write_bytes >= drain_threshold:
                await writer.drain()
                pending_write_bytes = 0

        if pending_write_bytes:
            await writer.drain()

    except Exception as e:
        if not is_normal_socket_error(e):
            write_log("tcp", f"forwarding error: {e}", "WARN")

    finally:
        force_close_writer(writer)


async def tunnel_bidirectional(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    remote_reader: asyncio.StreamReader,
    remote_writer: asyncio.StreamWriter,
):
    """
    鍙屽悜杞彂銆?    """
    tune_socket(client_writer)
    tune_socket(remote_writer)
    await asyncio.gather(
        pipe(client_reader, remote_writer, "client->remote"),
        pipe(remote_reader, client_writer, "remote->client"),
    )


async def handle_direct_connect(
    request_host: str,
    request_port: int,
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    conn_id=None,
):
    """
    HTTPS CONNECT 鐩磋繛锛?    娴忚鍣?-> Python -> 鐩爣缃戠珯
    """
    remote_reader, remote_writer = await asyncio.open_connection(
        request_host,
        request_port,
        limit=STREAM_LIMIT,
    )
    tune_socket(client_writer)
    tune_socket(remote_writer)

    set_remote_writer(conn_id, remote_writer)

    client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await client_writer.drain()

    write_log("tcp", f"DIRECT CONNECT {request_host}:{request_port}", "DEBUG")

    await tunnel_bidirectional(
        client_reader,
        client_writer,
        remote_reader,
        remote_writer,
    )


async def handle_direct_http(
    request_host: str,
    request_port: int,
    first_data: bytes,
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    conn_id=None,
):
    """
    鏅€?HTTP 鐩磋繛銆?    """
    remote_reader, remote_writer = await asyncio.open_connection(
        request_host,
        request_port,
        limit=STREAM_LIMIT,
    )
    tune_socket(client_writer)
    tune_socket(remote_writer)

    set_remote_writer(conn_id, remote_writer)

    first_data = rewrite_http_request_for_direct(first_data)

    remote_writer.write(first_data)
    await remote_writer.drain()

    write_log("tcp", f"DIRECT HTTP {request_host}:{request_port}", "DEBUG")

    await tunnel_bidirectional(
        client_reader,
        client_writer,
        remote_reader,
        remote_writer,
    )


async def handle_proxy_forward(
    final_group: str,
    target_port: int,
    first_data: bytes,
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    request_host: str,
    conn_id=None,
):
    """
    杞彂缁?mihomo 鏈湴 HTTP 浠ｇ悊绔彛銆?
    杩欓噷淇濇寔鍘熷 CONNECT / HTTP proxy 璇锋眰鏍煎紡涓嶅彉锛?    鍥犱负 mihomo 鐨?port 绔彛鏈潵灏辨槸 HTTP 浠ｇ悊绔彛銆?    """
    remote_reader, remote_writer = await asyncio.open_connection(
        "127.0.0.1",
        target_port,
        limit=STREAM_LIMIT,
    )
    tune_socket(client_writer)
    tune_socket(remote_writer)

    set_remote_writer(conn_id, remote_writer)

    remote_writer.write(first_data)
    await remote_writer.drain()

    write_log("tcp", f"{request_host} -> {final_group} -> 127.0.0.1:{target_port}")

    await tunnel_bidirectional(
        client_reader,
        client_writer,
        remote_reader,
        remote_writer,
    )


async def handle_client(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
):
    """
    澶勭悊娴忚鍣?搴旂敤鍙戞潵鐨勪竴涓?TCP 杩炴帴銆?    """
    conn_id = None

    try:
        tune_socket(client_writer)
        first_data = await client_reader.read(8192)

        if not first_data:
            force_close_writer(client_writer)
            return

        try:
            first_text = first_data.decode("iso-8859-1", errors="ignore")
        except Exception:
            force_close_writer(client_writer)
            return

        first_line = first_text.split("\r\n", 1)[0]

        if not first_line:
            force_close_writer(client_writer)
            return

        method = first_line.split(" ", 1)[0].upper()

        if method == "CONNECT":
            request_host, request_port = parse_connect_host(first_line)
            is_connect = True
        else:
            request_host, request_port = parse_http_host(first_line, first_text)
            is_connect = False

        if not request_host:
            write_log("tcp", "cannot parse requestHost, closing connection", "WARN")
            force_close_writer(client_writer)
            return

        process_name = get_process_name_by_client_writer(client_writer)
        final_group = await wait_for_group(
            request_host,
            timeout=group_wait_timeout_for_process(process_name),
        )

        if final_group is None:
            app_group = match_process_group(process_name)

            if app_group and app_group != DIRECT:
                final_group = app_group
                emit_routing_event(
                    "app",
                    request_host=request_host,
                    final_group=final_group,
                    process_name=process_name,
                    ttl=10,
                )
            else:
                final_group = DIRECT
                write_log(
                    "tcp",
                    f"{request_host} no Tab report; process={process_name} unmatched App rule -> {final_group}",
                    "DEBUG",
                )

        if final_group != DIRECT and is_group_restarting(final_group):
            emit_activity(
                f"分组 {final_group} 正在切换，已暂时拒绝新连接：{request_host}",
                "WARN",
                key=f"group-changing:{final_group}:{request_host}",
                ttl=5,
            )
            force_close_writer(client_writer)
            return

        conn_id = register_active_connection(
            client_writer=client_writer,
            request_host=request_host,
            request_port=request_port,
            final_group=final_group,
            process_name=process_name,
        )

        if final_group == DIRECT:
            if is_connect:
                await handle_direct_connect(
                    request_host,
                    request_port,
                    client_reader,
                    client_writer,
                    conn_id,
                )
            else:
                await handle_direct_http(
                    request_host,
                    request_port,
                    first_data,
                    client_reader,
                    client_writer,
                    conn_id,
                )

            return

        group_port_map = get_group_port_map()
        target_port = group_port_map.get(final_group)

        if target_port is None:
            emit_activity(f"分组 {final_group} 没有 listener 端口映射，已拒绝连接", "WARN", key=f"missing-listener:{final_group}", ttl=10)
            force_close_writer(client_writer)
            return

        await handle_proxy_forward(
            final_group,
            target_port,
            first_data,
            client_reader,
            client_writer,
            request_host,
            conn_id,
        )

    except Exception as e:
        if not is_normal_socket_error(e):
            write_log("tcp", f"connection handling error: {e}", "WARN")

        force_close_writer(client_writer)

    finally:
        unregister_active_connection(conn_id)


async def start_tcp_proxy():
    global tcp_server

    tcp_server = await asyncio.start_server(
        handle_client,
        TCP_LISTEN_HOST,
        TCP_LISTEN_PORT,
        limit=STREAM_LIMIT,
    )

    emit_activity(f"TCP 前置代理已启动：{TCP_LISTEN_HOST}:{TCP_LISTEN_PORT}", "INFO", key="tcp-started", ttl=5)

    try:
        async with tcp_server:
            await tcp_server.serve_forever()
    except asyncio.CancelledError:
        pass
    finally:
        await async_stop_tcp_proxy()


def stop_tcp_proxy():
    global tcp_server

    if tcp_server:
        tcp_server.close()

    close_all_active_connections()


async def async_stop_tcp_proxy():
    global tcp_server

    server = tcp_server
    tcp_server = None

    if server:
        server.close()
        try:
            await server.wait_closed()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            write_log("tcp", f"wait_closed failed: {exc}", "WARN")

    close_all_active_connections()
