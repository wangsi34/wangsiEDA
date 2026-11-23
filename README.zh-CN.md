# 念思 wangsiEDA

[English](README.md) | 中文

版本 1.5.1 · MIT · Python 3.9+（只依赖标准库） · 前端无第三方依赖

**面向 F2F 3D IC 的签核驱动 ECO 收敛平台**：多 Agent 规划，持久化作业引擎，3D 签核评审，以及 MCP 工具接口。

wangsiEDA 不重新实现 EDA 流程，而是把你已经验证过的工具链统一成一个可调度、可观测、可回溯的服务。这些工具链包括 ECO 构建器、签核评测器、STA 回读和签核门。设计数据、EDA 工具和模型调用都留在计算主机上，本地只需要一个启动器和浏览器。

> **本仓库包含什么。** 平台本身（作业引擎、Agent、守卫、签核门、3D 查看器、控制台、MCP 服务）、基于开源工具的模块 RTL→GDS 流程，以及合成演示。F2F 的 **ECO 构建器和签核评测器不在本仓库中**：接入你自己的构建器和评测器后才能运行 ECO 评测与收敛（见下文“安装与配置”和 [docs/配置参考.md](docs/配置参考.md)）。不接入时仍可使用演示、模块流程和 DEF/LEF 查看器。

---

## 功能

| 模块 | 说明 |
|---|---|
| 数据目录（catalog） | 只读索引已有评测结果（`RESULT.json` / `REJUDGE.json`）、标准签核状态、候选 ECO 程序池和模型调用账本，写入 SQLite |
| 工具层（tools） | 29 个工具，各自带 JSON Schema、副作用类别（read / compute / runtime / llm / asset）、调度池、超时和重试策略 |
| 作业引擎（engine） | 动态 DAG：每一步是独立的 worker 进程，并有自己的编号目录；状态存在 SQLite；服务重启后自动重新接管运行中的步骤；支持超时、重试、取消和单步重试 |
| 调度池（许可证） | evaluator / openroad / build / llm / cpu。evaluator 池统计整台主机上所有评测器进程，并受负载、磁盘、内存准入门约束 |
| 流程（flows） | `eco_eval`：单个 ECO 程序的完整签核<br>`eco_closure`：Agent 规划 → 审查 → 并行评测 → 反思<br>`block_flow`：模块级 RTL → GDS 闭环 |
| Agent | ECO 规划：ReAct 方式，只能调用只读工具<br>审查：确定性检查，包括候选池、不重放、可构建、预算<br>反思：写入经验库<br>RTL 调试、物理设计诊断：输出都要经过守卫校验 |
| 3D 评审 | 显示 DEF 中全部详细布线（按层着色）；工程视图风格：硅衬底、21 层金属（标准配色）、键合界面上的铜 HBT 焊盘、实体 SRAM 宏单元；场景直接从评测器输出生成（布局、21 层布线、键合 HBT、违例路径、DRC、guide 变更），用 WebGL2 渲染，不依赖第三方库 |
| 作业实时 3D | 构建后显示 ECO 新增的 guide，评测中显示每片 die 的布线违例下降，判定后生成新状态场景并与初始状态对比 |
| MCP 接口 | stdio JSON-RPC。只读工具直接调用，其余工具以作业形式提交，可接入任意 MCP 客户端 |
| 界面语言 | 中文 / English 一键切换 |
| 控制台 | 3D 评审、作业（DAG、过程、LLM 调用、文件）、候选与状态、评测记录、工具注册表、经验库、主机状态 |

## 架构

```
浏览器 ──SSH 隧道──▶ wangsid（计算主机 127.0.0.1:18765，HTTP + SSE，token 鉴权）
                      ├─ catalog.py     只读索引数据湖 → SQLite
                      ├─ engine.py      持久化 DAG 调度、调度池、重新接管
                      ├─ worker.py      单步执行（独立会话进程）
                      ├─ flows.py       eco_eval / eco_closure / block_flow 状态机
                      ├─ tools/         工具注册表与适配器
                      ├─ scene3d.py     从 DEF / violated_paths / DRC / HBT / guide 生成 3D 场景
                      ├─ llm.py         模型客户端（密钥只在计算主机读取）
                      ├─ mcp_server.py  MCP 接口
                      └─ ui/            控制台与 WebGL2 3D 查看器
```

