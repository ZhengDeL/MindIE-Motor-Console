# Operations UI reference pack

This source pack independently implements a service page and a complete raw-JSON
save/preflight/deploy editor. It also supplies button, action, icon and
language contracts. It uses the running host's React/API singleton and the
original deployment callback; it does not render the default editor.
The default theme remains selected; the pack's verified CSS styles its own page
and modal, while the small reference dictionary demonstrates translated labels.

Create a ZIP named `operations-ui.mcui` containing these five files directly at
the archive root: `manifest.json`, `entry.mjs`, `operations.css`, `README.md`,
and `LICENSE`. Import it in **Settings → UI plugins**, verify its digest and
source, select the intended slots and save. Restart the control process, then
save browser drafts and reload. Import alone executes no code.

All API calls act on the selected environment. The raw JSON editor preserves
arbitrary fields inside native `user_config`/`env`; the browser rejects unsafe
integers, while request-schema validation belongs to the backend. A valid preflight and any required
extra-resource approval are required before its deploy button is enabled.
Lost mutation responses remain unknown and disable resubmission in this editor.
Backend identity, role, CSRF, runtime, admission and audit checks remain required.

This pack is a source reference, not NPU or Kubernetes acceptance evidence.
Use an isolated control environment for integration review. It deliberately
offers fewer editing conveniences than the default visual deployment wizard.
