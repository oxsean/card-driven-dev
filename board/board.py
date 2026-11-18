#!/usr/bin/env python3
"""Live progress board for a card-driven project.

Reads the task cards (docs/tasks/*.md) and git history of a repository, plus the Claude Code
transcripts of sessions run in it (~/.claude/projects), and serves a page that polls /data every
few seconds. Nothing is written to the repository.

    python3 board.py --repo /path/to/repo [--port 8765] [--tasks docs/tasks]
"""
import argparse
import calendar
import http.server
import json
import random
import re
import subprocess
import threading
import time
from pathlib import Path

POINTS = {"小": 1, "中": 2, "大": 4}
CARD_ID = r"[A-Z][A-Za-z0-9]*-[A-Za-z0-9][A-Za-z0-9-]*"
HEADING = re.compile(rf"^## ({CARD_ID})(?:\s+(.*))?$")
PROGRESS_LOG = "进度记录.md"
FIELD = re.compile(r"^- \*\*(状态|依赖|规模)\*\*[:：]\s*(.*)$")
ID_IN_TEXT = re.compile(rf"(?<![A-Za-z0-9-])({CARD_ID})(?![A-Za-z0-9-])")
MAIN_FILE = re.compile(r"^M\d+\.md$")
SIMULATIONS = 2000
IDLE_GAP = 600
RUNNING_WINDOW = 120
TIMESTAMP = re.compile(rb'"timestamp":"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?)Z"')


class Activity:
    """Agent execution time from the Claude Code transcripts of the repository and its worktrees.

    Two consecutive entries of one transcript at most IDLE_GAP apart count as execution; time when
    several agents run at once counts once. Files are read incrementally.
    """

    def __init__(self, repo):
        key = re.sub(r"[^A-Za-z0-9]", "-", str(repo))
        root = Path.home() / ".claude" / "projects"
        self.dirs = [d for d in (root.glob(glob_escape(key) + "*") if root.exists() else [])
                     if d.name == key or d.name.startswith(key + "--claude-worktrees-")]
        self.files = {}

    def scan(self):
        for d in self.dirs:
            for f in d.rglob("*.jsonl"):
                st = self.files.setdefault(f, {"offset": 0, "last": None, "spans": []})
                try:
                    size = f.stat().st_size
                except OSError:
                    continue
                if size <= st["offset"]:
                    continue
                with f.open("rb") as fh:
                    fh.seek(st["offset"])
                    chunk = fh.read(size - st["offset"])
                end = chunk.rfind(b"\n") + 1
                st["offset"] += end
                for m in TIMESTAMP.finditer(chunk[:end]):
                    t = calendar.timegm(time.strptime(m.group(1)[:19].decode(), "%Y-%m-%dT%H:%M:%S"))
                    if st["last"] is not None and 0 < t - st["last"] <= IDLE_GAP:
                        st["spans"].append((st["last"], t))
                    if st["last"] is None or t > st["last"]:
                        st["last"] = t
        spans = sorted(s for st in self.files.values() for s in st["spans"])
        merged = []
        for a, b in spans:
            if merged and a <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        self.merged = merged
        self.last_seen = max((st["last"] for st in self.files.values() if st["last"]), default=None)
        return bool(merged)

    def between(self, lo, hi):
        return sum(max(0.0, min(b, hi) - max(a, lo)) for a, b in self.merged)


def glob_escape(text):
    return re.sub(r"([*?\[])", r"[\1]", text)


def card_state(status):
    s = status.strip().lstrip("✅🟡⏸ ").strip()
    if not status.strip():
        return "todo"
    if s.startswith("部分完成"):
        return "partial"
    if re.match(r"(已)?完成|关闭|已测量|局部修改已完成", s):
        return "done"
    if re.match(r"未开始|移出", s):
        return "todo"
    if s.startswith("暂停") or status.strip().startswith("⏸"):
        return "paused"
    return "active"


def git(repo, *args):
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else ""


def section(text, number):
    m = re.search(rf"^## {number}\.[^\n]*\n(.*?)(?=^## \d+\.|\Z)", text, re.S | re.M)
    return m.group(1) if m else ""


