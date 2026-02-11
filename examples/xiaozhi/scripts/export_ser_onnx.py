#!/usr/bin/env python3
"""
Export a HuggingFace audio emotion classification model to ONNX (CPU).
Apache/MIT-friendly suggestion: use a Wav2Vec2-based classifier.

Usage:
  uv run examples/xiaozhi/scripts/export_ser_onnx.py --model_id <hf_model_id> --output ser.onnx --window_seconds 1.0

Notes:
- The exported ONNX takes raw waveform float32 of shape [1, T] (16kHz), input name: "input", output name: "logits".
- After export, set config.py SER.MODEL_PATH to the ONNX path and SER.LABELS to model id2label order printed below.
"""
import argparse
import json
import os

import torch

try:
    from transformers import AutoConfig, AutoModelForAudioClassification
except Exception as e:
    raise RuntimeError("Please install transformers: uv add transformers") from e


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model_id", type=str, required=True, help="HuggingFace model id")
    parser.add_argument("--output", type=str, default="ser.onnx", help="Output ONNX path")
    parser.add_argument("--window_seconds", type=float, default=1.0, help="Waveform window seconds")
    parser.add_argument("--sample_rate", type=int, default=16000, help="Sample rate")
    args = parser.parse_args()

    cfg = AutoConfig.from_pretrained(args.model_id)
    model = AutoModelForAudioClassification.from_pretrained(args.model_id, config=cfg)
    model.eval()

    # Save label mapping for config usage
    id2label = getattr(cfg, "id2label", None)
    if id2label is None:
        # Fallback: common labels
        id2label = {0: "neutral", 1: "happy", 2: "angry", 3: "sad"}
    labels = [id2label[i] for i in sorted(id2label.keys())]

    # Wrapper to expose logits(input_values)
    class Wrapper(torch.nn.Module):
        def __init__(self, base):
            super().__init__()
            self.base = base
        def forward(self, input_values: torch.Tensor):
            out = self.base(input_values=input_values)
            return out.logits

    wrapper = Wrapper(model)
    wrapper.eval()

    T = int(args.sample_rate * args.window_seconds)
    dummy = torch.randn(1, T, dtype=torch.float32)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    torch.onnx.export(
        wrapper,
        dummy,
        args.output,
        input_names=["input"],
        output_names=["logits"],
        opset_version=18,
        do_constant_folding=True,
        dynamic_axes={"input": {1: "time"}, "logits": {1: "classes"}},
    )

    meta = {
        "model_id": args.model_id,
        "sample_rate": args.sample_rate,
        "window_seconds": args.window_seconds,
        "labels": labels,
        "input_name": "input",
        "output_name": "logits",
    }
    with open(args.output + ".labels.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print("Exported:", args.output)
    print("Labels:", labels)
    print("Update config.py SER with MODEL_PATH, INPUT_NAME=input, OUTPUT_NAME=logits and LABELS above.")


if __name__ == "__main__":
    main()
