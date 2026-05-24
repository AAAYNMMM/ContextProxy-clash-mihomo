# ContextProxy

## 基于上下文的智能分流

ContextProxy 的核心优势是“按上下文分流”，而不是传统代理客户端常见的单一全局代理或简单域名规则。它可以结合浏览器 Tab 上报、请求域名、App 进程规则，判断流量应该进入哪个分组，例如 Proxy、AI、Media 或用户自定义分组。

这种方式更适合现代使用场景：

### 软件根据进程名进入不同分组，减少手动切换节点和规则维护成本。

### 在可添加扩展的浏览器中，规则仅仅只需要填写标签站的域名，网页内的静态and动态域名都会归于标签域名之下，极大减少了维护规则的成本。

---

## 主要功能

- 图形化桌面客户端，基于 PySide6
- 系统代理开关，默认代理入口为 `127.0.0.1:18000`
- 单 mihomo 核心，多 listener 分组入口
- 浏览器扩展上报 `tabHost / requestHost`
- 域名规则分流
- 应用进程规则分流
- 无规则流量默认 Direct
- 命中代理分组后不降级 Direct
- 订阅添加、更新、删除
- 节点池管理与延迟测试
- 动态分组管理
- 规则管理
- 自动选择当前分组最低延迟节点
- 当前节点异常后自动重选当前分组节点
- 系统托盘控制
- 最近活动日志
- 浏览器先启动、代理后启动时的补报机制

---

## 当前架构

```text
浏览器 / 应用
    ↓
Windows 系统代理
127.0.0.1:18000
    ↓
Python 前置代理 tcp_proxy
    ↓
根据 Tab 上报 / 域名规则 / App 进程规则判断 final_group
    ↓
Direct：Python 直接连接目标
代理分组：转发到 mihomo 对应 listener 端口
    ↓
单 mihomo 核心
    ├─ Proxy listener
    ├─ AI listener
    ├─ Media listener
    └─ 自定义分组 listener
```

### 分流原则

```text
Tab / 域名规则命中代理分组 → 走对应分组
App 进程规则命中代理分组 → 走对应分组
未命中规则 → Direct
命中代理分组但节点不可用 → 拒绝 / 失败，不降级 Direct
```

---

## 目录结构

```text
ContextProxy/
  backend/                 后端核心逻辑
  gui/                     桌面 GUI
  extension/               浏览器扩展
  config/                  配置文件
  mihomo/                  mihomo 内核与自动生成配置
  logs/                    运行日志
  app_processes.txt        应用进程规则
  groups_domains.txt       域名规则
  requirements.txt         Python 依赖
  README.md
```

---

## 必须保留的配置文件

打包或发布时，建议保留以下默认配置：

```text
config/app_settings.yaml
config/group_nodes.yaml
config/node_pool.yaml
config/subscriptions/subscriptions.yaml
groups_domains.txt
app_processes.txt
mihomo/mihomo-windows-amd64-compatible.exe
```

可以删除的运行时文件：

```text
logs/
__pycache__/
*.pyc
config/runtime_selected_nodes.yaml
mihomo/config.yaml
mihomo/config.yaml.tmp
mihomo/config-*.yaml
mihomo/config-delay-test*.yaml
config/subscriptions/*_nodes.yaml
config/subscriptions/*_nodes.json
```

---

## 依赖安装

建议使用 Python 3.12。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

推荐 `requirements.txt`：

```txt
PySide6>=6.6
PyYAML>=6.0
requests>=2.31
psutil>=5.9
fastapi>=0.110
uvicorn>=0.27
pydantic>=2.0
pyinstaller>=6.0
```

---

## 开发环境启动

以 GUI 作为主入口：

```powershell
.\.venv\Scripts\python.exe gui\app.py
```

当前版本中，`gui/app.py` 是客户端主程序入口。`main.py` 可以保留作为命令行调试入口，但 GUI 正常运行时不应再通过子进程启动 `main.py`。

---

## 浏览器扩展使用

1. 打开浏览器扩展管理页面
2. 开启开发者模式
3. 选择“加载已解压的扩展程序”
4. 选择项目中的 `extension/` 目录
5. 在扩展设置中确认上报地址与客户端一致：

```text
receiver_host = 127.0.0.1
receiver_port = 17890
```

客户端对应配置在：

```yaml
proxy:
  receiver_port: 17890
```

如果浏览器先启动、代理后启动，扩展会在 receiver 恢复在线后补报当前标签页信息。已经建立的旧连接不会自动迁移到代理，刷新网页后可重新进入分流链路。

---

## 规则格式

### 域名规则

文件：

```text
groups_domains.txt
```

格式：

```text
分组,域名规则
```

示例：

```text
Proxy,*.google.com
AI,*.openai.com
Media,*.youtube.com
```

### 应用进程规则

文件：

```text
app_processes.txt
```

格式：

```text
分组,进程名
```

示例：

```text
Proxy,chrome.exe
Proxy,Code.exe
AI,ChatGPT.exe
```

---

## 分组管理

分组配置文件：

```text
config/group_nodes.yaml
```

每个分组包含：

```yaml
groups:
  Proxy:
    port: 7890
    controller: 9090
    nodes: []
```

说明：

- `port` 是该分组在单 mihomo 核心中的 listener 入口端口
- `controller` 字段为历史兼容字段，单核心架构下真实 controller 使用全局 controller
- `Proxy` 为保留分组，不建议删除
- 代理运行中分组管理为只读，避免 listener 端口被运行中的 mihomo 占用时误判冲突
- 停止代理后可以修改分组、端口、节点列表

---

## 节点池与测速

节点池文件：

```text
config/node_pool.yaml
```

节点池测速调用正式 mihomo 核心的 external-controller，不再启动额外测速 mihomo。

测速逻辑：

```text
GET /proxies/{node_name}/delay?url=http://www.gstatic.com/generate_204&timeout=5000
```

测速结果只保存在 GUI 内存中，不写入持久化文件。

---

## 自动选择策略

当前自动选择策略：

1. 启动代理后，每个分组选择一次最低延迟节点
2. 当前节点正常时不切换
3. 当前节点连续失败后，只重测当前故障分组
4. 手动点击“重新自动选择”时，只测试当前分组
5. 节点发生变化后，断开该分组旧连接
6. 所有节点不可用时保持当前选择，不降级 Direct

---

## 系统托盘

托盘菜单包含：

- 显示主窗口
- 启动 / 停止代理
- 退出

关闭窗口时，如果启用了“关闭窗口最小化到托盘”，程序会隐藏到托盘而不是退出。

真正退出时会尝试：

- 关闭系统代理
- 停止后端服务
- 停止 mihomo
- 清理后台线程

---

## 日志

GUI 最近活动只保留关键事件，例如：

```text
代理已启动
系统代理已启用
Tab 分流：example.com / api.example.com -> Proxy
App 分流：chrome.exe / android.clients.google.com -> Proxy
规则已保存
节点池延迟测试完成
自动选择完成
```

详细日志位于：

```text
logs/console.log
logs/activity.log
logs/routing.log
logs/tcp.log
logs/rules.log
```


---


---

## License

建议使用 GPL-3.0。

如果发布包中包含 mihomo 内核，请同时遵守 mihomo 自身许可证要求，并在发布说明中注明本项目依赖 mihomo。不要删除第三方项目的许可证声明。

---

## 免责声明

本项目仅用于本地网络分流与代理管理。请遵守所在地法律法规以及相关网络服务条款。项目不提供任何代理节点、订阅服务或绕过访问限制的保证。用户应自行承担配置、使用和分发本项目带来的责任。
