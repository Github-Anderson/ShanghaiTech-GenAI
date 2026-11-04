# 本地开发环境

两个源码仓库放在同级目录，例如 `Documents/GitHub/ShanghaiTech-GenAI/` 和 `Documents/GitHub/dsh-shanghaitech-genai/`。运行环境独立存放：

| 系统 | 默认运行目录 | 部署入口 |
| --- | --- | --- |
| macOS | `~/.local/share/shanghaitech-genai/` | `tools/Setup.sh` |
| Windows | `%LOCALAPPDATA%\ShanghaiTech-GenAI\` | `tools/Setup.ps1` 或 `tools/Setup.cmd` |

## 首次部署与更新

安装 Git、Docker Desktop 和 DSH Desktop。macOS 还需 Python 3。先启动 Docker Desktop，再在网关仓库运行：

macOS：

```sh
./tools/Setup.sh
```

Windows PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\Setup.ps1
```

Windows 也可双击 `tools/Setup.cmd`。首次部署和以后更新均使用相同入口：检查依赖、同步两个仓库当前源码、打包安装插件、部署并检查网关。已有私有配置会保留。两个系统均优先使用依赖缓存，缺失时联网解析。

默认 DSH 安装位置为 macOS 的 `/Applications/DeepSeek Harness.app`，以及 Windows 的 `%LOCALAPPDATA%\Programs\DeepSeek Harness`。可用 `--dsh-app` / `-DshDir` 自定义；用 `--local-root` / `-LocalRoot` 自定义运行目录；用 `--plugin-repo` / `-PluginRepo` 指定插件仓库。每次更新使用相同参数，或使用运行目录中自动生成的 `Update.sh` / `Update.ps1`。

部署完成后，完全退出 DSH，包括后台进程，再运行：

```sh
~/.local/share/shanghaitech-genai/deploy/Start.sh
```

```powershell
powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\ShanghaiTech-GenAI\deploy\Start.ps1"
```

完成部署后，插件读取已登记的私有配置，直接打开 DSH 应用也会使用配置的网关。使用本地网关时，可先用上述启动入口确保网关正在运行。

## 运行目录

```text
运行目录/
├── deploy/                  # 网关与启动工具运行副本
│   ├── gateway/
│   ├── tools/
│   ├── compose.yaml
│   └── private/
│       ├── dsh-gateway.json  # DSH 网关地址与密钥文件名
│       ├── gateway.key      # 自动生成的本地密钥
│       ├── container.json   # 本地网关配置
│       └── browser-credentials/
├── sources/plugin/          # 插件同步副本
├── packages/                # 唯一名称的本地插件包
├── dsh-backups/             # 部署前的 DSH profile 配置备份
├── logs/
├── Update.sh 或 Update.ps1
├── sync-state.json
├── gateway-files.json
└── plugin-files.json
```

个人配置、密钥和网站凭证只放在运行目录的 `deploy/private/` 中。首次同步创建缺失的配置和本地密钥；同步包括源码中的未提交修改，按清单清理已删除的程序文件，保留私有数据。`sync-state.json` 记录两个仓库的版本、工作区状态和插件包路径；macOS 还记录和核对文件摘要，部署结果写入 `deployment-checks.json`。

## 远程网关

可在首次部署时指定远程地址和已有密钥文件：

```sh
./tools/Setup.sh --gateway-url 'https://gateway.example.com/v1' --gateway-key-file '/path/to/gateway.key'
```

```powershell
.\tools\Setup.ps1 -GatewayUrl 'https://gateway.example.com/v1' -GatewayKeyFile 'C:\path\gateway.key'
```

密钥会复制到私有目录的 `remote-gateway.key`，地址保存为 `deploy/private/dsh-gateway.json`：

```json
{
  "base_url": "https://gateway.example.com/v1",
  "key_file": "remote-gateway.key"
}
```

也可在同步后、部署前手动编辑该文件。`key_file` 相对于私有目录，地址末尾可省略 `/v1`。`base_url` 缺省或留空时使用本地网关及 `gateway.key`；填写远程地址时，启动与部署跳过本地网关重建。远程网关自身的更新需在服务器上执行。

部署会在 DSH 数据目录（默认 `~/.dsh/`，可由 `DSH_HOME` 指定）登记私有目录绝对路径，插件读取该目录中的地址和密钥。移动运行目录后需重新部署以更新登记；修改地址或密钥后重启 DSH。

## 开发与迁移

代码只在两个正式仓库中修改。底层步骤仍可分别执行：先 `tools/Sync-Local.sh`，再 `tools/Deploy-Local.sh`；Windows 使用对应 `.ps1`。`--skip-plugin` / `-SkipPlugin` 可跳过插件安装。

迁移已有运行环境时，先退出 DSH，停止使用旧路径挂载的本地网关，再将完整运行目录移动到新位置；在新源码位置执行 Setup 并指定新运行目录。保留整个 `deploy/private/`，Setup 会重新生成同步状态和更新入口。Windows 也可用 `Sync-Local.ps1 -PrivateSource` 迁移已知私有文件；自定义命名的远程密钥需另行复制。

macOS 插件回归从同步副本运行，设置 `TMPDIR=/private/tmp` 并使用 DSH 内置 Node。检查范围见插件 README。首次使用还需导入网站登录凭证，见[安装指南](../安装指南.md)。健康与认证检查通过不代表真实模型调用已验证。
