import os, time, httpx
from . import db

TIMEOUT = float(os.getenv("CALL_TIMEOUT", "60"))
DAILY_LIMIT = int(os.getenv("DAILY_CALL_LIMIT", "200"))

def _count():
    day = time.strftime("%Y-%m-%d")
    db.x("INSERT OR IGNORE INTO usage(day,calls) VALUES(?,0)", (day,))
    db.x("UPDATE usage SET calls=calls+1 WHERE day=?", (day,))
    return db.q("SELECT calls FROM usage WHERE day=?", (day,), one=True)["calls"]

async def chat(a, system, user, max_tokens=800):
    """统一模型接入层：kind=openai（OpenAI 兼容）或 anthropic。"""
    key = db.dec(a["api_key_enc"])
    if not key:
        raise RuntimeError("未配置 API Key")
    if _count() > DAILY_LIMIT:
        raise RuntimeError("已超过每日调用上限")
    base = a["base_url"].rstrip("/")
    last = None
    for _ in range(2):  # 失败重试一次
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as c:
                if a["kind"] == "anthropic":
                    r = await c.post(base + "/v1/messages",
                        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                        json={"model": a["model"], "max_tokens": max_tokens, "system": system,
                              "messages": [{"role": "user", "content": user}]})
                    r.raise_for_status()
                    return "".join(b.get("text", "") for b in r.json()["content"])
                r = await c.post(base + "/chat/completions",
                    headers={"Authorization": "Bearer " + key},
                    json={"model": a["model"], "max_tokens": max_tokens,
                          "messages": [{"role": "system", "content": system},
                                       {"role": "user", "content": user}]})
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            last = e
    raise RuntimeError(f"调用失败：{type(last).__name__} {str(last)[:150]}")
