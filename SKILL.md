---
name: deepseek-codex-review
description: DeepSeek Harness 的 Codex 前置咨询与完工验收流程。接到用户需求时先评审方案，执行后续接同一 Codex 会话检查真实产物，修正后复验。
---

# DeepSeek 执行，Codex 审查

本 Skill 由 DeepSeek Harness 执行。Codex 在审查者角色下直接审查，不再调用本 Skill、Codex CLI 或其他智能体。

## 三个入口

先判断这次要什么，再选入口。**不要把需要评审的任务硬塞进方案流程，也不要为了省事跳过审核标准。**

| 入口 | 命令 | 是否要求方案 | 通过后意味着 |
|---|---|---|---|
| 咨询 | `consult` | 否 | 只拿到建议，**不构成验收**，不能放行交付 |
| 方案评审 | `plan` → `accept` | 是 | 允许按批准的方案实施，验收另做 |
| 项目评审 | `review` | **否** | 当前产物通过本次评审；直接审已有项目用它 |

- **解释概念、要个可行做法** → `consult`。轻量记录，不建正式任务、不生成需求清单、不算产物哈希。
- **从零开始做一件事** → `init` + `plan`，批准后再做，最后 `accept` 验收。
- **已经有代码/文档，要 Codex 挑问题** → `init`（不给 `--plan-file`）+ `review`。这类任务不必编造方案。

`review` 免掉的是方案审批，**不是审查标准**：它同样逐条核对需求覆盖、同样绑定产物哈希、同样续接同一会话、同样要 `verify`。审查范围与标准以 `request.md` 和 `requirements.json` 为准。

### 任务类型在 init 时固定，不能中途切换

`init` 时是否给了 `--plan-file` 决定任务类型，写进 `state.json` 的 `workflow`：

| `workflow` | 允许的命令 | 有效交付结论 |
|---|---|---|
| `PLAN_REQUIRED`（带方案初始化） | `plan`、`accept` | `ACCEPTANCE/PASS` |
| `DIRECT_REVIEW`（未给方案） | 仅 `review` | `REVIEW/PASS` |

**增删或修改任务目录里的 `plan.md` 都不会改变类型**，脚本会先判类型再判阶段。想在两种流程之间换，必须新建任务。这条由脚本强制，不只靠本文档约束。

旧任务（`state.json` 里没有 `workflow`）按历史自动推断并固定：有过方案批准记录或出现过 `plan` 轮次的算 `PLAN_REQUIRED`，其余算 `DIRECT_REVIEW`，并记录 `workflow_migrated_at`。推断后不再变化。

同一任务的澄清、修改、复评保留同一个明确的会话 ID；不同任务分别建会话。

## 调用入口

使用本 Skill 目录下的 `scripts/review.py`，Python 3.11+，仅用标准库。以下 PowerShell 示例中 `$review` 指向这个文件，路径以实际加载的 Skill 位置为准。本机默认路径：

```powershell
$review = 'C:\Users\Lenovo\.dsh\skills\deepseek-codex-review\scripts\review.py'
```

所有参数单独传递；长提示词由脚本通过 UTF-8 stdin 传给 Codex。脚本不指定模型、推理强度、服务档位，也不更改 Codex 配置，沿用本机与项目的有效配置。不得自行换模型或降低强度来绕过失败。

### Windows 运行权限

Codex 即使只读审查项目，也需要写自己的会话、日志和运行状态。若 DSH 命令沙箱拒绝写 `.codex`，这是外层运行限制，不能当成磁盘 ACL 损坏；不要修改 ACL、复制认证文件、换 CODEX_HOME 或换会话来绕过。使用宿主正式提供的单次命令审批；没有审批通道就报告具体拒绝与待授权步骤。不要擅自将整个 DeepSeek 会话切到完全权限。

如果用户已经批准并安装专用的 `codex_review` 工具，`plan` / `accept` / `review` / `status` / `verify` 优先用该工具，传 `action` 和 `task_dir`；它只允许调用固定审查脚本，保留 Codex 内层 `read-only`。`init`、旧任务的 `upgrade`、以及 `consult` 仍通过普通工作区命令执行。工具不可用时按下面的脚本方式调用，遇到权限拒绝遵守上一段。

## 咨询（可选）

需要向 Codex 要做法、判断或原理解释，而当前并不是要交付一个成果时用它：

```powershell
python $review consult --project '项目绝对路径' --question-file '问题绝对路径'
python $review consult --project '项目绝对路径' --question-file '追问绝对路径' --session '上次返回的 consult_id'
```

