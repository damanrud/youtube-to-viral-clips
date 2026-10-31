# YouTube to Viral Clips

Free open-source alternative to Submagic. Works with local AI via Ollama and OpenAI Whisper — no API key needed.

## What it does

Downloads YouTube videos, transcribes them with Whisper, finds viral moments using AI (Ollama, OpenAI, or Anthropic), and cuts the best clips with optional subtitles.

## Features

- Finds viral moments automatically (threshold is configurable)
- Supports multiple languages for transcription and subtitles
- Cuts clips in parallel — faster processing
- Works with Ollama (local), OpenAI, or Anthropic
- Subtitle styles made for social media
- Karaoke-style word highlighting (the active word pops in color)
- Vertical format for TikTok / Shorts
- Split-stack layout for two-person podcasts (speakers stacked vertically)
- Web interface

## Requirements

- Python
- FFmpeg
- Ollama (for local AI) or an API key for OpenAI / Anthropic

## Credits

The main prompts used in this project were taken from the [youtube-to-viral-clip](https://github.com/ВАШ_НИК/youtube-to-viral-clip) repository.
