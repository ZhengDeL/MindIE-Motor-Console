import { useEffect, useRef, useState } from "react";
import { readCount, readNotice } from "./readState";
import {
  KeyRound,
  Plus,
  RefreshCw,
  UserRound,
} from "./icons";
import { api, post, roleLabel, setCsrfToken, when } from "./api";
import type { ConsoleUser, UserRole } from "./api";
import {
  Badge,
  Button,
  Empty,
  ErrorBox,
  Field,
  InfoBox,
  Modal,
  SearchInput,
  Select,
  Spinner,
} from "./ui";

const roles: UserRole[] = ["admin", "operator", "viewer"];
const passwordValid = (value: string) =>
  value.length >= 15 && value.length <= 128;

export function PasswordDialog({
  onClose,
  onChanged,
}: {
  onClose: () => void;
  onChanged: () => Promise<void>;
}) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <Modal
      title="修改个人密码"
      subtitle="修改成功后，所有设备的会话将失效，需要重新登录"
      onClose={onClose}
      closeDisabled={busy}
      footer={
        <>
          <Button disabled={busy} onClick={onClose}>
            取消
          </Button>
          <Button
            kind="primary"
            loading={busy}
            disabled={!current || !next || !repeat}
            onClick={async () => {
              setError("");
              if (!passwordValid(next))
                return setError("新密码长度须为 15–128 个字符");
              if (next !== repeat) return setError("两次输入的新密码不一致");
              setBusy(true);
              try {
                await post("/auth/password", {
                  current_password: current,
                  new_password: next,
                });
                setCsrfToken("");
                await onChanged();
                onClose();
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            <KeyRound size={14} />
            修改密码
          </Button>
        </>
      }
    >
      {error && <ErrorBox message={error} />}
      <Field label="当前密码" required>
        <input
          aria-label="当前密码"
          type="password"
          autoComplete="current-password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          disabled={busy}
        />
      </Field>
      <Field label="新密码" required hint="15–128 个字符">
        <input
          aria-label="新密码"
          type="password"
          autoComplete="new-password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          disabled={busy}
        />
      </Field>
      <Field label="确认新密码" required>
        <input
          aria-label="确认新密码"
          type="password"
          autoComplete="new-password"
          value={repeat}
          onChange={(e) => setRepeat(e.target.value)}
          disabled={busy}
        />
      </Field>
    </Modal>
  );
}

type UserEditor = {
  user?: ConsoleUser;
  action: "create" | "edit" | "password" | "delete" | "disable" | "enable";
};
export default function AccountsPage({
  currentUser,
  onSessionChanged,
}: {
  currentUser: ConsoleUser | null;
  onSessionChanged: () => Promise<void>;
}) {
  const [users, setUsers] = useState<ConsoleUser[]>([]);
  const [hasSuccess, setHasSuccess] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [role, setRole] = useState("all");
  const [editor, setEditor] = useState<UserEditor | null>(null);
  const loadGeneration = useRef(0);
  const loadRequest = useRef<AbortController | null>(null);
  const load = async () => {
    loadRequest.current?.abort();
    const controller = new AbortController();
    loadRequest.current = controller;
    const generation = ++loadGeneration.current;
    setError("");
    setLoading(true);
    try {
      const result = await api<{ items: ConsoleUser[] }>("/auth/users", { signal: controller.signal });
      if (controller.signal.aborted || generation !== loadGeneration.current) return;
      setUsers(result.items);
      setHasSuccess(true);
    } catch (e) {
      if (!controller.signal.aborted && generation === loadGeneration.current)
        setError((e as Error).message);
    } finally {
      if (generation === loadGeneration.current) setLoading(false);
    }
  };
  useEffect(() => {
    void load();
    return () => {
      loadGeneration.current++;
      loadRequest.current?.abort();
    };
  }, []);
  const displayed = users.filter(
    (user) =>
      `${user.username} ${user.display_name}`
        .toLowerCase()
        .includes(query.toLowerCase()) &&
      (role === "all" || user.role === role),
  );
  return (
    <>
      <InfoBox>
        系统管理员管理账号、节点与系统维护；运维操作员管理推理服务；只读用户查看资源与状态。权限由服务端校验。
      </InfoBox>
      <section className="panel">
        <div className="table-toolbar">
          <SearchInput
            value={query}
            onChange={setQuery}
            placeholder="搜索用户名或显示名称"
          />
          <Select value={role} onChange={setRole} ariaLabel="筛选账号角色">
            <option value="all">全部角色</option>
            {roles.map((value) => (
              <option key={value} value={value}>
                {roleLabel(value)}
              </option>
            ))}
          </Select>
          <Button loading={loading} onClick={() => void load()}>
            <RefreshCw size={14} />
            刷新
          </Button>
          <Button
            kind="primary"
            onClick={() => setEditor({ action: "create" })}
          >
            <Plus size={14} />
            创建账号
          </Button>
          <span className="toolbar-right-note" role="status">{readCount({ loaded: hasSuccess, refreshing: loading, error }, displayed.length, "个账号")}</span>
        </div>
        {error && <ErrorBox message={error} />}
        {loading && !hasSuccess ? (
          <Spinner />
        ) : displayed.length ? (
          <div className="table-wrap">
            <table className="accounts-table">
              <thead>
                <tr>
                  <th>用户名</th>
                  <th>显示名称</th>
                  <th>角色</th>
                  <th>状态</th>
                  <th>创建时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {displayed.map((user) => (
                  <tr key={user.id}>
                    <td>
                      <b>{user.username}</b>
                      {currentUser?.id === user.id && (
                        <span className="tag small">当前账号</span>
                      )}
                    </td>
                    <td>{user.display_name || user.username}</td>
                    <td>{roleLabel(user.role)}</td>
                    <td>
                      <Badge status={user.disabled ? "stopped" : "running"}>
                        {user.disabled ? "已禁用" : "启用"}
                      </Badge>
                    </td>
                    <td className="tabular">{when(user.created_at ?? "")}</td>
                    <td>
                      <div className="row-actions">
                        <button
                          onClick={() => setEditor({ user, action: "edit" })}
                        >
                          编辑
                        </button>
                        {user.auth_source !== "oidc" && (
                          <button
                            onClick={() =>
                              setEditor({ user, action: "password" })
                            }
                          >
                            重置密码
                          </button>
                        )}
                        <button
                          className={user.disabled ? "" : "danger-text"}
                          onClick={() =>
                            setEditor({
                              user,
                              action: user.disabled ? "enable" : "disable",
                            })
                          }
                        >
                          {user.disabled ? "启用" : "禁用"}
                        </button>
                        <button
                          className="danger-text"
                          onClick={() => setEditor({ user, action: "delete" })}
                        >
                          删除
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty
            title={!hasSuccess ? readNotice({ loaded: false, refreshing: loading, error }) : query || role !== "all" ? "没有匹配的账号" : "暂无账号"}
            description={!hasSuccess ? "尚未取得账号列表，请检查错误提示后重试。" : "调整筛选条件或创建本地账号。"}
            icon={<UserRound size={25} />}
          />
        )}
      </section>
      {editor && (
        <UserDialog
          editor={editor}
          onClose={() => setEditor(null)}
          onSaved={async () => {
            await load();
            await onSessionChanged();
          }}
        />
      )}
    </>
  );
}

function UserDialog({
  editor,
  onClose,
  onSaved,
}: {
  editor: UserEditor;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const { user, action } = editor;
  const [username, setUsername] = useState(user?.username ?? "");
  const [displayName, setDisplayName] = useState(user?.display_name ?? "");
  const [role, setRole] = useState<UserRole>(user?.role ?? "viewer");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const labels = {
    create: "创建账号",
    edit: "编辑账号",
    password: "重置密码",
    delete: "删除账号",
    disable: "禁用账号",
    enable: "启用账号",
  };
  const destructive = ["delete", "disable", "password"].includes(action);
  const form = action === "create" || action === "edit";
  const passwordForm = action === "create" || action === "password";
  const typed = action === "delete" || action === "disable";
  const submit = async () => {
    setError("");
    if (action === "create" && !username.trim())
      return setError("请填写用户名");
    if (passwordForm && !passwordValid(password))
      return setError("密码长度须为 15–128 个字符");
    if (typed && confirmation !== user?.username)
      return setError("请精确输入用户名以确认");
    setBusy(true);
    try {
      if (action === "create")
        await post("/auth/users", {
          username: username.trim(),
          password,
          role,
          display_name: displayName.trim(),
        });
      else if (action === "password")
        await post(`/auth/users/${user!.id}/reset-password`, {
          new_password: password,
        });
      else if (action === "delete")
        await api(`/auth/users/${user!.id}`, { method: "DELETE" });
      else
        await api(`/auth/users/${user!.id}`, {
          method: "PATCH",
          body: JSON.stringify(
            action === "edit"
              ? { display_name: displayName.trim(), role }
              : { disabled: action === "disable" },
          ),
        });
      await onSaved();
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      title={`${labels[action]}${user ? " · " + user.username : ""}`}
      onClose={onClose}
      closeDisabled={busy}
      footer={
        <>
          <Button disabled={busy} onClick={onClose}>
            取消
          </Button>
          <Button
            kind={destructive ? "danger" : "primary"}
            loading={busy}
            disabled={
              (typed && confirmation !== user?.username) ||
              (passwordForm && !password) ||
              (action === "create" && !username.trim())
            }
            onClick={() => void submit()}
          >
            {labels[action]}
          </Button>
        </>
      }
    >
      {error && <ErrorBox message={error} />}
      {form && (
        <>
          <Field label="用户名" required>
            <input
              aria-label="账号用户名"
              autoComplete="off"
              value={username}
              disabled={busy || action === "edit"}
              onChange={(e) => setUsername(e.target.value)}
            />
          </Field>
          <Field label="显示名称">
            <input
              aria-label="账号显示名称"
              value={displayName}
              disabled={busy}
              onChange={(e) => setDisplayName(e.target.value)}
            />
          </Field>
          <Field label="角色" required>
            <Select
              value={role}
              onChange={(value) => setRole(value as UserRole)}
              ariaLabel="账号角色"
              disabled={busy}
            >
              {roles.map((value) => (
                <option key={value} value={value}>
                  {roleLabel(value)}
                </option>
              ))}
            </Select>
          </Field>
        </>
      )}
      {passwordForm && (
        <Field
          label={action === "create" ? "初始密码" : "新密码"}
          required
          hint={action === "create" ? "15–128 个字符" : "15–128 个字符；重置后该账号已有会话全部失效"}
        >
          <input
            aria-label="账号密码"
            type="password"
            autoComplete="new-password"
            value={password}
            disabled={busy}
            onChange={(e) => setPassword(e.target.value)}
          />
        </Field>
      )}
      {action === "delete" && (
        <p className="account-action-description">
          删除后该账号无法登录，已有会话立即失效；操作审计保留。系统不允许移除最后一个有效管理员。
        </p>
      )}
      {action === "disable" && (
        <p className="account-action-description">
          禁用后该账号无法登录，已有会话立即失效。系统不允许禁用最后一个有效管理员。
        </p>
      )}
      {action === "enable" && (
        <p className="account-action-description">
          允许账号 <b>{user?.username}</b> 使用其当前角色重新登录。
        </p>
      )}
      {typed && (
        <Field label="输入用户名以确认" required>
          <input
            aria-label="输入用户名以确认"
            autoComplete="off"
            value={confirmation}
            disabled={busy}
            onChange={(e) => setConfirmation(e.target.value)}
            placeholder={user?.username}
          />
        </Field>
      )}
    </Modal>
  );
}
