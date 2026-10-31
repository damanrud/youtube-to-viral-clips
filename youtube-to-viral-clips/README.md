# YouTube to Viral Clips

Submagic free open source alternative that can run with local AI model using Ollama and OpenAI Whisper (no API key needed).

## Overview

This tool downloads YouTube videos, transcribes them using OpenAI Whisper, analyzes the content for viral potential using AI (Ollama/OpenAI/Anthropic), and automatically extracts the most engaging clips with optional subtitles.

## Features

- Automatic viral moment detection with customizable scoring threshold
- Multi-language support (transcription and subtitles)
- Parallel clip extraction for faster processing
- Multiple AI providers: Ollama (local), OpenAI, Anthropic
- Customizable subtitle styles optimized for social media
- Karaoke word highlighting: the actively spoken word pops in color ("Viral Highlight" style and more)
- Vertical format option for TikTok/Shorts
- Split-stack layout for two-person podcasts: side-by-side speakers are stacked vertically
- Web interface

## Requirements

- Python
- FFmpeg
- Ollama for local AI or API keys for OpenAI/Anthropic
