"""Readable audit projections without rewriting the recorded identities/events."""
from __future__ import annotations

import re

from .observability import redact


ACTION_LABELS = {
    "auth.login": "账号登录",
    "auth.login_limited": "登录频率限制",
    "auth.logout": "账号退出",
    "auth.bootstrap": "首次初始化",
    "auth.user_create": "创建账号",
    "auth.user_update": "更新账号",
    "auth.user_delete": "删除账号",
    "auth.password_reset": "重置密码",
    "auth.password_change": "修改个人密码",
    "auth.oidc_provision": "创建单点登录账号",
    "auth.oidc_login": "单点登录",
}
_IDENTIFIER = re.compile(r"^(?:usr|svc|node|job|evt|auth|agt|plan|tpl)_[a-zA-Z0-9_-]{8,}$")


def _name_snapshot(value):
    if not isinstance(value, dict):
        return None
    identifier, username = value.get("id"), value.get("username")
    if not isinstance(identifier, str) or not isinstance(username, str) or not username:
        return None
    display_name = value.get("display_name")
    return {"id": identifier[:128], "username": username[:128],
            "display_name": display_name[:160] if isinstance(display_name, str) else ""}


def account_names(connection, identifiers):
    """Read only public account columns, in bounded batches; demo has no directory."""
    values = sorted({value for value in identifiers if isinstance(value, str) and value.startswith("usr_")})
    if not values:
        return {}
    return connection.accounts.public_names(values)


def capture_audit_names(connection, value, *, target_snapshot=None):
    """Snapshot display names inside the same transaction as a new audit event."""
    names = account_names(connection, (value.get("actor"), value.get("target")))
    supplied_target = _name_snapshot(target_snapshot)
    if supplied_target and supplied_target["id"] == value.get("target"):
        # A permitted self-deletion has already removed the actor's row too.
        # The trusted pre-delete account is still the exact same identity.
        names.setdefault(supplied_target["id"], supplied_target)
    for field, identity in (("actor_snapshot", value.get("actor")), ("target_snapshot", value.get("target"))):
        known = _name_snapshot(value.get(field))
        if known is None:
            known = supplied_target if field == "target_snapshot" else None
            known = known or names.get(identity)
            if known is not None and known["id"] == identity:
                value[field] = redact(known)
    return value


def audit_filter_values(connection, *, actor_username=None, action=None):
    actors = None
    if actor_username is not None:
        actors = [actor_username]
        row = connection.accounts.get_username(actor_username).fetchone()
        if row and row["id"] != actor_username:
            actors.append(row["id"])
    actions = None if action is None else list(dict.fromkeys([action, *(key for key, label in ACTION_LABELS.items() if label == action)]))
    return actors, actions


def project_audit_items(connection, items):
    identities = {item.get(field) for item in items for field in ("actor", "target") if isinstance(item.get(field), str)}
    names = account_names(connection, identities)
    output = []
    for original in items:
        value = dict(original)
        actor = value.get("actor", "")
        identity = actor if isinstance(actor, str) else ""
        snapshot = _name_snapshot(value.get("actor_snapshot"))
        if snapshot and snapshot["id"] != identity:
            snapshot = None
        current = names.get(identity)
        account = snapshot or current
        value.update(actor_display=None, actor_display_name=None, actor_username=None,
                     actor_kind="recorded", actor_name_source=None)
        if account:
            value.update(actor_display=account["username"], actor_username=account["username"],
                         actor_display_name=account["display_name"] or None, actor_kind="account",
                         actor_name_source="snapshot" if snapshot else "current_directory")
        elif identity in {"system", "anonymous"}:
            value.update(actor_display="系统任务" if identity == "system" else "未登录访问",
                         actor_kind="system" if identity == "system" else "anonymous",
                         actor_name_source="recorded")
        action = value.get("action", "")
        value["action_label"] = ACTION_LABELS.get(action) or (action if isinstance(action, str) and re.search(r"[\u3400-\u9fff]", action) else None)
        target = value.get("target", "")
        recorded_target = _name_snapshot(value.get("target_snapshot"))
        if recorded_target and recorded_target["id"] != target:
            recorded_target = None
        target_account = recorded_target or names.get(target)
        value["target_display"] = target_account["username"] if target_account else (
            "账号访问" if target == "account" and isinstance(action, str) and action.startswith("auth.")
            else target if isinstance(target, str) and target and not _IDENTIFIER.fullmatch(target) else None)
        output.append(redact(value))
    return output
