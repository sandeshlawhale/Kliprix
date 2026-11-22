import type { LucideIcon } from "lucide-react";

/** Page header shared by the About and How to use pages (D-039). */
export function InfoHeader({ title, lead }: { title: string; lead: string }) {
  return (
    <header className="flex flex-col gap-2">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      <p className="text-muted-foreground max-w-2xl text-balance">{lead}</p>
    </header>
  );
}

export function InfoSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-lg font-medium">{title}</h2>
      {children}
    </section>
  );
}

/** A short bulleted list in muted text. */
export function InfoList({ items }: { items: React.ReactNode[] }) {
  return (
    <ul className="text-muted-foreground flex list-disc flex-col gap-1.5 pl-5 text-sm leading-relaxed">
      {items.map((item, index) => (
        <li key={index}>{item}</li>
      ))}
    </ul>
  );
}

/** An icon, a bold line and one sentence: used for feature grids. */
export function InfoItem({
  icon: Icon,
  title,
  children,
}: {
  icon: LucideIcon;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex gap-3 rounded-xl border p-4">
      <Icon className="text-brand mt-0.5 size-5 shrink-0" aria-hidden />
      <div className="flex flex-col gap-1">
        <h3 className="text-sm font-medium">{title}</h3>
        <p className="text-muted-foreground text-sm leading-relaxed">{children}</p>
      </div>
    </div>
  );
}