记录写在项目的 `work/codex-review-consult/<consult_id>/`，只保留问题、答复、会话 ID 和状态，不包含 `requirements.json`、`artifacts.json`、产物哈希，也没有 PASS/REVISE。同一 `consult_id` 续接原会话，避免反复交代背景。

咨询答复只是历史参考，不是事实依据，**不能当作方案批准或验收通过**，也不能约束后续正式评审必须认同它。Codex 之前的建议可能是错的，正式评审要独立判断。

## 开工前

1. 保存用户需求原文和后续澄清，保留全部范围、参考标准、约束与源材料位置；另列本任务适用的用户长期偏好、全局及项目指令，保留其来源，区分用户要求与源文档内容。拟定方案和验证方法。此时只整理材料，不修改正式产物。

   直接评审已有项目（`review` 入口）时，第 1 步改为：把用户交来的项目范围、审核标准、起始版本和要检查的产物写清楚，**不编造开发方案**。审核标准例如"功能正确性、并发安全、异常处理、资源泄漏"，由用户要求决定，不是你替用户缩小或放宽。
2. 另存两份 JSON 清单。`requirements.json` 将全部需求、质量标准及适用约束逐项编号；ID 在同一任务内稳定且唯一。清单不能替代原文，Codex 仍须检查清单是否遗漏或弱化了原始要求。例如：

```json
{"requirements":[{"id":"R1","text":"完整实现用户指定的功能"},{"id":"R2","text":"通过用户指定的测试与质量标准"}]}
```

`artifacts.json` 列出预期的正式文件、输出目录、原始验证记录，以及会影响验收的参考输入。填写本任务的实际路径，不照抄示例。允许项目相对路径或明确的绝对路径，不使用通配符。目录会绑定其全部文件及增删；计划新增或删除的路径可以暂不存在。只交付文字答复而没有文件时可以明确使用空列表，完整答复必须保存在 `execution.md` 供验收。Git 项目还会自动绑定项目范围内的已跟踪和未忽略文件、HEAD、暂存内容；被 Git 忽略的正式产物必须显式列出。

```json
{"paths":["src","tests","outputs/report.xlsx","work/test-evidence"]}
```

3. 用上述文件初始化一次任务。`--plan-file` 只在有方案时给出；直接评审已有项目时省略：

```powershell
python $review init --project '项目绝对路径' --request-file '需求文件绝对路径' --plan-file '方案文件绝对路径' --requirements-file '需求清单绝对路径' --artifacts-file '产物清单绝对路径'

# 直接评审已有项目：不给 --plan-file
python $review init --project '项目绝对路径' --request-file '审核要求绝对路径' --requirements-file '需求清单绝对路径' --artifacts-file '产物清单绝对路径'
```

返回的 `task_dir` 是本任务记录目录，默认在项目的 `work/codex-review/` 下；返回的 `next` 指示下一步该走 `plan` 还是 `review`。记录并持续使用这个绝对路径。脚本在创建记录前捕获 Git 基线，复制材料，创建 `execution.md`、`state.json`；只将审查目录加入仓库本地 `info/exclude`，不修改项目的 `.gitignore`。外层沙箱不允许写 Git 元数据时会明确返回警告，按警告在有权限的终端添加该条排除规则，不扩大普通命令权限。内部基线及产物快照始终排除审查运行记录。

4. 必要时完善任务目录内的原文、方案及两份清单，然后调用：

```powershell
python $review plan --task '返回的 task_dir'
```

只有返回 **`PLAN / PASS` 且退出码为 0** 才开始执行。`REVISE` 则修改方案并再次 `plan`；缺少必要信息或访问时解决具体问题后继续。首次调用取得的 `thread_id` 自动持久化，后续自动续接。不要另外手写 `codex exec` 命令，不用 `--last` 或 `--ephemeral`。

## 执行与验收

按已通过的完整需求和方案执行任务。用户改变需求时，追加原始澄清、更新方案及清单并重新 `plan`，不能静默缩减范围、降低标准或分期交付。修改需求、方案或任一清单都会使旧的方案批准失效。

将实际改动、产物绝对路径、测试/构建/渲染的命令和原始结果、未解决问题写进任务目录的 `execution.md`。代码审查包括已提交、暂存、未暂存与新增文件；说明本任务范围和正确的起始版本，区分原有改动。文档、表格、图片、证据任务要提供实际文件与必要的预览/截图，不能只提交完工总结。

```powershell
# 方案已批准的任务：验收
python $review accept --task '同一个 task_dir'

# 直接评审已有项目：不需要 PLAN/PASS
python $review review --task '同一个 task_dir'
```

