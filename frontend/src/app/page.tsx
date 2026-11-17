import { BackendStatus } from "@/app/backend-status";

export default function Home() {
  return (
    <main className="mx-auto max-w-xl p-8">
      <h1 className="text-2xl font-semibold">Kliprix</h1>
      <BackendStatus />
    </main>
  );
}
