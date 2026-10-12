import assert from "node:assert/strict";
import test from "node:test";
import { privacyNotice } from "../src/privacy.ts";

test("privacy notice names the remote host and stays quiet when frames stay local", () => {
  const remote = privacyNotice({ simulated: false, inference_host: "gpu.modal.run", inference_encrypted: true });
  assert.match(remote ?? "", /sent to gpu\.modal\.run for inference: encrypted in transit \(HTTPS\)/);
  assert.match(privacyNotice({ simulated: false, inference_host: "gpu.example", inference_encrypted: false }) ?? "", /NOT encrypted/);
  assert.equal(privacyNotice({ simulated: true, inference_host: null, inference_encrypted: false }), null);
  assert.equal(privacyNotice({ simulated: false, inference_host: "127.0.0.1", inference_encrypted: false }), null);
  assert.equal(privacyNotice(null), null);
});
