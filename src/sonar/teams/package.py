"""Build the Teams app package:  uv run python -m sonar.teams.package

Teams installs a custom app from a zip holding manifest.json (the app's name, description and the Azure Bot's
App ID) and two icons. The zip is written to config/teams.yaml `package_dir` (inside data/, never committed,
because the manifest holds the App ID). Upload it in Teams: Apps > Manage your apps > Upload an app.
The icons are drawn here in code (concentric sonar rings), so no image file lives in the repo."""

import json
import math
import struct
import sys
import zipfile
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sonar import secrets
from sonar.config import TeamsSettings, load_teams_settings

SCHEMA_VERSION = "1.25"  # the first version with supportsChannelFeatures
ACCENT = (11, 110, 121)  # the color icon's background, also Teams' accent color for the app
SCOPES = ["personal", "team", "groupChat"]
Pixel = Callable[[float], tuple[int, int, int, int]]  # distance from the centre -> RGBA


def manifest(teams: TeamsSettings, app_id: str) -> dict[str, Any]:
    if len(teams.app_name) > 30 or len(teams.short_description) > 80 or len(teams.full_description) > 4000:
        raise ValueError("Teams limits: app_name 30, short_description 80, full_description 4000 characters")
    return {
        "$schema": f"https://developer.microsoft.com/json-schemas/teams/v{SCHEMA_VERSION}/MicrosoftTeams.schema.json",
        "manifestVersion": SCHEMA_VERSION,
        "version": teams.app_version,
        "id": app_id,
        "developer": {
            "name": teams.developer_name,
            "websiteUrl": teams.website_url,
            "privacyUrl": teams.privacy_url,
            "termsOfUseUrl": teams.terms_url,
        },
        "name": {"short": teams.app_name, "full": teams.app_name},
        "description": {"short": teams.short_description, "full": teams.full_description},
        "icons": {"color": "color.png", "outline": "outline.png"},
        "accentColor": "#{:02X}{:02X}{:02X}".format(*ACCENT),
        # Usable in private and shared channels too. "tier1" fits because the bot does not use channel membership,
        # files, cross-channel data or per-user-type behavior (Microsoft's "Apps for shared and private channels")
        "supportsChannelFeatures": "tier1",
        "bots": [{
            "botId": app_id,
            "scopes": SCOPES,
            "supportsFiles": True,  # lets the bot offer a PDF in a one-to-one chat (a file consent card)
            "isNotificationOnly": False,
            "commandLists": [{
                "scopes": SCOPES,
                "commands": [
                    {"title": "help", "description": "What you can ask Sonar"},
                    {"title": "reset", "description": "Start a new conversation"},
                ],
            }],
        }],
        "validDomains": [],
    }


def png(size: int, pixel: Pixel) -> bytes:
    """A square RGBA PNG; `pixel` gets each pixel's distance from the centre."""
    centre = (size - 1) / 2
    rows = b"".join(
        b"\x00" + bytes(c for x in range(size) for c in pixel(math.hypot(x - centre, y - centre)))
        for y in range(size)
    )

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def rings(d: float, radii: tuple[float, ...], width: float, dot: float) -> bool:
    return d <= dot or any(abs(d - r) <= width / 2 for r in radii)


def color_icon() -> bytes:
    """192x192: white sonar rings on the accent color (Teams' full-color icon)."""
    white, background = (255, 255, 255, 255), (*ACCENT, 255)
    return png(192, lambda d: white if rings(d, (40, 64, 86), 9, 15) else background)


def outline_icon() -> bytes:
    """32x32: white rings on a transparent background (Teams requires only white and transparent here)."""
    return png(32, lambda d: (255, 255, 255, 255) if rings(d, (8.5, 14), 2, 3) else (0, 0, 0, 0))


def build(teams: TeamsSettings, app_id: str) -> Path:
    teams.package_path.mkdir(parents=True, exist_ok=True)
    path = teams.package_path / f"sonar-teams-{teams.app_version}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest(teams, app_id), indent=2))
        zf.writestr("color.png", color_icon())
        zf.writestr("outline.png", outline_icon())
    return path


def main() -> int:
    teams = load_teams_settings()
    try:
        app_id, _, _ = secrets.teams_credentials()
        path = build(teams, app_id)
    except (RuntimeError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"Wrote {path}\nUpload it in Teams: Apps > Manage your apps > Upload an app > Upload a custom app.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
