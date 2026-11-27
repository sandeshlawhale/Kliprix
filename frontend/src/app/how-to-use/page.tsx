"use client";

import { OpenSettingsRoute } from "@/components/settings/settings-dialog";

/** Old address: shows Home with the settings dialog open on How to use (D-067). */
export default function Page() {
  return <OpenSettingsRoute tab="help" />;
}
