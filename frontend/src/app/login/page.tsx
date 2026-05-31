import { Suspense } from "react";

import { LoginForm } from "@/components/auth/login-form";

// `LoginForm` reads `?next` via `useSearchParams()`, which (Next 16) forces
// client rendering up to the nearest Suspense boundary — without this wrapper
// the prerender pass errors.
export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}
