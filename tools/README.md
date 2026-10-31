# 本地开发环境

正式代码只在 `ShanghaiTech-GenAI` 和同级的 `dsh-shanghaitech-genai` 仓库中修改。运行副本在同级 `local/`，不进入任一源码仓库。

在网关仓库中首次执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\Sync-Local.ps1
powershell -ExecutionPolicy Bypass -File .\tools\Deploy-Local.ps1
```

已有私有配置时，首次同步可加 `-PrivateSource '旧部署目录\private'`，仅复制缺失的密钥、配置和网站凭证，不覆盖目标。没有配置时自动生成。旧目录保留。

可用 `-PluginRepo`、`-LocalRoot` 指定源码和输出目录；两个脚本均支持 `-DshDir` 指定 DSH 安装目录。需要 Git、Docker Desktop（Linux containers）和 DSH Desktop；打包使用 DSH 自带 Node/pnpm。

每次修改后，先同步再部署，或在两个仓库的父目录直接运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\local\Update.ps1
```

该入口由同步脚本生成，会调用正式源码中的同步、部署脚本。不要编辑 `local/deploy/` 或 `local/sources/plugin/` 中的代码。

同步包括未提交和未忽略的新源码文件，限定公开文件范围，按清单清理已删除的程序文件；保留 `deploy/private/`。`sync-state.json` 记录提交版本、是否有未提交修改及当前插件包路径，`*-files.json` 记录同步清单。插件包使用唯一名称，避免同版本缓存导致更新不生效。同步失败时不会保留可部署状态。

部署会备份当前 DSH desktop profile 的配置文件到 `local/dsh-backups/`，将插件依赖从正式源码目录切换到本地包（离线安装，需要已有依赖缓存），然后重建网关、同步密钥并执行健康和认证检查。插件安装失败时不修改网关；安装依赖报错时先排查，不能视为部署完成。`-SkipPlugin` 可显式只部署网关。

本地环境是长期使用的单实例：沿用 `shanghaitech-genai` Docker 项目名、镜像标签和端口 `8000`，会替换同名的旧网关，请勿与另一套部署交替启动。不会自动退出 DSH；更新后完全退出并通过 `local/deploy/Start.ps1` 重新打开 DSH，使插件和网关地址生效。

首次使用还需在网关网页导入网站登录凭证。健康检查和模型列表通过不代表上游会话可用。配置及凭证不会被同步回源码；不要将整个 `local/` 作为发布包。正式用户仍按仓库的安装指南部署，无需本地开发工具。
