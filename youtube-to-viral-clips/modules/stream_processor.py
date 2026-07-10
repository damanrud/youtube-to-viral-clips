import cv2
import mediapipe as mp
import subprocess
from pathlib import Path
import ffmpeg
from typing import List, Dict, Tuple, Optional
import numpy as np
import logging

logger = logging.getLogger(__name__)


class StreamProcessor:
    def __init__(
        self,
        source_path: str,
        streamer_name: str,
        output_dir: str = 'outputs',
        camera_scale: float = 0.3,
        camera_position: str = 'top-left',  # 'top-left', 'top-right', 'bottom-left', 'bottom-right'
    ):
        self.source_path = source_path
        self.streamer_name = streamer_name
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.camera_scale = camera_scale
        self.camera_position = camera_position

        # Просто используем mp.solutions (работает в версиях 0.10.30+)
        self.face_detection = mp.solutions.face_detection.FaceDetection(
            model_selection=1, min_detection_confidence=0.5
        )
        self.camera_bbox = None

    def detect_camera_region_robust(
        self,
        video_path: str,
        num_frames: int = 30
    ) -> Optional[Tuple[int, int, int, int]]:
        """
        Обнаруживает лицо/область камеры, используя несколько кадров.
        Возвращает (x1, y1, x2, y2) или None.
        """
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            logger.warning(f"Не удалось открыть видео: {video_path}")
            return None

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total_frames == 0:
            cap.release()
            logger.warning(f"Видео не содержит кадров: {video_path}")
            return None

        step = max(1, total_frames // num_frames)
        boxes = []
        frame_shape = None

        for i in range(0, total_frames, step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, i)
            ret, frame = cap.read()
            if not ret:
                continue
            if frame_shape is None:
                frame_shape = frame.shape
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = self.face_detection.process(rgb)
            if results.detections:
                max_area = 0
                best_bbox = None
                for detection in results.detections:
                    bbox = detection.location_data.relative_bounding_box
                    h, w, _ = frame.shape
                    x1 = int(bbox.xmin * w)
                    y1 = int(bbox.ymin * h)
                    x2 = int((bbox.xmin + bbox.width) * w)
                    y2 = int((bbox.ymin + bbox.height) * h)
                    area = (x2 - x1) * (y2 - y1)
                    if area > max_area:
                        max_area = area
                        best_bbox = (x1, y1, x2, y2)
                if best_bbox:
                    boxes.append(best_bbox)

        cap.release()

        if boxes:
            avg_x1 = int(np.mean([b[0] for b in boxes]))
            avg_y1 = int(np.mean([b[1] for b in boxes]))
            avg_x2 = int(np.mean([b[2] for b in boxes]))
            avg_y2 = int(np.mean([b[3] for b in boxes]))
            padding = 0.2
            width = avg_x2 - avg_x1
            height = avg_y2 - avg_y1
            if frame_shape is not None:
                h, w, _ = frame_shape
                x1 = max(0, int(avg_x1 - width * padding))
                y1 = max(0, int(avg_y1 - height * padding))
                x2 = min(w, int(avg_x2 + width * padding))
                y2 = min(h, int(avg_y2 + height * padding))
                logger.info(f"Обнаружена камера: ({x1}, {y1}, {x2}, {y2})")
                return (x1, y1, x2, y2)

        # Если лиц не найдено – центр кадра (20% размера)
        cap = cv2.VideoCapture(video_path)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        region_w = int(width * 0.2)
        region_h = int(height * 0.2)
        x1 = (width - region_w) // 2
        y1 = (height - region_h) // 2
        x2 = x1 + region_w
        y2 = y1 + region_h
        logger.info(f"Лицо не найдено, используем центр кадра: ({x1}, {y1}, {x2}, {y2})")
        return (x1, y1, x2, y2)

    def _calculate_camera_position(
        self,
        video_width: int,
        video_height: int,
        cam_width: int,
        cam_height: int
    ) -> Tuple[int, int]:
        margin = 20
        if self.camera_position == 'top-left':
            return (margin, margin)
        elif self.camera_position == 'top-right':
            return (video_width - cam_width - margin, margin)
        elif self.camera_position == 'bottom-left':
            return (margin, video_height - cam_height - margin)
        elif self.camera_position == 'bottom-right':
            return (video_width - cam_width - margin, video_height - cam_height - margin)
        else:
            return (margin, margin)

    def process_stream_with_moments(
        self,
        moments: List[Dict],
        transcript: Dict,
        vertical: bool = True
    ) -> List[Dict]:
        from modules.video_processor import VideoProcessor
        video_proc = VideoProcessor(output_dir=self.output_dir)

        clips = []
        for i, moment in enumerate(moments):
            output_name = f"stream_clip_{i+1}_score_{moment['score']:.1f}"
            clip_path = video_proc.extract_clip(
                self.source_path,
                moment['start'],
                moment['end'],
                output_name,
                vertical_format=vertical
            )

            bbox = self.detect_camera_region_robust(clip_path, num_frames=20)

            if bbox is not None:
                x1, y1, x2, y2 = bbox
                probe = ffmpeg.probe(clip_path)
                video_stream = next(s for s in probe['streams'] if s['codec_type'] == 'video')
                width = int(video_stream['width'])
                height = int(video_stream['height'])

                cam_width = int(width * self.camera_scale)
                cam_aspect = (y2 - y1) / (x2 - x1) if (x2 - x1) > 0 else 1.0
                cam_height = int(cam_width * cam_aspect)
                if cam_height > height * 0.4:
                    cam_height = int(height * 0.4)
                    cam_width = int(cam_height / cam_aspect)

                cam_x, cam_y = self._calculate_camera_position(width, height, cam_width, cam_height)

                temp_clip = Path(clip_path).with_suffix('.temp.mp4')
                overlay_filter = (
                    f"[0:v]crop={x2-x1}:{y2-y1}:{x1}:{y1},scale={cam_width}:{cam_height}[cam];"
                    f"[0:v][cam]overlay={cam_x}:{cam_y}"
                )
                cmd_overlay = [
                    'ffmpeg', '-i', clip_path,
                    '-vf', overlay_filter,
                    '-c:a', 'copy',
                    '-y', str(temp_clip)
                ]
                try:
                    subprocess.run(cmd_overlay, check=True, capture_output=True)
                    Path(clip_path).unlink()
                    temp_clip.rename(clip_path)
                    logger.info(f"Наложена камера на клип {i+1} (позиция: {cam_x},{cam_y}, размер: {cam_width}x{cam_height})")
                except subprocess.CalledProcessError as e:
                    logger.error(f"Ошибка наложения камеры на клип {i+1}: {e.stderr.decode()}")

            clips.append({
                'path': clip_path,
                'start': moment['start'],
                'end': moment['end'],
                'duration': moment['end'] - moment['start'],
                'title': f"{self.streamer_name} - клип {i+1}",
                'description': f"Клип из стрима {self.streamer_name}"
            })

        return clips