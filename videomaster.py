#!/usr/bin/env python3
"""Split and transcode local videos using Apple's VideoToolbox encoders."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from collections import deque
from dataclasses import dataclass
from fractions import Fraction
import glob
import json
import math
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

OUTPUT_FOLDER = Path("VideoMaster_Exports")
DISK_LIMIT_PERCENT = 98
POLL_SECONDS = 0.5
# Canvas width, height and base HEVC bitrate in Mbps
PRESETS = {
    "source": (0, 0, 30),
    "1080p": (1920, 1080, 30),
    "2k": (2560, 1440, 50),
    "4k": (3840, 2160, 100),
    "8k": (7680, 4320, 200),
    "16k": (15360, 8640, 400),
}
ENCODERS = {"hevc": "hevc_videotoolbox", "prores": "prores_videotoolbox"}


class ExportError(Exception):
    """A user-facing validation or export failure."""


@dataclass(frozen=True)
class Video:
    duration: float
    offset: float
    codec: str
    frames: int


@dataclass(frozen=True)
class Settings:
    codec: str
    resolution: str
    bitrate: int
    fps: str | None
    enhance: bool

    @property
    def extension(self):
        return ".mov" if self.codec == "prores" else ".mp4"


def probe(path: Path) -> Video:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "V:0", "-show_entries",
         "format=duration,start_time:stream=codec_name,width,height,duration,start_time,nb_frames",
         "-of", "json", str(path.resolve())],
        capture_output=True, text=True, errors="replace",
    )
    if result.returncode:
        raise ExportError(f"Cannot read {path.name}. {result.stderr.strip()}")
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    if not streams or not streams[0].get("width") or not streams[0].get("height"):
        raise ExportError(f"{path.name} has no playable video stream.")
    stream = streams[0]
    container = data.get("format", {})

    def timestamp(fields, key, default):
        value = fields.get(key, "N/A")
        return default if value == "N/A" else float(value)

    origin = timestamp(container, "start_time", 0.0)
    offset = max(0.0, timestamp(stream, "start_time", origin) - origin)
    duration = timestamp(stream, "duration", timestamp(container, "duration", 0.0) - offset)
    if not math.isfinite(duration) or duration <= 0:
        raise ExportError(f"{path.name} has no usable video duration.")
    frames = stream.get("nb_frames", "N/A")
    return Video(duration, offset, stream["codec_name"], 0 if frames == "N/A" else int(frames))


def parse_segments(text: str, count: int) -> Sequence[int]:
    if text.strip().lower() == "all":
        return range(1, count + 1)
    selected = set()
    for part in text.split(","):
        match = re.fullmatch(r"\s*([0-9]+)(?:\s*-\s*([0-9]+))?\s*", part)
        if not match:
            raise ExportError("Choose segments such as 1-3,5 or all.")
        first = int(match[1])
        last = int(match[2] or match[1])
        if not 1 <= first <= last <= count:
            raise ExportError(f"Segment {part.strip()} is outside the valid range 1–{count} or reversed.")
        selected.update(range(first, last + 1))
    return sorted(selected)


def positive_integer(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise ValueError("Enter a positive whole number.")
    return number


def frame_rate(value: str) -> str:
    try:
        rate = Fraction(value)
    except (ValueError, ZeroDivisionError):
        raise argparse.ArgumentTypeError("use a frame rate such as 60 or 30000/1001") from None
    if not 0 < rate.numerator < 2**31 or rate.denominator >= 2**31:
        raise argparse.ArgumentTypeError("frame rate is outside the supported range")
    return str(rate)


def clock_time(seconds: float) -> str:
    minutes, millis = divmod(round(seconds * 1000), 60_000)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{millis / 1000:06.3f}"


def hevc_bitrate(multiplier: str, preset: str) -> int:
    try:
        value = float(multiplier.lower().removesuffix("x")) * PRESETS[preset][2] * 1_000_000
    except ValueError:
        value = 0
    if not 1 <= value < 2**63:
        raise ExportError("Enter a finite positive multiplier yielding a valid HEVC bitrate.")
    return round(value)


def input_path(raw: str) -> Path:
    path = Path(raw.strip()).expanduser()
    if path.is_file():
        return path
    try:
        parts = shlex.split(raw)
    except ValueError:
        parts = []
    if len(parts) == 1:
        return Path(parts[0]).expanduser()
    return path


def enable_completion():
    try:
        import readline
    except ImportError:
        return

    def complete(text, index):
        matches = glob.glob(glob.escape(str(Path(text).expanduser()) if text else "") + "*")
        return matches[index] if index < len(matches) else None

    readline.set_completer_delims("")
    readline.set_completer(complete)
    binding = "bind ^I rl_complete" if "libedit" in (readline.__doc__ or "") else "tab: complete"
    readline.parse_and_bind(binding)


def ask(label, convert, default=None):
    while True:
        raw = input(label).strip()
        if not raw and default is not None:
            return default
        try:
            return convert(raw)
        except (ValueError, ExportError) as exc:
            print(exc, file=sys.stderr)


def choose(label, choices, default):
    def convert(raw):
        if raw.lower() not in choices:
            raise ValueError(f"Choose one of {', '.join(choices)}.")
        return choices[raw.lower()]
    return ask(label, convert, default)


def guided_settings(video: Video):
    minutes = ask("Segment length in whole minutes [Enter for whole video]: ", positive_integer, 0)
    length = min(minutes * 60 or video.duration, video.duration)
    count = math.ceil(video.duration / length)
    segments = ask(f"Segments 1–{count}, e.g. 1-3,5 [all]: ",
                   lambda s: parse_segments(s, count), range(1, count + 1))
    codec = choose("Codec (1 HEVC, 2 ProRes 422 HQ) [1]: ", {"1": "hevc", "2": "prores"}, "hevc")
    choices = {str(i): name for i, name in enumerate(PRESETS)}
    menu = ", ".join(f"{i} {name}" for i, name in choices.items())
    preset = choose(f"Resolution ({menu}) [0]: ", choices, "source")
    bitrate = 0
    if codec == "hevc":
        bitrate = ask(f"HEVC bitrate multiplier ({PRESETS[preset][2]} Mbps base) [1x]: ",
                      lambda s: hevc_bitrate(s, preset), hevc_bitrate("1", preset))
    enhance = choose("Apply picture and sound enhancements? [y/N]: ",
                     {"y": True, "yes": True, "n": False, "no": False}, False)
    return length, segments, Settings(codec, preset, bitrate, None, enhance)


def video_filters(settings: Settings) -> str:
    width, height, _ = PRESETS[settings.resolution]
    if width:
        # Fit display aspect ratio (including anamorphic pixels) into a square-pixel canvas.
        filters = [f"scale=w='max(2,round(min({width},{height}*dar)/2)*2)':"
                   f"h='max(2,round(min({height},{width}/dar)/2)*2)':flags=lanczos",
                   "setsar=1", f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"]
    else:
        filters = ["pad=ceil(iw/2)*2:ceil(ih/2)*2"]
    if settings.enhance:
        filters.extend(["unsharp=5:5:0.8:5:5:0.0", "eq=saturation=1.15"])
    return ",".join(filters)


def encode_command(source: Path, dest: Path, start: float, duration: float, settings: Settings) -> list[str]:
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-n", "-v", "error", "-xerror",
           "-nostats", "-progress", "pipe:1", "-ss", f"{start:.6f}", "-i", str(source),
           "-t", f"{duration:.6f}", "-map", "0:V:0", "-map", "0:a?", "-map_chapters", "-1",
           "-c:v", ENCODERS[settings.codec], "-vf", video_filters(settings)]
    if settings.codec == "hevc":
        cmd += ["-b:v", str(settings.bitrate), "-tag:v", "hvc1", "-pix_fmt", "yuv420p"]
    else:
        cmd += ["-profile:v", "hq", "-pix_fmt", "p210le"]
    if settings.fps:
        cmd += ["-fps_mode", "cfr", "-r", str(settings.fps)]
    else:
        cmd += ["-fps_mode", "passthrough", "-enc_time_base:v", "demux"]
    cmd += ["-c:a", "aac", "-b:a", "320k"]
    if settings.enhance:
        cmd += ["-af", "compand=attacks=0:points=-30/-30|-20/-15|-10/-10|0/-8,volume=1.2"]
    return cmd + [str(dest)]


def check_disk(directory: Path) -> None:
    usage = shutil.disk_usage(directory)
    percent = 100 * (1 - usage.free / usage.total)
    if percent >= DISK_LIMIT_PERCENT:
        raise ExportError(f"Output volume is {percent:.1f}% full. Free space before retrying.")


def render(command: list[str], directory: Path, duration: float) -> None:
    check_disk(directory)
    errors = deque(maxlen=24)
    elapsed = 0.0

    def read_progress(pipe):
        nonlocal elapsed
        for raw in pipe:
            line = raw.rstrip()
            key, separator, value = line.partition("=")
            if key == "out_time_us" and value.isdigit():
                elapsed = int(value) / 1_000_000
            elif line and (not separator or not re.fullmatch(r"[a-z0-9_]+", key)):
                errors.append(line[:500])

    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, errors="replace") as process:
        reader = threading.Thread(target=read_progress, args=(process.stdout,))
        try:
            reader.start()
            while process.poll() is None:
                try:
                    process.wait(timeout=POLL_SECONDS)
                except subprocess.TimeoutExpired:
                    check_disk(directory)
                    if sys.stderr.isatty():
                        print(f"\r  {min(99.9, 100 * elapsed / duration):5.1f}%", end="", file=sys.stderr, flush=True)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if reader.ident is not None:
                reader.join()
            if sys.stderr.isatty():
                print("\r        \r", end="", file=sys.stderr, flush=True)
    if process.returncode:
        detail = "\n".join(errors) or "Check the encoder, source video, and output volume."
        raise ExportError(f"FFmpeg exited with code {process.returncode}.\n{detail}")
    check_disk(directory)


def create_run_directory(root: Path, source: Path, settings: Settings) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    # Leave room for the preset, codec and counter within a filesystem component.
    stem = source.stem.encode("utf-8")[:160].decode("utf-8", errors="ignore")
    base = f"{stem}_{settings.resolution}_{settings.codec}"
    number = 1
    while True:
        path = root / (base if number == 1 else f"{base}_{number}")
        try:
            path.mkdir()
            return path
        except FileExistsError:
            number += 1


def export(source: Path, video: Video, length: float, segments: Sequence[int],
           settings: Settings, root: Path) -> None:
    directory = create_run_directory(root, source, settings)
    print(f"Exporting {len(segments)} segment(s) to {directory}", flush=True)
    completed = 0
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix=".partial-", dir=directory) as tmp:
            for index in segments:
                start = (index - 1) * length
                duration = min(length, video.duration - start)
                name = f"Part_{index:04d}{settings.extension}"
                partial = Path(tmp) / name
                print(f"Rendering {name} ({clock_time(duration)})", flush=True)
                cmd = encode_command(source, partial, start + video.offset, duration, settings)
                render(cmd, directory, duration)
                output = probe(partial)
                if output.codec != settings.codec or output.frames < 1:
                    raise ExportError(f"{name} failed output validation.")
                partial.rename(directory / name)
                completed += 1
                print(f"100%  Saved {name}", flush=True)
    finally:
        elapsed = clock_time(time.monotonic() - started)
        print(f"Saved {completed} of {len(segments)} segment(s) in {directory} ({elapsed} elapsed)", flush=True)


def arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__,
                                     epilog="Run without arguments for guided export.")
    parser.add_argument("video", type=Path, nargs="?", help="local source video")
    parser.add_argument("--segment-minutes", type=positive_integer, help="whole minutes per segment (default: whole video)")
    parser.add_argument("--segments", default="all", help="all or a selection such as 1-3,5")
    parser.add_argument("--codec", choices=ENCODERS, default="hevc")
    parser.add_argument("--resolution", type=str.lower, choices=PRESETS, default="source")
    parser.add_argument("--bitrate-multiplier", help="HEVC target bitrate multiplier, e.g. 1.5x")
    parser.add_argument("--fps", type=frame_rate, help="constant frame rate, e.g. 60 or 30000/1001 (default: source timing)")
    parser.add_argument("--enhance", action="store_true", help="apply video and audio filters")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_FOLDER)
    args = parser.parse_args(argv)
    if argv and args.video is None:
        parser.error("provide the source video when using options")
    if args.codec == "prores" and args.bitrate_multiplier is not None:
        parser.error("--bitrate-multiplier applies only to HEVC")
    return args


def main(argv=None):
    args = arguments(sys.argv[1:] if argv is None else argv)
    try:
        for tool in ("ffmpeg", "ffprobe"):
            if not shutil.which(tool):
                raise ExportError(f"Missing {tool}. Install FFmpeg and add it to PATH.")
        if args.video is None:
            enable_completion()
            source = input_path(input("Video path (type, drop, or Tab to complete): "))
        else:
            source = args.video.expanduser()
        if not source.is_file():
            raise ExportError(f"Video file does not exist: {source}")
        source = source.resolve()
        video = probe(source)
        print(f"{source.name} • {clock_time(video.duration)}")
        if args.video is None:
            length, segments, settings = guided_settings(video)
        else:
            length = min(args.segment_minutes * 60, video.duration) if args.segment_minutes else video.duration
            segments = parse_segments(args.segments, math.ceil(video.duration / length))
            multiplier = "1" if args.bitrate_multiplier is None else args.bitrate_multiplier
            bitrate = hevc_bitrate(multiplier, args.resolution) if args.codec == "hevc" else 0
            settings = Settings(args.codec, args.resolution, bitrate, args.fps, args.enhance)
        export(source, video, length, segments, settings, args.output_dir.expanduser().resolve())
    except (ExportError, OSError, ValueError, OverflowError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nExport cancelled.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    # Let batch schedulers terminate an export through the same cleanup paths.
    def terminate(signum, _frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, terminate)
    sys.exit(main())
