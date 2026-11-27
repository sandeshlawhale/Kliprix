"use client";

import { OpenSettingsRoute } from "@/components/settings/settings-dialog";

/** Old address: shows Home with the settings dialog open on this tab (D-067). */
export default function Page() {
  return <OpenSettingsRoute tab="about" />;
}
