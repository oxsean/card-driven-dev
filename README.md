# card-driven-dev

A [Claude Code](https://docs.claude.com/en/docs/claude-code) skill for running a project card by card. The skill text is in Chinese.

You own the requirements, the trade-offs and the approvals. The main session splits the work into cards, dispatches them and checks the results. Sub agents implement each card and review it.

Flow: discuss requirements → design doc → milestone cards → for each card, implement → verify → independent review → fix → commit. `/loop` can drive the cards automatically.

## Contents

| Path | What it is |
|---|---|
| `SKILL.md` | The workflow: requirements, design, cards, per-card loop, quality, `/loop`, parallelism, cost control |
| `references/` | Card and README templates, testing and review rules, reference implementations and real-environment labs |
| `agents/card-worker.md` | The long-running implementation agent the skill dispatches (Opus, up to 1000 turns) |
| `board/` | A local, read-only progress board: a galaxy view of cards and dependencies, progress rings and a completion estimate |

## Install

```sh
git clone https://github.com/oxsean/card-driven-dev.git
ln -s "$PWD/card-driven-dev" ~/.claude/skills/card-driven-dev
ln -s "$PWD/card-driven-dev/agents/card-worker.md" ~/.claude/agents/card-worker.md
```

In a project, say 「拆卡」 or 「按任务卡推进」 to start; say 「开看板」 to open the board.

## Board

```sh
python3 board/board.py --repo <your repo>   # serves http://127.0.0.1:8765/
python3 board/board.py --repo <your repo> --dump
```

It reads the cards in `docs/tasks/`, the git history and the Claude Code transcripts under `~/.claude/projects/`. It only reads; it never writes the repository. It needs Python 3 and no other packages.

## License

MIT
