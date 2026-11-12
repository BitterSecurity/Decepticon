const fs = require('fs');

// The native runtime intentionally does not inherit arbitrary image ENV.
// Prefer an explicit operator path, then the system binary shipped in the
// sandbox. Outside that image, retain the existing real-Chrome channel.
function chromiumOptions() {
  const executable = process.env.INSANE_CHROMIUM_PATH ||
    (fs.existsSync('/usr/bin/chromium') ? '/usr/bin/chromium' : null);
  return {
    ...(executable ? { executablePath: executable } : { channel: 'chrome' }),
    // OpenShell blocks capset: Chromium's zygote capability drop would abort.
    // Child processes remain confined by the unchanged native runtime policy.
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--no-zygote'],
    // dconf must not depend on writable /dev/shm in a confined workload.
    env: { ...process.env, GSETTINGS_BACKEND: 'memory' },
  };
}

module.exports = { chromiumOptions };