参照的开源项目：
- OpenLane 2 / LibreLane 的 Flow–Step–State 模型：每一步一个目录，保留状态快照和检查点；
- MCP4EDA：用 MCP 暴露 EDA 工具；
- ORFS-agent：Agent 读取指标、调整参数、重跑；
- LangGraph：有状态的条件图。

## 目录结构

```
start.bat                      Windows 一键连接远程计算主机（检查服务 → 建立隧道 → 打开浏览器）；不用于本地演示
wangsi.py                      启动器：up | deploy | status | mcp
cloud/                         部署到计算主机的全部内容
  bin/wangsictl                服务控制：start | stop | restart | status | token | refresh | mcp | selftest
  wangsi_config.example.json   配置模板（复制为 wangsi_config.json 后填写）
  prompts/*.txt.example        提示词模板（复制为 *.txt 后自行编写）
  blocks/rv_alu/               示例模块：RTL、自检 testbench、规格、CONTRACT.json
  wangsieda/                   Python 包（只依赖标准库，Python ≥ 3.9）
```

## 先看演示（不需要任何私有数据）

在 macOS 或 Linux 上（Windows 请在 [WSL](https://learn.microsoft.com/zh-cn/windows/wsl/install) 中）解压项目，打开终端，进入含有 `wangsi.py` 的目录。查看示例只需 Python 3.9+ 和浏览器。运行以下命令。

```bash
python3 wangsi.py demo
```

会生成一个小型合成 F2F 设计（布局、两片 die 的布线、HBT、违例路径、DRC、候选程序、评测记录和一个已完成的 closure 作业），在本机启动控制台并打开浏览器。演示数据放在 `cloud/demo_root/`，与正式安装互不影响；页面顶部标有“演示数据（合成）”。

浏览期间保持终端开启。如果浏览器没有自动打开，请复制终端打印的完整网址，包括 `?token=…`。演示默认使用端口 `18766`。如果端口已被占用，运行 `python3 wangsi.py demo --port 18767`。看完后，在终端按 Ctrl+C 停止服务。

在 macOS 上，顶部状态栏和总览页中的可用内存、整机评测器进程数显示为“—”。你仍可查看 3D 评审和示例中已完成的作业。要提交新的 EDA 作业，请按下节配置 Linux 计算主机。

## 在 Linux 计算主机上运行作业

不需要任何私有数据，先把平台跑起来，再逐项补齐：

```bash
# 计算主机上
cd cloud
bin/wangsictl doctor          # 列出哪些功能已就绪、缺什么、怎么补
bin/wangsictl freeze-tb rv_alu  # 冻结示例模块的测试平台
bin/wangsictl start && bin/wangsictl token
```

本机运行 `python wangsi.py up`（Windows 可双击 `start.bat`），浏览器打开后：
- 什么都没配置：首页是“开始使用”，可以直接导入 DEF/LEF 查看版图；
- 配好 `oss_cad_bin`、`openroad_env`、`nangate45_dir`：可以跑示例模块 RTL→GDS（`rule` 策略不需要大模型）；
- 配好 ECO 相关条目和 `cases`：ECO 评测、Agentic closure 和 3D 签核评审全部可用。

各功能需要的配置项见下文和 [docs/配置参考.md](docs/配置参考.md)。

## 环境要求

**计算主机（Linux）**
- Python 3.9 或以上，只用标准库；
- 签核相关工具：你自己的 ECO 构建器、签核评测器、STA 回读脚本（ECO 流程需要）；
- 模块流程需要：Yosys、Icarus Verilog、Verilator（例如 oss-cad-suite）以及 OpenROAD。

**连接远程计算主机的工作站**
- Python 3.x；
- OpenSSH 客户端，并已配置好免密登录计算主机的 SSH 别名。

## 安装与配置

### 1. 部署代码

在本机设置环境变量，然后部署：

```powershell
$env:WANGSI_HOST = "你的SSH别名"
$env:WANGSI_REMOTE = "/计算主机上的/安装目录/wangsieda"
python wangsi.py deploy
```

`deploy` 会把 `cloud/` 同步到 `WANGSI_REMOTE`（不覆盖远端的 `wangsi_config.json`、`prompts/*.txt` 和运行数据），运行自检，重启服务，并打印安装诊断。macOS / Linux 使用 PATH 中的 `ssh`，也可用 `WANGSI_SSH` 指定。

### 2. 填写站点配置（自行配置）

在计算主机上：

```bash
cd $WANGSI_REMOTE
cp wangsi_config.example.json wangsi_config.json
```

然后按实际环境填写下列字段：

| 字段 | 含义 |
|---|---|
| `project_root`、`results_dir`、`scan_roots` | 存放历史评测结果的目录，只读扫描 |
| `normalized_states_json` | 标准签核状态表 |
| `gate_bounds_json` | 签核门上下界：包络门 Ge（数据字段 `v6_bounds`）与精确门 Gs（设计注册表中的 `policy`） |
| `design_registry_module` | 设计注册模块，需要提供 `DESIGNS` 和 `fb()` 构建器工厂 |
| `eco_builder_module` | ECO 构建器 |
| `model_ledger_jsonl` | 模型调用账本（可选） |
| `rootfs`、`runtime_dir`、`platform_dir`、`x0_runs` | 评测器运行环境、工艺平台目录、各设计初始状态（X0）的评测目录 |
| `oss_cad_bin`、`openroad_env`、`nangate45_dir` | 模块流程所需工具路径 |
| `derivations` | 派生工具配置。平台不修改已验证的研究脚本，而是生成只替换输出 / 运行路径字面量的副本：`source` 写源脚本路径，`replacements` 写“源字面量 → 新字面量”对，每个源字面量必须在源文件中只出现一次 |

### 3. 配置大模型密钥（自行配置）

**仓库不包含任何密钥。** 二选一：

- 在计算主机的服务环境中设置 `WANGSI_LLM_API_KEY` 和 `WANGSI_LLM_BASE_URL`；
- 或者在 `wangsi_config.json` 的 `llm_env_file` 指向一个仅本人可读（`chmod 600`）的文件，内容如下：

```
WANGSI_LLM_API_KEY=你的密钥
WANGSI_LLM_BASE_URL=https://你的模型服务地址/v1
```

模型名称填在 `llm_model`。客户端使用 OpenAI 兼容的 `/chat/completions` 接口，并要求 JSON 输出。

### 4. 提示词（内置，可替换）

`prompts/default/` 自带四个 Agent 的提示词，每个三版：A（极简）、B（结构化规则）、C（规则 + 一个示例）。默认版本按真实模型评测选定：规划 B、反思 C、RTL 调试 B、物理设计 C，数据见 [docs/提示词评测.md](docs/提示词评测.md)。`use_default_prompts` 默认打开，所以配好模型密钥后 Agent 即可工作。

| 文件 | 用途 | 输出格式 |
|---|---|---|
| `eco_planner` | ECO 规划 Agent，可使用占位符 `BUDGET` 表示评测预算 | 调用只读工具时输出 `{thought, tool, args}`；结束时输出 `{thought, final:{programs, hypothesis, risks}}` |
| `reflector` | 反思 Agent | `{lesson, trigger, action, evidence}` |
| `rtl_debug` | RTL 调试 Agent | `{diagnosis, file, find, replace, confidence}` |
| `pnr_diagnose` | 物理设计 Agent | `{diagnosis, knobs, resume_from, expected_effect}` |
| `assistant` | 控制台提问助手，可使用占位符 `SITE_CONTEXT` | 调用只读工具时输出 `{thought, tool, args}`；结束时输出 `{thought, final:{answer, proposed_job}}` |

优先级：`prompts/<名字>.txt`（你自己写的）> `prompts/default/<名字>.<版本>.txt`。版本用 `default_prompt_variant` 选择，可以统一写 `"B"`，也可以按 Agent 写 `{"rtl_debug": "C"}`。

用 `prompt-eval` 在固定场景上比较提示词（真实 Agent 代码与守卫，临时数据库，不影响你的数据）：

```bash
bin/wangsictl prompt-eval                                   # 4 个 Agent × A/B/C，约 60–90 次模型调用
bin/wangsictl prompt-eval --agents rtl_debug --variants B,C --repeats 3
bin/wangsictl prompt-eval --prompt-file rtl_debug=prompts/rtl_debug.txt   # 给你自己的提示词打分
bin/wangsictl prompt-eval --sim                             # RTL 修复用冻结 testbench 实际仿真判分（需 iverilog）
```

也可以不用计算主机，直接在本机（macOS / Linux，Python 3.9+）运行仓库根目录的 `./run_prompt_eval.sh`，它会提示输入 API key（不落盘）。

报告在 `var/prompt_eval/<时间>/report.md`，每次回复原文在 `results.json`。场景包括：已测程序陷阱、历史失败推翻记录的通过率、经验库与预算 2；五种 RTL 注入 bug（含需要靠 case 标签才能唯一定位的行）；六种 P&R 失败（含诱导改时钟）；四种反思情形（门失败、预测偏高、构建失败、混合结果）。

### 5. 冻结示例模块的测试平台

`blocks/rv_alu/CONTRACT.json` 中的 `tb_sha256` 在仓库里是空的。在计算主机上运行：

```bash
bin/wangsictl freeze-tb rv_alu
```

自己的模块放在 `blocks/<名字>/`（`<名字>.v`、`tb_<名字>.v`、`CONTRACT.json`、可选 `spec.md`），会被自动发现。

未填写时，仿真步骤会拒绝运行。这样可以防止 Agent 通过修改测试平台让自己“通过”。

### 6. 启动

```powershell
python wangsi.py up
```

或者直接双击 `start.bat`。启动器依次执行：
1. 启动远端服务；
2. 读取一次性 token；
3. 建立 `localhost:18765` 隧道；
4. 打开浏览器。

关闭窗口只断开隧道，作业继续在计算主机上运行。

## 使用

| 任务 | 操作 |
|---|---|
| 评审一个 ECO 结果 | 首页 “3D 评审” 按 7 步讲解：结构 → 问题 → 根因 → ECO 改动 → 签核结果 → 未通过对照 → 闭环结果。用工具 `scene_build` 为状态生成场景，标签用 `X0`、`CERTIFIED`、`NEAR_MISS`；作业生成的场景会自动加入 |
| 评测单个候选程序 | “候选与状态” 页点 “评测”，或者运行 `python3 -m wangsieda.cli submit eco_eval '{"case":"bp_fe","tag":"<程序标签>"}'` |
| 让 Agent 自主收敛 | “作业” 页提交 Agentic ECO closure，可设置预算和 dry run |
| 模块 RTL → GDS | “作业” 页提交 Block RTL→GDS |
| 安装诊断 | `bin/wangsictl doctor`，或本机 `python wangsi.py doctor` |
| 用自然语言提问 | 右上角“问 wangsiEDA”。例如“bp_fe 现在最好的认证结果是哪个？”“下一步该评测哪个程序？”“昨天那个作业为什么失败？”。助手只用只读工具回答，数字都来自查询；需要评测时给出建议作业，经校验（候选池内、未测过、参数白名单、时钟不可改）后显示为按钮，由你点击提交。每次提问是一个 `ask` 作业，过程可在作业页查看 |
| 运维（计算主机上） | `bin/wangsictl status`<br>`python3 -m wangsieda.cli jobs`<br>`python3 -m wangsieda.cli job <id>`<br>`python3 -m wangsieda.cli events <id>`<br>`python3 -m wangsieda.cli retry <step_id>` |
| 接入 MCP 客户端 | `python wangsi.py mcp` 会输出配置片段 |

## 3D 模型与校验

3D 模型的做法与 KLayout 2.5D、GDS3D 相同：
- 每段走线是一个实体，宽度取自工艺 LEF 的 `WIDTH`，厚度取自 `THICKNESS`，高度取自 `HEIGHT`；
- 通孔按 DEF 中的实例逐个绘制在相邻两层之间；
- 上层 die 按面对面键合镜像；
- 垂直方向可用“垂直放大”滑块放大。

点击“一致性校验”，可以把模型与独立来源逐项对照：

| 检查项 | 参考来源 |
|---|---|
| 布线段数、通孔数 | DEF 原文 |
| 标准单元数、宏单元数 | 评测器 6_report |
| DRC 标记数 | DRC 报告 |
| 总线长 | 评测器详细布线统计（容差 0.2%） |
| 逐层的高度、厚度、线宽、线段数与线长 | 工艺 LEF 与 DEF |

bp_fe 初始状态的核对结果：
- 269,174 段走线、246,062 个通孔、29,757 个标准单元、11 个 SRAM、4,257 个 DRC 标记，与来源逐一相同；
- 总线长与官方记录相差 0.11%。

## 本地数据包（双击即可打开）

在计算主机上导出脱敏快照，再生成本地数据包：

```bash
python3 -m wangsieda.export_snapshot /tmp/snapshot [设计名]
python3 make_offline_package.py /tmp/snapshot cloud/wangsieda/ui ./念思_数据包
```

`念思_数据包/打开念思.html` 在 Windows、macOS、Linux 上都可以直接双击打开，不需要安装任何软件，适合分发给同事和评审人员。导出时可以在配置的 `redact_terms` 中列出需要隐藏的名称。

## 导入自己的设计

控制台右上角的“导入设计”可以选择任意 DEF（可附带 LEF 与 OpenROAD DRC 报告），在浏览器本地解析，显示布局、全部布线和 DRC：
- 层顺序取自工艺 LEF，单元尺寸取自单元 LEF；
- 单元名带 `_upper` / `_bottom` 后缀时按两片 die 叠层显示。


## 安全设计

- **不修改已验证的工具**：只运行派生副本，并记录派生方式。
- **不重放**：项目中出现过的程序（评测、构建、回读、运行目录）一律拒绝重新评测。
- **只写独立命名空间**：所有写操作限制在平台目录和评测器运行目录下的 `wangsi/`。
- **Agent 输出必须通过守卫**：包括类型 / 范围 / 白名单校验、冻结契约（时钟、testbench）、不重复已试配置、续跑点校正、调用预算。
- **结果只在签核门全部通过时计入**：只有通过签核门的修复才会作为 verified 经验被自动复用。
- **控制台只能本机访问**：只监听 127.0.0.1，并要求 token。
- **敏感配置不入库**：`.gitignore` 已排除 `wangsi_config.json`、`prompts/*.txt`、`*.env`、`var/`、`jobs/`、`derived/`。

## 自检

```bash
bin/wangsictl selftest
```

自检会检查：
- 工具注册；
- 守卫是否拒绝非法动作；
- MCP 握手；
- 配置了 ECO 资产时，再检查判定逻辑与历史判定结果是否一致、派生工具能否生成。未配置的项目显示为 SKIP，不算失败。


## 文档

| 文档 | 内容 |
|---|---|
| [docs/部署与运维.md](docs/部署与运维.md) | 部署拓扑、systemd 服务、日常运维、备份、升级、容量与并发 |
| [docs/配置参考.md](docs/配置参考.md) | 全部配置项、签核门字段、评测器结果约定、外部模块接口 |
| [docs/接口参考.md](docs/接口参考.md) | HTTP API、MCP、3D 场景数据格式 |
| [docs/提示词评测.md](docs/提示词评测.md) | 内置提示词三个版本的真实模型评测结果与默认版本的理由 |
| [CHANGELOG.md](CHANGELOG.md) · [SECURITY.md](SECURITY.md) · [CONTRIBUTING.md](CONTRIBUTING.md) | 更新记录、安全说明、贡献指南 |

## 测试

```bash
cd cloud && python3 -m unittest discover tests     # 单元测试：签核判定、解析器、参数校验
bin/wangsictl selftest                              # 部署自检：工具注册、判定与历史结果一致性、派生、守卫、MCP
```

## 许可证

MIT，见 `LICENSE`。
