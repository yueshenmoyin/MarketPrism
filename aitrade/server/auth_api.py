from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel, Field

security = HTTPBasic()
HASH_ITERATIONS = 200_000
_ENV_TOKENS: dict[str, str] = {}


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class VerifyRequest(BaseModel):
    token: str = Field(min_length=1)


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1)
    expires_at: str | None = None
    enabled: bool = True


class UserUpdateRequest(BaseModel):
    password: str | None = Field(default=None, min_length=1)
    expires_at: str | None = None
    enabled: bool | None = None


def _auth_username() -> str:
    return os.environ.get("AITRADE_AUTH_USERNAME", "admin").strip() or "admin"


def _auth_password() -> str:
    return os.environ.get("AITRADE_AUTH_PASSWORD", "")


def _users_file() -> Path:
    configured = os.environ.get("AITRADE_AUTH_USERS_FILE")
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt":
        return Path.home() / ".aitrade" / "auth_users.json"
    return Path("/etc/aitrade-auth/users.json")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_expires_at(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) == 10 and cleaned[4] == "-" and cleaned[7] == "-":
        cleaned = f"{cleaned}T23:59:59Z"
    parsed = _parse_datetime(cleaned)
    return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="授权有效期格式不正确，请使用 YYYY-MM-DD 或 ISO 时间。") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _seconds_until(expires_at: str | None) -> int | None:
    if not expires_at:
        return None
    return max(0, int((_parse_datetime(expires_at) - _now()).total_seconds()))


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), HASH_ITERATIONS)
    return f"pbkdf2_sha256${HASH_ITERATIONS}${salt}${digest.hex()}"


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations, salt, expected = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt),
            int(iterations),
        ).hex()
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


def _load_store() -> dict[str, Any]:
    path = _users_file()
    if not path.exists():
        return {"users": {}}
    with path.open("r", encoding="utf-8") as store_file:
        payload = json.load(store_file)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="用户库格式不正确。")
    payload.setdefault("users", {})
    if not isinstance(payload["users"], dict):
        raise HTTPException(status_code=500, detail="用户库格式不正确。")
    return payload


def _save_store(store: dict[str, Any]) -> None:
    path = _users_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as store_file:
        json.dump(store, store_file, ensure_ascii=False, indent=2)
    temp_path.replace(path)


def _public_user(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "username": record["username"],
        "enabled": bool(record.get("enabled", True)),
        "expires_at": record.get("expires_at"),
        "expires_in_seconds": _seconds_until(record.get("expires_at")),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "last_login_at": record.get("last_login_at"),
    }


def _require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    expected_password = _auth_password()
    if not expected_password:
        raise HTTPException(status_code=503, detail="登录服务未配置账号密码。")
    username_ok = hmac.compare_digest(credentials.username.strip(), _auth_username())
    password_ok = hmac.compare_digest(credentials.password, expected_password)
    if not username_ok or not password_ok:
        raise HTTPException(status_code=401, detail="管理员账号或密码错误。", headers={"WWW-Authenticate": "Basic"})
    return credentials.username.strip()


def _verify_env_credentials(username: str, password: str) -> dict[str, Any] | None:
    expected_password = _auth_password()
    if not expected_password:
        return None
    username_ok = hmac.compare_digest(username.strip(), _auth_username())
    password_ok = hmac.compare_digest(password, expected_password)
    if not username_ok or not password_ok:
        return None
    return {
        "username": username.strip(),
        "enabled": True,
        "expires_at": None,
        "role": "admin",
    }


def _verify_user_credentials(username: str, password: str) -> dict[str, Any]:
    store = _load_store()
    record = store["users"].get(username.strip())
    if record:
        if not _verify_password(password, str(record.get("password_hash", ""))):
            raise HTTPException(status_code=401, detail="账号或密码错误。")
        if not record.get("enabled", True):
            raise HTTPException(status_code=403, detail="账号已停用，请联系管理员。")
        expires_at = record.get("expires_at")
        if expires_at and _parse_datetime(expires_at) <= _now():
            raise HTTPException(status_code=403, detail="授权已过期，请联系管理员续期。")
        record["last_login_at"] = _now_iso()
        _save_store(store)
        return record

    env_record = _verify_env_credentials(username, password)
    if env_record:
        return env_record
    if not _auth_password():
        raise HTTPException(status_code=503, detail="登录服务未配置账号密码。")
    raise HTTPException(status_code=401, detail="账号或密码错误。")