`review` 与 `accept` 的结果语义相同（都是完工类评审），区别只在于是否以方案批准为前置。**`review` 不能用来绕过 `accept` 的方案门禁**：有方案的开发任务必须走 `accept`。

需要让审查者直接看到图片时，用可重复的 `--image '图片绝对路径'`，`plan`、`accept` 和 `review` 都支持。需要写文件的测试、构建、渲染由 DeepSeek 执行，保存原始记录；Codex 使用 `read-only`，负责阅读、查看和判断。

PLAN 附图的 SHA-256 会纳入方案指纹及批准记录。原附图被替换、修改、删除或不可读时，必须带上当前附图重新 `plan`；不能沿用旧的 PLAN/PASS。验收自动携带并再次绑定原方案附图，即使 `accept` 没有重复填写 `--image` 也不会遗漏。旧版带附图但未绑定版本的方案批准同样需要重新 `plan`，仍续接原会话。`review` 没有方案附图概念，只绑定本轮 `--image` 和产物快照。

- **`ACCEPTANCE / PASS` 或 `REVIEW / PASS`，退出码 0**：脚本已核对所有需求 ID 恰好出现一次、正文一致、没有失败或未核实项；仍须判断需求分解与内容证据是否充分，并执行下方 `verify` 再交付。
- **`REVISE`，退出码 10**：修复具体问题，更新 `execution.md`，再次 `accept`（或 `review`），仍续接原会话。不能达到若干轮就自动通过。
- **`BLOCKED`，退出码 11**：解决缺失信息或访问问题；需要用户时明确说明。
- **调用或记录错误，退出码 12**：读取本轮 `stderr.log`、`meta.json` 和 JSONL。失败、超时、额度耗尽、空输出、无会话 ID、无效 JSON 都不算通过。保留记录，不能伪造结论或偷偷换会话。进程意外终止留下锁文件时，先核实原进程已经退出，再移除本任务的 `.review.lock` 并重试。准备阶段失败（例如材料复制失败）本轮没有启动 Codex，修正原因后重新调用即可，不会另建会话。

`PLAN / PASS` 不是完工验收。任何要求失败、未核实、漏项或仅有结构检查证据时，不得宣称完成。视觉、语义与内容质量必须真正检查；文件存在、数量够、自动验证器通过都不能替代这种判断。

## 等待、记录与交付

脚本同步等待 Codex，默认最多 3600 秒；需要时可明确增加 `--timeout`。宿主工具返回后台会话时继续等待该进程，不要重复启动审查。审查期间不要改需求、方案、执行记录或正式产物。超时会结束调用并记为错误，不会给出通过。审查运行期间 `status` 仍可查询进度并显示 `RUNNING`；此时 `verify` 返回 `BUSY` 和退出码 10，等本轮结束再核实。

完工类评审开始和结束时，脚本对真实文件逐一计算 SHA-256，包含清单中的目录成员、缺失路径、附图以及 Git HEAD 和暂存内容。审查期间版本变化时本轮记为错误。符号链接或目录联接无法完整绑定时明确拒绝，不会静默跳过；按错误提供实体产物或实际目标路径后重新评审清单。

交付前必须核实当前版本：

```powershell
python $review verify --task '同一个 task_dir'
```

只有 `current_acceptance_valid: true` 且退出码 0 才交付。产物、附图、材料或 Git 版本改变，最近一次不是完工通过，结果记录受损或是旧版未绑定的结论，都会返回 `STALE`、退出码 10；先修正问题并重新 `accept`（`review` 任务重新 `review`），涉及需求或清单变动时先重新 `plan`。`status` 展示历史及当前有效性，其退出码 0 仅表示成功读取状态。

旧版任务先补充两份清单，运行 `python $review upgrade --task '原 task_dir' --requirements-file '清单路径' --artifacts-file '清单路径'`，再 `plan`。升级保留原会话 ID 和全部历史，不把旧版 PASS 当成新校验已经通过。

每轮在 `runs/` 留存材料快照、产物哈希清单、提示词、JSONL、stderr、退出码和最终 JSON。结果必须带回本轮 `review_fingerprint`，不能挪用其他材料或产物版本的结论。交付时说明实际结果、仍存在的限制、审查会话 ID 和最终审查记录位置。

清单完整性和语义、视觉质量仍依赖真实审查；哈希只能绑定已声明或自动捕获的范围。常驻指令负责提醒 DeepSeek 执行此流程，脚本负责校验调用和版本；本地状态可写，不是不可篡改的审计或不可绕过的权限门禁。代码任务可以另接 PR 和必需检查，不能把一条“通过”评论当成已配置合并门禁。
