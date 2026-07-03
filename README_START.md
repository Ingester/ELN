# ELN App — 启动与使用

## 日常使用（推荐）
- 桌面上的 **「ELN 实验记录」** 图标：双击即可。它会静默确保后台服务在跑，然后打开界面。
- 已设置**开机自启**：登录 Windows 后，`启动` 文件夹里的 **「ELN 后台服务」** 会静默把服务拉起（无窗口、无黑框）。
- 后台由 `run_web.py` 提供，占用很小；日志超过 2MB 会自动清空。

## 访问地址
- **本机（电脑上用）**：`http://127.0.0.1:8550/`
- **手机（同一局域网）**：`http://<你的电脑IP>:8550/`，IP 在 App 的「设置」里能看到。
- **公网（Cloudflare）**：你的 Cloudflare 域名（`https://<子域>.<你的域名>`）。
  ⚠️ 端口已从 8000 改到 **8600**（原因见下）。请到 Cloudflare 面板
  `Networks → Tunnels → 你的隧道 → Public Hostname`，把 Service URL 从
  `localhost:8000` 改成 `localhost:8600`，公网访问才会指向 ELN。

## 端口说明
- Flet 界面：`8550`
- 数据 API / 手机原生执行页 `/run`：默认 **8600**（可用环境变量 `ELN_API_PORT` 覆盖）。
- 之所以从 8000 改到 8600：WSL 里的 `claude-science` 在镜像网络模式下占用了
  本机 `8000`，导致 ELN 的 API 无法启动。换到 8600 后互不冲突。

## 手动启动脚本（备用）
- `start_eln_background.ps1`：静默后台启动（桌面/自启快捷方式就是调它）。
- `start_eln_cloudflare.ps1`：需要先 `setx ELN_AUTH_PASSWORD ...` 设访问密码，
  用于对外的 Cloudflare Tunnel 模式。

## 数据位置
`C:\Users\Ingester\ELN_Data`（在项目文件夹之外，升级代码不影响数据）。
