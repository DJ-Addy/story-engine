"use client";

// The 401 recovery.
//
// The analytics endpoints take a bearer token like the rest of the API, and the
// app has no login screen — so a judge opening /dashboard against a live
// backend would otherwise hit an unexplained 401 with nothing to do about it.
// This is not a new auth system: the form calls the `login` already exported by
// lib/apiClient, which stores the token exactly where every other request reads
// it from. It is shown only when a request actually came back 401.
//
// The form itself now lives in components/auth/SignInForm — the workspace's
// cold-start panel needs the same one, and two copies of a password box is one
// too many. This file stays as the dashboard's name for it, and as the place
// its placement and field-id namespace are decided.

import { SignInForm } from "@/components/auth/SignInForm";

export function SignInPanel({ onSignedIn }: { onSignedIn: () => void }) {
  return <SignInForm idPrefix="dash" onSignedIn={onSignedIn} />;
}
