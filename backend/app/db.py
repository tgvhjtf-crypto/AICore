import sqlite3, os, time, base64, hashlib
from cryptography.fernet import Fernet

PATH = os.getenv("DB_PATH", "aicore.db")
_secret = os.getenv("AICORE_SECRET") or os.getenv("AICORE_TOKEN", "dev")
_f = Fernet(base64.urlsafe_b64encode(hashlib.sha256(_secret.encode()).digest()))

def enc(s): return _f.encrypt(s.encode()).decode() if s else ""
def dec(s): return _f.decrypt(s.encode()).decode() if s else ""

def _c():
    c = sqlite3.connect(PATH)
    c.row_factory = sqlite3.Row
    return c

def q(sql, a=(), one=False):
    with _c() as c:
        rows = [dict(r) for r in c.execute(sql, a).fetchall()]
    return (rows[0] if rows else None) if one else rows

def x(sql, a=()):
    with _c() as c:
        return c.execute(sql, a).lastrowid

SCHEMA = """
CREATE TABLE IF NOT EXISTS agents(id INTEGER PRIMARY KEY, name TEXT, role TEXT DEFAULT 'expert',
  prompt TEXT, kind TEXT DEFAULT 'openai', base_url TEXT, model TEXT, api_key_enc TEXT DEFAULT '', enabled INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY, title TEXT, status TEXT DEFAULT 'pending', result TEXT DEFAULT '', created REAL);
CREATE TABLE IF NOT EXISTS subtasks(id INTEGER PRIMARY KEY, task_id INTEGER, agent TEXT, instruction TEXT, status TEXT DEFAULT 'pending', output TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, task_id INTEGER, agent TEXT, kind TEXT, content TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY, content TEXT, source TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS usage(day TEXT PRIMARY KEY, calls INTEGER DEFAULT 0);
"""

SEED = [
 ("主控AI", "manager", "你是主控AI，负责理解并完善用户需求、按专家擅长分配任务、汇总并检查结果。回答简洁。", "openai", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.0-flash"),
 ("GPT", "expert", "你是综合分析专家，擅长复杂任务。", "openai", "https://api.openai.com/v1", "gpt-4o"),
 ("Claude", "expert", "你是代码分析与程序开发专家。", "anthropic", "https://api.anthropic.com", "claude-sonnet-5-5"),
 ("Gemini", "expert", "你是信息分析与多模态专家。", "openai", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.0-flash"),
 ("DeepSeek", "expert", "你是逻辑推理与代码辅助专家。", "openai", "https://api.deepseek.com", "deepseek-chat"),
 ("Grok", "expert", "你是信息分析与观点讨论专家。", "openai", "https://api.x.ai/v1", "grok-3"),
]

def init():
    with _c() as c:
        c.executescript(SCHEMA)
    for col in ("refined", "plan"):  # 迁移：旧库补列
        try: x(f"ALTER TABLE tasks ADD COLUMN {col} TEXT DEFAULT ''")
        except sqlite3.OperationalError: pass
    if not q("SELECT 1 FROM agents"):
        for n, r, p, k, u, m in SEED:
            x("INSERT INTO agents(name,role,prompt,kind,base_url,model) VALUES(?,?,?,?,?,?)", (n, r, p, k, u, m))

def now(): return time.time()
