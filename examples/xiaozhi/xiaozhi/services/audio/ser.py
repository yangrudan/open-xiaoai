import numpy as np
from typing import Optional

try:
    import onnxruntime as ort
except Exception:
    ort = None

from config import APP_CONFIG


class SER:
    _instance: Optional["SER"] = None

    def __init__(self):
        ser_cfg = APP_CONFIG.get("SER", {})
        self.model_path: str = ser_cfg.get("MODEL_PATH", "")
        self.input_name: str = ser_cfg.get("INPUT_NAME", "input")
        self.output_name: str = ser_cfg.get("OUTPUT_NAME", "logits")
        self.labels: list[str] = ser_cfg.get(
            "LABELS",
            ["neutral", "happy", "angry", "sad"],
        )
        self.sample_rate: int = int(ser_cfg.get("SAMPLE_RATE", 16000))
        self.window_seconds: float = float(ser_cfg.get("WINDOW_SECONDS", 1.0))
        self.session = None
        if ort and self.model_path:
            try:
                self.session = ort.InferenceSession(self.model_path, providers=["CPUExecutionProvider"])  # type: ignore
            except Exception:
                self.session = None

    @classmethod
    def instance(cls) -> "SER":
        if cls._instance is None:
            cls._instance = SER()
        return cls._instance

    def predict(self, waveform: np.ndarray) -> Optional[str]:
        if self.session is None:
            return None
        # Ensure mono float32 and fixed window length
        x = waveform.astype(np.float32)
        target_len = int(self.sample_rate * self.window_seconds)
        if len(x) >= target_len:
            x = x[-target_len:]
        else:
            pad = np.zeros(target_len - len(x), dtype=np.float32)
            x = np.concatenate([pad, x], axis=0)
        x = x.reshape(1, -1)
        try:
            outputs = self.session.run([self.output_name], {self.input_name: x})
            logits = np.asarray(outputs[0]).squeeze()
            idx = int(np.argmax(logits))
            if 0 <= idx < len(self.labels):
                return self.labels[idx]
            return None
        except Exception:
            return None
