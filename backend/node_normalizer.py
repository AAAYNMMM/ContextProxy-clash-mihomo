from __future__ import annotations

from copy import deepcopy
from typing import Any


def _clean_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _as_int(value: Any):
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _get_first(data: dict, *keys: str):
    for key in keys:
        if key in data and data.get(key) not in (None, ""):
            return data.get(key)
    return None


def _ensure_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def normalize_proxy_node(node: dict) -> dict:
    """Return a mihomo proxy node while preserving protocol-specific fields.

    ContextProxy does not implement proxy protocols itself; mihomo does.  This
    normalizer therefore must never rebuild nodes from a fixed allow-list of
    fields.  Unknown/new mihomo fields are intentionally kept so new protocols
    and transport options continue to work as long as mihomo supports them.

    Only tiny compatibility fixes are applied: common scalar normalization and
    adding canonical aliases for VLESS URI-style nodes.  Alias/source fields are
    not removed because Clash/Mihomo YAML subscriptions may already contain
    protocol-specific keys that this project should pass through untouched.
    """
    if not isinstance(node, dict):
        return node

    proxy = deepcopy(node)
    proxy_type = _clean_string(proxy.get("type")).lower()
    if proxy_type:
        proxy["type"] = proxy_type

    if "port" in proxy:
        proxy["port"] = _as_int(proxy.get("port"))

    if proxy_type != "vless":
        return proxy

    uuid_value = _get_first(proxy, "uuid", "id")
    if uuid_value:
        proxy["uuid"] = _clean_string(uuid_value)

    network = _clean_string(_get_first(proxy, "network", "net")).lower()
    if network:
        proxy["network"] = network

    sni = _get_first(proxy, "servername", "serverName", "sni", "peer")
    if sni:
        proxy["servername"] = _clean_string(sni)

    fingerprint = _get_first(proxy, "client-fingerprint", "clientFingerprint", "fp")
    if fingerprint:
        proxy["client-fingerprint"] = _clean_string(fingerprint)

    flow = _get_first(proxy, "flow")
    if flow is not None:
        proxy["flow"] = _clean_string(flow)

    # Reality aliases from VLESS URI parameters.
    reality_opts = _ensure_dict(proxy.get("reality-opts"))
    public_key = _get_first(reality_opts, "public-key", "publicKey") or _get_first(proxy, "pbk", "public-key", "publicKey")
    short_id = _get_first(reality_opts, "short-id", "shortId")
    if short_id is None and any(k in proxy for k in ("sid", "short-id", "shortId")):
        short_id = _get_first(proxy, "sid", "short-id", "shortId") or ""
    if public_key is not None:
        reality_opts["public-key"] = _clean_string(public_key)
    if short_id is not None:
        reality_opts["short-id"] = _clean_string(short_id)
    if reality_opts:
        reality_opts.pop("publicKey", None)
        reality_opts.pop("shortId", None)
        proxy["reality-opts"] = reality_opts

    grpc_opts = _ensure_dict(proxy.get("grpc-opts"))
    service_name = (
        _get_first(grpc_opts, "grpc-service-name", "serviceName", "service-name")
        or _get_first(proxy, "serviceName", "service-name", "grpc-service-name")
    )
    if network == "grpc":
        grpc_opts["grpc-service-name"] = _clean_string(service_name or "grpc")
        grpc_opts.pop("serviceName", None)
        grpc_opts.pop("service-name", None)
        proxy["grpc-opts"] = grpc_opts

    ws_opts = _ensure_dict(proxy.get("ws-opts"))
    path = _get_first(ws_opts, "path") or _get_first(proxy, "path")
    host = _get_first(proxy, "host")
    if network == "ws":
        if path is not None:
            ws_opts["path"] = _clean_string(path)
        if host:
            headers = _ensure_dict(ws_opts.get("headers"))
            headers["Host"] = _clean_string(host)
            ws_opts["headers"] = headers
        if ws_opts:
            proxy["ws-opts"] = ws_opts

    tls_enabled = bool(proxy.get("tls")) or bool(proxy.get("reality-opts"))
    if tls_enabled and not proxy.get("client-fingerprint"):
        proxy["client-fingerprint"] = "chrome"

    # VLESS + Reality + gRPC in mihomo is most stable when flow is present but
    # empty and ALPN explicitly allows h2. This keeps imported Clash YAML working
    # while avoiding missing-field failures for v2rayN-style subscriptions.
    if proxy.get("reality-opts") and network == "grpc":
        proxy.setdefault("flow", "")
        if not proxy.get("alpn"):
            proxy["alpn"] = ["h2"]

    return proxy


def normalize_proxy_nodes(nodes: list[dict]) -> list[dict]:
    result = []
    for node in nodes:
        if isinstance(node, dict):
            result.append(normalize_proxy_node(node))
    return result
