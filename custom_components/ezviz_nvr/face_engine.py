"""Face detection and recognition on numpy alone.

Home Assistant OS has numpy and Pillow but no wheels of OpenCV or onnxruntime,
so the two small models are run by a minimal interpreter of their graphs:

- YuNet (OpenCV Zoo, MIT) finds faces and five landmarks;
- SFace (OpenCV Zoo, Apache 2.0) turns an aligned face into a 128-value
  feature vector; two faces are the same person when the cosine similarity
  of their vectors is high.

The models were converted from ONNX by ``tools/convert_models.py`` into
``.npz`` files holding the weights and the graph as JSON. Everything here is
CPU-bound and blocking: run it in an executor.
"""
from __future__ import annotations

from dataclasses import dataclass
import io
import json
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import as_strided
from PIL import Image

MODELS = Path(__file__).parent / "models"

# Landmarks of an aligned 112x112 face (eyes, nose tip, mouth corners), as used by SFace
_REFERENCE = np.array(
    [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366], [41.5493, 92.3655], [70.7299, 92.2041]],
    dtype=np.float64,
)


class Net:
    """Runs a converted ONNX graph (only the operators these two models use)."""

    def __init__(self, path: Path) -> None:
        with np.load(path) as data:
            self.graph = json.loads(bytes(data["__graph__"]).decode())
            self.weights = {k: data[k].astype(np.float32) for k in data.files if k != "__graph__"}

    def run(self, inputs: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        values = dict(self.weights)
        values.update(inputs)
        for node in self.graph["nodes"]:
            args = [values[name] if name else None for name in node["inputs"]]
            out = getattr(self, "_" + node["op"])(*args, **node["attrs"])
            values[node["outputs"][0]] = out
        return {name: values[name] for name in self.graph["outputs"]}

    # ----- operators (batch size 1, NCHW) -----

    @staticmethod
    def _Conv(x, w, b=None, group=1, pads=(0, 0, 0, 0), strides=(1, 1), kernel_shape=None, dilations=(1, 1)):
        x = x[0]
        c, height, width = x.shape
        m, _, kh, kw = w.shape
        sh, sw = strides
        if any(pads):
            x = np.pad(x, ((0, 0), (pads[0], pads[2]), (pads[1], pads[3])))
        ho = (x.shape[1] - kh) // sh + 1
        wo = (x.shape[2] - kw) // sw + 1
        if group == 1 and kh == kw == 1:
            out = w.reshape(m, c) @ x[:, ::sh, ::sw][:, :ho, :wo].reshape(c, -1)
            out = out.reshape(m, ho, wo)
        elif group == 1:
            s0, s1, s2 = x.strides
            cols = as_strided(x, (c, kh, kw, ho, wo), (s0, s1, s2, s1 * sh, s2 * sw))
            out = (w.reshape(m, -1) @ cols.reshape(c * kh * kw, ho * wo)).reshape(m, ho, wo)
        elif group == c == m:
            out = np.zeros((m, ho, wo), dtype=np.float32)
            for i in range(kh):
                for j in range(kw):
                    out += x[:, i : i + sh * ho : sh, j : j + sw * wo : sw] * w[:, 0, i, j][:, None, None]
        else:
            raise NotImplementedError(f"Conv with group={group}")
        if b is not None:
            out += b[:, None, None]
        return out[None]

    @staticmethod
    def _BatchNormalization(x, scale, bias, mean, var, epsilon=1e-5, **_):
        k = scale / np.sqrt(var + epsilon)
        shape = (1, -1) + (1,) * (x.ndim - 2)
        return x * k.reshape(shape) + (bias - mean * k).reshape(shape)

    @staticmethod
    def _PRelu(x, slope):
        return np.where(x > 0, x, x * slope.reshape(1, -1, 1, 1) if slope.size == x.shape[1] else x * slope)

    @staticmethod
    def _Relu(x):
        return np.maximum(x, 0)

    @staticmethod
    def _Sigmoid(x):
        return 1.0 / (1.0 + np.exp(-x))

    @staticmethod
    def _Add(a, b):
        return a + b

    @staticmethod
    def _Sub(a, b):
        return a - b

    @staticmethod
    def _Mul(a, b):
        return a * b

    @staticmethod
    def _Dropout(x, *_, **__):
        return x

    @staticmethod
    def _MaxPool(x, kernel_shape, strides, pads=(0, 0, 0, 0), ceil_mode=0, **_):
        if tuple(kernel_shape) != (2, 2) or tuple(strides) != (2, 2) or any(pads) or ceil_mode:
            raise NotImplementedError("MaxPool other than 2x2 / 2")
        n, c, h, w = x.shape
        x = x[:, :, : h // 2 * 2, : w // 2 * 2]
        return x.reshape(n, c, h // 2, 2, w // 2, 2).max(axis=(3, 5))

    @staticmethod
    def _Resize(x, roi=None, scales=None, sizes=None, mode="nearest", **_):
        if mode != "nearest" or scales is None or list(scales[:2]) != [1, 1] or scales[2] != scales[3] or scales[2] != int(scales[2]):
            raise NotImplementedError("Resize other than an integer nearest upscale")
        f = int(scales[2])
        return x.repeat(f, axis=2).repeat(f, axis=3)

    @staticmethod
    def _Transpose(x, perm):
        return np.ascontiguousarray(x.transpose(perm))

    @staticmethod
    def _Reshape(x, shape, **_):
        shape = [x.shape[i] if int(s) == 0 else int(s) for i, s in enumerate(shape)]
        return x.reshape(shape)

    @staticmethod
    def _Flatten(x, axis=1):
        return x.reshape(int(np.prod(x.shape[:axis])), -1)

    @staticmethod
    def _Gemm(a, b, c=None, alpha=1.0, beta=1.0, transA=0, transB=0):
        out = alpha * ((a.T if transA else a) @ (b.T if transB else b))
        return out + beta * c if c is not None else out


@dataclass
class Face:
    """A face found in a picture, in the picture's pixels."""

    box: tuple[float, float, float, float]  # x, y, width, height
    score: float
    landmarks: np.ndarray  # 5 x 2: right eye, left eye, nose, mouth right, mouth left


def _nms(boxes: np.ndarray, scores: np.ndarray, iou: float) -> list[int]:
    order = scores.argsort()[::-1]
    keep: list[int] = []
    x1, y1, w, h = boxes.T
    x2, y2, area = x1 + w, y1 + h, w * h
    while order.size:
        i = order[0]
        keep.append(int(i))
        rest = order[1:]
        iw = np.maximum(0, np.minimum(x2[i], x2[rest]) - np.maximum(x1[i], x1[rest]))
        ih = np.maximum(0, np.minimum(y2[i], y2[rest]) - np.maximum(y1[i], y1[rest]))
        inter = iw * ih
        order = rest[inter / (area[i] + area[rest] - inter + 1e-9) <= iou]
    return keep


def _similarity(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """2x3 similarity transform mapping src points onto dst (Umeyama)."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    s, d = src - mu_s, dst - mu_d
    cov = d.T @ s / len(src)
    u, sig, vt = np.linalg.svd(cov)
    sign = np.eye(2)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        sign[1, 1] = -1
    rot = u @ sign @ vt
    scale = np.trace(np.diag(sig) @ sign) / s.var(0).sum()
    t = mu_d - scale * rot @ mu_s
    return np.hstack([scale * rot, t[:, None]])


class FaceEngine:
    """Finds faces in a picture and describes each one with a feature vector."""

    def __init__(self, models: Path = MODELS) -> None:
        self._detector = Net(models / "face_detection_yunet_2023mar.npz")
        self._recognizer = Net(models / "face_recognition_sface_2021dec.npz")

    def detect(self, image: Image.Image, min_score: float = 0.8, max_side: int = 960) -> list[Face]:
        rgb = image.convert("RGB")
        scale = min(1.0, max_side / max(rgb.size))
        if scale < 1:
            rgb = rgb.resize((round(rgb.width * scale), round(rgb.height * scale)), Image.BILINEAR)
        width, height = rgb.size
        pw, ph = (width - 1) // 32 * 32 + 32, (height - 1) // 32 * 32 + 32
        blob = np.zeros((1, 3, ph, pw), dtype=np.float32)
        # The detector expects BGR values in 0..255
        blob[0, :, :height, :width] = np.asarray(rgb, dtype=np.float32)[:, :, ::-1].transpose(2, 0, 1)
        out = self._detector.run({"input": blob})
        boxes, scores, marks = [], [], []
        for stride in (8, 16, 32):
            cols, rows = pw // stride, ph // stride
            cls = np.clip(out[f"cls_{stride}"].reshape(-1), 0, 1)
            obj = np.clip(out[f"obj_{stride}"].reshape(-1), 0, 1)
            score = np.sqrt(cls * obj)
            idx = np.nonzero(score >= min_score)[0]
            if not idx.size:
                continue
            r, c = (idx // cols).astype(np.float32), (idx % cols).astype(np.float32)
            bbox = out[f"bbox_{stride}"].reshape(-1, 4)[idx]
            kps = out[f"kps_{stride}"].reshape(-1, 10)[idx]
            cx, cy = (c + bbox[:, 0]) * stride, (r + bbox[:, 1]) * stride
            w, h = np.exp(bbox[:, 2]) * stride, np.exp(bbox[:, 3]) * stride
            boxes.append(np.stack([cx - w / 2, cy - h / 2, w, h], 1))
            scores.append(score[idx])
            lm = np.empty((len(idx), 5, 2), dtype=np.float32)
            lm[:, :, 0] = (kps[:, 0::2] + c[:, None]) * stride
            lm[:, :, 1] = (kps[:, 1::2] + r[:, None]) * stride
            marks.append(lm)
        if not boxes:
            return []
        all_boxes, all_scores, all_marks = np.concatenate(boxes), np.concatenate(scores), np.concatenate(marks)
        return [
            Face(tuple(float(v) / scale for v in all_boxes[i]), float(all_scores[i]), all_marks[i] / scale)
            for i in _nms(all_boxes, all_scores, 0.3)
        ]

    @staticmethod
    def align(image: Image.Image, face: Face) -> Image.Image:
        """The face rotated and scaled to 112x112, eyes and mouth at fixed places."""
        m = _similarity(face.landmarks.astype(np.float64), _REFERENCE)
        inv = np.linalg.inv(np.vstack([m, [0, 0, 1]]))[:2]
        return image.convert("RGB").transform((112, 112), Image.AFFINE, tuple(inv.reshape(-1)), Image.BILINEAR)

    def describe(self, aligned: Image.Image) -> np.ndarray:
        """Unit-length 128-value feature vector of an aligned face."""
        blob = np.asarray(aligned.convert("RGB"), dtype=np.float32).transpose(2, 0, 1)[None]
        vec = self._recognizer.run({"data": np.ascontiguousarray(blob)})["fc1"][0]
        return vec / (np.linalg.norm(vec) + 1e-9)

    @staticmethod
    def sharpness(aligned: Image.Image) -> float:
        """Variance of the Laplacian of the face: low for blurred faces."""
        g = np.asarray(aligned.convert("L"), dtype=np.float32)
        lap = g[1:-1, :-2] + g[1:-1, 2:] + g[:-2, 1:-1] + g[2:, 1:-1] - 4 * g[1:-1, 1:-1]
        return float(lap.var())


def jpeg(image: Image.Image, quality: int = 85) -> bytes:
    buf = io.BytesIO()
    image.convert("RGB").save(buf, "JPEG", quality=quality)
    return buf.getvalue()
