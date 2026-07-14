# ContextProxy

## 基于上下文的智能分流

ContextProxy 的核心优势是“按上下文分流”，而不是传统代理客户端常见的单一全局代理或简单域名规则。它可以结合浏览器 Tab 上报、请求域名、App 进程规则，判断流量应该进入哪个分组，例如 Proxy、AI、Media 或用户自定义分组。

## 本客户端使用的是clash格式订阅链接

## 客户端截图

<img width="1220" height="801" alt="Snipaste_2026-05-26_02-17-57" src="https://github.com/user-attachments/assets/76adc12f-0eb5-4431-9a29-3bde5af5f71f" />

<img width="1220" height="801" alt="Snipaste_2026-05-26_02-18-30" src="https://github.com/user-attachments/assets/8ca99359-a895-4e3a-b366-8d8aebc6fa97" />

<img width="1220" height="801" alt="Snipaste_2026-05-26_02-18-48" src="https://github.com/user-attachments/assets/e1f4455e-9359-482e-b700-f0534ef2afb3" />

<img width="1220" height="801" alt="Snipaste_2026-05-26_02-19-12" src="https://github.com/user-attachments/assets/8a6ac435-4119-4ff6-bd25-3dcc98c47771" />

<img width="1220" height="801" alt="Snipaste_2026-05-26_02-19-28" src="https://github.com/user-attachments/assets/35d2bb4d-be5a-4552-992a-859392e056f3" />

<img width="1220" height="801" alt="Snipaste_2026-05-26_02-19-37" src="https://github.com/user-attachments/assets/c06f109d-9bf4-46fe-bbff-58f98fab5fe0" />

---

## 主要功能

- 图形化桌面客户端，基于 PySide6
- 系统代理开关，默认代理入口为 `127.0.0.1:18000`
- 单 mihomo 核心，多 listener 分组入口
- 浏览器扩展上报 `tabHost / requestHost`
- 域名规则分流
- 应用进程规则分流
- 无规则流量默认 Direct
- 订阅添加、更新、删除
- 节点池管理与延迟测试
- 动态分组管理
- 规则管理
- 自动选择节点
- 当前节点异常后自动重选当前分组节点
- 系统托盘控制
- 最近活动日志

---

## Firefox 扩展

Firefox 使用 .xpi 包安装扩展。在项目根目录运行：

    powershell -ExecutionPolicy Bypass -File .\scripts\package-firefox-extension.ps1

生成文件位于 dist/ContextProxy-Reporter-firefox-<version>.xpi。该包已包含 Firefox 的后台脚本兼容配置和固定 Gecko 扩展 ID。

Firefox 正式版和 Beta 版只能安装 Mozilla 签名后的 XPI；脚本生成的是待签名包。开发版、Nightly 或按企业策略配置的 ESR 可用于未签名测试。签名后可通过 AMO 的自分发渠道或文件安装方式分发。

---

## 本项目不实现 TUN 模式。

原因：
1. 项目核心定位是系统代理模式下的上下文分流客户端。
2. Go core 只负责 HTTP/CONNECT 前置代理、Tab 上报、App 反查、规则分流和转发。
3. 不处理虚拟网卡、路由表、DNS 劫持、Fake-IP、UDP/QUIC、Wintun、WinDivert 等 TUN 相关功能。
4. 如果用户需要完整 TUN 接管系统流量，建议使用 Clash Verge Rev、v2rayN、sing-box、mihomo party 等其他成熟代理软件。
5. 本项目以后也不把 TUN 作为规划功能，避免影响当前架构稳定性。
---

## License

GPL-3.0。

本项目依赖 mihomo。

---

## 免责声明

本项目仅用于本地网络分流与代理管理。请遵守所在地法律法规以及相关网络服务条款。项目不提供任何代理节点、订阅服务或绕过访问限制的保证。用户应自行承担配置、使用和分发本项目带来的责任。
