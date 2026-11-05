# 开发、客户端安装与网关部署

代码在 `ShanghaiTech-GenAI` 和 `dsh-shanghaitech-genai` 两个正式仓库中修改；运行副本、插件包和私有数据保存到仓库之外。

## macOS 隔离开发

安装 Docker Desktop、DSH Desktop、Git 和 Python 3。启动 Docker，在工作区运行：

```sh
/path/to/ShanghaiTech-GenAI/tools/Dev.sh \
  --local-root "$PWD/local" \
  --plugin-repo /path/to/dsh-shanghaitech-genai
./local/deploy/Start.sh
```

`Dev.sh` 默认使用当前目录的 `local/`。它同步两端当前源码（含未提交修改）、打包插件、部署 `127.0.0.1:8000` 网关，并安装到独立的 `local/dsh/`。首次安装会启动测试 DSH 初始化 profile，再仅停止该测试实例。DSH Desktop 默认位于 `/Applications/DeepSeek Harness.app`，可用 `--dsh-app` 指定。

开发配置持久化在 `local/environment.json`：测试 DSH home、Electron 用户目录、网关管理模式、Compose 项目及源码路径。后续更新和启动不依赖当前终端的环境变量：

```sh
./local/Update.sh
./local/deploy/Start.sh
```

更新和重新启动会定向停止由此入口启动的测试实例。日常 DSH 的 profile、凭证登记和连接地址独立保存。测试实例使用 `local/desktop/` 作为 Electron 用户目录，可与日常实例同时运行。

```text
local/
├── environment.json         # 环境选择与路径，不含凭证
├── deploy/                  # 网关和启动工具运行副本
│   └── private/             # 配置、网关密钥、网站凭证
├── dsh/                     # 测试 profile、会话及凭证登记
├── desktop/                 # 测试 Electron 用户目录
├── sources/plugin/          # 插件同步副本及测试
├── packages/                # 唯一名称的本地插件包
├── dsh-backups/             # 测试 profile 元数据备份
├── logs/
├── Update.sh
├── sync-state.json          # 两端提交、工作区状态和文件摘要
└── deployment-checks.json
```

如本机直连上游不可用，可在开发私有 `container.json` 设置 `upstream_proxy_url`（HTTP/HTTPS 代理地址，不含认证信息），然后更新网关。Docker Desktop 访问宿主代理可用 `host.docker.internal`。未配置时直连，不继承系统代理环境变量。

网关初次生成独立密钥；网站登录凭证需导入开发私有目录。`--private-source /absolute/private` 可导入缺失的网关密钥、容器配置和网站凭证，保留目标已有文件；开发环境不迁移会话映射，纯网关迁移保留已有会话删除账本。部署健康、认证和凭证存在检查与真实模型调用分别验证。

## 连接远程网关的日常客户端

使用已发布的插件标签和独立客户端运行目录：

```sh
./tools/Install-Client.sh \
  --local-root /path/to/client-runtime \
  --dsh-home "$HOME/.dsh" \
  --plugin-source 'github:Github-Anderson/dsh-shanghaitech-genai#v1.0.6' \
  --gateway-url http://gateway-host:8000/v1 \
  --gateway-key-file /protected/path/gateway.key
```

此入口只连接已有网关、安装固定标签的插件及登记凭证，不要求本机 Docker 或插件源码。安装前完全退出目标日常 DSH，完成后通过客户端运行目录的 `deploy/Start.sh` 启动。后续 `Update.sh` 保留配置的发布标签；升级时重新执行安装入口并指定新标签。首次登记将地址和密钥一起迁入 DSH 原生凭证库，普通更新与启动保留界面保存的连接；明确传入 `--gateway-url`（Windows 为 `-GatewayUrl`）时才替换连接。

已有非空、未标记的运行目录需要先制定迁移方案，不能直接补标记或覆盖。原有 `Setup.sh`、`Sync-Local.sh`、`Deploy-Local.sh` 入口仍可用，默认运行目录为 `~/.local/share/shanghaitech-genai/`；`--skip-plugin` 跳过插件打包、安装和 DSH 凭证登记。

## 网关独立部署

在网关源码仓库运行，不需要 DSH、Node 或插件仓库：

```sh
./tools/Setup.sh --environment gateway \
  --local-root /path/to/gateway-runtime \
  --bind-address 127.0.0.1 --compose-project genai-gateway
```

`--gateway-mode managed` 表示由脚本构建和启动网关，`external` 表示只连接已有网关；不再根据 URL 是否填写来决定已配置环境的部署行为。`--bind-address`、`--port` 配置托管网关监听，默认只绑定 `127.0.0.1:8000`。

## 发布后部署远程网关

先完成本地验证并发布注解标签，再从本机运行：

```sh
./tools/Deploy-Remote.sh \
  --host remote-ssh-alias \
  --repo /absolute/remote/ShanghaiTech-GenAI \
  --root /absolute/remote/genai-releases \
  --tag vX.Y.Z \
  --bind-address 100.x.y.z \
  --private-source /absolute/remote/existing/deploy/private \
  --plan
```

`--plan` 只显示目标，不连接远程。确认目标后去掉它执行部署；本机需已登录对应仓库账号的 `gh`，远程需 Git、Python 3、Docker 及已有网关密钥。已有额外 Compose 网络配置时，用 `--compose-override /absolute/remote/compose.network.yaml` 指定并保留；该配置随环境记录用于后续更新。首次部署前核对监听地址、Compose 项目和旧服务布局；迁移已有网关时先停止旧服务及请求，再导入私有状态，避免会话账本在复制期间变化。

入口验证 GitHub Release 已发布及远端标签提交，按提交创建独立检出，保留原源码分支。稳定运行目录在 `--root/runtime/`；发布检出在 `--root/releases/<commit>/`。私有文件持续保留，部署失败时恢复此前由此入口记录的版本；首次迁入的旧部署须另行保留回退方案。`deployment.json` 和 `history/` 记录实际提交、健康版本及认证结果。真实模型验证需按任务范围执行。

## 测试

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tools/test-environments.py
PYTHONDONTWRITEBYTECODE=1 python3 tools/test-local-layout.py \
  --gateway-repo /path/to/ShanghaiTech-GenAI \
  --plugin-repo /path/to/dsh-shanghaitech-genai
PYTHONPATH=gateway python3 -m unittest discover -s gateway/tests -p 'test_*.py'
```

网关测试需要 `gateway/requirements.txt` 中的依赖。macOS 插件迁移回归从同步副本运行，设置 `TMPDIR=/private/tmp`；SDK 集成测试使用 DSH 的 Electron-as-Node、`DSH_TEST_SDK_ROOT` 和隔离测试数据。

## Windows 入口

保留 `tools/Setup.ps1` / `Setup.cmd`、`Sync-Local.ps1`、`Deploy-Local.ps1` 和 `Start.ps1`，默认运行目录为 `%LOCALAPPDATA%\ShanghaiTech-GenAI\`。Windows 使用现有参数 `-LocalRoot`、`-PluginRepo`、`-DshDir`、`-GatewayUrl`、`-GatewayKeyFile` 和 `-SkipPlugin`；本页新增的隔离开发和发布后远程部署入口为 Python/macOS 流程。
