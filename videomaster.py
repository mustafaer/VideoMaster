#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
VideoMaster - Apple Silicon Optimized Video Processor
Hardware-accelerated video splitting, upscaling, and enhancement tool.
Features dual-engine encoding: HEVC (H.265) with dynamic bitrate and ProRes for professional editing.
"""

import subprocess
import os
import re
import time
import sys
import math
import shutil

# --- CONFIGURATIONS (BASE BITRATES FOR HEVC) ---
RESOLUTION_CONFIGS = {
    "1": {"name": "1080p", "scale": None, "base_bitrate_mbps": 30},
    "2": {"name": "2K", "scale": "2560:1440", "base_bitrate_mbps": 50},
    "3": {"name": "4K", "scale": "3840:2160", "base_bitrate_mbps": 100},
    "4": {"name": "8K", "scale": "7680:4320", "base_bitrate_mbps": 200},
    "5": {"name": "16K", "scale": "15360:8640", "base_bitrate_mbps": 400}
}

DISK_LIMIT_PERCENT = 98.0
OUTPUT_FOLDER = "VideoMaster_Exports"

# --- macOS TAB COMPLETION ---
try:
    import readline
    import glob
    if 'libedit' in readline.__doc__:
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")

    def complete_path(text: str, state: int):
        text = text.replace("'", "").replace('"', "")
        return (glob.glob(text + '*') + [None])[state]

    readline.set_completer(complete_path)
    readline.set_completer_delims(' \t\n;')
except ImportError:
    pass

# --- HELPER FUNCTIONS ---
def check_dependencies() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["ffprobe", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except FileNotFoundError:
        return False

def format_time(seconds: float) -> str:
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

def get_disk_usage() -> float:
    total, used, free = shutil.disk_usage(".")
    return (used / total) * 100

def get_video_duration(video_path: str) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", video_path]
    try:
        return float(subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True).strip())
    except Exception:
        return 0.0

def parse_segments(input_str: str, max_segment: int) -> list:
    segments = set()
    parts = input_str.replace(' ', '').split(',')
    for part in parts:
        if '-' in part:
            try:
                start, end = part.split('-')
                for i in range(int(start), int(end) + 1):
                    if 1 <= i <= max_segment: segments.add(i)
            except ValueError: continue
        else:
            try:
                i = int(part)
                if 1 <= i <= max_segment: segments.add(i)
            except ValueError: continue
    return sorted(list(segments))

# --- MAIN EXECUTION ---
def main():
    if not check_dependencies():
        print("❌ ERROR: 'ffmpeg' or 'ffprobe' is not installed or not in PATH.")
        print("Please install them using 'brew install ffmpeg' on macOS.")
        sys.exit(1)

    print("="*65)
    print("🎬 VIDEOMASTER - APPLE SILICON OPTIMIZED PROCESSOR")
    print("="*65)

    video_input = input("\n🎥 Video Path (Drag & Drop or use TAB): ")
    video_path = video_input.strip().strip("'\"").strip()

    if not os.path.exists(video_path):
        print("❌ ERROR: The specified video file was not found.")
        sys.exit(1)

    total_seconds = get_video_duration(video_path)
    if total_seconds == 0.0:
        print("❌ ERROR: Could not read video duration. File might be corrupted or unsupported.")
        sys.exit(1)

    print(f"\n📊 Analysis: Total duration is {format_time(total_seconds)}")

    try:
        length_input = input("⏳ Segment length (in minutes): ")
        segment_length_min = int(length_input.strip())
        total_segments = math.ceil((total_seconds / 60) / segment_length_min)

        print(f"💡 Info: This video can be split into {total_segments} segments of {segment_length_min} minutes each.")
        segment_input = input(f"✂️ Target segments to process (e.g., 1-5, 8) [Max: {total_segments}]: ")
        target_segments = parse_segments(segment_input, total_segments)

        if not target_segments:
            print("❌ ERROR: Invalid segment selection.")
            sys.exit(1)

        print("\n⚙️  Select Target Codec Engine:")
        print("1- HEVC / H.265 (High Quality, Small Size - Best for YouTube/Storage)")
        print("2- Apple ProRes (Visually Lossless, Huge Size - Best for Heavy Editing)")
        codec_choice = input("Your choice (1/2): ").strip()
        is_prores = (codec_choice == "2")

        print("\n🚀 Select Base Resolution:")
        for key, val in RESOLUTION_CONFIGS.items():
            print(f"{key}- {val['name'].ljust(6)}")
        res_choice = input("Your choice (1/2/3/4/5): ").strip()

        config = RESOLUTION_CONFIGS.get(res_choice, RESOLUTION_CONFIGS["1"])
        scale_val = config["scale"]
        res_tag = config["name"]

        # DYNAMIC BITRATE MULTIPLIER (Only for HEVC)
        target_bitrate_str = None
        multiplier_tag = ""

        if not is_prores:
            mult_input = input(f"\n🔥 Enter Bitrate Multiplier for {res_tag} (Base: {config['base_bitrate_mbps']} Mbps) [Default: 1]: ").strip()
            try:
                clean_mult = mult_input.lower().replace('x', '')
                bitrate_multiplier = float(clean_mult) if clean_mult else 1.0
            except ValueError:
                print("⚠️ Warning: Invalid multiplier format. Defaulting to 1x.")
                bitrate_multiplier = 1.0

            final_bitrate_mbps = int(config["base_bitrate_mbps"] * bitrate_multiplier)
            target_bitrate_str = f"{final_bitrate_mbps}M"
            multiplier_tag = f"_{bitrate_multiplier}x" if bitrate_multiplier % 1 != 0 else f"_{int(bitrate_multiplier)}x"

        enhance_input = input("\n✨ Apply Visual & Audio Enhancements? (Y/N): ").strip().upper()
        use_enhancements = (enhance_input == 'Y')

    except ValueError:
        print("❌ ERROR: Numeric input required.")
        sys.exit(1)

    segment_seconds = segment_length_min * 60
    time_pattern = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")

    codec_display_name = "ProRes 422" if is_prores else f"HEVC {target_bitrate_str}"
    print(f"\n⚙️  Processing Started... Mode: {res_tag} | Engine: {codec_display_name} | Enhancements: {'ON' if use_enhancements else 'OFF'}")

    os.makedirs(OUTPUT_FOLDER, exist_ok=True)
    print(f"📁 All outputs will be saved to: ./{OUTPUT_FOLDER}/")
    print("-" * 65)

    for seg_no in target_segments:
        if get_disk_usage() >= DISK_LIMIT_PERCENT:
            print(f"\n🛑 CRITICAL WARNING: Disk usage reached {DISK_LIMIT_PERCENT}%. Process halted to prevent system crash.")
            break

        start_time_sec = (seg_no - 1) * segment_seconds

        # Extension and filename logic
        ext = "mov" if is_prores else "mp4"
        codec_tag = "ProRes" if is_prores else f"HEVC{multiplier_tag}"
        output_filename = f"Master_{res_tag}_{codec_tag}_Part_{seg_no}.{ext}"
        output_filepath = os.path.join(OUTPUT_FOLDER, output_filename)

        cmd = [
            "ffmpeg", "-hide_banner",
            "-ss", str(start_time_sec), "-i", video_path,
            "-t", str(segment_seconds)
        ]

        # Engine specific arguments
        if is_prores:
            cmd.extend([
                "-c:v", "prores_videotoolbox",
                "-profile:v", "3",          # ProRes 422 HQ
                "-fps_mode", "cfr", "-r", "60"
            ])
        else:
            cmd.extend([
                "-c:v", "hevc_videotoolbox",
                "-b:v", target_bitrate_str,
                "-tag:v", "hvc1",
                "-pix_fmt", "yuv420p",
                "-fps_mode", "cfr", "-r", "60"
            ])

        # Video Filters
        vf = []
        if scale_val:
            vf.append(f"scale={scale_val}:flags=lanczos")
        if use_enhancements:
            vf.append("unsharp=5:5:0.8:5:5:0.0,eq=saturation=1.15")

        if vf:
            cmd.extend(["-vf", ",".join(vf)])

        # Audio Filters
        if use_enhancements:
            cmd.extend(["-c:a", "aac", "-b:a", "320k", "-af", "compand=attacks=0:points=-30/-30|-20/-15|-10/-10|0/-8,volume=1.2"])
        else:
            cmd.extend(["-c:a", "aac", "-b:a", "320k"])

        cmd.extend([output_filepath, "-y"])

        print(f"💎 Rendering: {output_filename}")
        start_t = time.time()

        try:
            process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, universal_newlines=True)
            error_log = ""

            for line in process.stderr:
                error_log += line
                match = time_pattern.search(line)
                if match:
                    current_sec = (float(match.group(1)) * 3600) + (float(match.group(2)) * 60) + float(match.group(3))
                    progress_pct = min((current_sec / segment_seconds) * 100, 100)

                    fill_count = int(25 * progress_pct / 100)
                    bar = '█' * fill_count + '-' * (25 - fill_count)
                    sys.stdout.write(f"\r  └─ [{bar}] {progress_pct:.1f}% | Disk: {get_disk_usage():.1f}%")
                    sys.stdout.flush()

            process.wait()

            if process.returncode != 0:
                print(f"\n❌ FFmpeg Execution Error (Code: {process.returncode}). Recent Logs:\n{error_log[-600:]}\n")
            else:
                elapsed_time = format_time(time.time() - start_t)
                print(f"\n✅ Completed: {output_filename} (Elapsed: {elapsed_time})\n")

        except Exception as e:
            print(f"\n❌ Unexpected Python Error: {e}")

    print("-" * 65 + f"\n🎉 All tasks completed! Outputs are in ./{OUTPUT_FOLDER}/. Final Disk Usage: {get_disk_usage():.1f}%")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n🛑 Process manually aborted by user. Exiting...")
        sys.exit(0)