def file_order(readme, tasks_dir):
    names = re.findall(r"^\|\s*([^|\s]+\.md)\s*\|", section(readme, 2), re.M)
    names = [n for n in names if (tasks_dir / n).exists()]
    return names or sorted(p.name for p in tasks_dir.glob("*.md") if p.name not in ("README.md", PROGRESS_LOG))


def parse_cards(tasks_dir, files):
    cards = []
    for name in files:
        current = None
        for line in (tasks_dir / name).read_text(encoding="utf-8").splitlines():
            h = HEADING.match(line)
            if h:
                current = {"id": h.group(1), "title": (h.group(2) or "").strip(), "file": name,
                           "status": "", "deps_text": "", "size": ""}
                cards.append(current)
                continue
            if line.startswith("## "):
                current = None
                continue
            f = FIELD.match(line)
            if current and f:
                key = {"状态": "status", "依赖": "deps_text", "规模": "size"}[f.group(1)]
                if not current[key]:
                    current[key] = f.group(2).strip()
    return cards


def resolve_deps(cards):
    by_file = {}
    for c in cards:
        by_file.setdefault(c["file"].removesuffix(".md"), []).append(c["id"])
    ids = {c["id"] for c in cards}
    for c in cards:
        text = c["deps_text"]
        deps = set()
        excluded = set()
        for m in re.finditer(r"([A-Za-z0-9]+)\s*全部(?:（([^）]*)除外）)?", text):
            excluded |= set(ID_IN_TEXT.findall(m.group(2) or ""))
            deps |= set(by_file.get(m.group(1), []))
        text_wo_all = re.sub(r"[A-Za-z0-9]+\s*全部(?:（[^）]*）)?", "", text)
        deps |= {d for d in ID_IN_TEXT.findall(text_wo_all) if d in ids}
        deps -= excluded | {c["id"]}
        c["deps"] = sorted(deps)


def progress_log(readme, tasks_dir):
    path = tasks_dir / PROGRESS_LOG
    extra = path.read_text(encoding="utf-8") if path.exists() else ""
    return section(readme, 4) + "\n" + extra


def completion_times(repo, cards, readme, tasks_dir):
    times = {}
    for line in git(repo, "log", "--format=%ct\t%s").splitlines():
        ts, _, subject = line.partition("\t")
        for cid in re.findall(rf"\(({CARD_ID})\)", subject):
            times.setdefault(cid, int(ts))
    log = progress_log(readme, tasks_dir)
    for m in re.finditer(rf"^\|\s*(\d{{4}}-\d{{2}}-\d{{2}})\s*\|\s*({CARD_ID})\s*\|", log, re.M):
        times.setdefault(m.group(2), int(time.mktime(time.strptime(m.group(1) + " 12", "%Y-%m-%d %H"))))
    for c in cards:
        if c["state"] in ("done", "partial") and c["id"] not in times:
            m = re.search(r"(\d{4}-\d{2}-\d{2})", c["status"])
            if m:
                times[c["id"]] = int(time.mktime(time.strptime(m.group(1) + " 12", "%Y-%m-%d %H")))
    return times


def ancestors(cid, by_id):
    seen, stack = set(), [cid]
    while stack:
        for d in by_id[stack.pop()]["deps"]:
            if d not in seen:
                seen.add(d)
                stack.append(d)
    return seen


def percentile(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))]


def rate_samples(cards, times, start, duration):
    """Hours per point of each completed card, timed from the completion before it."""
    done = sorted(((times[c["id"]], c) for c in cards if c["state"] in ("done", "partial") and c["id"] in times),
                  key=lambda e: e[0])
    samples, prev = [], start
    for t, c in done:
        samples.append(duration(prev, t) / 3600 / c["points"])
        prev = max(prev, t)
    return samples, prev


def simulate(samples, groups):
    """Cumulative hours to finish each group in order, SIMULATIONS draws per group."""
    rng = random.Random(0)
    finish = [[] for _ in groups]
    for _ in range(SIMULATIONS):
        total = 0.0
        for i, group in enumerate(groups):
            for c in group:
                total += rng.choice(samples) * c["points"]
            finish[i].append(total)
    return finish


