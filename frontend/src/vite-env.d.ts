/// <reference types="vite/client" />

/** Build identity inlined by Vite at build time; see main.ts's `logBuild`. */
interface ImportMetaEnv {
  readonly VITE_APP_VERSION?: string;
  readonly VITE_GIT_COMMIT?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
