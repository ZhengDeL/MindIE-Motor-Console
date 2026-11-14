import type { LoginPageProps } from '@mindie-motor/ui-sdk';
import { UiElement, withUiSlot, UiButton, UiText } from "./uiPluginSdk";
import { useEffect, useId, useState } from "react";
import type { InputHTMLAttributes } from "react";
import {
  Eye,
  EyeOff,
  Moon,
  ShieldCheck,
  Sun,
} from "./icons";
import { useAppearance } from "./appearance";
import { api, setCsrfToken } from "./api";
import type { AuthStatus } from "./api";
import { Button, Field } from "./ui";
import PlatformBrand from "./PlatformBrand";
import "./login.css";

function DefaultPasswordInput({
  label,
  value,
  onChange,
  disabled,
  autoComplete,
  hint,
  secretName = "密码",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  disabled: boolean;
  autoComplete: InputHTMLAttributes<HTMLInputElement>["autoComplete"];
  hint?: string;
  secretName?: string;
}) {
  const id = useId();
  const [visible, setVisible] = useState(false);
  return (
    <UiElement as="div" className="field login-password-field">
      <UiElement as="label" className="field-label" htmlFor={id}>
        {label}
      </UiElement>
      <UiElement as="div" className="login-password-control">
        <UiElement as="input"
          id={id}
          aria-label={label}
          aria-describedby={hint ? `${id}-hint` : undefined}
          type={visible ? "text" : "password"}
          data-private="true"
          autoComplete={autoComplete}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          disabled={disabled}
          spellCheck={false}
        />
        <UiButton actionId="auth.password.visibility"
          className="login-reveal"
          type="button"
          aria-label={`${visible ? "隐藏" : "显示"}${secretName}`}
          aria-pressed={visible}
          disabled={disabled}
          onClick={() => setVisible(!visible)}
        >
          {visible ? <Eye size={16} /> : <EyeOff size={16} />}
        </UiButton>
      </UiElement>
      {hint && <UiElement as="small" id={`${id}-hint`}>{hint}</UiElement>}
    </UiElement>
  );
}

