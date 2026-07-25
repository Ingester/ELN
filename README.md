# ELN

一个面向实验室日常记录的本地优先电子实验记录本。应用提供实验 stepper、Markdown 数据记录、图片与文件附件、语音速记、待归档收件箱、计时器/闹钟、实验历史、样品盒管理，以及可选的 AI 整理和语音转写。

默认以 FastAPI 原生网页运行：

- 本机入口：`http://127.0.0.1:8600/run`
- 速记入口：`http://127.0.0.1:8600/capture`
- 局域网入口：`http://<电脑局域网 IP>:8600/run`
- 默认数据目录：`C:\Users\<用户名>\ELN_Data`

## 系统要求

- Windows 10/11
- Python 3.10 或更新版本
- Git（仅克隆和更新代码时需要）
- 可选：Cloudflare Tunnel，用于从实验室外访问

## 安装

在 PowerShell 中执行：

```powershell
git clone https://github.com/Ingester/ELN.git
cd ELN

py -3.10 -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

如果电脑没有 `py` 启动器，也可以使用：

```powershell
python -m venv .venv
```

## 启动

在已激活虚拟环境的 PowerShell 中运行：

```powershell
python run_web.py
```

然后打开：

```text
http://127.0.0.1:8600/run
```

默认只启动原生网页，不启动旧版 Flet 外壳。如确实需要旧版 Flet：

```powershell
$env:ELN_START_FLET = "1"
python run_web.py
```

### Windows 后台启动

仓库包含 `start_eln_background.ps1`，可用于静默启动并维持闹钟/计时器。该脚本中的 `$python` 必须指向实际 Python：

```powershell
$python = "C:\path\to\ELN\.venv\Scripts\python.exe"
```

确认路径后运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\start_eln_background.ps1 -OpenBrowser
```

需要开机自启时，可为该脚本创建快捷方式并放入：

```text
shell:startup
```

## 数据与备份

实验数据不会保存在 Git 仓库中。默认数据目录为：

```text
C:\Users\<用户名>\ELN_Data
```

其中包括 SQLite 数据库、照片、上传附件、报告和本地设置。升级代码不会覆盖该目录，但仍建议定期完整备份 `ELN_Data`。

以下内容已被 `.gitignore` 排除，不应提交到 GitHub：

- 数据库文件
- `ELN_Data/`
- `photos/`、`uploads/`、`reports/`
- `.env` 和本地密钥
- 日志与缓存
- Cloudflare 本地配置和凭据

## 基本配置

常用环境变量：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `ELN_API_HOST` | `0.0.0.0` | API 监听地址；仅本机使用可设为 `127.0.0.1` |
| `ELN_API_PORT` | `8600` | 原生网页和 API 端口 |
| `ELN_NATIVE_ONLY` | `1` | 使用原生网页，不启动旧版 Flet 外壳 |
| `ELN_START_FLET` | 未启用 | 设为 `1` 时启动旧版 Flet 外壳 |
| `ELN_AUTH_PASSWORD` | 未设置 | 公网访问密码 |
| `ELN_AUTH_COOKIE_SECRET` | 自动生成 | 公网模式建议设置独立的长随机值 |

PowerShell 当前会话示例：

```powershell
$env:ELN_API_HOST = "127.0.0.1"
$env:ELN_API_PORT = "8600"
$env:ELN_AUTH_PASSWORD = "replace-with-a-long-password"
$env:ELN_AUTH_COOKIE_SECRET = "replace-with-another-long-random-secret"
python run_web.py
```

永久保存当前 Windows 用户的公网密码：

```powershell
setx ELN_AUTH_PASSWORD "replace-with-a-long-password"
setx ELN_AUTH_COOKIE_SECRET "replace-with-another-long-random-secret"
```

重新打开 PowerShell 后生效。

## AI 整理与语音转写

AI 整理和转写密钥可在应用的设置页面配置，配置保存在本机 `ELN_Data\settings.json`，不会进入 Git。

支持的转写方式：

- 本地转写：安装 `faster-whisper`
- 腾讯云 ASR
- OpenAI 兼容转写接口

本地转写安装：

```powershell
python -m pip install faster-whisper
$env:ELN_WHISPER_MODEL = "small"
```

也可以使用环境变量配置云端转写：

```powershell
$env:ELN_TRANSCRIBE_PROVIDER = "openai"
$env:OPENAI_API_KEY = "your-key"
$env:ELN_OPENAI_TRANSCRIBE_MODEL = "gpt-4o-mini-transcribe"
```

腾讯云对应变量为：

```text
TENCENTCLOUD_SECRET_ID
TENCENTCLOUD_SECRET_KEY
TENCENTCLOUD_REGION
```

不要把真实密钥写入仓库文件。

## Cloudflare 公网访问

详细说明见 [cloudflare/README.md](cloudflare/README.md)。

公网访问至少应同时配置：

1. Cloudflare Tunnel 指向 `http://localhost:8600`
2. `ELN_AUTH_PASSWORD`
3. Cloudflare Access，仅允许指定账号访问

安装 `cloudflared`：

```powershell
winget install --id Cloudflare.cloudflared
```

本地配置模式可从示例开始：

```powershell
Copy-Item .\cloudflare\config.example.yml .\cloudflare\config.yml
```

`cloudflare/config.yml` 和凭据 JSON 已被 Git 忽略。

## 测试

```powershell
python -m pip install pytest
python -m pytest
```

## 更新

先停止正在运行的 ELN 服务，再在代码目录执行：

```powershell
git pull
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python run_web.py
```

数据库结构会在启动时自动初始化或补充。更新前仍建议备份 `ELN_Data`。
