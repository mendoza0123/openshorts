"""BlazeFace behind the legacy `mp.solutions.face_detection` interface.

requirements.txt used to pin mediapipe 0.10.14, whose
`mp.solutions.face_detection.FaceDetection` main.py and screencast_layout.py
call. The releases with Python 3.14 wheels dropped `mp.solutions` altogether:
their Tasks `FaceDetector` needs a .tflite file on disk and returns pixel boxes.
`face_detection()` returns the legacy object when it exists, and otherwise an
adapter whose `.process(rgb).detections[i].location_data.relative_bounding_box`
has the same shape, so the callers do not change.

The Tasks API only ships the short-range BlazeFace model (faces within ~2 m),
where the legacy call used full-range (model_selection=1). Small, distant faces
detect less often on this path; the YOLO person fallback in main.py covers them.
"""
import os
import urllib.request
from types import SimpleNamespace

import mediapipe as mp
import numpy as np

MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_detector/"
             "blaze_face_short_range/float16/latest/blaze_face_short_range.tflite")


def _model_path():
    path = os.environ.get("FACE_DETECTOR_MODEL_PATH") or os.path.join(
        os.path.expanduser("~"), ".cache", "openshorts", "blaze_face_short_range.tflite")
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".part"
        urllib.request.urlretrieve(MODEL_URL, tmp)
        os.replace(tmp, path)
    return path


class _TasksFaceDetection:
    def __init__(self, min_detection_confidence):
        from mediapipe.tasks.python.core.base_options import BaseOptions
        from mediapipe.tasks.python.vision import face_detector

        options = face_detector.FaceDetectorOptions(
            base_options=BaseOptions(model_asset_path=_model_path()),
            min_detection_confidence=min_detection_confidence)
        self._detector = face_detector.FaceDetector.create_from_options(options)

    def process(self, rgb):
        h, w = rgb.shape[:2]
        image = mp.Image(image_format=mp.ImageFormat.SRGB,
                         data=np.ascontiguousarray(rgb))
        detections = []
        for d in self._detector.detect(image).detections:
            b = d.bounding_box
            box = SimpleNamespace(xmin=b.origin_x / w, ymin=b.origin_y / h,
                                  width=b.width / w, height=b.height / h)
            score = d.categories[0].score if d.categories else 0.0
            detections.append(SimpleNamespace(
                location_data=SimpleNamespace(relative_bounding_box=box),
                score=[score]))
        return SimpleNamespace(detections=detections)


def face_detection(model_selection=1, min_detection_confidence=0.5):
    solutions = getattr(mp, "solutions", None)
    if solutions is not None:
        return solutions.face_detection.FaceDetection(
            model_selection=model_selection,
            min_detection_confidence=min_detection_confidence)
    return _TasksFaceDetection(min_detection_confidence)
