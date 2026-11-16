# ktpwarp-server attribution

This directory contains a source snapshot of [celesWuff/ktpwarp-server](https://github.com/celesWuff/ktpwarp-server).
The snapshot is pinned to upstream commit `5904e68163973e7c5af2d9b988993d4426927411`.

The upstream project is distributed under the MIT License; see `LICENSE`.
Course Hub keeps it as an independent Node.js sidecar. Desktop configuration is
edited through Settings and stored in the application's local `config.json` under
`ketangpai`; `runtimeConfig.ts` reads that profile when `COURSE_HUB_KTP_CONFIG`
is supplied. Standalone use still supports the upstream `config.ts` convention.
Both configuration files, `node_modules/` and logs are excluded from this repository.
The original README and License are retained; the runtime configuration bridge
and desktop controls are local modifications.
Local attendance fixes use numeric `unusual` values, evaluate the response
envelope status, validate explicitly configured coordinates, and release batch
state after request failures. Example coordinates are intentionally empty.
