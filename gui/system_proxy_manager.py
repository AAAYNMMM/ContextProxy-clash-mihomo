import ctypes


INTERNET_SETTINGS_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
INTERNET_OPTION_SETTINGS_CHANGED = 39
INTERNET_OPTION_REFRESH = 37


def _context_proxy_server(host: str, port: int) -> str:
    return f"{host}:{port}"


def _notify_proxy_changed():
    try:
        wininet = ctypes.windll.Wininet
        wininet.InternetSetOptionW(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
        wininet.InternetSetOptionW(0, INTERNET_OPTION_REFRESH, 0, 0)
    except Exception:
        pass


def _open_settings_key(access):
    import winreg

    return winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        INTERNET_SETTINGS_KEY,
        0,
        access,
    )


def get_current_system_proxy() -> dict:
    try:
        import winreg

        with _open_settings_key(winreg.KEY_READ) as key:
            try:
                proxy_enable = winreg.QueryValueEx(key, "ProxyEnable")[0]
            except FileNotFoundError:
                proxy_enable = 0

            try:
                proxy_server = winreg.QueryValueEx(key, "ProxyServer")[0]
            except FileNotFoundError:
                proxy_server = ""

            try:
                proxy_override = winreg.QueryValueEx(key, "ProxyOverride")[0]
            except FileNotFoundError:
                proxy_override = ""

    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "proxy_enable": 0,
            "proxy_server": "",
            "proxy_override": "",
        }

    return {
        "ok": True,
        "error": None,
        "proxy_enable": int(proxy_enable or 0),
        "proxy_server": str(proxy_server or ""),
        "proxy_override": str(proxy_override or ""),
    }


def is_contextproxy_system_proxy(host="127.0.0.1", port=18000) -> bool:
    current = get_current_system_proxy()
    return bool(current.get("ok") and current.get("proxy_server") == _context_proxy_server(host, port))


def enable_system_proxy(host="127.0.0.1", port=18000) -> tuple[bool, str | None]:
    try:
        import winreg

        with _open_settings_key(winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, _context_proxy_server(host, port))
            winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, "<local>")

        _notify_proxy_changed()
    except Exception as exc:
        return False, str(exc)

    return True, "\u7cfb\u7edf\u4ee3\u7406\u5df2\u542f\u7528"


def disable_system_proxy_if_contextproxy(host="127.0.0.1", port=18000) -> tuple[bool, str | None]:
    current = get_current_system_proxy()
    if not current.get("ok"):
        return False, current.get("error") or "\u8bfb\u53d6\u7cfb\u7edf\u4ee3\u7406\u5931\u8d25"

    if current.get("proxy_server") != _context_proxy_server(host, port):
        return True, "\u5f53\u524d\u7cfb\u7edf\u4ee3\u7406\u4e0d\u662f ContextProxy\uff0c\u672a\u4fee\u6539"

    try:
        import winreg

        with _open_settings_key(winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
            winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, "")
            winreg.SetValueEx(key, "ProxyOverride", 0, winreg.REG_SZ, "")

        _notify_proxy_changed()
    except Exception as exc:
        return False, str(exc)

    return True, "\u7cfb\u7edf\u4ee3\u7406\u5df2\u5173\u95ed"
