import cv2
import mediapipe as mp
import subprocess
from pathlib import Path
import ffmpeg
from typing import List, Dict

class StreamProcessor:
    def __init__(self, source_path, streamer_name, output_dir='outputs'):
        self.source_path = source_path
        self.streamer_name = streamer_name
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.mp_face_detection = mp.solutions.face_detection
        self.face_detection = self.mp_face_detection.FaceDetection(
            model_selection=1, min_detection_confidence=0.5
        )
        self.camera_bbox = None  # последняя известная позиция (x1,y1,x2,y2)

    def detect_camera_region(self, frame):
        """Обнаруживает лицо и возвращает bounding box (x1,y1,x2,y2)."""
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self.face_detection.process(rgb)
        if results.detections:
            # Берём первое лицо (или можно выбрать самое большое)
            detection = results.detections[0]
            bbox = detection.location_data.relative_bounding_box
            h, w, _ = frame.shape
            x1 = int(bbox.xmin * w)
            y1 = int(bbox.ymin * h)
            x2 = int((bbox.xmin + bbox.width) * w)
            y2 = int((bbox.ymin + bbox.height) * h)
            self.camera_bbox = (x1, y1, x2, y2)
            return self.camera_bbox
        # Если лиц не найдено, возвращаем последнюю известную позицию
        return self.camera_bbox

    def process_stream_with_moments(self, moments: List[Dict], transcript: Dict, vertical: bool = True) -> List[Dict]:
        """
        Принимает список моментов (с start, end, score) и транскрипт.
        Для каждого момента вырезает клип, находит лицо на первом кадре и накладывает его сверху.
        Возвращает список клипов с путями и метаданными.
        """
        from modules.video_processor import VideoProcessor
        video_proc = VideoProcessor(output_dir=self.output_dir)

        clips = []
        for i, moment in enumerate(moments):
            # Вырезаем клип (вертикальный)
            output_name = f"stream_clip_{i+1}_score_{moment['score']:.1f}"
            clip_path = video_proc.extract_clip(
                self.source_path,
                moment['start'],
                moment['end'],
                output_name,
                vertical_format=vertical
            )

            # Определяем лицо на первом кадре вырезанного клипа
            cap = cv2.VideoCapture(clip_path)
            ret, frame = cap.read()
            cap.release()
            if not ret:
                bbox = None
            else:
                bbox = self.detect_camera_region(frame)

            if bbox is not None:
                # Накладываем лицо сверху (30% высоты)
                x1, y1, x2, y2 = bbox
                # Получаем размеры клипа
                probe = ffmpeg.probe(clip_path)
                video_stream = next(s for s in probe['streams'] if s['codec_type'] == 'video')
                width = int(video_stream['width'])
                height = int(video_stream['height'])
                cam_height = int(height * 0.3)

                # Временный файл для наложения
                temp_clip = Path(clip_path).with_suffix('.temp.mp4')
                overlay_filter = (
                    f"[0:v]crop={x2-x1}:{y2-y1}:{x1}:{y1},scale={width}:{cam_height}[cam];"
                    f"[0:v][cam]overlay=0:0"
                )
                cmd_overlay = [
                    'ffmpeg', '-i', clip_path,
                    '-vf', overlay_filter,
                    '-c:a', 'copy',
                    '-y', str(temp_clip)
                ]
                subprocess.run(cmd_overlay, check=True, capture_output=True)
                # Заменяем клип на новый
                Path(clip_path).unlink()
                temp_clip.rename(clip_path)

            # Добавляем метаданные
            clips.append({
                'path': clip_path,
                'start': moment['start'],
                'end': moment['end'],
                'duration': moment['end'] - moment['start'],
                'title': f"Twitch: {self.streamer_name} - клип {i+1}",
                'description': f"Twitch: {self.streamer_name}\nКлип из стрима"
            })

        return clips