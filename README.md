# Wallpaper Motor

> A modern, dark-themed PyQt6 GUI application for managing **live animated stream wallpapers** on openSUSE XFCE (and other X11 desktop environments).

![Wallpaper Motor](assets/wallpaper-motor.png)

---

## Features

- 🎬 **Live Stream Wallpapers** — Deploy YouTube live streams or any yt-dlp-compatible URL as your desktop background using `xwinwrap` + `mpv`.
- 📂 **Local Video Support** — Use local MP4/MKV/WebM files as animated wallpapers.
- 📺 **Embedded Preview** — Preview any stream inside the app before deploying it.
- ⭐ **Favorites & Categories** — Organize your library with tags and a favorites system.
- 🔍 **Search** — Filter streams in real time by name or category.
- 🖥️ **Multi-Monitor** — Auto-detects connected screens and supports custom geometry.
- 💤 **Sleep/Wake Handling** — Automatically stops the wallpaper on system suspend and restores it on wake.
- 🔔 **System Tray** — Runs silently in the XFCE system tray with Open/Pause/Stop/Exit controls.

---

## Runtime Dependencies (Host System)

These must be installed on your system — they are **not** bundled in the AppImage:

| Tool | Purpose | Install (openSUSE) |
|---|---|---|
| `mpv` | Video rendering backend | `sudo zypper in mpv` |
| `xwinwrap` | X11 desktop overlay | Build from [source](https://github.com/ujjwal96/xwinwrap) |
| `ffmpeg` | Media processing (used by mpv) | `sudo zypper in ffmpeg` |

The app will alert you with a styled dialog at startup if any of these are missing.

---

## Bundled Dependencies (Inside the AppImage)

| Package | Purpose |
|---|---|
| Python 3.11 | Runtime |
| PyQt6 | GUI framework |
| yt-dlp | Stream URL resolver (used by mpv via `--ytdl-format`) |

---

## Installation

### AppImage (Recommended)

1. Download `Wallpaper-Motor-x86_64.AppImage` from the [Releases](../../releases) page.
2. Make it executable and run:

```bash
chmod +x Wallpaper-Motor-x86_64.AppImage
./Wallpaper-Motor-x86_64.AppImage
```

### From Source

```bash
# Install Python dependencies
pip install PyQt6 yt-dlp

# Run directly
python3 wallpaper_manager.py
```

---

## Wallpaper Command (What Gets Executed)

```bash
xwinwrap -ov -g 2560x1440 -- mpv -wid WID \
  --no-osc --osd-level=0 \
  --input-default-bindings=no --input-vo-keyboard=no \
  --no-audio --keep-open=yes --loop-file=inf \
  --ytdl-format="bestvideo" "[STREAM_URL]"
```

---

## CI/CD

Every push to `master` triggers a GitHub Actions build that produces a portable `Wallpaper-Motor-x86_64.AppImage` artifact. See [`.github/workflows/build-appimage.yml`](.github/workflows/build-appimage.yml).

---

## License

MIT
