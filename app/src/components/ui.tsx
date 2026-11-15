import { useEffect, useId, useRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Info, Loader2, XCircle } from "lucide-react";
import { level, percent, type Tone } from "../lib/format";

export function Button({
  variant = "secondary",
  size = "md",
  pending = false,
  icon,
  children,
  className = "",
  disabled,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md";
  pending?: boolean;
  icon?: ReactNode;
}) {
  return (
    <button
      type="button"
      className={`btn btn-${variant} btn-${size} ${className}`}
      disabled={disabled || pending}
      aria-busy={pending || undefined}
      {...rest}
    >
      {pending ? <Loader2 className="spin" size={14} aria-hidden /> : icon}
      {children && <span>{children}</span>}
    </button>
  );
}

/** Selo com ponto e texto: o estado nunca depende só da cor. */
export function Badge({ tone = "neutral", children, dot = true }: { tone?: Tone; children: ReactNode; dot?: boolean }) {
  return (
    <span className={`badge tone-${tone}`}>
      {dot && <span className="dot" aria-hidden />}
      {children}
    </span>
  );
}

export function Card({ children, className = "", ...rest }: { children: ReactNode; className?: string } & React.HTMLAttributes<HTMLElement>) {
  return (
    <section className={`card ${className}`} {...rest}>
      {children}
    </section>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="page-header">
      <div>
        <h1 id="page-title">{title}</h1>
        {subtitle && <p className="secondary page-subtitle">{subtitle}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

/** Barra de uso com o mesmo código de cores da telinha; o texto do nível acompanha a cor. */
export function UsageBar({ label, value, hint, accent = "orange" }: { label: string; value: number | null; hint?: ReactNode; accent?: "orange" | "blue" }) {
  const nivel = level(value);
  const nome = nivel === "critical" ? "crítico" : nivel === "warning" ? "atenção" : null;
  return (
    <div className="usage">
      <div className="usage-row">
        <span className="usage-label">{label}</span>
        <span className="mono usage-value">
          {percent(value)}
          {nome && <span className={`usage-level tone-${nivel === "critical" ? "critical" : "warning"}`}> · {nome}</span>}
        </span>
      </div>
      <div
        className="usage-track"
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={value ?? undefined}
        aria-valuetext={value == null ? "sem dado" : `${Math.round(value)}%${nome ? `, ${nome}` : ""}`}
      >
        {value != null && <div className={`usage-fill fill-${nivel === "normal" ? accent : nivel}`} style={{ width: `${Math.min(100, Math.max(0, value))}%` }} />}
      </div>
      {hint && <div className="usage-hint muted">{hint}</div>}
    </div>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  description,
  disabled,
  pending,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  description?: ReactNode;
  disabled?: boolean;
  pending?: boolean;
}) {
  const id = useId();
  return (
    <div className="toggle-row">
      <div className="toggle-text">
        <label htmlFor={id}>{label}</label>
        {description && (
          <p className="muted" id={`${id}-d`}>
            {description}
          </p>
        )}
      </div>
      <div className="toggle-control">
        {pending && <Loader2 className="spin muted" size={14} aria-label="Salvando" />}
        <button
          id={id}
          type="button"
          role="switch"
          aria-checked={checked}
          aria-describedby={description ? `${id}-d` : undefined}
          disabled={disabled || pending}
          className={`switch ${checked ? "on" : ""}`}
          onClick={() => onChange(!checked)}
        >
          <span className="knob" aria-hidden />
        </button>
      </div>
    </div>
  );
}

const NOTICE_ICON = { info: Info, success: CheckCircle2, warning: AlertTriangle, critical: XCircle };

export function Notice({
  tone = "info",
  title,
  children,
  actions,
}: {
  tone?: "info" | "success" | "warning" | "critical";
  title?: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
}) {
  const Icon = NOTICE_ICON[tone];
  return (
    <div className={`notice notice-${tone}`} role={tone === "critical" ? "alert" : "status"}>
      <Icon size={16} aria-hidden className="notice-icon" />
      <div className="notice-body">
        {title && <strong>{title}</strong>}
        {children && <div className="notice-text">{children}</div>}
        {actions && <div className="notice-actions">{actions}</div>}
      </div>
    </div>
  );
}

export function Spinner({ label = "Carregando" }: { label?: string }) {
  return (
    <span className="spinner" role="status">
      <Loader2 className="spin" size={16} aria-hidden />
      <span className="muted">{label}…</span>
    </span>
  );
}

export function Empty({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      {icon && <div className="empty-icon">{icon}</div>}
      <h3>{title}</h3>
      {children && <p className="muted">{children}</p>}
    </div>
  );
}

export function KeyValues({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="kv">
      {items.map(([k, v], i) => (
        <div key={i} className="kv-row">
          <dt className="muted">{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Diálogo modal nativo (foco preso, Esc fecha). Só confirma pelo botão explícito. */
export function Dialog({
  open,
  title,
  children,
  onClose,
  actions,
  wide,
}: {
  open: boolean;
  title: string;
  children: ReactNode;
  onClose: () => void;
  actions: ReactNode;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) {
      if (typeof d.showModal === "function") d.showModal();
      else d.setAttribute("open", "");
    } else if (!open && d.open) {
      if (typeof d.close === "function") d.close();
      else d.removeAttribute("open");
    }
  }, [open]);
  const titleId = useId();
  return (
    <dialog
      ref={ref}
      className={`dialog ${wide ? "dialog-wide" : ""}`}
      aria-labelledby={titleId}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      {open && (
        <>
          <h2 id={titleId}>{title}</h2>
          <div className="dialog-body">{children}</div>
          <div className="dialog-actions">{actions}</div>
        </>
      )}
    </dialog>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
  disabled,
}: {
  value: T;
  options: { value: T; label: string; title?: string }[];
  onChange: (v: T) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <div className="segmented" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={o.value === value}
          title={o.title}
          disabled={disabled}
          className={o.value === value ? "active" : ""}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
