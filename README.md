# ShanghaiTech GenAI

将 ShanghaiTech GenAI 接入 DeepSeek Harness 的本地网关与 Windows 部署工具。当前版本为 **1.0.2**，配套插件适用于 **DSH Desktop 0.2.1-rc.2**。

网关提供模型列表、对话、联网搜索、Thinking 选项和网站附件接口；DSH 插件提供模型来源、会话选项及基于 Standard 的 Agent 预设。

## 开始使用

安装 Docker Desktop（Linux containers）与 DSH Desktop，然后按[安装指南](安装指南.md)生成本地网关密钥、安装插件、启动服务并导入网站登录凭证。

```powershell
git clone git@github.com:Github-Anderson/ShanghaiTech-GenAI.git
cd ShanghaiTech-GenAI
powershell -ExecutionPolicy Bypass -File .\Generate-Key.ps1 -Copy
```

在 DSH 插件安装界面提交 `github:Github-Anderson/dsh-shanghaitech-genai#v1.0.1`，配置凭证引用 `SHANGHAITECH_GENAI_API_KEY`，完全退出 DSH 后运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\Start.ps1
```

默认网关地址为 `http://127.0.0.1:8000/`。首次使用需登录 ShanghaiTech GenAI 网站，使用 `userscript/export-credentials.user.js` 导出凭证，再通过网关网页导入。详细步骤见安装指南。

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

插件源码独立维护于 [dsh-shanghaitech-genai](https://github.com/Github-Anderson/dsh-shanghaitech-genai)，通过 `v1.0.1` Git 标签安装，不再随部署仓库提供压缩包。

## 本地配置

开发者长期本地部署、插件打包及源码同步见 [本地开发环境](tools/README.md)。运行副本位于源码仓库外，修改代码后先同步再部署。

`private/` 保存网关密钥、网站凭证和运行配置，已加入 Git 忽略规则。克隆仓库后运行 `Generate-Key.ps1` 重新生成自己的配置。网站凭证有效期结束后，需重新导出并导入。

## 许可证

本项目采用 [MIT License](LICENSE)。配套插件的许可证随安装包提供，其源码与版权说明见独立插件仓库。
