# 模板

## 任务卡

```markdown
## <阶段>-<模块>-<序号> <标题>
- **状态**：✅ 完成（日期）：一句话结论 + 关键证据；证据见 [进度记录](进度记录.md)
- **依赖**：<卡号>、<卡号>
- **范围**：做什么；不做什么（写明推迟到哪里）。
- **产出**：代码路径、命令、文档、环境状态。
- **验证**：不依赖未完成的卡；每条有明确的通过标准（测试名 / 对照工具 / 真实环境现象 / 命令输出）。
- **规模**：大 / 中 / 小 · **参考**：§x
```

状态行只在做了之后加：🟡 进行中、✅ 完成、⏸ 暂停。部分完成要写清剩下什么、记在哪里。状态行不超过三行：数字、逐格结果和 review 细节写进进度记录或结果文件，状态行只留结论和指向。

提交说明带 `(<卡号>)`，看板（SKILL.md「看板」）据此取完成时间。

## docs/tasks/README.md 的结构

1. **下一步**：「下次从这里开始」写当前卡和具体的第一步；用户的排期决定带日期。
2. **文件**：表格，列出每个文件的内容、卡数和已完成数。
3. **待办（backlog）**：还没排期的事项，每条写记录日期、由来（谁提的、哪张卡发现的）、范围、要决定的事和规模。
   做完的用删除线标出，并写明由哪张卡完成。
4. **进度记录**：只放一行链接，指向 `docs/tasks/进度记录.md`。那个文件是一张表格，每张卡（或每一轮）一行，列为日期、卡、结果、证据。证据写可核对的事实，以及 review 修了什么。单元格不换行。实现 agent 和 review 不必读它。
5. **使用说明**：卡号规则、字段含义、统一的完成标准。
6. **工作流程**：即 SKILL.md 阶段 3 的步骤。
7. **总览 / 卡片索引**：依赖图和编号表。

## 实现 agent（card-worker）的 prompt

```
You are the agent executing this card. Do the work yourself; do not spawn agents and do not just report status.
Card: <卡号> <标题> (docs/tasks/<文件>). HEAD is <提交>. Do implementation and verification. Don't commit.
Context: <项目约束文件；本卡要解决什么；已定的决定，不再讨论；已完成的部分（接手时）>.
1. Read: the card, design §x, <样本 / 上次的记录>; for a large file give sections or line ranges. Do not read whole: <用不到的大文件>.
2. Reference (if any): <对照工具与要加的场景>; output must match byte for byte.
3. Code: <要点；保持最小、照现有风格>.
4. Tests: <卡片验证字段要求的测试，含失败与超时分支>.
5. Run: <全量测试或门禁命令>.
6. Real environment (if any): <构建、操作、要记的证据>; restore: <还原清单>; check output for secrets.
7. Docs: card status line (three lines at most), design notes. Serial mode only: progress row in docs/tasks/进度记录.md and 「下次从这里开始」 → <下一张>; in parallel mode the main session writes those.
Commands longer than 10 minutes run detached from the session (setsid), logging to a scratch file; wait for the pid with one run_in_background waiter, no polling.
Read the whole region you need (a whole function, or 200+ lines; files under 400 lines whole) and several files per command; read large docs by section after rg; do not re-read a file to confirm an edit.
Report the changed files, test results, evidence, decisions you made yourself, and 「砍掉和推后的」.
```

## 独立 review 的 prompt

```
You are an independent, READ-ONLY reviewer for <repo>. Do not edit repo files, do not change git state, and do not touch shared environments.
Review the uncommitted working-tree changes for <卡号> (git diff + untracked). Ignore <用户自己的未提交文件>.
Background: <这张卡要解决什么、已知的真实行为、实现声称做了什么>.
Check:
1. Correctness, concurrency, leaks, timeouts, error codes vs existing conventions.
2. Output faithful to the reference (if any); goldens actually used by byte-for-byte tests.
3. Tests meaningful: mutate a scratch copy in a temp directory (never the repo) and confirm the tests fail.
4. Docs consistent with each other, the code and the evidence; inference not written as fact; nothing overstated; acceptance criteria met.
5. Conventions: <项目约定，如注释语言、测试数据、无密钥>.
Run: <静态检查 / 相关测试>. The implementer's check log is <提交号, 命令, 结果>; when that commit equals the branch head, rerun only the tests tied to your findings and mutations, otherwise run the full suite.
Keep search output short: `rg -l`/`-c` first, then `-m` or `| head`; `git diff --stat` before per-file diffs. Read the code you judge in full.
Output findings ranked high / medium / low, each with file:line, the problem, a concrete failure scenario and a fix. Say explicitly when a level has none.
```

## 把 review 结果发回实现 agent

```
Independent review: high N / medium N / low N. Fix them in this order and still don't commit.
Medium 1 (<file:line>): <问题> → <期望的改法；先核实什么>.
Low 1 … <每条一句改法；可以不改的写明「成本高就跳过并说明」>.
If the fix changes a conclusion from the real environment: retest once (<步骤>), restore.
Then run <全量测试或门禁命令> and report the changed files and evidence.
```
