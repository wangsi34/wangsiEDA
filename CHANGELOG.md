# 更新记录

## v1.5.1（2026-11-23）

- 修复 macOS 本地演示读取 Linux 的 `/proc/meminfo` 导致顶部状态栏和总览页报错的问题。macOS 上的可用内存和整机评测器进程数显示为“—”。
- Windows 上直接运行 `python wangsi.py demo` 时给出明确提示（本地演示控制台只支持 macOS / Linux，Windows 请用 WSL），不再报 `fcntl` 导入错误；README 注明 `start.bat` 用于连接远程 Linux 计算主机。
- README 开头说明本仓库包含和不包含的部分：ECO 构建器与签核评测器需自行接入。
- 中文 README 更正工具数（29）和最低 Python 版本（3.9）。
- CI：Python 3.9 改用 uv 安装（ubuntu-latest 已不提供 3.9）。

## v1.5.0（公开发布候选）

- 自带演示：`python3 wangsi.py demo`（或 `bin/wangsictl demo`）在本机生成一个小型合成 F2F 设计（两片 die 的布局布线、HBT、违例路径、DRC、候选池、评测记录、一个已完成的 closure 作业），用真实的场景提取流程生成 3D 场景并启动控制台；数据在 `cloud/demo_root/`，与正式安装隔离，页面标注“演示数据（合成）”。`--package OUT` 生成可双击打开的离线副本。
- 英文 README（README.md），中文 README 改为 README.zh-CN.md；新增 THIRD_PARTY_NOTICES.md、GitHub Actions CI（Python 3.9–3.13 单元测试、自检、模拟评测、演示冒烟测试、前端语法检查）与 issue 模板。
- 界面统一使用 Ge / Gs 术语；签核结果文字按实际判定显示（不再写死 PASS）；签核全景图例按设计中的方法自动生成；英文模式下标题副标题也翻译。
- 清理内部代号：评测器派生名改为 `evaluator_wangsi`（仍兼容旧名），评测器进程标记可配置（`evaluator_marker`）。

## v1.4.1

- 兼容 v1.0 配置：没有 `cases`、`readbacks`、`runtime_scan_subdirs` 时按 v1.0 的写死行为补默认值（4 个设计、bp_fe/bp_be 回读、sca_ncso 扫描），避免升级后回读缺失导致签核门全部报 `clock_UNMEASURED`。`doctor` 会提示检测到旧配置。

## v1.4.0

- 新增“问 wangsiEDA”：控制台右上角的对话框，用自然语言询问设计、候选程序、评测、作业和主机状态。助手只能调用只读工具（新增 `list_jobs`、`job_info`），答案中的数字和标签都来自查询。
- 助手最多建议一个作业；建议须通过校验（作业类型与参数白名单、候选池内且未测过、流程本身的检查）才显示为按钮，由人点击并确认后提交。
- 每次提问是一个 `ask` 作业（可追溯、受模型调用预算约束），作业列表默认不显示它们。
- 内置助手提示词 A/B/C（默认 B），prompt-eval 新增 6 个助手场景：中文问答、建议下一步评测、拒绝重跑已测程序、拒绝改时钟、统计失败、门限对比。
- 模块流程检查 `knobs` 参数名，未知旋钮（包括时钟）直接拒绝。

## v1.3.0

- 两轮真实模型评测（deepseek-flash，思考模式 high，共约 300 次调用）后定稿默认提示词：规划 B、反思 C、RTL 调试 B、物理设计 C。结果与理由见 docs/提示词评测.md。
- prompt-eval 评分修正：“未测量”场景只检查带 %/ns/ps 的数值是否来自输入。

## v1.2.3

- 依据第 1 轮真实评测（deepseek-flash，思考模式 high）设定默认提示词：规划 B、反思 C、RTL 调试 B、物理设计 C。
- prompt-eval：新增三道更难的 RTL 题（SRL 写成算术右移、SLT 有符号/无符号混比、移位位宽取错位）；修正两处评分误判（反思中条件句里的 "passes"；GPL-0301 时同时抬高 place_density 是正确做法）。

## v1.2.2

