# VideoMaster

Split a local recording into numbered clips on an Apple Silicon Mac, or transcode the whole file. VideoMaster uses FFmpeg and Apple’s VideoToolbox encoders. Choose **HEVC in MP4** for delivery or **ProRes 422 HQ in MOV** for editing. Both formats re-encode the video.

## First export

You need Python 3.9 or newer and FFmpeg with its companion tool, `ffprobe`. VideoMaster uses the Python standard library. On a Mac with Homebrew, you can install the prerequisites with `brew install python ffmpeg`.

Download the project and start the guided exporter. If you already have the project, run the last command from its directory.

```sh
git clone https://github.com/mustafaer/VideoMaster.git
cd VideoMaster
python3 videomaster.py
```

Drop a video from Finder into the path prompt and press Enter. You can also type the path or use Tab to complete it. Press Enter at the remaining prompts to export the whole recording as one HEVC file with source dimensions, source frame timing, and enhancements off.

For an input named `match.mov`, the first default export lands here, relative to the directory where you launched the script.

```text
VideoMaster_Exports/
└── match_source_hevc/
    └── Part_0001.mp4
```

## Command-line examples

**Keep the opening and closing clips.** For a 25-minute recording, the following command saves minutes 0–10 as `Part_0001.mp4` and minutes 20–25 as `Part_0003.mp4`. At 1080p, the 1.5× multiplier sets a 45 Mbps video target.

```sh
python3 videomaster.py "match.mov" --segment-minutes 10 --segments 1,3 \
  --resolution 1080p --bitrate-multiplier 1.5x
```

Segment numbers start at 1. Use positive whole minutes for the segment length. A selection can contain numbers, inclusive ranges such as `1-3,5`, or `all`. Omitting `--segments` exports every part. Omitting `--segment-minutes` treats the entire video as part 1.

**Prepare an editing copy.** This exports the entire recording as ProRes 422 HQ at a constant 30000/1001 fps (about 29.97 fps) under `Editing Exports/`. Omit `--fps` to keep the source timing, including variable frame rates.

```sh
python3 videomaster.py "match.mov" --codec prores \
  --fps 30000/1001 --output-dir "Editing Exports"
```

Run `python3 videomaster.py --help` for the full option list.

## Picture and sound

| `--resolution` | Output canvas | HEVC target at 1× |
| --- | --- | ---: |
| `source` (default) | Source dimensions | 30 Mbps |
| `1080p` | 1920 × 1080 | 30 Mbps |
| `2k` | 2560 × 1440 | 50 Mbps |
| `4k` | 3840 × 2160 | 100 Mbps |
| `8k` | 7680 × 4320 | 200 Mbps |
| `16k` | 15360 × 8640 | 400 Mbps |

With `source`, an odd width or height gains one pixel of padding. Fixed canvases use Lanczos scaling and center the picture with black bars wherever needed to preserve its display aspect ratio. For example, a portrait clip can stay portrait with `source`, or fit inside a landscape frame with `1080p`.

- **HEVC** uses 8-bit 4:2:0 video with the QuickTime-compatible `hvc1` tag. The bitrate multiplier applies to HEVC only. Actual bitrate and file size depend on the footage and encoder.
- **ProRes** uses the 10-bit 4:2:2 HQ profile. Allow extra disk space for editing copies.
- **Tracks** include the first video stream (excluding cover art) and all audio streams, each encoded as AAC with a 320 kbps target. Silent footage stays silent. Exports omit subtitles, chapters, attachments, and additional video angles.
- **Enhancements** are optional. Add `--enhance` to sharpen the image, raise saturation, compress audio dynamics, and boost volume.

## Recover an interrupted export

Look for the `Saved … of …` summary and the run directory it names. Each finished clip is moved into place after `ffprobe` confirms the expected codec, a positive duration, and at least one encoded frame. On an encoder error or cancellation, VideoMaster stops the batch and cleans up the current temporary clip.

To retry, use the same input, segment length, and encoding options, then select the missing part numbers. For example, if part 1 of the HEVC example succeeded and part 3 failed, rerun it with `--segments 3`. The retry writes `Part_0003.mp4` in `match_1080p_hevc_2/`. Earlier clips keep their original paths, including when two exports run concurrently.

| Symptom | What to check |
| --- | --- |
| Missing `ffmpeg` or `ffprobe` | Run `ffmpeg -version` and `ffprobe -version` in the same terminal used to launch VideoMaster. Both executables must be on `PATH`. |
| FFmpeg cannot open the encoder | Run `ffmpeg -hide_banner -encoders` and look for `hevc_videotoolbox` or `prores_videotoolbox` for your chosen codec. If it is listed, try a smaller canvas to check whether frame size is the limit. |
| Output volume reaches 98% usage | Free space or choose another volume with `--output-dir`, then retry the missing parts. The exporter checks the destination before and after each encode and at half-second intervals during encoding. |

For scripts, exit status `0` means the batch completed. Status `1` indicates an input or export failure, `2` a command-line argument error, `130` Ctrl-C or a closed input prompt, and `143` SIGTERM.

## Tests and hardware coverage

From the project directory, run the regression suite with `python3 -m unittest -v`. On a Mac with both hardware encoders, the following command also runs the HEVC and ProRes export tests.

```sh
VIDEOMASTER_HARDWARE_TESTS=1 python3 -m unittest -v
```

Both codecs were verified with FFmpeg 9.0.2 on Apple Silicon, including two-frame exports at 2K, 4K, and 8K. These checks cover encoder compatibility. Sustained 8K performance and the 16K preset remain unverified.

## License

VideoMaster is distributed under the [MIT License](LICENSE).
