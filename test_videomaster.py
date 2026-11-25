"""Run with python3 -m unittest -v. Set VIDEOMASTER_HARDWARE_TESTS=1 on a Mac for encoder tests."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import videomaster as vm


class InputTests(unittest.TestCase):
    def test_segments(self):
        self.assertEqual(vm.parse_segments("3, 1 - 2, 2", 4), [1, 2, 3])
        self.assertEqual(list(vm.parse_segments(" ALL ", 2)), [1, 2])
        for text in ("0", "-1", "2-1", "1,foo", "2,", "1-99999999999", ""):
            with self.subTest(text=text), self.assertRaises(vm.ExportError):
                vm.parse_segments(text, 4)

    def test_bitrates(self):
        self.assertEqual(vm.hevc_bitrate("0.125X", "1080p"), 3_750_000)
        for value in ("0", "-1", "1x2", "nan", "inf", "1e999", "1e-999", ""):
            with self.subTest(value=value), self.assertRaises(vm.ExportError):
                vm.hevc_bitrate(value, "source")

    def test_frame_rates(self):
        self.assertEqual(vm.frame_rate("30000/1001"), "30000/1001")
        self.assertEqual(vm.frame_rate("23.976"), "2997/125")
        for value in ("0", "-3", "inf", "1/0", "9999999999"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                vm.frame_rate(value)

    def test_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "editor's clip.mp4"
            path.touch()
            for raw in (str(path), f'"{path}"', str(path).replace("'", "\\'").replace(" ", "\\ ")):
                self.assertEqual(vm.input_path(raw), path)

    def test_guided_validation(self):
        answers = ["0", "1", "1,3", "2", "9", "1", "z", "1", "nan", "2x", "y"]
        with patch("builtins.input", side_effect=answers), redirect_stderr(io.StringIO()):
            length, parts, settings = vm.guided_settings(vm.Video(60.5, 0, "hevc", 10))
        self.assertEqual((length, parts), (60, [2]))
        self.assertEqual(settings, vm.Settings("hevc", "1080p", 60_000_000, None, True))


class ExportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "example.mov"
        self.video = vm.Video(61.25, 0, "hevc", 24)
        self.settings = vm.Settings("hevc", "source", 30_000_000, None, False)

    def test_concurrent_runs_have_distinct_directories(self):
        with ThreadPoolExecutor(8) as pool:
            paths = list(pool.map(lambda _: vm.create_run_directory(self.root, self.source, self.settings), range(16)))
        self.assertEqual(len(set(paths)), 16)

    def test_failed_second_segment_preserves_first(self):
        def render(cmd, directory, duration):
            Path(cmd[-1]).write_bytes(b"encoded bytes")
            if duration != 60:
                self.assertEqual(duration, 1.25)
                raise vm.ExportError("encode failed")

        with patch.object(vm, "render", side_effect=render), patch.object(vm, "probe", return_value=self.video), redirect_stdout(io.StringIO()) as output:
            with self.assertRaisesRegex(vm.ExportError, "encode failed"):
                vm.export(self.source, self.video, 60, [1, 2], self.settings, self.root)
        self.assertIn("Saved 1 of 2", output.getvalue())
        directory, = self.root.iterdir()
        published, = directory.iterdir()
        self.assertEqual(published.name, "Part_0001.mp4")

    def test_incomplete_file_fails_verification(self):
        def render(cmd, *_):
            Path(cmd[-1]).touch()
        with patch.object(vm, "render", side_effect=render), patch.object(vm, "probe", return_value=vm.Video(1, 0, "hevc", 0)), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(vm.ExportError, "validation"):
                vm.export(self.source, self.video, 60, [1], self.settings, self.root)
        self.assertEqual(list(self.root.rglob("*.mp4")), [])

    def test_failed_encoder_exposes_diagnostics(self):
        cmd = [sys.executable, "-c", "import sys; sys.stderr.write('Encoder unavailable\\n'); sys.exit(7)"]
        with self.assertRaisesRegex(vm.ExportError, "(?s)code 7.*Encoder unavailable"):
            vm.render(cmd, self.root, 1)

    def test_disk_limit_during_silent_encode(self):
        cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
        with patch.object(vm, "POLL_SECONDS", 0.02), patch.object(vm, "check_disk", side_effect=[None, vm.ExportError("full")]) as check:
            with self.assertRaisesRegex(vm.ExportError, "full"):
                vm.render(cmd, self.root, 1)
        self.assertEqual(check.call_count, 2)

    def test_disk_limit_before_encode(self):
        with patch("shutil.disk_usage", return_value=shutil._ntuple_diskusage(100, 98, 2)), patch("subprocess.Popen") as popen:
            with self.assertRaisesRegex(vm.ExportError, "98.0%"):
                vm.render([], self.root, 1)
        popen.assert_not_called()


class MediaTests(unittest.TestCase):
    def setUp(self):
        if not all(shutil.which(t) for t in ("ffmpeg", "ffprobe")):
            self.skipTest("Requires FFmpeg and ffprobe")
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.script = Path(vm.__file__).resolve()

    def fixture(self, name, *args):
        path = self.root / name
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", *args, str(path)], check=True, timeout=60)
        return path

    def cli(self, *args, answers=None):
        return subprocess.run([sys.executable, self.script, *args], input=answers or "",
                              cwd=self.root, text=True, capture_output=True, timeout=90)

    def streams(self, path):
        data = subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-of", "json", path])
        return json.loads(data)["streams"]

    def test_audio_only_rejected(self):
        path = self.fixture("audio.m4a", "-i", "sine=d=0.1", "-c:a", "aac")
        result = self.cli(path)
        self.assertEqual(result.returncode, 1)
        self.assertIn("no playable video", result.stderr)
        self.assertFalse((self.root / vm.OUTPUT_FOLDER).exists())

    def test_fixed_canvas_preserves_picture_shape(self):
        settings = vm.Settings("hevc", "1080p", 30_000_000, None, False)
        for size, sar, edge in [("240x320", "1", (50, 540)), ("320x240", "2", (960, 50))]:
            with self.subTest(size=size, sar=sar):
                image = subprocess.check_output([
                    "ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"color=white:s={size},setsar={sar}",
                    "-frames:v", "1", "-vf", vm.video_filters(settings), "-pix_fmt", "gray",
                    "-f", "rawvideo", "-",
                ])
                self.assertEqual(len(image), 1920 * 1080)
                x, y = edge
                self.assertLess(image[y * 1920 + x], 20)
                self.assertGreater(image[540 * 1920 + 960], 230)

    def test_interactive_cancel(self):
        result = self.cli()
        self.assertEqual(result.returncode, 130)

    def test_interrupted_encode_removes_partial_and_reaps_child(self):
        source = self.fixture("source.mov", "-i", "testsrc2=d=0.2", "-c:v", "mpeg4")
        # A sleeping encoder makes interruption deterministic on machines without VideoToolbox.
        encoder = self.root / "ffmpeg"
        encoder.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys, time\n"
            "from pathlib import Path\n"
            "Path(sys.argv[-1]).write_bytes(b'incomplete')\n"
            "Path('encoder.pid').write_text(str(os.getpid()))\n"
            "time.sleep(20)\n"
        )
        encoder.chmod(0o755)
        env = dict(os.environ, PATH=f"{self.root}{os.pathsep}{os.environ['PATH']}")
        marker = self.root / "encoder.pid"
        for sig in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(sig=sig), subprocess.Popen(
                [sys.executable, self.script, source], cwd=self.root, env=env,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            ) as process:
                try:
                    deadline = time.monotonic() + 5
                    while not marker.exists() and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertTrue(marker.exists(), "encoder did not start")
                    process.send_signal(sig)
                    output, error = process.communicate(timeout=5)
                    self.assertEqual(process.returncode, 128 + sig, error)
                    self.assertIn("Saved 0 of 1", output)
                    self.assertFalse(list(self.root.rglob(".partial-*")))
                    self.assertFalse(list(self.root.rglob("*.mp4")))
                    with self.assertRaises(ProcessLookupError):
                        os.kill(int(marker.read_text()), 0)
                finally:
                    if process.poll() is None:
                        process.kill()
                    marker.unlink(missing_ok=True)

    @unittest.skipUnless(os.environ.get("VIDEOMASTER_HARDWARE_TESTS") == "1", "Requires Apple hardware encoding")
    def test_hardware_exports(self):
        path = self.fixture("two parts.mp4", "-i", "testsrc2=size=320x240:rate=24",
                            "-f", "lavfi", "-i", "sine", "-t", "61.25",
                            "-map", "0:v", "-map", "1:a", "-map", "1:a",
                            "-c:v", "mpeg4", "-c:a", "aac")
        result = self.cli(answers=f"{path}\n1\nall\n1\n0\n1\nn\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Saved 2 of 2", result.stdout)
        root = self.root / vm.OUTPUT_FOLDER
        first, last = sorted(root.rglob("*.mp4"))
        streams = self.streams(first)
        self.assertEqual([s["codec_name"] for s in streams], ["hevc", "aac", "aac"])
        self.assertEqual(streams[0]["avg_frame_rate"], "24/1")
        self.assertEqual(streams[0]["codec_tag_string"], "hvc1")
        self.assertAlmostEqual(vm.probe(last).duration, 1.25, places=2)
        first_size = first.stat().st_size
        result = self.cli(path, "--segment-minutes", "1", "--segments", "2", "--resolution", "source")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(first.stat().st_size, first_size)
        self.assertEqual(len(list(root.rglob("Part_0002.mp4"))), 2)
        result = self.cli(path, "--segment-minutes", "1", "--segments", "2",
                          "--codec", "prores", "--enhance", "--resolution", "1080p", "--fps", "60")
        self.assertEqual(result.returncode, 0, result.stderr)
        prores, = root.rglob("*.mov")
        stream = self.streams(prores)[0]
        self.assertEqual((stream["profile"], stream["width"], stream["height"]), ("HQ", 1920, 1080))
        self.assertEqual((stream["sample_aspect_ratio"], stream["avg_frame_rate"]), ("1:1", "60/1"))

    @unittest.skipUnless(os.environ.get("VIDEOMASTER_HARDWARE_TESTS") == "1", "Requires Apple hardware encoding")
    def test_silent_vfr(self):
        path = self.fixture("silent.mov", "-i", "testsrc2=size=320x240:rate=30:d=1",
                            "-vf", "select='lt(n,10)+not(mod(n,3))'", "-fps_mode", "vfr", "-c:v", "mpeg4")
        result = self.cli(path, "--enhance")
        self.assertEqual(result.returncode, 0, result.stderr)
        output, = (self.root / vm.OUTPUT_FOLDER).rglob("*.mp4")

        def frame_times(media):
            data = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                           "-show_frames", "-show_entries", "frame=pts_time", "-of", "json", media])
            return [float(f["pts_time"]) for f in json.loads(data)["frames"]]

        self.assertEqual(frame_times(path), frame_times(output))
        self.assertEqual(len(self.streams(output)), 1)


if __name__ == "__main__":
    unittest.main()
