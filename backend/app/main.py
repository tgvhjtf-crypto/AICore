import asyncio, json, os
from fastapi import FastAPI, Depends, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from . import db
from .providers import chat
from .orchestrator import plan_task, execute_task

TOKEN = os.getenv("AICORE_TOKEN")
if not TOKEN:
    raise RuntimeError("必须设置环境变量 AICORE_TOKEN")

app = FastAPI(title="AICore")
app.add_middleware(CORSMiddleware, allow_origins=[os.getenv("ALLOWED_ORIGIN", "*")],
                   allow_methods=["*"], allow_headers=["*"])
db.init()

def auth(x_token: str = Header(default="")):
    if x_token != TOKEN:
        raise HTTPException(401, "令牌错误")

R = Depends(auth)

class AgentIn(BaseModel):
    name: str | None = None
    role: str | None = None
    prompt: str | None = None
    kind: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    enabled: bool | None = None

def pub(a):
    a = dict(a); a["has_key"] = bool(a.pop("api_key_enc", "")); return a

@app.get("/api/health")
def health(): return {"ok": True}

@app.get("/api/agents", dependencies=[R])
def agents(): return [pub(a) for a in db.q("SELECT * FROM agents ORDER BY id")]

@app.post("/api/agents", dependencies=[R])
def add_agent():
    i = db.x("INSERT INTO agents(name,role,prompt,kind,base_url,model) VALUES('新专家','expert','你是一名专家。','openai','https://api.openai.com/v1','gpt-4o')")
    return {"id": i}

@app.patch("/api/agents/{i}", dependencies=[R])
def patch_agent(i: int, b: AgentIn):
    d = b.model_dump(exclude_none=True)
    if "api_key" in d:
        k = d.pop("api_key")
        if k: d["api_key_enc"] = db.enc(k)
    if "enabled" in d: d["enabled"] = int(d["enabled"])
    for k, v in d.items():
        db.x(f"UPDATE agents SET {k}=? WHERE id=?", (v, i))  # k 来自 pydantic 白名单字段
    return {"ok": True}

@app.delete("/api/agents/{i}", dependencies=[R])
def del_agent(i: int):
    db.x("DELETE FROM agents WHERE id=?", (i,)); return {"ok": True}

@app.post("/api/agents/{i}/test", dependencies=[R])
async def test_agent(i: int):
    a = db.q("SELECT * FROM agents WHERE id=?", (i,), one=True)
    try:
        await chat(a, "你是测试助手。", "回复OK", 20)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

class TaskIn(BaseModel):
    title: str

@app.post("/api/tasks", dependencies=[R])
async def new_task(b: TaskIn):
    i = db.x("INSERT INTO tasks(title,created) VALUES(?,?)", (b.title.strip()[:2000], db.now()))
    asyncio.create_task(plan_task(i))
    return {"id": i}

@app.get("/api/tasks", dependencies=[R])
def tasks(): return db.q("SELECT id,title,status,created FROM tasks ORDER BY id DESC LIMIT 50")

@app.get("/api/tasks/{i}", dependencies=[R])
def task(i: int):
    t = db.q("SELECT * FROM tasks WHERE id=?", (i,), one=True)
    if not t: raise HTTPException(404)
    t["subtasks"] = db.q("SELECT id,agent,instruction,status FROM subtasks WHERE task_id=?", (i,))
    t["messages"] = db.q("SELECT * FROM messages WHERE task_id=? ORDER BY id", (i,))
    t["plan"] = json.loads(t.get("plan") or "[]")
    return t

@app.post("/api/tasks/{i}/retry", dependencies=[R])
async def retry(i: int):
    t = db.q("SELECT plan FROM tasks WHERE id=?", (i,), one=True)
    asyncio.create_task(execute_task(i) if t and t["plan"] else plan_task(i))
    return {"ok": True}

class Confirm(BaseModel):
    refined: str
    picks: list[int]

@app.post("/api/tasks/{i}/confirm", dependencies=[R])
async def confirm(i: int, b: Confirm):
    t = db.q("SELECT * FROM tasks WHERE id=?", (i,), one=True)
    if not t or t["status"] != "awaiting":
        raise HTTPException(400, "任务不在待确认状态")
    plan = json.loads(t["plan"])
    sel = [plan[k] for k in b.picks if 0 <= k < len(plan)]
    if not sel:
        raise HTTPException(400, "至少选择一位专家")
    db.x("UPDATE tasks SET refined=?, plan=?, status='running' WHERE id=?",
         (b.refined.strip() or t["refined"], json.dumps(sel, ensure_ascii=False), i))
    asyncio.create_task(execute_task(i))
    return {"ok": True}

@app.post("/api/tasks/{i}/cancel", dependencies=[R])
def cancel(i: int):
    db.x("UPDATE tasks SET status='cancelled' WHERE id=? AND status IN ('awaiting','failed')", (i,))
    return {"ok": True}

class MemIn(BaseModel):
    content: str

@app.get("/api/memories", dependencies=[R])
def mems(q: str = ""):
    return db.q("SELECT * FROM memories WHERE content LIKE ? ORDER BY ts DESC LIMIT 100", (f"%{q}%",))

@app.post("/api/memories", dependencies=[R])
def add_mem(b: MemIn):
    return {"id": db.x("INSERT INTO memories(content,source,ts) VALUES(?,?,?)", (b.content, "user", db.now()))}

@app.delete("/api/memories/{i}", dependencies=[R])
def del_mem(i: int):
    db.x("DELETE FROM memories WHERE id=?", (i,)); return {"ok": True}
