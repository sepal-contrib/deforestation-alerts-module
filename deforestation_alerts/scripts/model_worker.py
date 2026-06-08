# Thin shim: CNN code consolidated into cnn_worker (D3). Kept so
# analysis_tile's `from ...model_worker import apply_dl_model, save_prediction_prob`
# keeps working. Removed in Task 10 once analysis_tile is rewritten.
from deforestation_alerts.scripts.cnn_worker import (  # noqa: F401
    apply_dl_model,
    save_prediction_prob,
    RepeatElements,
)
