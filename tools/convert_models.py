"""Convert the ONNX face models into the .npz files read by face_engine.py.

Development only (needs the onnx package):

    pip install onnx numpy
    python tools/convert_models.py face_detection_yunet_2023mar.onnx face_recognition_sface_2021dec.onnx

The models come from the OpenCV Zoo (https://github.com/opencv/opencv_zoo):
models/face_detection_yunet and models/face_recognition_sface. Weights of
large tensors are stored as float16 to halve the download.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
import onnx
from onnx import helper, numpy_helper

OUT = Path(__file__).resolve().parents[1] / "custom_components" / "ezviz_nvr" / "models"


def convert(path: Path) -> Path:
    model = onnx.load(path)
    weights = {}
    for t in model.graph.initializer:
        arr = numpy_helper.to_array(t)
        if arr.dtype == np.float32 and arr.size > 4096:
            arr = arr.astype(np.float16)
        weights[t.name] = arr
    nodes = []
    for node in model.graph.node:
        attrs = {}
        for a in node.attribute:
            value = helper.get_attribute_value(a)
            if isinstance(value, bytes):
                value = value.decode()
            elif isinstance(value, list):
                value = [v.decode() if isinstance(v, bytes) else v for v in value]
            attrs[a.name] = value
        nodes.append({"op": node.op_type, "inputs": list(node.input), "outputs": list(node.output), "attrs": attrs})
    graph = {"nodes": nodes, "outputs": [o.name for o in model.graph.output]}
    target = OUT / (path.stem + ".npz")
    OUT.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(target, __graph__=np.frombuffer(json.dumps(graph).encode(), dtype=np.uint8), **weights)
    return target


if __name__ == "__main__":
    for arg in sys.argv[1:]:
        print(convert(Path(arg)))
