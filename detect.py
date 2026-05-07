import os

import cv2
import numpy as np
import onnxruntime as ort
from utils import load_toml_as_dict

debug = load_toml_as_dict("cfg/general_config.toml")['super_debug'] == "yes"

def get_optimal_threads(max_limit=4):
    threads = os.cpu_count()
    threads_amount = min(max(2, threads // 2), max_limit)
    print(f"Detected {threads} CPU threads, using {threads_amount} threads.")
    return threads_amount

optimal_threads_amount = get_optimal_threads()


def _nms_numpy(preds, conf_thres=0.6, iou_thres=0.6):
    """YOLOv8 ONNX postprocess + per-class NMS using numpy + cv2.dnn.NMSBoxes.

    preds shape: (1, 4+nc, na) (default YOLOv8 ONNX export) or (1, na, 4+nc).
    Returns: list with one numpy array per batch image, shape (n, 6):
        [x1, y1, x2, y2, conf, cls].
    """
    if preds.ndim == 3 and preds.shape[1] < preds.shape[2]:
        preds = preds.transpose(0, 2, 1)

    results = []
    for batch_pred in preds:  # (na, 4+nc)
        boxes_xywh = batch_pred[:, :4]
        class_scores = batch_pred[:, 4:]
        cls_ids = np.argmax(class_scores, axis=1)
        confs = class_scores[np.arange(len(cls_ids)), cls_ids]

        mask = confs >= conf_thres
        if not np.any(mask):
            results.append(np.zeros((0, 6), dtype=np.float32))
            continue

        boxes_xywh = boxes_xywh[mask]
        confs = confs[mask].astype(np.float32)
        cls_ids = cls_ids[mask]

        x, y, w, h = boxes_xywh[:, 0], boxes_xywh[:, 1], boxes_xywh[:, 2], boxes_xywh[:, 3]
        x1 = x - w / 2
        y1 = y - h / 2
        x2 = x + w / 2
        y2 = y + h / 2

        boxes_xywh_topleft = np.stack([x1, y1, x2 - x1, y2 - y1], axis=1).astype(np.float32)

        keep_indices = []
        for c in np.unique(cls_ids):
            cmask = cls_ids == c
            cbboxes = boxes_xywh_topleft[cmask].tolist()
            cconfs = confs[cmask].tolist()
            idx = cv2.dnn.NMSBoxes(cbboxes, cconfs, conf_thres, iou_thres)
            if len(idx) == 0:
                continue
            idx = np.array(idx).flatten()
            global_idx = np.where(cmask)[0][idx]
            keep_indices.extend(global_idx.tolist())

        if not keep_indices:
            results.append(np.zeros((0, 6), dtype=np.float32))
            continue

        keep = np.array(keep_indices, dtype=int)
        out = np.stack([
            x1[keep], y1[keep], x2[keep], y2[keep],
            confs[keep], cls_ids[keep].astype(np.float32)
        ], axis=1).astype(np.float32)
        results.append(out)
    return results

class Detect:
    def __init__(self, model_path, ignore_classes=None, classes=None, input_size=(640, 640)):
        self.preferred_device = load_toml_as_dict("cfg/general_config.toml")['cpu_or_gpu']
        self.model_path = model_path
        self.classes = classes
        self.ignore_classes = ignore_classes if ignore_classes else []
        self.input_size = input_size
        self.model, self.device = self.load_model()
        self._padded_img_buffer = np.full(
            (1, 3, self.input_size[0], self.input_size[1]),
            128.0 / 255.0,
            dtype=np.float32
        )


    def load_model(self):
        available_providers = ort.get_available_providers()
        if self.preferred_device == "gpu" or self.preferred_device == "auto":
            if "CUDAExecutionProvider" in available_providers:
                onnx_provider = "CUDAExecutionProvider"
                print("Using CUDA GPU")
            elif "DmlExecutionProvider" in available_providers:
                onnx_provider = "DmlExecutionProvider"
                print("Using GPU")
            elif "AzureExecutionProvider" in available_providers:
                onnx_provider = "AzureExecutionProvider"
            else:
                print("Using CPU as no GPU provider found")
                onnx_provider = "CPUExecutionProvider"

        else:
            onnx_provider = "CPUExecutionProvider"

        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        so.intra_op_num_threads = optimal_threads_amount
        so.inter_op_num_threads = optimal_threads_amount
        model = ort.InferenceSession(self.model_path, sess_options=so, providers=[onnx_provider])

        return model, onnx_provider

    def preprocess_image(self, img):
        h, w, _ = img.shape
        scale = min(self.input_size[0] / h, self.input_size[1] / w)
        new_w = int(w * scale)
        new_h = int(h * scale)

        resized_img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        self._padded_img_buffer.fill(128.0 / 255.0)
        self._padded_img_buffer[0, :, :new_h, :new_w] = np.transpose(resized_img, (2, 0, 1)).astype(np.float32) / 255.0

        return self._padded_img_buffer, new_w, new_h

    def postprocess(self, preds, img, orig_img_shape, resized_shape, conf_tresh=0.6):
        preds = _nms_numpy(preds, conf_thres=conf_tresh, iou_thres=0.6)

        orig_h, orig_w = orig_img_shape
        resized_w, resized_h = resized_shape

        scale_w = orig_w / resized_w
        scale_h = orig_h / resized_h

        results = []
        for pred in preds:
            if len(pred):
                pred[:, 0] *= scale_w
                pred[:, 1] *= scale_h
                pred[:, 2] *= scale_w
                pred[:, 3] *= scale_h
                results.append(pred)

        return results

    def detect_objects(self, img, conf_tresh=0.6):
        orig_h, orig_w = img.shape[:2]
        orig_img_shape = (orig_h, orig_w)

        # Preprocess the image
        preprocessed_img, resized_w, resized_h = self.preprocess_image(img)
        resized_shape = (resized_w, resized_h)

        outputs = self.model.run(None, {'images': preprocessed_img})

        detections = self.postprocess(outputs[0], preprocessed_img, orig_img_shape, resized_shape, conf_tresh)

        results = {}
        for detection in detections:
            for *xyxy, conf, cls in detection:
                x1, y1, x2, y2 = map(int, xyxy)
                class_id = int(cls)
                class_name = self.classes[class_id]

                if class_id in self.ignore_classes or class_name in self.ignore_classes:
                    continue
                if class_name not in results:
                    results[class_name] = []
                results[class_name].append([x1, y1, x2, y2])

        return results


