# Third-Party Notices

`wsctl` 0.2.0 bundles a pre-built frontend in `src/wsctl/static/`. The source
and exact versions are pinned in `web/package.json` and `web/pnpm-lock.yaml`.
License texts for the bundled packages are available from their npm packages
and are shipped in the wheel as `wsctl/THIRD_PARTY_NOTICES.md`; the exact
package versions are pinned in `web/pnpm-lock.yaml` (which records integrity
hashes, not license text). Apache-2.0 components are listed explicitly above.

| Component | Version | License |
|---|---|---|
| Vue | 3.5.x | MIT |
| Pinia | 4.x | MIT |
| Vite (build only) | 8.x | MIT |
| Naive UI | 2.45.x | MIT |
| xterm.js (`@xterm/xterm`) | 6.0.x | MIT |
| @xterm/addon-fit | 0.11.x | MIT |
| @xterm/addon-webgl | 0.19.x | MIT |
| @xterm/addon-canvas | 0.7.x | MIT |
| @xterm/addon-search | 0.16.x | MIT |
| @xterm/addon-unicode11 | 0.9.x | MIT |
| @xterm/addon-web-links | 0.12.x | MIT |
| @xterm/addon-image | 0.9.x | MIT |
| @xterm/addon-serialize | 0.14.x | MIT |
| @xterm/addon-clipboard | 0.2.x | MIT |
| CodeMirror 6 | 6.x | MIT |
| splitpanes | 4.x | MIT |
| asciinema-player | 3.17.x | Apache-2.0 |
| zmodem.js | 0.1.10 | Apache-2.0 |
| vue-i18n | 11.x | MIT |
| TanStack Query (Vue) | 5.x | MIT |
| openapi-fetch | 0.17.x | MIT |
| openapi-typescript (build only) | 7.13.x | MIT |
| Tailwind CSS (build only) | 4.x | MIT |
| ESLint / Prettier (build only) | 9.x / 3.x | MIT |

Backend Python dependencies are declared in `pyproject.toml`; their licenses
remain with the respective packages and are resolved by pip at install time.

`wsctl` itself is licensed under the MIT License — see [LICENSE](LICENSE).