def estimate(cards, times, first_commit, files, now):
    samples, prev = rate_samples(cards, times, first_commit, lambda a, b: max(b - a, 0))
    main = [f for f in files if MAIN_FILE.match(f)]
    remaining = [[c for c in cards if c["file"] == f and c["state"] not in ("done", "partial")] for f in main]
    if not samples:
        return {"samples": 0, "milestones": []}
    milestones = []
    for f, group, hours in zip(main, remaining, simulate(samples, remaining)):
        milestones.append({
            "file": f, "remaining": len(group),
            "remaining_points": sum(c["points"] for c in group),
            "p50": now + percentile(hours, 0.5) * 3600, "p90": now + percentile(hours, 0.9) * 3600,
        })
    return {
        "samples": len(samples),
        "hours_per_point_median": percentile(samples, 0.5),
        "hours_per_point_mean": sum(samples) / len(samples),
        "since_last_done_hours": (now - prev) / 3600,
        "milestones": milestones,
    }


def agent_time(activity, cards, times, files, now):
    """Agent execution hours: per point so far, per active card, and left for the main line.

    Cards often run two at a time, so the time between two completions says little about one card;
    a card's expectation uses the overall hours per point instead, with the sampled 90th percentile.
    """
    if not activity or not activity.scan():
        return None
    start = activity.merged[0][0]
    samples, last_done = rate_samples(cards, times, start, activity.between)
    done_points = sum(c["points"] for c in cards if c["state"] in ("done", "partial") and c["id"] in times)
    rate = activity.between(start, last_done) / 3600 / done_points if done_points else None
    main = [f for f in files if MAIN_FILE.match(f)]
    remaining = [[c for c in cards if c["file"] == f and c["state"] not in ("done", "partial")] for f in main]
    finish = simulate(samples, remaining) if samples else []
    running = activity.last_seen is not None and now - activity.last_seen <= RUNNING_WINDOW
    current = []
    for c in cards:
        if c["state"] != "active":
            continue
        began = max((times[d] for d in c["deps"] if d in times), default=last_done)
        spent = activity.between(began, now)
        per_card = [x * c["points"] for x in samples]
        current.append({
            "id": c["id"], "title": c["title"], "points": c["points"], "since": began,
            "wall_hours": (now - began) / 3600, "agent_hours": spent / 3600,
            "expect_p50": rate * c["points"] if rate else None,
            "expect_p90": max(rate * c["points"], percentile(per_card, 0.9)) if rate and per_card else None,
        })
    return {
        "total_hours": activity.between(start, now) / 3600,
        "running": running, "last_seen": activity.last_seen,
        "hours_per_point": rate,
        "current": current,
        "milestones": [{"file": f, "p50": percentile(h, 0.5), "p90": percentile(h, 0.9)}
                       for f, h in zip(main, finish)],
    }


def progress(cards):
    main = [c for c in cards if MAIN_FILE.match(c["file"])]
    done = [c for c in main if c["state"] in ("done", "partial")]
    return {"main_cards": len(main), "main_done": len(done),
            "main_points": sum(c["points"] for c in main), "main_done_points": sum(c["points"] for c in done)}


def burnup(cards, times, first_commit):
    events = sorted((times[c["id"]], POINTS.get(c["size"][:1], 2), c["id"])
                    for c in cards if c["state"] in ("done", "partial") and c["id"] in times
                    and MAIN_FILE.match(c["file"]))
    total, points = 0, [{"t": first_commit, "points": 0, "id": ""}]
    for t, p, cid in events:
        total += p
        points.append({"t": t, "points": total, "id": cid})
    return points


