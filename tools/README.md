# 本地开发环境

正式代码只在 `ShanghaiTech-GenAI/` 和同级的 `dsh-shanghaitech-genai/` 仓库中修改。长期运行副本在同级 `local/`，不进入任一源码仓库。

## Windows 首次部署

需要 Git、Docker Desktop（Linux containers）和 DSH Desktop。先启动 Docker Desktop，在网关仓库中打开 PowerShell 并依次执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\Sync-Local.ps1
powershell -ExecutionPolicy Bypass -File .\tools\Deploy-Local.ps1
```

默认 DSH 安装目录为 `$env:LOCALAPPDATA/Programs/DeepSeek Harness`，打包使用应用自带 Node/pnpm，安装使用 `resources/runtime/cli/bin/dsh.cmd`。同步脚本支持 `-PluginRepo`、`-LocalRoot`，同步和部署均支持 `-DshDir`；自定义路径时两个步骤传入相同的运行目录与 DSH 路径。

已有私有配置时，首次同步可加 `-PrivateSource '旧部署目录\private'`，仅复制目标中缺失的密钥、配置与网站凭证，不覆盖已有内容；没有配置时自动生成。旧目录保留。

Windows 部署脚本以 `--offline` 安装本地插件包，需要已有依赖缓存；安装失败时不会修改网关。部署会备份 desktop profile 配置、重建网关并同步密钥，检查健康版本、有效密钥模型列表及网站凭证状态。`-SkipPlugin` 可只部署网关。Windows 脚本不执行 macOS 入口的摘要核对和无效密钥检查，也不生成 `deployment-checks.json`。

## macOS 首次部署

需要 Git、Python 3（`python3`）、Docker Desktop 和 DSH Desktop。先启动 Docker Desktop。默认 DSH 路径为 `/Applications/DeepSeek Harness.app`，打包使用其中自带的 Node/pnpm，安装插件使用其中的 CLI。

在网关仓库中依次执行：

```sh
./tools/Sync-Local.sh
./tools/Deploy-Local.sh
```

首次同步自动生成 `local/deploy/private/container.json` 和 `gateway.key`，并创建网站凭证目录；已有文件保留。macOS 脚本不支持 Windows 入口的 `-PrivateSource` 参数。如需迁移旧配置，应在首次同步前将旧 `private/` 安全备份到工作区外；首次同步后、部署前，仅将需要迁移的私有文件复制到 `local/deploy/private/`，不要覆盖需要保留的本地配置，也不要复制到源码仓库。

同步脚本可用 `--plugin-repo` 指定插件仓库；同步、部署脚本均可用 `--local-root` 指定运行目录、`--dsh-app` 指定 `.app` 路径。运行目录必须在两个仓库之外。使用自定义路径时，同步和部署传入相同参数，例如：

```sh
./tools/Sync-Local.sh --dsh-app "$HOME/Applications/DeepSeek Harness.app"
./tools/Deploy-Local.sh --dsh-app "$HOME/Applications/DeepSeek Harness.app"
```

## 后续更新

每次修改后先同步再部署，或在两个仓库的父目录运行对应系统的入口。

Windows（PowerShell）：

```powershell
powershell -ExecutionPolicy Bypass -File .\local\Update.ps1
```

macOS（终端）：

```sh
./local/Update.sh
```

该入口由同步脚本生成，保存本次使用的路径，依次调用正式源码中的同步、部署脚本。不要编辑 `local/deploy/` 或 `local/sources/plugin/` 中的代码。

同步包括未提交和未忽略的新源码文件，限定公开文件范围，按清单清理已删除的程序文件，并保留 `deploy/private/`。`sync-state.json` 记录两个仓库的提交版本、未提交状态与插件包路径，macOS 入口还记录文件摘要；`*-files.json` 记录同步清单。插件包使用唯一名称，避免同版本缓存导致更新不生效。同步失败时不会保留可部署状态；macOS 部署前还会核对同步文件和插件包摘要。

macOS 部署会备份 DSH desktop profile 的配置文件到 `local/dsh-backups/`，从 `local/packages/` 安装本地插件包。依赖优先使用 pnpm 缓存，缺失时联网解析；不是强制离线安装。随后重建网关、同步网关密钥，检查健康版本、无密钥与错误密钥被拒绝、有效密钥模型列表和网站凭证状态。结果保存在 `local/deployment-checks.json`。插件安装失败时不修改网关；不能将依赖安装报错视为部署完成。`--skip-plugin` 可显式只部署网关。

## 重启 DSH

本地环境是长期使用的单实例，沿用 `shanghaitech-genai` Docker 项目名、镜像标签和端口 `8000`，会替换同名旧网关。不要与另一套部署交替启动。

两个系统的部署入口均不会自动退出或重启 DSH。Windows 检查完成后，完全退出 DSH（包括托盘后台），从两个仓库的父目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\local\deploy\Start.ps1
```

此入口会启动网关、DSH 和网页。自定义 DSH 可用 `-DshExe '实际的 DeepSeek Harness.exe 完整路径'`；只重建网关可运行 `local/deploy/Start-Gateway.ps1`。

macOS 检查结果后，使用“退出 DeepSeek Harness”或 `Command-Q`，确认应用及后台子进程完全退出，再从两个仓库的父目录运行：

```sh
./local/deploy/Start.sh
open http://127.0.0.1:8000/
```

`Start.sh` 会重建并检查网关、同步密钥，再直接启动 macOS 应用并传入 `SHANGHAITECH_GENAI_BASE_URL`。它不会自动打开网关网页。自定义应用路径时，启动命令也需加 `--dsh-app '/实际路径/DeepSeek Harness.app'`。只重建网关可运行 `./local/deploy/Start-Gateway.sh`。

首次使用还需在网关网页导入网站登录凭证。缺失凭证时应明确报告；健康检查和模型列表通过不代表上游会话或真实模型调用已验证。配置及凭证不会被同步回源码；不要将整个 `local/` 作为发布包。同步和部署不代表授权提交、推送或发布。

## 回归检查

需要回归检查时，使用同步后的源码。macOS 插件离线检查需设置 `TMPDIR=/private/tmp`，避免系统临时目录符号链接触发会话删除路径防护，并使用 DSH 内置 Node。具体检查范围见插件仓库 README。不要用真实会话或凭证替代合成测试数据。

Windows 使用 `.ps1` 与 `Sync-Key.cjs`；macOS 使用 `.sh`、`tools/local-macos.py` 与 `Sync-Key-mac.cjs`。两套入口的路径、参数与依赖缓存策略不同，应按系统选择。
