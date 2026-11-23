import asyncio, json, os, re
from . import db
from .providers import chat

MAX_SUBTASKS = int(os.getenv("MAX_SUBTASKS", "3"))
MAX_ROUNDS = int(os.getenv("MAX_DISCUSS_ROUNDS", "0"))  # 0 = 关闭互评，最省钱
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "2000"))
PLAN_TOKENS = 700

def say(tid, agent, kind, text):
    db.x("INSERT INTO messages(task_id,agent,kind,content,ts) VALUES(?,?,?,?,?)", (tid, agent, kind, text, db.now()))

def load_agents():
    agents = {a["name"]: a for a in db.q("SELECT * FROM agents WHERE enabled=1")}
    mgr = next((a for a in agents.values() if a["role"] == "manager"), None)
    experts = {n: a for n, a in agents.items() if a["role"] == "expert"}
    if not mgr or not experts:
        raise RuntimeError("请先启用一个主控AI和至少一个专家")
    return mgr, experts

def parse(text, experts, fallback):
    try:
        d = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
        plan = [p for p in d.get("plan", []) if p.get("agent") in experts][:MAX_SUBTASKS]
        if plan:
            return str(d.get("refined") or fallback), plan
    except Exception:
        pass
    return fallback, [{"agent": next(iter(experts)), "instruction": fallback}]

def fail(tid, e):
    say(tid, "系统", "error", str(e))
    db.x("UPDATE tasks SET status='failed', result=? WHERE id=?", (str(e), tid))

async def plan_task(tid):
    """第一步：便宜的主控AI完善需求并按专长分配。只调用 1 次，之后等待用户确认。"""
    t = db.q("SELECT * FROM tasks WHERE id=?", (tid,), one=True)
    try:
        db.x("UPDATE tasks SET status='planning' WHERE id=?", (tid,))
        db.x("DELETE FROM messages WHERE task_id=?", (tid,))
        mgr, experts = load_agents()
        mem = db.q("SELECT content FROM memories ORDER BY ts DESC LIMIT 5")
        ctx = "\n".join("- " + m["content"][:200] for m in mem) or "（无）"
        team = "\n".join(f"- {n}：{a['prompt'][:80]}（模型 {a['model']}）" for n, a in experts.items())
        out = await chat(mgr, mgr["prompt"],
            f"共享记忆：\n{ctx}\n\n用户原话：{t['title']}\n\n专家团队：\n{team}\n\n"
            f"请：1) 理解并完善用户需求，写成清晰具体的任务描述；"
            f"2) 按专家擅长拆分并分配，能由一位专家完成就只分配一位，最多{MAX_SUBTASKS}位，避免重复劳动。\n"
            f'只输出JSON：{{"refined":"完善后的需求","plan":[{{"agent":"专家名","instruction":"该专家的子任务"}}]}}',
            PLAN_TOKENS)
        refined, plan = parse(out, experts, t["title"])
        db.x("UPDATE tasks SET status='awaiting', refined=?, plan=? WHERE id=?",
             (refined, json.dumps(plan, ensure_ascii=False), tid))
        say(tid, mgr["name"], "plan", "已完善需求并完成分配。确认之前不会调用任何专家，不产生专家费用。")
    except Exception as e:
        fail(tid, e)

async def execute_task(tid):
    """第二步：用户确认后才执行。"""
    t = db.q("SELECT * FROM tasks WHERE id=?", (tid,), one=True)
    try:
        db.x("UPDATE tasks SET status='running' WHERE id=?", (tid,))
        db.x("DELETE FROM subtasks WHERE task_id=?", (tid,))
        db.x("DELETE FROM messages WHERE task_id=? AND kind!='plan'", (tid,))
        mgr, experts = load_agents()
        plan = [p for p in json.loads(t["plan"] or "[]") if p["agent"] in experts]
        if not plan:
            raise RuntimeError("没有可执行的专家，请检查专家是否已启用")
        goal = t["refined"] or t["title"]
        ids = [db.x("INSERT INTO subtasks(task_id,agent,instruction) VALUES(?,?,?)", (tid, p["agent"], p["instruction"])) for p in plan]

        async def work(sid, p):
            a = experts[p["agent"]]
            db.x("UPDATE subtasks SET status='running' WHERE id=?", (sid,))
            try:
                out = await chat(a, a["prompt"], f"总任务：{goal}\n你的子任务：{p['instruction']}", MAX_TOKENS)
                db.x("UPDATE subtasks SET status='done', output=? WHERE id=?", (out, sid))
                say(tid, a["name"], "work", out)
                return a["name"], out
            except Exception as e:
                db.x("UPDATE subtasks SET status='failed', output=? WHERE id=?", (str(e), sid))
                say(tid, a["name"], "error", str(e))
        results = [r for r in await asyncio.gather(*(work(i, p) for i, p in zip(ids, plan))) if r]
        if not results:
            raise RuntimeError("所有专家都执行失败")

        for _ in range(MAX_ROUNDS):
            if len(results) < 2: break
            async def review(name):
                others = "\n\n".join(f"【{n}】{o}" for n, o in results if n != name)
                try:
                    out = await chat(experts[name], experts[name]["prompt"],
                        f"总任务：{goal}\n其他专家的观点：\n{others}\n\n请简要指出问题或补充。", MAX_TOKENS)
                    say(tid, name, "discuss", out)
                    return f"【{name} 的评议】{out}"
                except Exception as e:
                    say(tid, name, "error", str(e))
            results += [("评议", r) for r in await asyncio.gather(*(review(n) for n, _ in list(results))) if r]

        if len(results) == 1:  # 只有一位专家：直接采用，省掉汇总调用
            final = results[0][1]
        else:
            body = "\n\n".join(f"【{n}】{o}" for n, o in results)
            final = await chat(mgr, mgr["prompt"],
                f"用户任务：{goal}\n专家结果：\n{body}\n\n请检查质量并整合为一份最终答案。", MAX_TOKENS)
        say(tid, mgr["name"], "final", final)
        db.x("UPDATE tasks SET status='done', result=? WHERE id=?", (final, tid))
        db.x("INSERT INTO memories(content,source,ts) VALUES(?,?,?)", (f"任务「{t['title'][:40]}」结论：{final[:400]}", "task", db.now()))
    except Exception as e:
        fail(tid, e)