def _token_for(username: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"aitrade_{stamp}_{secrets.token_urlsafe(32)}"


def _issue_token(username: str) -> str:
    token = _token_for(username)
    store = _load_store()
    record = store["users"].get(username)
    if record:
        tokens = record.setdefault("tokens", {})
        if not isinstance(tokens, dict):
            tokens = {}
            record["tokens"] = tokens
        tokens[token] = _now_iso()
        _save_store(store)
    elif username == _auth_username():
        _ENV_TOKENS[token] = username
    return token


def _assert_user_usable(record: dict[str, Any]) -> None:
    if not record.get("enabled", True):
        raise HTTPException(status_code=403, detail="账号已停用，请联系管理员。")
    expires_at = record.get("expires_at")
    if expires_at and _parse_datetime(expires_at) <= _now():
        raise HTTPException(status_code=403, detail="授权已过期，请联系管理员续期。")


def _verify_token(token: str) -> dict[str, Any]:
    store = _load_store()
    for record in store["users"].values():
        tokens = record.get("tokens")
        if isinstance(tokens, dict) and token in tokens:
            _assert_user_usable(record)
            return record
    env_username = _ENV_TOKENS.get(token)
    if env_username and env_username == _auth_username():
        return {
            "username": env_username,
            "enabled": True,
            "expires_at": None,
            "role": "admin",
        }
    raise HTTPException(status_code=401, detail="登录已失效，请重新登录。")


def _admin_html() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>MarketPrism 用户管理</title>
  <style>
    :root { color-scheme: light; --ink:#17211f; --muted:#6b6258; --line:#d9d0c2; --accent:#9b2f37; --bg:#f6f1e8; --panel:#fffaf1; }
    * { box-sizing:border-box; }
    body { margin:0; font-family:"Microsoft YaHei", "PingFang SC", sans-serif; background:var(--bg); color:var(--ink); }
    main { max-width:1180px; margin:0 auto; padding:34px 22px 48px; }
    header { display:flex; align-items:flex-end; justify-content:space-between; gap:20px; border-bottom:1px solid var(--line); padding-bottom:20px; }
    h1 { margin:0; font-size:32px; letter-spacing:0; }
    p { color:var(--muted); }
    section { margin-top:22px; background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:20px; box-shadow:0 16px 40px rgba(57,45,32,.08); }
    form { display:grid; grid-template-columns:1.1fr 1.1fr .9fr .7fr auto; gap:12px; align-items:end; }
    label { display:grid; gap:7px; font-weight:700; color:#4d453d; }
    input, select, button { height:42px; border-radius:6px; border:1px solid #c9bdae; background:#fffdf8; color:var(--ink); font:inherit; padding:0 12px; }
    button { cursor:pointer; border-color:var(--accent); background:var(--accent); color:#fff; font-weight:800; }
    button.secondary { background:#fffdf8; color:var(--accent); }
    table { width:100%; border-collapse:collapse; margin-top:10px; }
    th, td { text-align:left; border-bottom:1px solid var(--line); padding:13px 10px; vertical-align:middle; }
    th { color:#5b5046; font-size:14px; }
    .actions { display:flex; gap:8px; flex-wrap:wrap; }
    .danger { border-color:#7f1d1d; color:#7f1d1d; }
    .danger:hover { background:#fff4f4; }
    .status { min-height:24px; color:var(--accent); font-weight:700; }
    .pill { display:inline-flex; padding:5px 9px; border-radius:999px; background:#e7efe8; color:#24513b; font-weight:800; }
    .off { background:#f4dedc; color:#7f1d1d; }
    @media (max-width:900px) { form { grid-template-columns:1fr; } header { align-items:flex-start; flex-direction:column; } }
  </style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>MarketPrism 用户管理</h1>
      <p>创建试用账号、修改密码、停用账号，并设置授权有效期。</p>
    </div>
    <button class="secondary" onclick="loadUsers()">刷新</button>
  </header>
  <section>
    <h2>新建用户</h2>
    <form id="create-form">
      <label>账号<input id="new-username" autocomplete="off" required /></label>
      <label>密码<input id="new-password" type="password" autocomplete="new-password" required /></label>
      <label>有效期<input id="new-expires" type="date" required /></label>
      <label>状态<select id="new-enabled"><option value="true">启用</option><option value="false">停用</option></select></label>
      <button type="submit">创建</button>
    </form>
    <p class="status" id="status"></p>
  </section>
  <section>
    <h2>所有用户</h2>
    <table>
      <thead><tr><th>账号</th><th>状态</th><th>有效期</th><th>最近登录</th><th>操作</th></tr></thead>
      <tbody id="users"></tbody>
    </table>
  </section>
</main>
<script>
const statusEl = document.querySelector("#status");
function setStatus(text) { statusEl.textContent = text || ""; }
function dateOnly(value) { return value ? value.slice(0, 10) : ""; }
async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  if (!response.ok) {
    let detail = await response.text();
    try { detail = (JSON.parse(detail).detail || detail); } catch {}
    throw new Error(detail || "请求失败");
  }
  return response.json();
}
function row(user) {
  const tr = document.createElement("tr");
  tr.innerHTML = `
    <td><strong>${user.username}</strong></td>
    <td><span class="pill ${user.enabled ? "" : "off"}">${user.enabled ? "启用" : "停用"}</span></td>
    <td><input type="date" value="${dateOnly(user.expires_at)}" aria-label="有效期" /></td>
    <td>${user.last_login_at ? user.last_login_at.replace("T", " ").replace("Z", "") : "从未登录"}</td>
    <td class="actions">
      <button class="secondary save">保存有效期</button>
      <button class="secondary password">改密码</button>
      <button class="secondary toggle">${user.enabled ? "停用" : "启用"}</button>
      <button class="secondary danger delete">删除</button>
    </td>`;
  const dateInput = tr.querySelector("input");
  tr.querySelector(".save").onclick = async () => {
    await api(`/admin/api/users/${encodeURIComponent(user.username)}`, { method:"PATCH", body:JSON.stringify({ expires_at: dateInput.value }) });
    setStatus("已保存有效期。"); await loadUsers();
  };
  tr.querySelector(".password").onclick = async () => {
    const password = prompt(`请输入 ${user.username} 的新密码`);
    if (!password) return;
    await api(`/admin/api/users/${encodeURIComponent(user.username)}`, { method:"PATCH", body:JSON.stringify({ password }) });
    setStatus("密码已更新。");
  };
  tr.querySelector(".toggle").onclick = async () => {
    await api(`/admin/api/users/${encodeURIComponent(user.username)}`, { method:"PATCH", body:JSON.stringify({ enabled: !user.enabled }) });
    setStatus("状态已更新。"); await loadUsers();
  };
  tr.querySelector(".delete").onclick = async () => {
    if (!confirm(`确认删除 ${user.username}？`)) return;
    await api(`/admin/api/users/${encodeURIComponent(user.username)}`, { method:"DELETE" });
    setStatus("用户已删除。"); await loadUsers();
  };
  return tr;
}
async function loadUsers() {
  const tbody = document.querySelector("#users");
  tbody.innerHTML = "<tr><td colspan='5'>加载中...</td></tr>";
  try {
    const payload = await api("/admin/api/users");
    tbody.innerHTML = "";
    if (!payload.items.length) tbody.innerHTML = "<tr><td colspan='5'>还没有创建普通用户。</td></tr>";
    payload.items.forEach((user) => tbody.appendChild(row(user)));
  } catch (error) {
    tbody.innerHTML = `<tr><td colspan='5'>${error.message}</td></tr>`;
  }
}
document.querySelector("#create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/admin/api/users", {
      method:"POST",
      body:JSON.stringify({
        username: document.querySelector("#new-username").value.trim(),
        password: document.querySelector("#new-password").value,
        expires_at: document.querySelector("#new-expires").value,
        enabled: document.querySelector("#new-enabled").value === "true",
      }),
    });
    event.target.reset();
    setStatus("用户已创建。");
    await loadUsers();
  } catch (error) { setStatus(error.message); }
});
loadUsers();
</script>
</body>
</html>"""


def create_app() -> FastAPI:
    app = FastAPI(title="MarketPrism Server API", version="0.1.0")
    from aitrade.server.data_gateway import register_data_gateway_routes
    from aitrade.server.analysis_jobs import register_analysis_routes
    from aitrade.server.precache import start_precache_scheduler

    register_data_gateway_routes(app)
    register_analysis_routes(app)
    start_precache_scheduler()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/auth/login")
    def login(request: LoginRequest) -> dict[str, Any]:
        username = request.username.strip()
        record = _verify_user_credentials(username, request.password)
        expires_at = record.get("expires_at")
        return {
            "access_token": _issue_token(username),
            "token_type": "bearer",
            "expires_at": expires_at,
            "expires_in_seconds": _seconds_until(expires_at),
            "user": {"name": record["username"], "username": record["username"]},
        }

    @app.post("/auth/verify")
    def verify(request: VerifyRequest) -> dict[str, Any]:
        record = _verify_token(request.token.strip())
        expires_at = record.get("expires_at")
        return {
            "access_token": request.token.strip(),
            "token_type": "bearer",
            "expires_at": expires_at,
            "expires_in_seconds": _seconds_until(expires_at),
            "user": {"name": record["username"], "username": record["username"]},
        }

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page(_: str = Depends(_require_admin)) -> str:
        return _admin_html()

    @app.get("/admin/api/users")
    def list_users(_: str = Depends(_require_admin)) -> dict[str, Any]:
        store = _load_store()
        users = [_public_user(record) for record in store["users"].values()]
        users.sort(key=lambda item: item["username"])
        return {"items": users}

    @app.post("/admin/api/users")
    def create_user(request: UserCreateRequest, _: str = Depends(_require_admin)) -> dict[str, Any]:
        username = request.username.strip()
        if not username:
            raise HTTPException(status_code=400, detail="账号不能为空。")
        if username == _auth_username():
            raise HTTPException(status_code=400, detail="普通用户不能和管理员账号同名。")
        store = _load_store()
        if username in store["users"]:
            raise HTTPException(status_code=409, detail="用户已存在。")
        timestamp = _now_iso()
        record = {
            "username": username,
            "password_hash": _hash_password(request.password),
            "enabled": request.enabled,
            "expires_at": _normalize_expires_at(request.expires_at),
            "created_at": timestamp,
            "updated_at": timestamp,
            "last_login_at": None,
            "tokens": {},
        }
        store["users"][username] = record
        _save_store(store)
        return _public_user(record)

    @app.patch("/admin/api/users/{username}")
    def update_user(username: str, request: UserUpdateRequest, _: str = Depends(_require_admin)) -> dict[str, Any]:
        store = _load_store()
        record = store["users"].get(username)
        if not record:
            raise HTTPException(status_code=404, detail="用户不存在。")
        if request.password:
            record["password_hash"] = _hash_password(request.password)
            record["tokens"] = {}
        if request.expires_at is not None:
            record["expires_at"] = _normalize_expires_at(request.expires_at)
        if request.enabled is not None:
            record["enabled"] = request.enabled
        record["updated_at"] = _now_iso()
        _save_store(store)
        return _public_user(record)

    @app.delete("/admin/api/users/{username}")
    def delete_user(username: str, _: str = Depends(_require_admin)) -> dict[str, bool]:
        store = _load_store()
        if username not in store["users"]:
            raise HTTPException(status_code=404, detail="用户不存在。")
        del store["users"][username]
        _save_store(store)
        return {"ok": True}

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run("aitrade.server.auth_api:app", host="127.0.0.1", port=18080, reload=False)


if __name__ == "__main__":
    main()
