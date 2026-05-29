import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Unmount and clear the DOM between tests so renders can't leak into each
// other (we use explicit imports rather than Vitest globals, so RTL's
// auto-cleanup isn't registered — do it here).
afterEach(() => {
  cleanup();
});
