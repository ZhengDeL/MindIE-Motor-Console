# 贡献与发布流程

擎寰平台接受缺陷修复、文档、运行时适配和社区插件贡献。提交前先阅读[项目文档](web/docs/README.md)，确认目标版本、组件边界及相关接口；安全问题按 [SECURITY.md](SECURITY.md) 私密报告。

## 正式主线与本地任务分支

项目主仓库为 [`ZhengDeL/MindIE-Motor-Console`](https://github.com/ZhengDeL/MindIE-Motor-Console)，仅以 `main` 作为公开代码主线，首个产品发行版本为 `v0.11.0`。固定标签仅对应本仓实际发行；临时开发分支默认仅在本地保留。维护者在完成审核和明确授权后集成正式主线。安装用户使用 `main` 或所需版本的固定标签。

维护者每个独立任务先新建**本地工作分支**，同一任务的修正继续使用该分支。任务分支默认仅在本地保留，只有明确要求时才发布到主仓库。下面的 `BASE_REF` 是需要替换的基线，例如 `origin/main` 或已审查的提交；`YYYYMMDD-topic` 替换为日期和简短主题：

```bash
git status --short
git branch --show-current
git fetch origin
git switch -c feat/YYYYMMDD-topic BASE_REF
git submodule update --init --recursive
```

先保存并检查现有未提交工作，不用 `reset`、`clean` 或覆盖文件获取干净目录。实现提交保留在任务分支；审核和验证完成后，取得明确授权再合入 `main`、推送主线或创建发行标签。不得因测试或清理分支而直接在 `main` 修改实现、绕过审核、强推或移动标签。暂存时显式选择本次变更文件。

## 通过 Fork 提交贡献

外部贡献者先在自己的 GitHub 账号下 Fork 主仓库，从 Fork 中建立工作分支，再向 `ZhengDeL/MindIE-Motor-Console:main` 提交 PR。以下命令适用于首次克隆自己的 Fork；将 `YOUR_GITHUB_ACCOUNT` 和分支主题替换为实际值。已有工作树先核对远端配置，不重复克隆或添加已有的 `upstream`：

```bash
git clone https://github.com/YOUR_GITHUB_ACCOUNT/MindIE-Motor-Console.git mindie-motor-console
cd mindie-motor-console
git remote add upstream https://github.com/ZhengDeL/MindIE-Motor-Console.git
git remote -v
git fetch upstream
git switch -c feat/YYYYMMDD-topic upstream/main
git submodule update --init --recursive
```

提交前确认 `origin` 指向自己的 Fork，`upstream` 指向主仓库。完成本地修改和验证后，显式暂存相关文件、创建提交，再将工作分支推送到自己的 Fork：

```bash
git push --set-upstream origin feat/YYYYMMDD-topic
```

在 GitHub 创建目标为主仓库 `main` 的 PR。固定标签与 `main` 可能具有不同能力，不能只凭版本字符串判断接口；社区包开发还应核对目标宿主的 `/api/plugin-sdk`，详见[插件开发指南](web/PLUGIN_DEVELOPMENT.md)。

## 实现合同

- 保持 `mindie-motor/upstream` 固定提交；读取相关原生实现后适配，不静默更新上游。保留未知原生 JSON 字段。
- Web 通过业务 HTTP API 访问状态；不得依赖数据库文件、SQLAlchemy 连接或数据库驱动。存储适配实现版本化 Repository / UoW、CAS、审计及恢复合同。
- 插件通过受信制品交付；管理员导入只校验与暂存，明确选择后在下次启动装配。插件不是权限豁免或进程沙箱，权限、产品身份、审计和资源准入由稳定核心校验。
- Agent 操作账本保持在节点本地；不将每次资源准入变为依赖远端数据库。结果未知时保留原操作 ID，先对账，不盲目重复副作用请求。
- 数据库、API/Web 镜像和原生 Motor 版本分别管理；兼容组合需要独立验证。平台更新不能自动改写在运行的模型配置。

## 验证与发布

运行与实际改动相符的检查。类型/构建检查、单元合同、浏览器样例、本地真实 API、数据库、镜像构建、真实 Agent 和 NPU 推理分别记录；页面打开、容器 Running 或 HTTP 200 均不能代表完整业务验收。

界面检查包括各页签与窗口、长文换行、权限、焦点/键盘、明暗主题、宽度/缩放、请求失败、慢响应、隐藏/恢复、未保存草稿和配置修订冲突。危险操作须核对目标范围、所有权、执行回执及资源是否收敛。

本仓库不接收测试源码、测试配置、浏览器验收脚本和生成的验证产物；在本地运行相关检查，在 PR 中给出脱敏命令、结果摘要及未覆盖范围。提交前检查暂存区与最终 Git 树。不得提交凭据、kubeconfig、运行数据库、节点日志、私有 `.env` 或客户操作记录；公开示例使用 `example.test`、文档保留 IP 和通用目录。

PR 应说明具体问题、修改后的行为、影响范围、验证结果和升级/回退要求。涉及新依赖时同时更新锁文件、来源及许可证材料；涉及接口或部署步骤时同步更新使用文档。维护者审查通过后合并，版本发布与源码合并分别进行。

## 社区插件贡献

社区插件包与公开 SDK 自平台 `0.11.0` 起提供。插件作者使用对应发布源码或目标 PR 的基线，并确认目标宿主明确报告所需能力。阅读[插件开发指南](web/PLUGIN_DEVELOPMENT.md)，复制 [community-status 完整示例](web/plugins/examples/community-status/README.md)，按说明运行 `build` / `inspect`。样例的最低宿主版本为 `0.11.0`，不能据此自动修改其他插件声明的版本上界。

每个贡献应包括稳定 ID、作者、许可证全文、兼容范围、显式依赖与最低访问角色，以及能运行的入口、说明文档和升级/回退方法。不得在导入模块时启动线程、联网、修改原生配置或执行部署；资源在工厂中创建后立即登记退出清理。

PR 描述需说明新增能力、真实数据来源、网络/文件副作用、对已有配置的影响、权限与失败路径、实际验证及未验证边界。外部系统连接、模拟响应、演示页面、容器构建和真实 NPU 运行分别记录。测试、浏览器脚本和截图只保留本地，提交脱敏结果摘要；不要为了满足 PR 模板将这些文件加入发布树。

审查重点包括：重复/缺失依赖、兼容版本、命名冲突、路径与角色归属、同步生命周期、无界工作、修订冲突、账号切换后的旧响应、目录失联和未保存输入、深浅主题及窄屏。安全问题按 [SECURITY.md](SECURITY.md) 私密报告，许可证及第三方来源按 [NOTICE.md](NOTICE.md) 保留。
