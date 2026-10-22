# deepseek-codex-review

供 DeepSeek Harness 使用的 Codex 前置咨询与完工验收 Skill。

```text
收到需求 → 原文及逐项清单 → Codex 评审方案 → DeepSeek 执行 → 同一 Codex 会话验收 → 修复并复验 → 核实版本后交付
```

每项任务使用独立的 Codex 审查会话 ID，方案修订、验收和复验持续续接这个 ID。模型、推理强度与服务档位沿用 Codex 的有效配置。

## 文件

| 文件 | 用途 |
|---|---|
| `SKILL.md` | DeepSeek 加载的流程与审查要求 |
| `scripts/review.py` | 调用 Codex、续接会话、记录与校验结果 |
| `scripts/dsh-host-tool.mjs` | 经用户授权后使用的 DSH 专用审查工具 |
| `references/review.schema.json` | 审查结果的 JSON Schema |
| `tests/` | 可离线复现的脚本行为及宿主边界回归测试 |
| `.github/workflows/tests.yml` | Windows / Linux 上的公开回归检查 |

## 环境

- DeepSeek Harness，启用本地 Skill 发现与全局指令加载。
- Python 3.11+，调用脚本仅依赖 Python 标准库。
- 已安装并登录的 Codex 原生 CLI，能够从 PATH 找到。
- Git 用于记录代码任务开始时的版本与工作区状态。
- DSH 宿主适配工具使用其已安装的 Node.js 和 `@deepseek-ai/dsh-tools`。

本机实际验证环境为 Windows、Codex CLI 0.163.0；其他版本升级后应检查兼容性。

## 安装与使用

默认安装目录为 `~/.dsh/skills/deepseek-codex-review`；设置了 `DSH_HOME` 时使用其 `skills` 子目录。`SKILL.md` 中的本机绝对路径示例应按实际安装位置调整。

首次安装时，在目标目录不存在的前提下可执行：

```powershell
gh repo clone wucong220/deepseek-codex-review "$env:USERPROFILE\.dsh\skills\deepseek-codex-review"
```

要让每项需求自动触发，在 DSH 的全局 `AGENTS.md` 中追加常驻要求，保留其其他内容：

> 每项用户需求在实质开工前加载 deepseek-codex-review Skill，先通过 Codex 方案评审；执行后续接同一个审查会话验收，修复后复验，通过后再交付。保存完整需求、适用约束与真实产物证据。Codex 审查者不递归调用此流程。

命令入口和材料要求见 [SKILL.md](SKILL.md)。基本调用：

```powershell
$review = "$env:USERPROFILE\.dsh\skills\deepseek-codex-review\scripts\review.py"
python $review init --project '项目绝对路径' --request-file '需求文件绝对路径' --plan-file '方案文件绝对路径' --requirements-file '需求清单绝对路径' --artifacts-file '产物清单绝对路径'
python $review plan --task '上一步返回的 task_dir'
# 按通过的需求和方案执行，更新该任务的 execution.md 后：
python $review accept --task '同一个 task_dir'
python $review verify --task '同一个 task_dir'
python $review status --task '同一个 task_dir'
```

需求清单格式为 `{"requirements":[{"id":"R1","text":"完整要求"}]}`；产物清单格式为 `{"paths":["src","outputs/report.xlsx"]}`。清单必须按实际任务编写，范围、来源及例子见 [SKILL.md](SKILL.md)。两份清单都参加方案批准校验，不能在验收时静默删减要求或产物。

退出码：`0` 为相应阶段通过，`10` 为需要修改或版本已失效，`11` 为阻塞，`12` 为调用或记录错误。`PLAN/PASS` 只允许进入执行阶段；完工必须取得 `ACCEPTANCE/PASS`，并在交付前执行 `verify`。`status` 的退出码 0 只表示成功读取，不代表验收通过。

记录保存在项目的 `work/codex-review/<task-id>/`，各轮材料、提示词、JSONL、stderr 与最终审查结果单独留存。

## Windows 与专用审查工具

Codex 在只读审查项目时仍需要写自己的会话与运行状态。DSH 的外层命令沙箱可能阻止这些写入。应使用正式命令审批，或在用户明确授权后配置 `scripts/dsh-host-tool.mjs`。

专用工具的 DSH 插件配置需要本机的 `dshRoot`、`pythonExe`、`reviewScript`、`scriptSha256` 和明确的 `authorizedHostReview: true`。它只接受 `plan`、`accept`、`status`、`verify`，校验任务归属当前工作区和固定脚本的 SHA-256；Codex 内层保持 `read-only`。每台机器单独配置自己的路径与授权。

该工具启用后，DeepSeek 优先使用 `codex_review` 的 `action`、`task_dir` 参数；任务初始化和旧任务升级继续使用普通工作区命令。普通命令的权限设置保持原样。

## 维护

- 调整流程和审查要求时修改 `SKILL.md`；单次任务要求保留在任务材料里。
- 修改 `scripts/review.py` 后，先测试，再更新 DSH 专用工具配置中的受信任 `scriptSha256`，重载配置。保留哈希校验。
- 修改宿主适配工具后，重新验证工作区边界、动作限制和取消行为，再重载 DSH。
- 本仓库保留文件字节，避免 Git 的换行转换改变审查脚本哈希。
- 修改后先查看 `git diff`，再提交并 `git push`。更新前有本地修改时先处理这些修改，再 `git pull --ff-only`。
- 旧任务用 `upgrade --task ... --requirements-file ... --artifacts-file ...` 补齐清单，保留原会话并重新 `plan`；旧版未绑定产物的 PASS 不会自动变成有效验收。

检查审查脚本哈希：

```powershell
Get-FileHash "$env:USERPROFILE\.dsh\skills\deepseek-codex-review\scripts\review.py" -Algorithm SHA256
```

## 回归测试

无需 Codex 登录、API Key 或调用模型即可运行：

```powershell
python -X utf8 -B -m unittest discover -s tests -v
node --test tests/test_host_tool.mjs
```

Python 测试使用模拟审查结果，验证漏项、重复/未知需求、错误会话、调用失败、超时、并发、Git 基线、文件变化及过期结论等行为；不证明模型审查质量。Node 测试验证工作区归属、目录重定向和配置约束。

本机已安装 DSH 时，设置绝对路径环境变量 `DSH_PACKAGE_ROOT`（含 `node_modules/@deepseek-ai/dsh-tools` 的 DSH 包目录）、`PYTHON_EXE` 后运行 Node 测试，还会验证真实 DSH 工具注册、固定脚本调用、动作白名单和哈希校验。未设置时该项明确跳过，其余边界测试仍运行。公开 CI 不安装或调用大模型客户端。

## 校验范围

脚本要求审查结果对需求清单的每个 ID 恰好检查一次、正文一致，PASS 没有失败/未核实项。验收绑定真实文件内容、目录增删、附图、Git HEAD 与暂存内容；审查中变化会报错，通过后变化会被 `verify` 拒绝。审查运行记录本身被排除，初始化基线在创建记录前捕获；Git 本地排除规则避免记录污染普通工作区状态。

需求分解是否完整、证据是否充分以及语义和视觉质量，仍须对照原始需求检查。被 Git 忽略的产物要显式列入清单；无法完整绑定的链接会明确报错。版本哈希不证明质量，本地状态及记录也可写。此 Skill 提供执行约定和一致性检查，不能声称是不可绕过的权限门禁；代码任务可额外接入 PR 和必需检查。
