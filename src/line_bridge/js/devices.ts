export const deviceVersions: Record<string, string> = {
  ANDROIDSECONDARY: "26.6.2", IOSIPAD: "26.7.2", DESKTOPWIN: "26.5.0.3975", DESKTOPMAC: "26.2.0",
};
export function deviceProfile(device: string) {
  const version = deviceVersions[device];
  if (!version) throw new Error("device_refused");
  return { device, version };
}
export function sessionProfile(saved: any) {
  const profile = deviceProfile(saved.device);
  if (saved.version !== profile.version || !["desktop-shared-v1", "secondary-shared-v2"].includes(saved.schema) ||
      saved.schema === "desktop-shared-v1" && saved.device !== "DESKTOPWIN") throw new Error("session_refused");
  return { ...profile, legacy: saved.schema === "desktop-shared-v1" };
}
