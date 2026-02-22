# VideoMaster: Apple Silicon Video Processor 🎬

![Python](https://img.shields.io/badge/python-3.x-blue.svg)
![macOS](https://img.shields.io/badge/os-macOS-silver)
![FFmpeg](https://img.shields.io/badge/ffmpeg-required-green)

**VideoMaster** is a production-ready, CLI-based video processing tool specifically optimized for Mac computers featuring Apple Silicon (M1, M2, M3 series). It utilizes the hardware-accelerated Media Engine (`hevc_videotoolbox`) to split, upscale, and enhance massive video files with zero loss in visual fidelity and incredibly small file sizes.

## ✨ Key Features

* **Apple Silicon Hardware Acceleration:** Leverages `hevc_videotoolbox` to encode videos at blazing speeds without maxing out CPU thermals.
* **Dynamic Bitrate Scaling:** Forget hardcoded limits. Choose a base resolution and input any custom multiplier (e.g., `2x`, `3.5x`, `6x`) to perfectly balance file size and visual fidelity on the fly.
* **Smart Disk Protection:** Continuously monitors your hard drive. If disk usage hits 98%, the script automatically halts processing to prevent macOS system crashes.
* **Targeted Segmentation:** Easily slice long videos (e.g., 4-hour recordings) into equal chunks. Process the whole video or select specific segments (e.g., `1-3, 5, 8-10`).
* **Extreme Resolution Upscaling:** Supports native 1080p all the way up to experimental **16K (8640p)** using high-fidelity Lanczos filtering.
* **A/V Enhancement Pipeline:** Optional built-in filters to sharpen visual textures, boost color saturation, and dynamically compress audio for clear voiceovers over loud gameplay.
* **QuickTime Compatibility:** Enforces `hvc1` tags and `yuv420p` pixel formats to ensure HEVC files play natively on macOS without black screens.
* **Terminal UX:** Includes interactive CLI elements, live progress bars, tab-completion for file paths, and graceful keyboard interrupts.

## ⚙️ Prerequisites

You need `python3` and `ffmpeg` installed on your Mac. The easiest way to install FFmpeg is via Homebrew:

`brew install ffmpeg`

## 🚀 Quick Start

1. Clone or download this repository.
2. Make the script executable (optional but recommended):
   `chmod +x videomaster.py`
3. Run the script:
   `python3 videomaster.py`
4. Drag and drop your video file into the terminal (or type the path using the TAB key to autocomplete).
5. Follow the interactive prompts to split, scale, and multiply your bitrate.

## 🧠 Under the Hood: Dynamic Bitrate Architecture

To maintain "Mastering" quality while giving you full control over file sizes, VideoMaster uses a **Base Bitrate * Multiplier** approach:
- **1080p Base:** 30 Mbps
- **2K Base:** 50 Mbps
- **4K Base:** 100 Mbps
- **8K / 16K Base:** 200+ Mbps

When prompted, you can enter multipliers like `1` (for base storage), `2` (for high quality), or `6x` (for extreme, ProRes-like archival quality). *(Warning: Pushing beyond 8K or using massive multipliers may hit Apple API hardware limits depending on your exact M-series chip).*

## 🛡️ License

Distributed under the MIT License. See `LICENSE` for more information.

---
*Built to tame massive gameplay files, render faster, and keep your disk space sane.*