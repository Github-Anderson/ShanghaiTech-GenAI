# ShanghaiTech GenAI

将 ShanghaiTech GenAI 接入 DeepSeek Harness 的本地网关与 Windows / macOS 部署工具。当前版本为 **1.0.5**，配套插件适用于 **DSH Desktop ≥ 0.2.1-rc.2**。

网关提供模型列表、对话、联网搜索、Thinking 选项和网站附件接口；DSH 插件提供模型来源、会话选项及基于 Standard 的 Agent 预设。

## 开始使用

Windows 和 macOS 均使用 Docker Desktop 与 DSH Desktop。按对应系统操作，网站凭证导入步骤共用。

### Windows

安装 Docker Desktop（Linux containers）和 DSH Desktop，在 PowerShell 中进入网关仓库：

```powershell
git clone git@github.com:Github-Anderson/ShanghaiTech-GenAI.git
cd ShanghaiTech-GenAI
powershell -ExecutionPolicy Bypass -File .\Generate-Key.ps1 -Copy
```

在 DSH 插件安装界面提交 `github:Github-Anderson/dsh-shanghaitech-genai#v1.0.4`，在凭证管理中将剪贴板中的密钥保存为 `SHANGHAITECH_GENAI_API_KEY`。完全退出 DSH（包括托盘后台），运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\Start.ps1
```

脚本会启动网关、DSH 和网页。Windows 本地源码开发也支持双仓库同步部署，见[本地开发环境](tools/README.md)。

### macOS

安装 Git、Python 3（`python3`）、Docker Desktop 和 DSH Desktop。默认 DSH 安装位置为 `/Applications/DeepSeek Harness.app`；先启动 Docker Desktop，再在终端执行：

```sh
git clone git@github.com:Github-Anderson/ShanghaiTech-GenAI.git
git clone git@github.com:Github-Anderson/dsh-shanghaitech-genai.git
./ShanghaiTech-GenAI/tools/Sync-Local.sh
./ShanghaiTech-GenAI/tools/Deploy-Local.sh
```

两个仓库保持同级。同步脚本会在同级 `local/` 创建长期运行副本、生成缺失的配置与密钥，并打包当前插件源码。部署脚本安装本地插件、重建网关，将密钥自动同步到 DSH 凭证引用 `SHANGHAITECH_GENAI_API_KEY`，并执行健康与认证检查。

完全退出 DSH（包括后台进程），从两个仓库的父目录运行：

```sh
./local/deploy/Start.sh
open http://127.0.0.1:8000/
```

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
| `Start.sh`、`Start-Gateway.sh` | 在同步后的 `local/deploy/` 中启动 DSH 与网关，或只重建网关 |
| `Sync-Key-mac.cjs` | 将网关密钥同步到 macOS DSH 凭证管理 |
| `Generate-Key.ps1` | Windows：生成私有配置与密钥，可复制密钥到剪贴板 |
| `Start.ps1`、`Start-Gateway.ps1` | Windows：启动 DSH 与网关，或只重建网关 |
| `Sync-Key.cjs` | Windows：同步 DSH 网关凭证 |
| `tools/Sync-Local.ps1`、`tools/Deploy-Local.ps1` | Windows：同步源码、打包并部署本地插件与网关 |
| `userscript/` | 网站凭证导出脚本 |

插件源码独立维护于 [dsh-shanghaitech-genai](https://github.com/Github-Anderson/dsh-shanghaitech-genai)，Windows / macOS 本地源码部署均从当前插件源码生成唯一名称的本地包，不依赖已发布标签；也可按插件仓库说明安装 `v1.0.4` Git 标签。

## 本地配置

开发者长期本地部署、插件打包及源码同步见 [本地开发环境](tools/README.md)。运行副本位于源码仓库外，修改代码后先同步再部署。

Windows 直接部署使用仓库下的 `private/`（已加入 Git 忽略规则），由 `Generate-Key.ps1` 准备。Windows / macOS 双仓库本地部署使用 `local/deploy/private/`，保存网关密钥、网站凭证和运行配置，位于两个源码仓库之外。首次同步自动生成缺失的密钥与配置；后续同步保留已有内容，不将其同步回源码或放入插件包。网站凭证有效期结束后，需重新导出并导入。

## 许可证

本项目采用 [MIT License](LICENSE)。配套插件的许可证随安装包提供，其源码与版权说明见独立插件仓库。
