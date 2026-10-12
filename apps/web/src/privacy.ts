import type { Capabilities } from "./api";

const LOCAL_HOSTS = new Set(["127.0.0.1", "localhost", "::1"]);

/** Where frames go for inference, or null when nothing leaves this machine. */
export function privacyNotice(capabilities: Pick<Capabilities, "simulated" | "inference_host" | "inference_encrypted"> | null): string | null {
  if (!capabilities || capabilities.simulated || !capabilities.inference_host) return null;
  const host = capabilities.inference_host;
  if (LOCAL_HOSTS.has(host)) return null;
  const transport = capabilities.inference_encrypted ? "encrypted in transit (HTTPS)" : "NOT encrypted in transit";
  return `Video frames are sent to ${host} for inference: ${transport}, and readable by the hosting provider while they are processed. Only use footage you are allowed to share there.`;
}
