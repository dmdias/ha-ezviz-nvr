# Face models

Converted from the [OpenCV Zoo](https://github.com/opencv/opencv_zoo) with
`tools/convert_models.py` (weights of the large tensors stored as float16):

| File | Model | Licence |
|---|---|---|
| `face_detection_yunet_2023mar.npz` | [YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet), face detection with five landmarks | MIT |
| `face_recognition_sface_2021dec.npz` | [SFace](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface), 128-value face features | Apache 2.0 |

They are run by `face_engine.py` on numpy alone.
