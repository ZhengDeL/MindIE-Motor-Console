# Sources and licensing

MindIE-Motor Console uses the Apache License 2.0 contained in the repository's original LICENSE file.

The separately versioned `mindie-motor/upstream` submodule is Ascend/MindIE-Motor, provided by its respective copyright holders under Mulan PSL v2. The fixed commit is `37d615f232a36b8dba8630ffc3a01a20d289c65e`; its license is in `mindie-motor/upstream/LICENSE.md`, with additional notices under that source tree. This repository does not relicense the upstream project.

Runtime adapters use the pinned upstream configuration helpers, templates and native entry points. Original source integrity is checked using the recorded SHA256 manifest. Keep the upstream license and notices when building or redistributing an image containing those sources.

The MindIE-Motor Console interface adapts layout, component presentation and visual tokens from Portainer Community Edition 2.45.3, copyright Portainer.io, fixed source commit `0fa764dc81c76bb7914d65d8e62b026d7c746899`, under the zlib license. These are altered implementations for its React components, Chinese interface and model deployment workflows, not the original Portainer software. Portainer's name and marks identify the reference product; this project retains its independent product identity. The original zlib notice is reproduced in `web/frontend/public/third-party-licenses.txt`. See `web/docs/UI_REFERENCE.md` for the reference pages and adaptation scope.

The browser interface uses `lucide-react` 0.577.0, the same outlined Lucide icon family used by the fixed Portainer 2.45.3 interface reference. Application action and navigation components retain local compatibility wrappers in `web/frontend/src/icons.tsx`. Lucide is provided under the ISC License, with portions derived from Feather under the MIT License. The full installed package license, including its Feather attribution, and the React/React DOM MIT notices are shipped in [`web/frontend/public/third-party-licenses.txt`](web/frontend/public/third-party-licenses.txt), copied into the built frontend as `/third-party-licenses.txt`. Preserve that file when distributing the frontend assets or container image.

Docker and Kubernetes runtime images under `web/frontend/public/runtime` are informational runtime identifiers retained from the interface adaptation. Their names and marks remain the property of their respective owners; they are not the platform's product logo. Keep the applicable upstream notices when redistributing these assets.

The interface uses client system fonts. The current source and frontend build do not bundle custom font binaries or a third-party product logo. Administrator-uploaded presentation icons are deployment data and are not part of the source distribution.
