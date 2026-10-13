import type { Capabilities } from "./api";

const LOCAL_HOSTS = new Set(["127.0.0.1", "localhost", "::1"]);

/** Where frames go for inference, or null when nothing leaves this machine. */
export function privacyNotice(capabilities: Pick<Capabilities, "simulated" | "inference_host" | "inference_encrypted"> | null): string | null {
  if (!capabilities || capabilities.simulated || !capabilities.inference_host) return null;
  const host = capabilities.inference_host;
  if (LOCAL_HOSTS.has(host)) return null;
  const transport = capabilities.inference_encrypted ? "" : " without encryption";
  return `Video frames are sent${transport} to a remote GPU server for analysis. Only use footage you are allowed to share.`;
}
