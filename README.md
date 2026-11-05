# ShanghaiTech GenAI

将 ShanghaiTech GenAI 接入 DeepSeek Harness 的本地网关与 Windows / macOS 部署工具。当前版本为 **1.0.9**，配套插件适用于 **DSH Desktop ≥ 0.2.1-rc.2**。

网关提供模型列表、对话、联网搜索、Thinking 选项和网站附件接口；DSH 插件提供模型来源、会话选项及基于 Standard 的 Agent 预设。

## 开始使用

Windows 和 macOS 均使用 Docker Desktop 与 DSH Desktop。按对应系统操作，网站凭证导入步骤共用。

两个仓库保持同级。安装 Git、Docker Desktop 和 DSH Desktop，先启动 Docker Desktop；macOS 还需 Python 3。

```sh
git clone https://github.com/Github-Anderson/ShanghaiTech-GenAI.git
git clone https://github.com/Github-Anderson/dsh-shanghaitech-genai.git
```

macOS：

```sh
./ShanghaiTech-GenAI/tools/Setup.sh
```

Windows PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\ShanghaiTech-GenAI\tools\Setup.ps1
```

Windows 也可双击 `tools/Setup.cmd`。首次部署和后续更新使用同一入口，自动同步源码、打包安装插件并部署。运行文件存放在仓库之外：macOS 为 `~/.local/share/shanghaitech-genai/`，Windows 为 `%LOCALAPPDATA%\ShanghaiTech-GenAI\`。个人配置在运行目录的 `deploy/private/` 中。

部署完成后，完全退出 DSH，再运行该运行目录中的 `deploy/Start.sh` 或 `deploy/Start.ps1`。自定义路径和远程网关配置见[本地开发环境](tools/README.md)。

首次使用需登录 ShanghaiTech GenAI 网站，使用 `userscript/export-credentials.user.js` 导出凭证，再通过网关网页导入。健康和认证检查通过不代表真实模型调用已验证。详细步骤见[安装指南](安装指南.md)。

## 网站附件

网关与配套插件接受 PNG、JPEG、WebP、GIF 图片（单文件严格小于 20 MiB），以及 PDF、TXT、CSV、DOCX、XLS、XLSX、PPT、PPTX、HTML、JSON 文档（单文件严格小于 10 MiB）。所选模型需开放对应附件能力；文档需由网站成功提取文字，扫描件不提供 OCR 回退。

2026-11-01 使用 DeepSeek 的网站文档上传接口和合成样例验证：XLS、XLSX、PPT、PPTX、HTML、JSON 均返回文件 ID 并提取唯一文本标记；DOC、XML 被网站明确拒绝，保持关闭。旧版 Office 样例由 Microsoft Office 生成真实二进制文件，不通过改后缀模拟。该结果不表示所有模型、文档复杂度或接近大小上限的文件都已验证。

复测工具为 `tools/probe-document-formats.py`，先测试 TXT 对照，失败时停止判定格式；`--gateway --chat` 可验证经网关上传、解析及 DeepSeek 附件问答。只从指定私有目录读取凭证，不输出凭证或完整响应。

## 删除会话

网关页面的“删除当前会话”会删除关联的网站历史，并清空当前页面的对话。“新会话”只切换当前页面，不删除网站历史。

配套插件在 DSH 输入区提供“删除会话”：确认后永久删除本地会话及其子代理，可选择同时删除网站历史。网站删除失败时保留本地会话；本地全文搜索未启用不会影响删除成功。请先停止正在运行的会话。

删除映射保存在本地私有目录，网关重启后仍可清理已记录的网站会话。升级前已经丢失映射，或网站登录令牌已更换的旧会话，需到网站删除；DSH 可取消“同时删除网站历史”，仅删除本地会话。

## 目录

| 路径 | 内容 |
| --- | --- |
| `gateway/` | Python 网关、静态管理页面与 Dockerfile |
| `config/container.example.json` | 不含凭据的容器配置示例 |
| `compose.yaml` | 本地 Docker Compose 部署 |
| `tools/Sync-Local.sh` | 同步源码、准备私有配置并打包插件 |
| `tools/Deploy-Local.sh` | 安装本地插件、重建网关并检查部署 |
| `tools/local-macos.py` | macOS 同步、部署与启动实现 |
| `Start.sh`、`Start-Gateway.sh` | 在运行目录的 `deploy/` 中启动 DSH 与网关，或只重建网关 |
| `Sync-Key-mac.cjs` | 将网关地址与密钥同步到 macOS DSH 凭证管理 |
| `Generate-Key.ps1` | Windows：生成私有配置与密钥，可复制密钥到剪贴板 |
| `Start.ps1`、`Start-Gateway.ps1` | Windows：启动 DSH 与网关，或只重建网关 |
| `Sync-Key.cjs` | Windows：同步 DSH 网关凭证 |
| `tools/Sync-Local.ps1`、`tools/Deploy-Local.ps1` | Windows：同步源码、打包并部署本地插件与网关 |
| `userscript/` | 网站凭证导出脚本 |

插件源码独立维护于 [dsh-shanghaitech-genai](https://github.com/Github-Anderson/dsh-shanghaitech-genai)，Windows / macOS 本地源码部署均从当前插件源码生成唯一名称的本地包，不依赖已发布标签；也可按插件仓库说明安装 `v1.0.7` Git 标签。

## 本地配置

开发者长期本地部署、插件打包及源码同步见 [本地开发环境](tools/README.md)。运行副本位于源码仓库外，修改代码后先同步再部署。

个人配置位于运行目录的 `deploy/private/`，保存网关密钥、网站凭证和运行配置。首次同步生成缺失的配置和本地密钥，后续同步保留已有内容。安装脚本首次将 `dsh-gateway.json` 中的地址及对应密钥迁入 DSH 原生凭证库，地址留空时使用本地网关。之后可在插件详情页的“网关连接”中修改；普通更新和启动保留已保存的连接。网站凭证过期时重新导出并导入。

## 许可证

本项目采用 [MIT License](LICENSE)。配套插件的许可证随安装包提供，其源码与版权说明见独立插件仓库。
