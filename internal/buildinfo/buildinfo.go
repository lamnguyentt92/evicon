// Package buildinfo names the release the binaries were built from.
package buildinfo

// Release must equal the VERSION file at the repository root (checked by test).
const Release = "080"

// PatchID identifies the build in /healthz answers and manifests.
const PatchID = "evicon-" + Release
