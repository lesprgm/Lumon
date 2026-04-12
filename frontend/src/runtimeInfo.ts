import runtimeContract from "../../lumon_runtime_contract.json";

type FrontendFeatureRecord = {
  ui_telemetry?: boolean;
  ui_ready_handshake?: boolean;
};

const frontendFeatures = (runtimeContract.frontend_features || {}) as FrontendFeatureRecord;

export const LUMON_FRONTEND_RUNTIME_VERSION = runtimeContract.runtime_version;

export const LUMON_FRONTEND_FEATURES = {
  uiTelemetry: frontendFeatures.ui_telemetry === true,
  uiReadyHandshake: frontendFeatures.ui_ready_handshake === true,
} as const;
