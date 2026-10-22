# deepseek-codex-review

供 DeepSeek Harness 使用的 Codex 前置咨询与完工验收 Skill。

```text
收到需求 → Codex 评审方案 → DeepSeek 执行 → 同一 Codex 会话验收 → 修复并复验 → 通过后交付
```

每项任务使用独立的 Codex 审查会话 ID，方案修订、验收和复验持续续接这个 ID。模型、推理强度与服务档位沿用 Codex 的有效配置。

## 文件

| 文件 | 用途 |
|---|---|
| `SKILL.md` | DeepSeek 加载的流程与审查要求 |
| `scripts/review.py` | 调用 Codex、续接会话、记录与校验结果 |
| `scripts/dsh-host-tool.mjs` | 经用户授权后使用的 DSH 专用审查工具 |
| `references/review.schema.json` | 审查结果的 JSON Schema |

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
python $review init --project '项目绝对路径' --request-file '需求文件绝对路径' --plan-file '方案文件绝对路径'
python $review plan --task '上一步返回的 task_dir'
# 按通过的需求和方案执行，更新该任务的 execution.md 后：
python $review accept --task '同一个 task_dir'
python $review status --task '同一个 task_dir'
```

退出码：`0` 为相应阶段通过，`10` 为需要修改，`11` 为阻塞，`12` 为调用或记录错误。`PLAN/PASS` 只允许进入执行阶段，完工必须取得 `ACCEPTANCE/PASS`。

记录保存在项目的 `work/codex-review/<task-id>/`，各轮材料、提示词、JSONL、stderr 与最终审查结果单独留存。

## Windows 与专用审查工具

Codex 在只读审查项目时仍需要写自己的会话与运行状态。DSH 的外层命令沙箱可能阻止这些写入。应使用正式命令审批，或在用户明确授权后配置 `scripts/dsh-host-tool.mjs`。

专用工具的 DSH 插件配置需要本机的 `dshRoot`、`pythonExe`、`reviewScript`、`scriptSha256` 和明确的 `authorizedHostReview: true`。它只接受 `plan`、`accept`、`status`，校验任务归属当前工作区和固定脚本的 SHA-256；Codex 内层保持 `read-only`。每台机器单独配置自己的路径与授权。

该工具启用后，DeepSeek 优先使用 `codex_review` 的 `action`、`task_dir` 参数；任务初始化继续使用普通工作区命令。普通命令的权限设置保持原样。

## 维护

- 调整流程和审查要求时修改 `SKILL.md`；单次任务要求保留在任务材料里。
- 修改 `scripts/review.py` 后，先测试，再更新 DSH 专用工具配置中的受信任 `scriptSha256`，重载配置。保留哈希校验。
- 修改宿主适配工具后，重新验证工作区边界、动作限制和取消行为，再重载 DSH。
- 本仓库保留文件字节，避免 Git 的换行转换改变审查脚本哈希。
- 修改后先查看 `git diff`，再提交并 `git push`。更新前有本地修改时先处理这些修改，再 `git pull --ff-only`。

检查审查脚本哈希：

```powershell
Get-FileHash "$env:USERPROFILE\.dsh\skills\deepseek-codex-review\scripts\review.py" -Algorithm SHA256
```

## 已验证的行为

2026-10-23 已完成 11 项脚本行为测试、专用工具边界检查，以及真实的方案评审、错误产物拒绝、DeepSeek 修复、同一会话复验通过。调用未附加模型或推理强度覆盖。

常驻指令提供执行约定，脚本校验调用记录和结果一致性。完整性、语义和视觉质量仍须按全部用户要求检查真实产物，成果变更后需要重新验收。
