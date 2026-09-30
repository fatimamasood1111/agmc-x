// electron-builder's own icon-embedding step (win.signAndEditExecutable)
// calls rcedit via the winCodeSign package — which also bundles an
// unrelated macOS darwin/*.dylib tree containing real symlinks.
// Extracting those symlinks needs a Windows privilege this no-admin-rights
// dev machine doesn't have, so turning signAndEditExecutable on fails
// outright with "Cannot create symbolic link: A required privilege is not
// held by the client" (see CLAUDE.md's packaging section for the original
// version of this same wall). A past attempt already got far enough to
// leave a real, working rcedit-x64.exe sitting in electron-builder's cache
// before hitting the darwin symlinks — this hook reuses that cached binary
// directly, running it by hand against the packed exe, so the real icon
// gets embedded without ever touching the darwin/symlink part of the
// package again.
const { execFileSync } = require("child_process");
const path = require("path");
const fs = require("fs");

function findCachedRcedit() {
  const cacheRoot = path.join(
    process.env.LOCALAPPDATA || "",
    "electron-builder",
    "Cache",
    "winCodeSign"
  );
  if (!fs.existsSync(cacheRoot)) return null;
  for (const entry of fs.readdirSync(cacheRoot)) {
    const candidate = path.join(cacheRoot, entry, "rcedit-x64.exe");
    if (fs.existsSync(candidate) && fs.statSync(candidate).size > 500000) {
      return candidate;
    }
  }
  return null;
}

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== "win32") return;

  const rcedit = findCachedRcedit();
  const exePath = path.join(
    context.appOutDir,
    `${context.packager.appInfo.productFilename}.exe`
  );
  const iconPath = path.resolve(__dirname, "../icon.ico");

  if (!rcedit) {
    console.warn(
      "[afterPack] No cached rcedit-x64.exe found in electron-builder's cache — " +
        "exe will keep Electron's default icon. Run `npm run dist` once with " +
        "win.signAndEditExecutable temporarily true to (partially) populate the " +
        "cache first, or extract rcedit-x64.exe from " +
        "https://github.com/electron/rcedit/releases manually."
    );
    return;
  }
  if (!fs.existsSync(exePath)) {
    console.warn(`[afterPack] Expected exe not found at ${exePath} — skipping icon embed.`);
    return;
  }

  console.log(`[afterPack] Embedding real app icon via cached rcedit: ${exePath}`);
  execFileSync(rcedit, [exePath, "--set-icon", iconPath]);
};
