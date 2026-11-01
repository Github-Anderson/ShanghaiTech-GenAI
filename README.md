# ShanghaiTech GenAI

将 ShanghaiTech GenAI 接入 DeepSeek Harness 的本地网关与 Windows 部署工具。当前版本为 **1.0.3**，配套插件适用于 **DSH Desktop ≥ 0.2.1-rc.2**。

网关提供模型列表、对话、联网搜索、Thinking 选项和网站附件接口；DSH 插件提供模型来源、会话选项及基于 Standard 的 Agent 预设。

## 开始使用

安装 Docker Desktop（Linux containers）与 DSH Desktop，然后按[安装指南](安装指南.md)生成本地网关密钥、安装插件、启动服务并导入网站登录凭证。

```powershell
git clone git@github.com:Github-Anderson/ShanghaiTech-GenAI.git
cd ShanghaiTech-GenAI
powershell -ExecutionPolicy Bypass -File .\Generate-Key.ps1 -Copy
```

在 DSH 插件安装界面提交 `github:Github-Anderson/dsh-shanghaitech-genai#v1.0.3`，配置凭证引用 `SHANGHAITECH_GENAI_API_KEY`，完全退出 DSH 后运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\Start.ps1
```

默认网关地址为 `http://127.0.0.1:8000/`。首次使用需登录 ShanghaiTech GenAI 网站，使用 `userscript/export-credentials.user.js` 导出凭证，再通过网关网页导入。详细步骤见安装指南。

## 网站附件

网关与配套插件接受 PNG、JPEG、WebP、GIF 图片（单文件严格小于 20 MiB），以及 PDF、TXT、CSV、DOCX、XLS、XLSX、PPT、PPTX、HTML、JSON 文档（单文件严格小于 10 MiB）。所选模型需开放对应附件能力；文档需由网站成功提取文字，扫描件不提供 OCR 回退。

2026-11-01 使用 DeepSeek 的网站文档上传接口和合成样例验证：XLS、XLSX、PPT、PPTX、HTML、JSON 均返回文件 ID 并提取唯一文本标记；DOC、XML 被网站明确拒绝，保持关闭。旧版 Office 样例由 Microsoft Office 生成真实二进制文件，不通过改后缀模拟。该结果不表示所有模型、文档复杂度或接近大小上限的文件都已验证。

复测工具为 `tools/probe-document-formats.py`，先测试 TXT 对照，失败时停止判定格式；`--gateway --chat` 可验证经网关上传、解析及 DeepSeek 附件问答。只从指定私有目录读取凭证，不输出凭证或完整响应。

## 目录

| 路径 | 内容 |
| --- | --- |
| `gateway/` | Python 网关、静态管理页面与 Dockerfile |
| `config/container.example.json` | 不含凭据的容器配置示例 |
| `compose.yaml` | 本地 Docker Compose 部署 |
| `Generate-Key.ps1` | 生成 `private/` 下的密钥和运行配置 |
| `Start.ps1`、`Start-Gateway.ps1` | 启动网关与 DSH |
| `Sync-Key.cjs` | 将网关密钥同步到 DSH 凭证管理 |
| `userscript/` | 网站凭证导出脚本 |

插件源码独立维护于 [dsh-shanghaitech-genai](https://github.com/Github-Anderson/dsh-shanghaitech-genai)，通过 `v1.0.3` Git 标签安装，不再随部署仓库提供压缩包。

## 本地配置

开发者长期本地部署、插件打包及源码同步见 [本地开发环境](tools/README.md)。运行副本位于源码仓库外，修改代码后先同步再部署。

`private/` 保存网关密钥、网站凭证和运行配置，已加入 Git 忽略规则。克隆仓库后运行 `Generate-Key.ps1` 重新生成自己的配置。网站凭证有效期结束后，需重新导出并导入。

## 许可证

本项目采用 [MIT License](LICENSE)。配套插件的许可证随安装包提供，其源码与版权说明见独立插件仓库。
