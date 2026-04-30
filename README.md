# OMO Relay / OMO 任务队列管理

> 一次安排，自动执行，安心摸鱼。

**English Name**: `omo-relay`  
**中文名**: `OMO 任务队列管理`

OMO Relay 是一个为 [OpenCode](https://opencode.ai) + [OMO](https://omo.sh) 设计的任务队列管理系统。你可以一次性写入多个任务，让它们按顺序自动执行，支持 **ULW 循环** 和 **Ralph 循环** 模式，实现真正的"安排完就去摸鱼"。

---

## 功能介绍

### 核心功能

| 功能 | 描述 |
|------|------|
| **任务队列管理** | 创建、排序、执行、跳过、重试、删除任务 |
| **三种执行模式** | 单次执行 (One-Shot)、ULW 循环、Ralph 循环 |
| **自动接力执行** | 当前任务完成后自动启动下一个任务 |
| **智能监控 (Watcher)** | 实时监控 OpenCode 会话状态，自动判断何时发送下一个任务 |
| **失败重试机制** | 指数退避重试，最大重试次数可配置 |
| **邮件通知** | 任务完成或队列清空时发送邮件通知 |
| **Web 管理界面** | 基于 Vue 3 的现代化管理面板 |
| **多项目管理** | 支持管理多个 OpenCode 项目的任务队列 |
| **会话选择** | 可指定任务发送到特定的 OpenCode 会话 |
| **Tmux 集成** | 自动创建和管理 tmux 会话来运行 OpenCode |
| **恢复机制** | 服务重启后自动恢复未完成的任务 |

### 执行模式说明

- **单次执行 (One-Shot)**: 发送一次 prompt，等待完成
- **ULW 循环 (Ultra Work Loop)**: 启动 `/ulw-loop` 命令，让 AI 持续工作直到任务完成
- **Ralph 循环**: 启动 `/ralph-loop` 命令，使用 Ralph 模式持续迭代

### 任务状态流转

```
待执行 (PENDING) → 运行中 (RUNNING) → 已完成 (DONE)
                          ↓
                   等待重试 (RETRY_WAIT) → 重试后回到 RUNNING
                          ↓
                   超过最大重试次数 → 保持 RETRY_WAIT (需手动干预)
                          ↓
                   手动跳过 → 已跳过 (SKIPPED)
```

---

## 系统架构

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   Web UI        │────▶│   Backend API   │────▶│   SQLite Store  │
│   (Vue 3)       │     │   (Python)      │     │   (omo_task_    │
└─────────────────┘     └─────────────────┘     │    queue.db)    │
        ▲                       │                └─────────────────┘
        │                       │
        │                       ▼
        │              ┌─────────────────┐
        │              │   Watcher       │
        │              │   (监控循环)     │
        │              └─────────────────┘
        │                       │
        │                       ▼
        │              ┌─────────────────┐
        └──────────────│   OpenCode      │
                       │   Session       │
                       └─────────────────┘
```

### 核心模块

| 模块 | 文件 | 职责 |
|------|------|------|
| **状态机** | `src/omo_task_queue/state.py` | 任务状态定义与合法流转 |
| **调度器** | `src/omo_task_queue/dispatcher.py` | 任务分发、执行、自动推进 |
| **存储层** | `src/omo_task_queue/store.py` | SQLite 持久化、原子 claim |
| **观察者** | `src/omo_task_queue/opencode_observer.py` | 读取 OpenCode DB 监控会话状态 |
| **会话续写** | `src/omo_task_queue/session_continuer.py` | 通过 tmux 向 OpenCode 发送任务 |
| **监控循环** | `src/omo_task_queue/watch.py` | 主循环：观察→决策→执行 |
| **重试管理** | `src/omo_task_queue/retry.py` | 指数退避重试策略 |
| **Web 服务** | `src/omo_task_queue/ui/server.py` | HTTP API 服务 |
| **前端面板** | `frontend/src/App.vue` | Vue 3 管理界面 |

---

## 安装

### 环境要求

- **Python**: 3.9+
- **Node.js**: 18+ (用于前端构建)
- **Tmux**: 必须安装并可用
- **OpenCode**: 已安装并配置 (`~/.local/bin/opencode`)

#### 安装 Tmux

Tmux 是 OMO Relay 的核心依赖，用于创建后台会话来运行 OpenCode 和任务调度。

**macOS:**
```bash
brew install tmux
```

**Ubuntu/Debian:**
```bash
sudo apt-get update && sudo apt-get install -y tmux
```

**CentOS/RHEL/Fedora:**
```bash
sudo yum install -y tmux
# 或
sudo dnf install -y tmux
```

**验证安装:**
```bash
tmux -V
# 应输出类似: tmux 3.3a
```

> **注意**: 脚本默认查找 `~/.local/bin/tmux` 或系统 PATH 中的 tmux。如果安装在其他位置，请设置环境变量 `TMUX_BIN`:
> ```bash
> export TMUX_BIN=/usr/local/bin/tmux
> ```

### 1. 克隆仓库

```bash
git clone https://github.com/tavisWei/omo-relay.git
cd omo-relay
```

### 2. 安装 Python 依赖

```bash
# 使用 pip
pip install -r requirements.txt

# 或使用 uv
uv pip install -r requirements.txt
```

### 3. 安装前端依赖

```bash
cd frontend
npm install
```

### 4. 构建前端

```bash
npm run build
```

---

## 更新

```bash
# 拉取最新代码
git pull origin main

# 更新 Python 依赖
pip install -r requirements.txt --upgrade

# 更新前端依赖并重新构建
cd frontend
npm install
npm run build
```

---

## 启动 / 重启项目

项目包含四个核心服务，使用提供的脚本一键管理：

### 一键启动所有服务

```bash
./scripts/restart-all.sh
```

这会依次启动：
1. **Backend** - Python API 服务
2. **Frontend** - Vue 开发服务器
3. **OpenCode Tmux** - tmux 会话中的 OpenCode
4. **Watcher** - 任务监控循环

### 单独管理服务

```bash
# 仅重启后端
./scripts/restart-backend.sh

# 仅重启前端
./scripts/restart-frontend.sh

# 仅重启 OpenCode tmux 会话
./scripts/restart-opencode-tmux.sh

# 仅重启 Watcher 监控
./scripts/restart-watcher.sh
```

### 服务说明

| 服务 | 脚本 | 默认端口 | 说明 |
|------|------|----------|------|
| Backend | `restart-backend.sh` | 动态分配 (20000-29999) | Python HTTP API |
| Frontend | `restart-frontend.sh` | 动态分配 (30000-39999) | Vue 开发服务器 |
| Watcher | `restart-watcher.sh` | - | 后台监控进程 |
| Tmux | `restart-opencode-tmux.sh` | - | OpenCode 终端会话 |

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `OMO_UI_HOST` | `127.0.0.1` | 后端监听地址 |
| `OMO_UI_PORT` | 动态 | 后端端口 |
| `FRONTEND_HOST` | `127.0.0.1` | 前端监听地址 |
| `FRONTEND_PORT` | 动态 | 前端端口 |
| `BACKEND_START_TIMEOUT` | `20` | 后端启动超时(秒) |
| `FRONTEND_START_TIMEOUT` | `20` | 前端启动超时(秒) |
| `WATCHER_POLL_INTERVAL` | `5` | Watcher 轮询间隔(秒) |
| `WATCHER_START_TIMEOUT` | `20` | Watcher 启动超时(秒) |

### 停止服务

```bash
# 查找并停止进程
kill $(cat .backend.*.pid)
kill $(cat .frontend.*.pid)
kill $(cat .watcher.*.pid)

# 或者停止 tmux 会话
tmux kill-session -t omo-$(python3 -c "import hashlib; print(hashlib.sha256('$(pwd)'.encode()).hexdigest()[:12])")
```

---

## 配置

配置文件位于项目根目录：`omo_task_queue.json`

```json
{
  "idle_threshold": 30,
  "soft_stalled_threshold": 90,
  "stalled_threshold": 300,
  "max_retries": 3,
  "retry_backoff_seconds": 5,
  "notification_settings": {
    "enabled": true,
    "smtp_host": "smtp.example.com",
    "smtp_port": 587,
    "smtp_user": "your-email@example.com",
    "smtp_password": "your-password",
    "smtp_use_tls": true,
    "smtp_use_ssl": false,
    "sender": "your-email@example.com",
    "recipient": "notify-to@example.com"
  }
}
```

### 配置项说明

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `idle_threshold` | int | 30 | 会话空闲多少秒后认为可以续写 |
| `soft_stalled_threshold` | int | 90 | 软停滞阈值(秒) |
| `stalled_threshold` | int | 300 | 硬停滞阈值(秒) |
| `max_retries` | int | 3 | 任务最大重试次数 |
| `retry_backoff_seconds` | int | 5 | 重试退避基数(秒) |
| `notification_settings.enabled` | bool | false | 是否启用邮件通知 |
| `notification_settings.smtp_*` | string | - | SMTP 服务器配置 |

---

## 使用 Web 界面

启动后访问前端地址（默认 `http://127.0.0.1:30000+`）：

### 任务队列管理

1. **添加任务**: 在"新建任务"区域输入 Prompt，选择执行模式，点击"添加任务"
2. **调整顺序**: 点击任务旁边的 ↑ ↓ 箭头调整执行顺序
3. **操作任务**:
   - ⏭ 跳过: 跳过当前任务
   - ✓ 完成: 手动标记为完成
   - ↻ 重试: 失败后的任务重新执行
   - ✕ 删除: 删除任务

### 通知管理

在"通知管理"标签页配置 SMTP，测试并保存邮件通知设置。

---

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/queue` | 获取任务队列 |
| GET | `/api/queue/running` | 获取正在运行的任务 |
| POST | `/api/queue` | 添加新任务 |
| POST | `/api/queue/reorder` | 重新排序任务 |
| POST | `/api/queue/{id}/skip` | 跳过任务 |
| POST | `/api/queue/{id}/done` | 标记完成 |
| POST | `/api/queue/{id}/retry` | 重试任务 |
| DELETE | `/api/queue/{id}` | 删除任务 |
| GET | `/api/status` | 获取系统状态 |
| GET | `/api/sessions` | 获取会话列表 |
| POST | `/api/sessions/select` | 选择会话 |
| GET | `/api/projects` | 获取项目列表 |
| GET | `/api/notify/config` | 获取通知配置 |
| POST | `/api/notify/config` | 保存通知配置 |
| POST | `/api/notify/test` | 发送测试邮件 |

---

## 开发

### 运行测试

```bash
# 运行所有测试
pytest

# 运行特定测试
pytest tests/test_dispatcher.py
pytest tests/test_integration.py

# 带覆盖率
pytest --cov=src/omo_task_queue --cov-report=html
```

### 代码检查

```bash
# 格式化
ruff format .

# 检查
ruff check .

# 类型检查
mypy .
```

### 前端开发

```bash
cd frontend
npm run dev      # 开发服务器
npm run build    # 生产构建
npm run preview  # 预览构建
```

---

## 项目结构

```
.
├── src/omo_task_queue/          # Python 后端源码
│   ├── __init__.py
│   ├── state.py                  # 状态机
│   ├── dispatcher.py             # 任务调度器
│   ├── store.py                  # SQLite 存储
│   ├── watch.py                  # 监控循环
│   ├── opencode_observer.py      # OpenCode 观察者
│   ├── session_continuer.py      # 会话续写
│   ├── retry.py                  # 重试管理
│   ├── recovery.py               # 恢复管理
│   ├── notifier.py               # 邮件通知
│   ├── tmux_target.py            # Tmux 管理
│   ├── project_registry.py       # 项目注册表
│   ├── session_selection.py      # 会话选择
│   ├── status_provider.py        # 状态提供
│   ├── logging_config.py         # 日志配置
│   └── ui/                       # Web 界面
│       ├── server.py             # HTTP 服务器
│       └── panel.py              # 面板逻辑
├── frontend/                     # Vue 3 前端
│   ├── src/
│   │   ├── App.vue               # 主应用
│   │   ├── api/client.js         # API 客户端
│   │   ├── components/           # 组件
│   │   ├── views/                # 页面
│   │   └── styles/               # 样式
│   └── package.json
├── scripts/                      # 启动脚本
│   ├── restart-all.sh
│   ├── restart-backend.sh
│   ├── restart-frontend.sh
│   ├── restart-watcher.sh
│   └── restart-opencode-tmux.sh
├── tests/                        # 测试用例
├── omo_task_queue.json           # 配置文件
├── omo_task_queue.db             # SQLite 数据库
└── README.md                     # 本文件
```

---

## 许可证

[MIT License](LICENSE)

---

## 贡献

欢迎提交 Issue 和 Pull Request！

---

## 致谢

- [OpenCode](https://opencode.ai) - AI 编程助手
- [OMO](https://omo.sh) - 智能开发工具
- [Vue.js](https://vuejs.org) - 前端框架
- [Vite](https://vitejs.dev) - 构建工具