function DefaultLoginPage({
  status,
  onAuthenticated,
}: LoginPageProps) {
  const appearance = useAppearance();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [bootstrapToken, setBootstrapToken] = useState("");
  const [error, setError] = useState(() =>
    new URLSearchParams(window.location.search).get("auth_error") === "oidc"
      ? "企业账号登录失败，请重试或联系管理员"
      : "",
  );
  const [busy, setBusy] = useState(false);
  const setup = status.setup_required;
  useEffect(() => {
    const url = new URL(window.location.href);
    if (url.searchParams.has("auth_error")) {
      url.searchParams.delete("auth_error");
      window.history.replaceState(
        null,
        "",
        url.pathname + url.search + url.hash,
      );
    }
  }, []);
  const submit = async () => {
    if (busy) return;
    setError("");
    if (!username.trim() || !password) return setError("请填写用户名与密码");
    if (setup && (password.length < 15 || password.length > 128))
      return setError("密码长度须为 15–128 个字符");
    if (setup && !bootstrapToken.trim())
      return setError("请输入部署时配置的一次性初始化凭据");
    setBusy(true);
    try {
      const session = await api<AuthStatus>(
        setup ? "/auth/bootstrap" : "/auth/login",
        {
          method: "POST",
          headers: setup ? { "X-Bootstrap-Token": bootstrapToken.trim() } : {},
          body: JSON.stringify({
            username: username.trim(),
            password,
            ...(setup ? { display_name: displayName.trim() } : {}),
          }),
        },
      );
      setCsrfToken(session.csrf_token);
      setPassword("");
      setBootstrapToken("");
      await onAuthenticated();
    } catch (caught) {
      setError((caught as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <UiElement as="div" className="account-signin login-page">
      <UiElement as="div" className="login-header-actions">
        <UiButton actionId="appearance.mode.toggle" type="button" className="login-mode-toggle"
          aria-label={appearance.resolvedMode === "dark" ? "切换浅色模式" : "切换深色模式"}
          onClick={() => appearance.setAppearance({ mode: appearance.resolvedMode === "dark" ? "light" : "dark" })}>
          {appearance.resolvedMode === "dark" ? <Sun size={17} /> : <Moon size={17} />}
        </UiButton>
      </UiElement>
      <UiElement as="main" className={`login-main${setup ? " login-main-setup" : ""}`}>
        <UiElement as="div" className="login-column">
        <UiElement as="header" className="login-header">
          <UiElement as="div" className="login-brand">
            <PlatformBrand variant="login" />
          </UiElement>
        </UiElement>
        <UiElement as="div" className="login-heading">
          <UiElement as="h1">{setup ? "初始化管理员" : "登录您的账号"}</UiElement>
          <UiElement as="p" className="login-description">
            {setup
              ? "创建首个管理员账号，开始配置运行环境。"
              : "欢迎回来，请输入您的账号信息"}
          </UiElement>
        </UiElement>
        <UiElement as="form" data-action-id={status.setup_required ? "auth.setup" : "auth.login"}
          className="signin-card login-card"
          aria-label={setup ? "初始化管理员" : "登录控制台"}
          aria-busy={busy}
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          {!setup && status.sso?.enabled && status.sso.login_url && (
            <UiElement as="div" className="login-enterprise">
              <UiElement as="a" className="signin-sso login-sso" href={status.sso.login_url}>
                <ShieldCheck size={17} />
                <UiText>{"企业统一身份登录"}</UiText>
              </UiElement>
              <UiElement as="div" className="login-divider"><UiElement as="span"><UiText>{"或"}</UiText></UiElement></UiElement>
            </UiElement>
          )}
          <Field label="用户名">
            <UiElement as="input"
              aria-label="用户名"
              placeholder="请输入用户名"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              disabled={busy}
              autoFocus
            />
          </Field>
          {setup && (
            <Field label="显示名称">
              <UiElement as="input"
                aria-label="显示名称"
                autoComplete="name"
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                disabled={busy}
              />
            </Field>
          )}
          <PasswordInput
            label={setup ? "管理员密码" : "密码"}
            value={password}
            onChange={setPassword}
            disabled={busy}
            autoComplete={setup ? "new-password" : "current-password"}
            hint={setup ? "15–128 个字符，请使用独立的长密码" : undefined}
          />
          {setup && (
            <PasswordInput
              label="一次性初始化凭据"
              value={bootstrapToken}
              onChange={setBootstrapToken}
              disabled={busy}
              autoComplete="off"
              hint="由系统部署人员提供，初始化成功后不再使用"
              secretName="初始化凭据"
            />
          )}
          <Button actionId="auth.submit-button"
            type="submit"
            kind="primary"
            size="large"
            className="login-submit"
            loading={busy}
            disabled={setup && (!username.trim() || !password || !bootstrapToken.trim())}
          >
            <UiElement as="span" role={busy ? "status" : undefined}>
              {busy
                ? setup
                  ? "正在创建管理员…"
                  : "正在验证身份…"
                : setup
                  ? "创建管理员并登录"
                  : "登录"}
            </UiElement>
          </Button>
          {error && <UiElement as="p" className="login-error" role="alert">{error}</UiElement>}
          {setup && (
            <UiElement as="p" className="login-setup-note">
              <UiText>{"系统没有默认账号或密码，初始化后请妥善保管管理员凭据。"}</UiText>
            </UiElement>
          )}
        </UiElement>
        </UiElement>
      </UiElement>
    </UiElement>
  );
}

const PasswordInput = withUiSlot("component.PasswordInput", DefaultPasswordInput);
const LoginPage = withUiSlot("page.login", DefaultLoginPage);
export default LoginPage;

// Reference package implementation exports.
export { DefaultPasswordInput, DefaultLoginPage };