def build(repo, tasks_rel, activity=None):
    tasks_dir = repo / tasks_rel
    readme_path = tasks_dir / "README.md"
    readme = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""
    files = file_order(readme, tasks_dir)
    cards = parse_cards(tasks_dir, files)
    resolve_deps(cards)
    by_id = {c["id"]: c for c in cards}
    for c in cards:
        c["state"] = card_state(c["status"])
        c["points"] = POINTS.get(c["size"][:1], 2)

    next_up = section(readme, 1)
    active_ids = set()
    for m in re.finditer(r"进行中[：:]\s*([^；;。\n]+)", next_up):
        active_ids |= {i for i in ID_IN_TEXT.findall(m.group(1)) if i in by_id}
    pause = re.search(r"[^；;。\n（(]*暂停[^；;。\n]*", next_up)
    pause_text = pause.group(0).strip(" -*：:") if pause else ""
    pause_until = None
    if pause:
        m = re.search(rf"暂停到\s*\**\s*({CARD_ID})", pause.group(0))
        pause_until = m.group(1) if m and m.group(1) in by_id else None
        if pause_until and by_id[pause_until]["state"] in ("done", "partial"):
            pause = None
            pause_text, pause_until = "", None
    exempt = ancestors(pause_until, by_id) | {pause_until} if pause_until else set()

    for c in cards:
        if c["state"] in ("done", "partial", "paused"):
            continue
        if c["id"] in active_ids or c["state"] == "active":
            c["state"] = "active"
        elif all(by_id[d]["state"] in ("done", "partial") for d in c["deps"]):
            c["state"] = "paused" if pause and c["id"] not in exempt else "ready"
        else:
            c["state"] = "waiting"

    times = completion_times(repo, cards, readme, tasks_dir)
    for c in cards:
        c["done_at"] = times.get(c["id"]) if c["state"] in ("done", "partial") else None
    first = git(repo, "log", "--reverse", "--format=%ct").split()
    first_commit = int(first[0]) if first else int(time.time())
    now = time.time()
    commits = [dict(zip(("t", "hash", "subject"), l.split("\t", 2)))
               for l in git(repo, "log", "-12", "--format=%ct\t%h\t%s").splitlines()]
    packages = []
    for f in files:
        if not cards or not any(c["file"] == f for c in cards):
            n = len(re.findall(r"^## [A-Z]\d+\b", (tasks_dir / f).read_text(encoding="utf-8"), re.M))
            if n:
                packages.append({"file": f, "count": n})
    groups = []
    for f in files:
        group = [c for c in cards if c["file"] == f]
        if group:
            m = re.search(rf"^\|\s*{re.escape(f)}\s*\|\s*([^|]+)\|", section(readme, 2), re.M)
            groups.append({"file": f, "label": f.removesuffix(".md"),
                           "summary": m.group(1).strip() if m else "", "main": bool(MAIN_FILE.match(f)),
                           "cards": [c["id"] for c in group]})
    return {
        "repo": repo.name, "now": now, "cards": cards, "groups": groups, "packages": packages,
        "pause": pause_text, "pause_until": pause_until,
        "estimate": estimate(cards, times, first_commit, files, now),
        "agent": agent_time(activity, cards, times, files, now),
        "progress": progress(cards),
        "burnup": burnup(cards, times, first_commit),
        "commits": commits,
    }


class Handler(http.server.BaseHTTPRequestHandler):
    page = Path(__file__).with_name("board.html")
    lock = threading.Lock()
    cache = (0.0, b"")

    def log_message(self, *args):
        pass

    def send(self, body, kind):
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/data"):
            with Handler.lock:
                at, body = Handler.cache
                if time.time() - at > 1.5:
                    body = json.dumps(build(self.server.repo, self.server.tasks, self.server.activity),
                                      ensure_ascii=False).encode()
                    Handler.cache = (time.time(), body)
            self.send(body, "application/json; charset=utf-8")
        elif self.path in ("/", "/index.html"):
            self.send(self.page.read_bytes(), "text/html; charset=utf-8")
        else:
            self.send_error(404)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", default=".", help="repository root (default: current directory)")
    p.add_argument("--tasks", default="docs/tasks", help="task card directory, relative to the repo")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--dump", action="store_true", help="print the data once as JSON and exit")
    a = p.parse_args()
    repo = Path(a.repo).resolve()
    activity = Activity(repo)
    if a.dump:
        print(json.dumps(build(repo, a.tasks, activity), ensure_ascii=False, indent=1))
        return
    server = http.server.ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    server.repo, server.tasks, server.activity = repo, a.tasks, activity
    print(f"board: http://127.0.0.1:{a.port}/  ({repo})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
