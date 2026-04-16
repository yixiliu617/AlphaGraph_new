// ---------------------------------------------------------------------------
// Route entry point — intentionally thin.
// All logic lives in EngineContainer; all UI lives in EngineView.
// ---------------------------------------------------------------------------

import EngineContainer from "./EngineContainer";

export default function EnginePage() {
  return <EngineContainer />;
}
