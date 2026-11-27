import { cn } from "@/lib/utils";

/** Selected toggle in the settings dialog: accent border on a plain background (D-067). */
export const ACCENT_TOGGLE =
  "data-[state=on]:border-brand data-[state=on]:bg-transparent data-[state=on]:text-foreground";

/** A titled group of rows inside a settings pane. */
export function SettingGroup({
  title,
  children,
  className,
}: {
  title?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("flex flex-col", className)}>
      {title && <h3 className="text-muted-foreground pb-1 text-xs font-medium">{title}</h3>}
      <div className="divide-border/60 flex flex-col divide-y">{children}</div>
    </section>
  );
}

/** One line: name (and an optional short hint) on the left, the control on the right. */
export function SettingRow({
  label,
  hint,
  htmlFor,
  children,
  className,
}: {
  label: React.ReactNode;
  hint?: React.ReactNode;
  htmlFor?: string;
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex min-h-14 items-center justify-between gap-6 py-3", className)}>
      <div className="flex min-w-0 flex-col gap-0.5">
        {htmlFor ? (
          <label htmlFor={htmlFor} className="text-sm font-medium">
            {label}
          </label>
        ) : (
          <span className="text-sm font-medium">{label}</span>
        )}
        {hint && <span className="text-muted-foreground text-xs">{hint}</span>}
      </div>
      {children && <div className="flex shrink-0 items-center gap-2">{children}</div>}
    </div>
  );
}
