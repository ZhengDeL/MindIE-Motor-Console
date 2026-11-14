import { UiElement, UiRegion, withUiSlot, UiButton, UiText } from "./uiPluginSdk";
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

function DefaultPasswordDialog({
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
          <Button actionId="action.Accounts.001" disabled={busy} onClick={onClose}>
            <UiText>{"取消"}</UiText>
          </Button>
          <Button actionId="action.Accounts.002"
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
            <UiText>{"修改密码"}</UiText>
          </Button>
        </>
      }
    >
      {error && <ErrorBox message={error} />}
      <Field label="当前密码" required>
        <UiElement as="input"
          aria-label="当前密码"
          type="password"
          autoComplete="current-password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          disabled={busy}
        />
      </Field>
      <Field label="新密码" required hint="15–128 个字符">
        <UiElement as="input"
          aria-label="新密码"
          type="password"
          autoComplete="new-password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          disabled={busy}
        />
      </Field>
      <Field label="确认新密码" required>
        <UiElement as="input"
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
function DefaultAccountsPage({
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
        <UiText>{"系统管理员管理账号、节点与系统维护；运维操作员管理推理服务；只读用户查看资源与状态。权限由服务端校验。"}</UiText>
      </InfoBox>
      <UiRegion name="layout.panel" as="section" className="panel">
        <UiElement as="div" className="table-toolbar">
          <SearchInput
            value={query}
            onChange={setQuery}
            placeholder="搜索用户名或显示名称"
          />
          <Select value={role} onChange={setRole} ariaLabel="筛选账号角色">
            <UiElement as="option" value="all"><UiText>{"全部角色"}</UiText></UiElement>
            {roles.map((value) => (
              <UiElement as="option" key={value} value={value}>
                {roleLabel(value)}
              </UiElement>
            ))}
          </Select>
          <Button actionId="action.Accounts.003" loading={loading} onClick={() => void load()}>
            <RefreshCw size={14} />
            <UiText>{"刷新"}</UiText>
          </Button>
          <Button actionId="action.Accounts.004"
            kind="primary"
            onClick={() => setEditor({ action: "create" })}
          >
            <Plus size={14} />
            <UiText>{"创建账号"}</UiText>
          </Button>
          <UiElement as="span" className="toolbar-right-note" role="status">{readCount({ loaded: hasSuccess, refreshing: loading, error }, displayed.length, "个账号")}</UiElement>
        </UiElement>
        {error && <ErrorBox message={error} />}
        {loading && !hasSuccess ? (
          <Spinner />
        ) : displayed.length ? (
          <UiElement as="div" className="table-wrap">
            <UiElement as="table" className="accounts-table">
              <UiElement as="thead">
                <UiElement as="tr">
                  <UiElement as="th"><UiText>{"用户名"}</UiText></UiElement>
                  <UiElement as="th"><UiText>{"显示名称"}</UiText></UiElement>
                  <UiElement as="th"><UiText>{"角色"}</UiText></UiElement>
                  <UiElement as="th"><UiText>{"状态"}</UiText></UiElement>
                  <UiElement as="th"><UiText>{"创建时间"}</UiText></UiElement>
                  <UiElement as="th"><UiText>{"操作"}</UiText></UiElement>
                </UiElement>
              </UiElement>
              <UiElement as="tbody">
                {displayed.map((user) => (
                  <UiElement as="tr" key={user.id}>
                    <UiElement as="td">
                      <UiElement as="b">{user.username}</UiElement>
                      {currentUser?.id === user.id && (
                        <UiElement as="span" className="tag small"><UiText>{"当前账号"}</UiText></UiElement>
                      )}
                    </UiElement>
                    <UiElement as="td">{user.display_name || user.username}</UiElement>
                    <UiElement as="td">{roleLabel(user.role)}</UiElement>
                    <UiElement as="td">
                      <Badge status={user.disabled ? "stopped" : "running"}>
                        {user.disabled ? "已禁用" : "启用"}
                      </Badge>
                    </UiElement>
                    <UiElement as="td" className="tabular">{when(user.created_at ?? "")}</UiElement>
                    <UiElement as="td">
                      <UiElement as="div" className="row-actions">
                        <UiButton actionId="action.Accounts.005"
                          onClick={() => setEditor({ user, action: "edit" })}
                        >
                          <UiText>{"编辑"}</UiText>
                        </UiButton>
                        {user.auth_source !== "oidc" && (
                          <UiButton actionId="action.Accounts.006"
                            onClick={() =>
                              setEditor({ user, action: "password" })
                            }
                          >
                            <UiText>{"重置密码"}</UiText>
                          </UiButton>
                        )}
                        <UiButton actionId="action.Accounts.007"
                          className={user.disabled ? "" : "danger-text"}
                          onClick={() =>
                            setEditor({
                              user,
                              action: user.disabled ? "enable" : "disable",
                            })
                          }
                        >
                          {user.disabled ? "启用" : "禁用"}
                        </UiButton>
                        <UiButton actionId="action.Accounts.008"
                          className="danger-text"
                          onClick={() => setEditor({ user, action: "delete" })}
                        >
                          <UiText>{"删除"}</UiText>
                        </UiButton>
                      </UiElement>
                    </UiElement>
                  </UiElement>
                ))}
              </UiElement>
            </UiElement>
          </UiElement>
        ) : (
          <Empty
            title={!hasSuccess ? readNotice({ loaded: false, refreshing: loading, error }) : query || role !== "all" ? "没有匹配的账号" : "暂无账号"}
            description={!hasSuccess ? "尚未取得账号列表，请检查错误提示后重试。" : "调整筛选条件或创建本地账号。"}
            icon={<UserRound size={25} />}
          />
        )}
      </UiRegion>
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

function DefaultUserDialog({
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
          <Button actionId="action.Accounts.009" disabled={busy} onClick={onClose}>
            <UiText>{"取消"}</UiText>
          </Button>
          <Button actionId="action.Accounts.010"
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
            <UiElement as="input"
              aria-label="账号用户名"
              autoComplete="off"
              value={username}
              disabled={busy || action === "edit"}
              onChange={(e) => setUsername(e.target.value)}
            />
          </Field>
          <Field label="显示名称">
            <UiElement as="input"
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
                <UiElement as="option" key={value} value={value}>
                  {roleLabel(value)}
                </UiElement>
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
          <UiElement as="input"
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
        <UiElement as="p" className="account-action-description">
          <UiText>{"删除后该账号无法登录，已有会话立即失效；操作审计保留。系统不允许移除最后一个有效管理员。"}</UiText>
        </UiElement>
      )}
      {action === "disable" && (
        <UiElement as="p" className="account-action-description">
          <UiText>{"禁用后该账号无法登录，已有会话立即失效。系统不允许禁用最后一个有效管理员。"}</UiText>
        </UiElement>
      )}
      {action === "enable" && (
        <UiElement as="p" className="account-action-description">
          <UiText>{"允许账号"}</UiText> <UiElement as="b">{user?.username}</UiElement> <UiText>{"使用其当前角色重新登录。"}</UiText>
        </UiElement>
      )}
      {typed && (
        <Field label="输入用户名以确认" required>
          <UiElement as="input"
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

export const PasswordDialog = withUiSlot("component.PasswordDialog", DefaultPasswordDialog);
const AccountsPage = withUiSlot("component.AccountsPage", DefaultAccountsPage);
export default AccountsPage;
const UserDialog = withUiSlot("component.UserDialog", DefaultUserDialog);

// Reference package implementation exports.
export { DefaultPasswordDialog, DefaultAccountsPage, DefaultUserDialog };
