import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(scriptDir, "../..");
const runtimeContractPath = resolve(repoRoot, "lumon_runtime_contract.json");
const runtimeManifestPath = resolve(repoRoot, "frontend/public/lumon-runtime.json");

const contract = JSON.parse(readFileSync(runtimeContractPath, "utf8"));
const manifest = {
  runtime_version: contract.runtime_version,
  features: contract.frontend_features || {},
};

mkdirSync(dirname(runtimeManifestPath), { recursive: true });
writeFileSync(runtimeManifestPath, `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
