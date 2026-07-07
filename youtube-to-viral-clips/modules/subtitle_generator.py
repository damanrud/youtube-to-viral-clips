import subprocess
import os
import re
import uuid
from pathlib import Path
from typing import List, Dict, Optional

from config import SUBTITLE_STYLE, VERTICAL_SUBTITLE_STYLE, OUTPUTS_DIR, SUBTITLE_TEMPLATES
from modules.transcriber import VideoTranscriber
from utils.video_metadata import get_video_info


class SubtitleGenerator:
    EMOJI_ASS_FALLBACKS = {
        "🔥": ("⚡", "Apple Symbols"),
        "💰": ("$", None),
        "😂": ("☺", "Apple Symbols"),
        "🤯": ("⚠", "Apple Symbols"),
        "💡": ("☀", "Apple Symbols"),
        "🚀": ("▶", "Apple Symbols"),
        "⚠": ("⚠", "Apple Symbols"),
        "⚡": ("⚡", "Apple Symbols"),
        "❤": ("♥", None),
        "🤔": ("☯", "Apple Symbols"),
        "✅": ("✔", "Zapf Dingbats"),
        "❌": ("✖", "Zapf Dingbats"),
    }

    def __init__(self, output_dir: Path = OUTPUTS_DIR):
        self.output_dir = output_dir
        self.output_dir.mkdir(exist_ok=True)
        self.transcriber = VideoTranscriber()
        self.temp_dir = Path("C:/sub_temp")
        self.temp_dir.mkdir(exist_ok=True)
        print(f"Temp directory: {self.temp_dir}")

    def add_subtitles(self, video_path: str, transcript: Dict,
                     start_time: float, end_time: float,
                     output_name: Optional[str] = None, vertical_format: bool = True,
                     clip_start_time: Optional[float] = None, style_template: str = "Classic",
                     language: str = "en", position_override: Optional[float] = None) -> str:
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(f"Video file not found: {video_path}")

        if output_name:
            output_filename = f"{output_name}_subtitled.mp4"
        else:
            output_filename = f"{video_path.stem}_subtitled.mp4"

        output_path = self.output_dir / output_filename

        ass_filename = f"sub_{uuid.uuid4().hex[:8]}.ass"
        ass_path = self.temp_dir / ass_filename

        try:
            video_info = get_video_info(str(video_path))
            duration = float(video_info.get('duration', 0.0))
            video_width = int(video_info.get('width', 1080))
            video_height = int(video_info.get('height', 1920))

            # ------ ПОЛУЧАЕМ НАСТРОЙКИ СТИЛЯ ИЗ ШАБЛОНА ------
            if style_template in SUBTITLE_TEMPLATES:
                template = SUBTITLE_TEMPLATES[style_template]
                style_settings = template['vertical'] if vertical_format else template['horizontal']
                if 'font' not in style_settings:
                    style_settings['font'] = 'Arial'
                style_settings = dict(style_settings)
            else:
                style = VERTICAL_SUBTITLE_STYLE if vertical_format else SUBTITLE_STYLE
                style_settings = {
                    'fontsize': style['fontsize'],
                    'color': 'white' if style['color'] == 'white' else 'black',
                    'stroke_color': 'black' if style['stroke_color'] == 'black' else 'white',
                    'stroke_width': style['stroke_width'],
                    'position': style['position'][1] if isinstance(style['position'], tuple) else style['position'],
                    'font': 'Arial',
                    'max_words': 3,
                    'smart_emojis': False
                }

            if position_override is not None:
                style_settings['position'] = position_override

            if clip_start_time is not None:
                video_offset = clip_start_time
            else:
                video_offset = start_time

            words = self.transcriber.get_words_in_range(
                transcript,
                video_offset,
                video_offset + duration
            )

            actual_language = transcript.get('language', language)
            max_words = style_settings.get('max_words', 3)
            word_groups = self._group_words(words, max_words, actual_language)

            if style_settings.get('smart_emojis', False):
                emoji_density = float(style_settings.get('emoji_density', 0.4))
                word_groups = self._add_smart_emojis(word_groups, language=actual_language, density=emoji_density)

            self._write_ass_file(word_groups, style_settings, video_offset,
                                 video_width, video_height, str(ass_path))

            if not ass_path.exists():
                raise FileNotFoundError(f"ASS file not created: {ass_path}")
            if ass_path.stat().st_size == 0:
                raise Exception(f"ASS file is empty: {ass_path}")

            # ---------- ИСПРАВЛЕННАЯ ЧАСТЬ: используем фильтр ass с экранированием ----------
            ass_path_str = str(ass_path).replace('\\', '/')
            # Экранируем двоеточие после буквы диска (C:/ → C\:/)
            ass_path_escaped = ass_path_str.replace(':', '\\:', 1)
            filter_str = f"ass='{ass_path_escaped}'"

            cmd = [
                'ffmpeg',
                '-i', str(video_path),
                '-vf', filter_str,
                '-c:v', 'libx264',
                '-c:a', 'copy',
                '-preset', 'veryfast',
                '-crf', '23',
                '-movflags', 'faststart',
                '-y',
                str(output_path)
            ]

            print(f"FFmpeg command: {' '.join(cmd)}")

            result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')

            if result.returncode != 0:
                raise Exception(f"FFmpeg error:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}")

            if ass_path.exists():
                os.unlink(ass_path)

            return str(output_path)

        except Exception as e:
            if ass_path.exists():
                os.unlink(ass_path)
            raise Exception(f"Error adding subtitles: {str(e)}")

    # ===== ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ (без изменений, оставляем как есть) =====
    def _write_ass_file(self, word_groups: List[Dict], style_settings: Dict, video_offset: float,
                        video_width: int, video_height: int, ass_path: str):
        with open(ass_path, 'w', encoding='utf-8') as f:
            fontsize = max(10, int(style_settings['fontsize'] * video_height / 1920))
            primary_color = self._color_to_ass(style_settings['color'])
            outline_color = self._color_to_ass(style_settings['stroke_color'])
            outline_width = max(1, int(style_settings['stroke_width'] * 0.3))

            margin_lr = max(10, int(video_width * 0.03))
            min_margin_v = int(video_height * 0.05)

            y_pos = style_settings['position']
            if y_pos < 0.3:
                alignment = 8
                margin_v = max(min_margin_v, int(y_pos * video_height))
            elif y_pos >= 0.6:
                alignment = 2
                margin_v = max(min_margin_v, int((1 - y_pos) * video_height))
            else:
                alignment = 5
                margin_v = max(min_margin_v, int(video_height * 0.1))

            f.write("[Script Info]\n")
            f.write("Title: Generated Subtitles\n")
            f.write("ScriptType: v4.00+\n")
            f.write("Collisions: Normal\n")
            f.write("PlayDepth: 0\n")
            f.write("Timer: 100.0000\n")
            f.write(f"PlayResX: {video_width}\n")
            f.write(f"PlayResY: {video_height}\n")
            f.write("WrapStyle: 1\n\n")

            f.write("[V4+ Styles]\n")
            f.write("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, ")
            f.write("OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ")
            f.write("ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, ")
            f.write("Alignment, MarginL, MarginR, MarginV, Encoding\n")

            font_name = style_settings.get('font', 'Arial')
            f.write(f"Style: Default,{font_name},{fontsize},{primary_color},{primary_color},")
            f.write(f"{outline_color},&H00000000,-1,0,0,0,100,100,0,0,1,{outline_width},0,")
            f.write(f"{alignment},{margin_lr},{margin_lr},{margin_v},1\n\n")

            f.write("[Events]\n")
            f.write("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")

            highlight_color = style_settings.get('highlight_color')

            for group in word_groups:
                start_time = group['start'] - video_offset
                end_time = group['end'] - video_offset
                if start_time < 0 or end_time < 0:
                    continue

                if highlight_color and group.get('words'):
                    events = self._build_highlight_events(
                        group, style_settings, video_offset, primary_color
                    )
                    for event_start, event_end, text in events:
                        start_str = self._seconds_to_ass_time(event_start)
                        end_str = self._seconds_to_ass_time(event_end)
                        f.write(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{text}\n")
                else:
                    start_str = self._seconds_to_ass_time(start_time)
                    end_str = self._seconds_to_ass_time(end_time)
                    text = self._format_ass_text(group['text'])
                    f.write(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{text}\n")

    def _build_highlight_events(self, group: Dict, style_settings: Dict,
                                video_offset: float, primary_color: str) -> List[tuple]:
        highlight_color = self._color_to_ass(style_settings['highlight_color'])
        scale = int(style_settings.get('highlight_scale', 110))
        highlight_tag = f"{{\\c{highlight_color}\\fscx{scale}\\fscy{scale}}}"
        reset_tag = f"{{\\c{primary_color}\\fscx100\\fscy100}}"

        words = group['words']
        tokens = [self._format_ass_text(w['word']) for w in words]
        emoji_token = self._format_ass_text(group['emoji']) if group.get('emoji') else None

        group_start = group['start'] - video_offset
        group_end = group['end'] - video_offset

        events = []
        for i, word in enumerate(words):
            event_start = max(word['start'] - video_offset, group_start)
            if i + 1 < len(words):
                event_end = words[i + 1]['start'] - video_offset
            else:
                event_end = group_end

            if event_end <= event_start:
                continue

            parts = []
            for j, token in enumerate(tokens):
                if j == i:
                    parts.append(f"{highlight_tag}{token}{reset_tag}")
                else:
                    parts.append(token)
            if emoji_token:
                parts.append(emoji_token)

            events.append((event_start, event_end, ' '.join(parts)))

        return events

    def _color_to_ass(self, color) -> str:
        if isinstance(color, str):
            if color == 'white':
                return "&H00FFFFFF"
            elif color == 'black':
                return "&H00000000"
            elif color == 'yellow':
                return "&H0000FFFF"
            elif color == 'cyan':
                return "&H00FFFF00"
            elif color == 'red':
                return "&H000000FF"
            else:
                return "&H00FFFFFF"
        elif isinstance(color, tuple) and len(color) == 3:
            r, g, b = color
            return f"&H00{b:02X}{g:02X}{r:02X}"
        else:
            return "&H00FFFFFF"

    def _sanitize_ass_text(self, text: str) -> str:
        sanitized = text.replace('\\', '')
        sanitized = sanitized.replace('{', '(').replace('}', ')')
        return ' '.join(sanitized.split())

    def _normalize_emoji_key(self, text: str) -> str:
        return text.replace('\ufe0f', '').replace('\u200d', '')

    def _to_ass_symbol(self, token: str) -> str:
        normalized = self._normalize_emoji_key(token.strip())
        if normalized in self.EMOJI_ASS_FALLBACKS:
            symbol, font_name = self.EMOJI_ASS_FALLBACKS[normalized]
            if font_name:
                return rf"{{\fn{font_name}}}{symbol}{{\r}}"
            return symbol
        return token

    def _format_ass_text(self, text: str) -> str:
        sanitized = self._sanitize_ass_text(text)
        if not sanitized:
            return sanitized
        tokens = sanitized.split()
        converted_tokens = [self._to_ass_symbol(token) for token in tokens]
        return ' '.join(converted_tokens)

    def _seconds_to_ass_time(self, seconds: float) -> str:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        centisecs = int((seconds % 1) * 100)
        return f"{hours}:{minutes:02d}:{secs:02d}.{centisecs:02d}"

    def _group_words(self, words: List[Dict], max_words: int = 3, language: str = "en") -> List[Dict]:
        if not words:
            return []
        if language == "fr":
            short_words = {
                'le', 'la', 'les', 'un', 'une', 'des', 'de', 'du', 'à', 'au', 'aux',
                'et', 'ou', 'mais', 'donc', 'or', 'ni', 'car', 'que', 'qui', 'où',
                'ce', 'ces', 'cet', 'cette', 'mon', 'ma', 'mes', 'ton', 'ta', 'tes',
                'son', 'sa', 'ses', 'notre', 'nos', 'votre', 'vos', 'leur', 'leurs',
                'je', 'tu', 'il', 'elle', 'on', 'nous', 'vous', 'ils', 'elles',
                'me', 'te', 'se', 'ne', 'y', 'en', 'lui', 'leur',
                'est', 'sont', 'suis', 'es', 'êtes', 'sommes', 'ai', 'as', 'a',
                'ont', 'avons', 'avez', "j'ai", "c'est", "n'est", "qu'est",
                'd\'un', 'd\'une', 'l\'un', 'l\'une', 'jusqu\'à', 'qu\'il', 'qu\'elle'
            }
        else:
            short_words = {
                'the', 'a', 'an', 'in', 'on', 'at', 'to', 'for', 'of', 'with',
                'and', 'or', 'but', 'is', 'are', 'was', 'were', 'be', 'been',
                'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
                'could', 'should', 'may', 'might', 'can', "can't", "don't",
                "won't", "isn't", "aren't", "wasn't", "weren't", "i'm", "you're",
                "he's", "she's", "it's", "we're", "they're", "i've", "you've",
                "we've", "they've", "i'll", "you'll", "he'll", "she'll", "we'll"
            }
        merged_words = []
        i = 0
        while i < len(words):
            w = dict(words[i])
            clean = w['word'].strip()
            if clean.endswith("'") and i + 1 < len(words):
                nxt = words[i + 1]
                w['word'] = clean + nxt['word'].strip()
                w['end'] = nxt['end']
                merged_words.append(w)
                i += 2
                continue
            if clean.startswith("'") and merged_words:
                prev = merged_words[-1]
                prev['word'] = prev['word'].strip() + clean
                prev['end'] = w['end']
                i += 1
                continue
            merged_words.append(w)
            i += 1
        words = merged_words
        groups = []
        current_group = []
        current_word_timings = []
        current_start = None
        current_end = None
        for i, word in enumerate(words):
            word_text = word['word'].strip().lower()
            if current_start is None:
                current_start = word['start']
            clean_word = word['word'].strip().replace('\\', '')
            current_group.append(clean_word)
            current_word_timings.append({
                'word': clean_word,
                'start': word['start'],
                'end': word['end']
            })
            current_end = word['end']
            should_continue = False
            if i < len(words) - 1:
                next_word = words[i + 1]
                time_gap = next_word['start'] - word['end']
                if (word_text in short_words and time_gap < 0.3 and
                    len(current_group) < max_words):
                    should_continue = True
                elif (len(current_group) < max_words and time_gap < 0.2 and
                      len(' '.join(current_group + [next_word['word']])) < 30):
                    should_continue = True
            if not should_continue or i == len(words) - 1:
                groups.append({
                    'text': ' '.join(current_group),
                    'start': current_start,
                    'end': current_end,
                    'words': current_word_timings
                })
                current_group = []
                current_word_timings = []
                current_start = None
                current_end = None
        return groups

    def _add_smart_emojis(self, word_groups: List[Dict], language: str = "en", density: float = 0.4) -> List[Dict]:
        if not word_groups:
            return word_groups
        density = max(0.0, min(1.0, density))
        max_emoji_groups = int(len(word_groups) * density)
        if max_emoji_groups == 0 and density > 0:
            max_emoji_groups = 1
        enriched_groups = []
        used_emoji_groups = 0
        last_emoji = None
        for group in word_groups:
            enriched_group = dict(group)
            text = enriched_group.get('text', '').strip()
            if (
                used_emoji_groups >= max_emoji_groups
                or not text
                or self._contains_emoji(text)
                or len(text.split()) <= 1
            ):
                enriched_groups.append(enriched_group)
                continue
            emoji = self._pick_emoji_for_text(text, language, avoid_emoji=last_emoji)
            if emoji:
                enriched_group['text'] = f"{text} {emoji}"
                enriched_group['emoji'] = emoji
                used_emoji_groups += 1
                last_emoji = emoji
            enriched_groups.append(enriched_group)
        return enriched_groups

    def _pick_emoji_for_text(self, text: str, language: str = "en", avoid_emoji: Optional[str] = None) -> Optional[str]:
        normalized = re.sub(r"[^\w\s']", " ", text.lower(), flags=re.UNICODE)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        tokens = set(normalized.split())
        phrase_rules = [
            ("🤯", ["mind blown", "no way", "c est fou", "impossible"]),
            ("💡", ["pro tip", "top tip", "petit conseil", "astuce rapide"]),
            ("💰", ["make money", "earn money", "gagner de l argent"]),
        ]
        for emoji, phrases in phrase_rules:
            if avoid_emoji == emoji:
                continue
            if any(phrase in normalized for phrase in phrases):
                return emoji
        keyword_rules = [
            ("😂", {
                "funny", "joke", "hilarious", "laugh", "lol", "haha",
                "drôle", "blague", "mdr", "rire"
            }),
            ("🔥", {
                "viral", "insane", "crazy", "wild", "amazing", "epic", "best", "fire",
                "incroyable", "dingue", "ouf", "énorme"
            }),
            ("🤯", {
                "shocking", "unbelievable", "unexpected", "secret", "truth",
                "choquant", "hallucinant", "surprenant"
            }),
            ("💡", {
                "tip", "tips", "hack", "lesson", "learn", "advice", "strategy",
                "astuce", "conseil", "apprendre", "stratégie", "leçon"
            }),
            ("💰", {
                "money", "cash", "profit", "income", "revenue", "dollar", "million", "business",
                "argent", "revenu", "euros", "euro", "entreprise"
            }),
            ("🚀", {
                "grow", "growth", "scale", "launch", "boost", "explode", "success",
                "croissance", "lancer", "boost", "succès"
            }),
            ("⚠️", {
                "warning", "danger", "risk", "mistake", "avoid", "careful",
                "attention", "danger", "risque", "erreur", "évite", "evite"
            }),
            ("❤️", {
                "love", "heart", "beautiful", "amour", "aime", "coeur", "cœur"
            }),
            ("⚡", {
                "fast", "quick", "instant", "now", "today", "rapidement", "vite", "maintenant"
            }),
        ]
        for emoji, keywords in keyword_rules:
            if avoid_emoji == emoji:
                continue
            if tokens.intersection(keywords):
                return emoji
        if avoid_emoji != "🤔" and "?" in text:
            return "🤔"
        if avoid_emoji != "🔥" and "!" in text:
            return "🔥"
        return None

    def _contains_emoji(self, text: str) -> bool:
        for char in text:
            code_point = ord(char)
            if (
                0x1F300 <= code_point <= 0x1FAFF
                or 0x2600 <= code_point <= 0x26FF
                or 0x2700 <= code_point <= 0x27BF
            ):
                return True
        return False


if __name__ == "__main__":
    generator = SubtitleGenerator()