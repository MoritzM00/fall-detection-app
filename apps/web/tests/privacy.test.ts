import assert from "node:assert/strict";
import test from "node:test";
import { privacyNotice } from "../src/privacy.ts";

test("privacy notice flags remote inference and stays quiet when frames stay local", () => {
  const remote = privacyNotice({ simulated: false, inference_host: "gpu.modal.run", inference_encrypted: true });
  assert.match(remote ?? "", /^Video frames are sent to a remote GPU server, where the hosting provider can see them during analysis\./);
  assert.match(privacyNotice({ simulated: false, inference_host: "gpu.example", inference_encrypted: false }) ?? "", /sent without encryption to a remote GPU server/);
  assert.equal(privacyNotice({ simulated: true, inference_host: null, inference_encrypted: false }), null);
  assert.equal(privacyNotice({ simulated: false, inference_host: "127.0.0.1", inference_encrypted: false }), null);
  assert.equal(privacyNotice(null), null);
});