- macOS 上 python.org 版 Python 未安装根证书时，HTTPS 自动改用 certifi 或系统证书（/etc/ssl/cert.pem），不再报 CERTIFICATE_VERIFY_FAILED。
- prompt-eval 开始前先发一次极小的测试请求，连不上模型就直接停止并说明原因，不再生成无效报告。

## v1.2.1

- 模型请求支持 `llm_extra`（如 DeepSeek 思考模式与 reasoning_effort）、`llm_max_tokens`、`llm_timeout_s`；服务端拒绝 JSON 模式时自动去掉 `response_format` 重试。
- 配置模板默认 `deepseek-flash` + 思考模式 high（与论文实验一致）。
- 新增 `run_prompt_eval.sh`：本机直接评测提示词（Python 3.9+，无需计算主机），key 交互输入、不落盘。

## v1.2.0

- 内置四个 Agent 的提示词（`prompts/default/`，A/B/C 三版），`use_default_prompts` 默认打开，配好模型即可使用；自己的 `prompts/<名字>.txt` 优先。
- 新增 `wangsictl prompt-eval`：在固定场景上运行真实 Agent 代码与守卫，按版本打分并输出报告；`--mock good|bad` 无需模型即可检验评分。
- RTL 调试 Agent 的输入里显式给出文件名（`FILE: <block>.v`）。
- `doctor` 显示每个 Agent 实际使用的提示词来源。
- 部署会同步内置提示词，但不覆盖远端 `prompts/*.txt`。

## v1.1.0

易用性：
- 全新安装即可启动：未配置私有数据时服务正常运行，首页显示“开始使用”，列出已就绪和缺失的功能。
- 新增 `wangsictl doctor`（以及本机 `python wangsi.py doctor`）：逐项检查配置、EDA 工具、提示词、密钥、测试平台冻结状态，并给出补齐方法。
- 新增 `wangsictl freeze-tb <block>`：一条命令冻结测试平台。
- 设计、模块不再写死：设计列表、3D 设计、回读设计、时钟名都来自配置；`blocks/` 下的模块自动发现；界面菜单由 `/api/config` 生成。
- 3D 讲解自动选焦点线网（ECO 改动过的线网中扇出最大者），设计简介与时钟周期改为数据驱动。
- 启动器支持 macOS / Linux（自动找 `ssh`），路径加引号；部署不覆盖远端配置和提示词；部署后打印诊断。

正确性与安全：
- 签核门：缺失线长、Cut Short 时判为不通过（原先默认放行）；可选面积、单元数上界。
- `/api/scene` 校验设计名；HTTP 错误不再把 traceback 返回给前端（写入日志）。
- 流程参数校验改为显式异常（原 `assert` 在 `python -O` 下失效）。
- 步骤超时后 120 s 未退出则发 SIGKILL。
- 快照脱敏覆盖更多路径前缀（/data、/scratch、/mnt、/nfs、/Users、Windows 盘符等）。
- RTL 补丁与旋钮动作增加结构校验；反思 Agent 写入的触发条件按字面匹配。
- systemd 单元改名为模板 `wangsieda@.service`。
- 面向用户的文字统一为通用术语（签核评测器、ECO 构建器、包络门 Ge / 精确门 Gs）。

## v1.0.0

- 持久化作业引擎：动态 DAG、独立 worker 进程、服务重启后自动接管、超时、重试、取消、单步重试。
- 调度池与主机准入门：评测器全机并发上限，同时检查负载、磁盘和内存。
- 三条流程：`eco_eval`、`eco_closure`、`block_flow`。
- Agent 与守卫：
  - ECO 规划（ReAct，只读工具）；
  - 确定性审查：候选池、不重放、可构建、预算；
  - 反思、RTL 调试、物理设计诊断。
- 3D 签核评审：全部详细布线、键合端子、DRC、违例路径、guide 差分，以及作业实时 3D。
- 浏览器内导入 DEF/LEF/DRC。
- 中文/英文界面。
- 脱敏快照导出与本地数据包（双击打开）。
- MCP 接口。
- 单元测试。
- 工艺准确的 2.5D 模型：线宽、厚度、高度取自工艺 LEF，通孔逐个实例化，垂直放大可调。
- 3D 模型一致性校验：与 DEF、评测器报告、DRC 报告逐项对照。
