# 安装、运行与更新流程

## 下载后先选择入口

WorkBuddy 用户先看 [环境与安装](004-environment.md)：公开技能只有 `SKILL.md` 和通用规则资源，使用自己的宿主模型与工具，无需配置作者的网关或目录。

需要试跑 Python 参考实现时，要求 Python 3.10+。运行示例无需第三方运行依赖；可编辑安装首次可能需要联网准备构建工具。

```bash
pip install -e .
enbanc doctor --offline
```

`doctor` 不读取认证配置，不调用云模型，检查结果不能证明账号已登录或某个型号已获授权。

## 文本参考实现的流程

在当前项目目录准备 `req.md` 与 `tasks.json`。每个任务描述输入、输出和验收器，所有相对路径相对于这次任务的明确目录。

```json
[{"id":"writer","prompt":"Write a three-section analysis","kind":"doc",
  "out":"analysis.md","verifier":"document","scope":["analysis.md"]}]
```

```bash
enbanc --root runs init --run-id r1 --requirement-file req.md
enbanc --root runs dispatch --run-id r1 --tasks tasks.json --provider mock
enbanc --root runs collect --run-id r1
enbanc --root runs review --run-id r1 --provider mock
enbanc --root runs render --run-id r1
enbanc --root runs stage --run-id r1 --set P5
```

`pass` 允许正常收尾；`revise` 时先处理实际问题。产物、任务输出与评审记录保存在 `runs/`，只用于本机，不进入公开版本。

真实模型接入使用用户自己的 CLI 或 provider。通用 CLI 模板需要正确传入任务 `{prompt}` 和评审 `{system}`，并按实际 CLI 返回格式解析。默认只继承少量基础环境变量；凭据和代理变量只能在用户明确批准后通过适配器 `env` 传入，不能硬编码或输出。凭据存储、登录、文件权限与 Office 工具由宿主负责；不要把命令替换视为完整适配。

参考实现以文本为主，不能生成完整 PPTX/Word 文件。`escalate` 记录评审链进度，不自动依据链名切换云模型，factory 接入需显式完成模型选择。

## 更新后的标准动作

1. 在本机修改自用技能，保留私有配置；不要复制自用目录到公开目录。
2. 运行变化检测，人工判断哪些规则与实现需要迁移。首次或自定义位置可显式指定用户自己的技能目录：

```bash
python .sync/publish.py sync
```

同步状态只保留内容与章节摘要，不保留绝对路径、标题、正文。`sync` 不自动改写公开代码或文档。

3. 整理公开实现、两份 README 和变更说明，再运行：

```bash
python .sync/publish.py check
python .sync/publish.py stage
```

文件白名单、隐私检查、索引与历史检查、测试、技能入口和中英一致性必须全部通过。诊断仅给相对位置与类别，不打印敏感值。`stage` 不自动加入文件，也不显示可能含敏感数据的差异正文。

4. 手动复核准许公开的文件。Git 初始化与身份选择由作者决定，尤其检查提交姓名、邮箱与消息是否适合公开。按照已核验文件清单暂存并提交，不应随意 `git add -A` 后直接上传。
5. 配置无内嵌凭据的远端。推送使用下列命令，但只有明确决定发布时才执行：

```bash
python .sync/publish.py push --yes
```

此命令真正联网。它会再次强制运行所有门禁，工作区不干净、没有安全远端或无法完整检查历史时拒绝。不是加了 `--yes` 就能跳过审核。

维护者仍须人工检查商业秘密、附件与截图内容。正则扫描不是全面审计，Git 忽略也不是追溯删除已公开历史的方法。

## 本机生成技能包

```bash
python .sync/publish.py export-skill
```

检查通过后，只将公开技能入口、通用规则与 MIT 许可文件打包为 `dist/enbanc-skill.zip`，使用 `enbanc/` 单层根目录并重新核验 ZIP 成员和内容。它不会读取自用技能目录或上传。已有导出包不自动覆盖，避免误替换已审核版本。

`dist/` 不进入源码提交，生成的技能 ZIP 是独立交付物；每次规则更新都应重新审核后生成。不要用通用整目录压缩代替安全导出。

## 协作与独立维护

欢迎提交问题、文档、测试或适配改进，也欢迎按 MIT 许可 fork 或另建仓库分享。作者是个人维护者，更新和回复可能延迟；愿意告知独立版本时，可以通过 README 中的公开邮箱发送仓库链接。通知是自愿交流，不是使用或分发许可条件。

提交贡献时只使用合成示例，不带私人配置、机器信息、原始运行记录或密钥。安全问题先通过邮件沟通，公开 Issue 不贴敏感材料。
