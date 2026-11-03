# 环境、账户模型与可移植运行

## 三条路径

| 路径 | 必需条件 | 不需要什么 |
|---|---|---|
| WorkBuddy 技能 | 已登录宿主、账户可调用的模型、任务需要的工具 | 作者的网关、模型矩阵、API key、Python、Git |
| Python 离线示例 | Python 3.10+、本仓库文件、可写临时目录 | 账户、联网、自定义模型、Office、CLI |
| Python + 外部 CLI | Python、用户自己安装并登录的 CLI、正确参数与授权 | 作者的任何端点或目录 |

“自动适配”指基于当前已授权工作区、技能根目录、宿主可用工具和默认模型进行能力判断，不指自动破解账户权限、安装缺失软件或获得未授权模型。

## WorkBuddy 技能安装

仓库根部 `SKILL.md` 与 `references/safety-rules.md` 组成独立技能。将这两项按以下结构打包，或使用随仓库附带的导出功能：

```text
enbanc/
  SKILL.md
  LICENSE
  references/
    safety-rules.md
```

在 WorkBuddy 的技能页选择“添加技能 → 上传技能”，导入技能 ZIP。导入时由宿主完成安装配置，不要求用户手工填作者的绝对路径。随后新开任务输入 `/enbanc` 或“合议：”，并指定自己的工作区与验收要求。

导出 ZIP 是本机操作，不是上传市场。不要直接压缩自用技能目录：公开技能是独立整理的版本。实际导入、宿主工具权限与端到端 Office 任务需要在下载者自己的账户上验证，本仓库的离线检查不等于完成这些验证。

## 没有外部自定义模型能不能用

可以采用宿主的账户模型，不必另配外部 API key。官方文档说明模型配置有内置默认层，子代理可使用 `default`、`lite`、`reasoning` 或当前可用型号。没有更高优先级覆盖时，默认编排最终可继承主会话模型。

但要区分：

- WorkBuddy 技能使用宿主原生模型与子代理能力；本技能不调用作者网关，不读取用户模型配置取密钥。
- Python 命令目前只内置 `mock` 与通用 `cli` 适配器，没有专用 `--provider workbuddy`。不能因为查到宿主支持账户模型，就声称 Python 已完成账户接入。
- CLI 是另一个运行入口，需用户在 CLI 中完成官方登录，不能假设桌面已登录就一定免登录。用交互模型选择器查看自己有权用的型号；列表显示不代表服务端授权成功。
- `default` / `lite` / `reasoning` 是路由方式，不保证三个不同模型，更不保证跨供应商。只有同一模型时保留独立上下文复核，并披露限制；需要跨模型时使用账户实际支持的另一型号或用户授权的服务。
- 权限、额度或模型不可用时明确停下或请求用户选择已有可用模型，不切换账户、绕过限制或自动收费。

## 可选工具

制作 Word、PPT、表格应使用宿主相应文档能力，或用户明确批准的 Office 工具链。网页需要开发/预览工具，视觉评审需要可渲染结果与图像读取能力。事实核验需要可追溯来源。

这些不是 enbanc 本身提供的全部功能。缺工具时选择有能力的输出格式或报告“未验收”，不要把改名为 PPTX 的文本当作 PPT。Git 只用于版本、范围取证和发布；非 Git 目录可以显式列改动范围，不能猜测。

## 离线自检

源码检出后，无需安装第三方 Python 运行包。离线示例和 doctor 不要求 Git；完整测试包含 Git 暂存和历史测试，因此需要 Git 命令可用，并允许测试清理自行创建的临时文件：

```bash
python -m compileall -q src/enbanc
python examples/demo/run.py
python tests/run.py
```

安装 Python 参考实现后：

```bash
pip install -e .
enbanc doctor --offline
```

`doctor` 只检查运行资源、命令是否存在与 mock 流程。它不会执行账户 CLI，不联网、不读取认证或模型配置、不打印找到的可执行文件绝对路径。缺少 CLI 和 Office 仍可运行 mock；登录和真实模型授权始终显示“未验证”。临时目录由 `ENBANC_TEST_TMP`、系统临时环境或系统默认目录确定。

`pip install -e .` 首次可能联网安装构建依赖；“零运行时依赖”不表示首次安装天然离线。

## 官方依据（2026-11-03 核对）

- [技能导入与使用](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)
- [技能包结构](https://open.workbuddy.cn/docs/skill)
- [模型配置与默认解析](https://www.workbuddy.cn/docs/cli/models)
- [子代理模型与工具权限](https://www.workbuddy.cn/docs/cli/sub-agents)
- [CLI 安装与登录](https://www.workbuddy.cn/docs/cli/quickstart)

CLI 的 npm 安装方式要求 Node.js 18.20+，原生安装器不要求单独安装 Node.js。以使用版本的官方说明为准，本项目不自动安装运行时。
