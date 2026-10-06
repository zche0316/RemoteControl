#!/usr/bin/env bash
# Build, sign, notarize, and verify a distributable RemoteAgent DMG.
#
# Prerequisites:
#   * macOS with Xcode command-line tools and this project's .venv
#   * Developer ID Application signing identity in the login Keychain
#   * a notarization Keychain profile created with `xcrun notarytool store-credentials`
#
# The private key and notarization API credentials are intentionally never read
# from this script or stored in the repository.

set -euo pipefail

readonly APP_NAME="RemoteAgent"
readonly BUNDLE_PATH="dist/${APP_NAME}.app"
readonly DEFAULT_SIGNING_IDENTITY="Developer ID Application: Gabriel Ralph (WVLFJFML4Z)"
readonly DEFAULT_NOTARY_PROFILE="Squidly Notary"

usage() {
  cat <<'EOF'
Usage: scripts/release-macos.sh <version> [options]

Build a signed, notarized RemoteAgent DMG named:
  dist/RemoteAgent-<version>-macos-<architecture>.dmg

Options:
  --build-number <number>   CFBundleVersion. Defaults to the Git commit count.
  --notary-profile <name>   Keychain profile for notarytool.
                            Defaults to "Squidly Notary".
  --allow-dirty             Permit a release from a dirty Git worktree.
  -h, --help                Show this help.

Examples:
  scripts/release-macos.sh 1.2.0
  scripts/release-macos.sh 1.2.0 --build-number 42

Before the first notarized release on a Mac, an authorized Team member must
create the Keychain profile. With an App Store Connect API key:
  xcrun notarytool store-credentials "Squidly Notary" \
    --key /secure/path/AuthKey_<KEY_ID>.p8 \
    --key-id <KEY_ID> \
    --issuer <ISSUER_ID>
EOF
}

fail() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

version="${1:-}"
if [[ -z "$version" || "$version" == "-h" || "$version" == "--help" ]]; then
  usage
  [[ -n "$version" ]] && exit 0
  exit 2
fi
shift

[[ "$version" =~ ^[0-9]+(\.[0-9]+){1,2}([.-][0-9A-Za-z.-]+)?$ ]] || \
  fail "version must look like 1.2.0 (received: $version)"

build_number=""
notary_profile="${RELEASE_NOTARY_PROFILE:-$DEFAULT_NOTARY_PROFILE}"
allow_dirty=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --build-number)
      [[ $# -ge 2 ]] || fail "--build-number requires a value"
      build_number="$2"
      shift 2
      ;;
    --notary-profile)
      [[ $# -ge 2 ]] || fail "--notary-profile requires a value"
      notary_profile="$2"
      shift 2
      ;;
    --allow-dirty)
      allow_dirty=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      fail "unknown option: $1"
      ;;
  esac
done

[[ -x .venv/bin/python ]] || fail "missing project interpreter: .venv/bin/python"
command -v hdiutil >/dev/null || fail "hdiutil is required (run this script on macOS)"
command -v codesign >/dev/null || fail "codesign is required (install Xcode command-line tools)"
xcrun --find notarytool >/dev/null || fail "notarytool is required (install Xcode command-line tools)"
xcrun --find stapler >/dev/null || fail "stapler is required (install Xcode command-line tools)"

if [[ "$allow_dirty" != true ]] && [[ -n "$(git status --porcelain --untracked-files=all)" ]]; then
  fail "working tree has changes; commit them or explicitly use --allow-dirty"
fi

if [[ -z "$build_number" ]]; then
  build_number="$(git rev-list --count HEAD)"
fi
[[ "$build_number" =~ ^[0-9]+$ ]] || fail "build number must be numeric (received: $build_number)"

architecture="$(uname -m)"
release_name="${APP_NAME}-${version}-macos-${architecture}"
dmg_path="dist/${release_name}.dmg"
checksum_path="${dmg_path}.sha256"

[[ ! -e "$dmg_path" ]] || fail "refusing to overwrite existing release: $dmg_path"
[[ ! -e "$checksum_path" ]] || fail "refusing to overwrite existing checksum: $checksum_path"

signing_identity="${RELEASE_SIGNING_IDENTITY:-$DEFAULT_SIGNING_IDENTITY}"
if ! security find-identity -v -p codesigning | grep -Fq "$signing_identity"; then
  fail "signing identity is unavailable in this Keychain: $signing_identity"
fi

if ! xcrun notarytool history --keychain-profile "$notary_profile" >/dev/null 2>&1; then
  fail "notarization profile '$notary_profile' is unavailable in this user's Keychain; run notarytool store-credentials first"
fi

staging_dir="$(mktemp -d -t remoteagent-release)"
mounted_dir=""
cleanup() {
  if [[ -n "$mounted_dir" ]]; then
    hdiutil detach "$mounted_dir" -quiet >/dev/null 2>&1 || true
    rmdir "$mounted_dir" >/dev/null 2>&1 || true
  fi
  # staging_dir is created by mktemp in this script and is never user supplied.
  rm -rf "$staging_dir"
}
trap cleanup EXIT

printf 'Building %s version %s (build %s)\n' "$APP_NAME" "$version" "$build_number"
REMOTE_AGENT_VERSION="$version" REMOTE_AGENT_BUILD="$build_number" \
  .venv/bin/python -m PyInstaller packaging/RemoteAgent.spec --noconfirm

[[ -d "$BUNDLE_PATH" ]] || fail "PyInstaller did not produce $BUNDLE_PATH"
codesign --verify --deep --strict --verbose=2 "$BUNDLE_PATH"

ditto "$BUNDLE_PATH" "$staging_dir/$APP_NAME.app"
hdiutil create \
  -volname "$APP_NAME" \
  -srcfolder "$staging_dir" \
  -format UDZO \
  -ov "$dmg_path"

codesign --force --timestamp --sign "$signing_identity" "$dmg_path"
codesign --verify --verbose=2 "$dmg_path"
hdiutil verify "$dmg_path"

xcrun notarytool submit "$dmg_path" --keychain-profile "$notary_profile" --wait
xcrun stapler staple "$dmg_path"
xcrun stapler validate "$dmg_path"
hdiutil verify "$dmg_path"

mounted_dir="$(mktemp -d -t remoteagent-mounted)"
hdiutil attach -readonly -nobrowse -mountpoint "$mounted_dir" "$dmg_path" >/dev/null
codesign --verify --deep --strict --verbose=2 "$mounted_dir/$APP_NAME.app"
spctl --assess --type execute --verbose=4 "$mounted_dir/$APP_NAME.app"
hdiutil detach "$mounted_dir" -quiet
rmdir "$mounted_dir"
mounted_dir=""

shasum -a 256 "$dmg_path" > "$checksum_path"
printf '\nRelease ready:\n  %s\n  %s\n' "$dmg_path" "$checksum_path"
