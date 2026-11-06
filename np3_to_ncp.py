#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Convert Nikon Imaging Cloud ``.NP3`` (Flexible Color Picture Control) recipes into
classic ``.NCP`` Picture Controls for older Z bodies such as the Z6II.

Why a conversion is needed
--------------------------
``.NP3`` (version 0310) is Nikon's *Flexible Color* container.  It can carry
things classic ``.NCP`` (version 0100) cannot:

* a custom tone curve (NCP can carry this, and we keep it),
* 8-colour Blender (hue/chroma/brightness per colour band),
* 3-range Colour Grading (shadows / midtones / highlights),
* clarity and mid-range sharpening.

The Z6II can load classic ``.NCP`` files, but not Flexible Color.  This script
therefore writes a valid 638-byte NCP that preserves as much as possible:

* the NP3 tone curve, or the contrast/highlight/shadow/white/black sliders,
* Colour Grading brightness is baked into the NCP curve (``--mode enhanced``),
* saturation from the NP3 saturation slider, Blender chroma and Grading chroma,
* sharpening,
* monochrome recipes become a Monochrome NCP with the nearest filter.

Per-colour hue shifts and split-toning cannot be represented by a single 1D NCP
curve; those are necessarily approximate.  Use ``--mode faithful`` to match the
more conservative conversion used by NP3 Lab (no Grading brightness/chroma).

Notes
-----
* The 32 recipes in this repository are version 0310 "Flexible Color" files.
  The parser also accepts older 0300 files.
* Only the built-in creative base ``0x0802`` (Morning) has a small approximation
  table.  Other creative bases fall back to Neutral plus the NP3 tone values;
  extend ``CREATIVE_CURVES`` if you need more.

Usage
-----
    python3 tools/np3_to_ncp.py
    python3 tools/np3_to_ncp.py --mode faithful
    python3 tools/np3_to_ncp.py -i path/to/NP3 -o path/to/NCP --no-card-pack

The script only uses the Python standard library.
"""

from __future__ import annotations

import argparse
import csv
import math
import struct
from pathlib import Path

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

RAW_LENGTH = 257          # NCP tone-curve LUT has 257 entries (0..256)
RAW_MAX = 32767
CURVE_AT = 0x78           # LUT offset in the 638-byte NCP
NCP_SIZE = 638
NCP_NAME_OFF = 0x10
NCP_BASE_OFF = 0x24
NCP_SLIDER_OFF = 0x28

NEUTRAL_BASE = (0x03, 0xC2, 0x02, 0xFF)
MONO_BASE = (0x06, 0x4D, 0x00, 0xFF)

# NP3 base profile numbers that are monochrome looks.
MONO_BASES = {
    0x064D,  # Monochrome
    0x080D, 0x080E, 0x080F, 0x0810, 0x0811, 0x0812, 0x0813, 0x0814,
}

# Map for the report only.
CREATIVE_BASE_NAMES = {
    0x0801: "Dream", 0x0802: "Morning", 0x0803: "Pop", 0x0804: "Sunday",
    0x0805: "Somber", 0x0806: "Drama", 0x0807: "Silence", 0x0808: "Bleach",
    0x0809: "Melancholic", 0x080A: "Pure", 0x080B: "Denim", 0x080C: "Toy",
    0x080D: "Sepia", 0x080E: "Blue", 0x080F: "Red", 0x0810: "Pink",
    0x0811: "Charcoal", 0x0812: "Graphite", 0x0813: "Binary", 0x0814: "Carbon",
}

# A few creative bases can be approximated by a classic base plus a tone curve.
# ``0x0802`` is the built-in "Morning" look: lifted blacks, bright midtones and
# a soft highlight roll-off.  This is only used when the NP3 has no custom curve
# and no tone-slider adjustments of its own.
CREATIVE_CURVES = {
    0x0802: ((0.00, 0.10), (0.25, 0.38), (0.50, 0.65), (0.75, 0.88), (1.00, 1.00)),
}
CREATIVE_NCP_BASES = {
    0x0802: 0x0001,  # Standard
}

BLENDER_COLORS = ("red", "orange", "yellow", "green", "cyan", "blue", "purple", "magenta")
GRADING_RANGES = ("shadows", "midTone", "highlights")


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def smoothstep(a: float, b: float, x: float) -> float:
    if a == b:
        return 1.0 if x >= b else 0.0
    t = clamp((x - a) / (b - a), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def inv_smooth(v: float) -> float:
    """Inverse of smoothstep(0,1,x); flattens contrast while keeping 0/1 fixed."""
    v = clamp(v, 0.0, 1.0)
    return 0.5 - math.sin(math.asin(1.0 - 2.0 * v) / 3.0)


def srgb_to_oklab_l(t: float) -> float:
    """OKLab lightness of a neutral sRGB value, used by the Colour Grading model."""
    lin = t / 12.92 if t <= 0.04045 else ((t + 0.055) / 1.055) ** 2.4
    return max(lin, 0.0) ** (1.0 / 3.0)


def oklab_l_to_srgb(L: float) -> float:
    lin = clamp(L, 0.0, 1.0) ** 3
    return lin * 12.92 if lin <= 0.0031308 else 1.055 * (lin ** (1.0 / 2.4)) - 0.055


# --------------------------------------------------------------------------- #
# NP3 parser
# --------------------------------------------------------------------------- #

def parse_np3(data: bytes) -> dict:
    """Parse a Nikon ``.NP3`` / Flexible Color Picture Control into a dict.

    The file is a tagged container: ``[tag u32be][size u32be][payload]...``
    terminated by a zero tag.  The name and tone-curve records use their own
    fixed layouts inside the payload.
    """
    if len(data) < 16 or data[:4] != b"NCP\x00":
        raise ValueError("not a Nikon Picture Control file (missing NCP\\0 magic)")

    records: dict[int, bytes] = {}
    order: list[tuple[int, int]] = []
    off = 4
    while off + 8 <= len(data):
        tag, size = struct.unpack(">II", data[off:off + 8])
        off += 8
        if tag == 0:
            break
        if size > len(data) - off:
            raise ValueError("truncated record")
        payload = data[off:off + size]
        records[tag] = payload
        order.append((tag, size))
        off += size

    def quarter_value(tag: int) -> float | None:
        """NP3 quarter-step value (type byte 0x04), e.g. sharpening -3..+9."""
        payload = records.get(tag)
        if not payload:
            return None
        v = payload[0]
        if v == 0xFF:
            return None
        if v == 0x00:
            return 0.0
        if v == 0x01:
            return None
        return (v - 0x80) / 4.0

    def plain_value(tag: int) -> float | None:
        """NP3 plain value (type byte 0x00/0x01), e.g. contrast -100..+100."""
        payload = records.get(tag)
        if not payload:
            return None
        v = payload[0]
        if v == 0xFF:
            return None
        if v == 0x00:
            return 0.0
        if v == 0x01:
            return None
        return float(v - 0x80)

    # Name (tag 0x0200, 20 bytes, NUL padded)
    name = ""
    for byte in records.get(0x0200, b""):
        if byte == 0:
            break
        name += chr(byte)

    # Base profile (tag 0x0300, big-endian u16)
    base_bytes = records.get(0x0300, b"\x00\x00")
    base = (base_bytes[0] << 8) | base_bytes[1] if len(base_bytes) >= 2 else 0

    # Tone settings.  Version 0310 stores them in 0x1900..0x1E00; older NP3
    # files use 0x0800..0x0B00.  Prefer the newer tags when present.
    tone_tag_map = {
        "contrast": (0x1900, 0x0800),
        "highlights": (0x1A00, None),
        "shadows": (0x1B00, None),
        "whiteLevel": (0x1C00, None),
        "blackLevel": (0x1D00, None),
        "saturation": (0x1E00, 0x0A00),
    }
    tone: dict[str, float | None] = {}
    for key, (new_tag, old_tag) in tone_tag_map.items():
        if new_tag in records:
            tone[key] = plain_value(new_tag)
        elif old_tag is not None:
            tone[key] = quarter_value(old_tag)
        else:
            tone[key] = None

    params = {
        "name": name,
        "base": base,
        "sharpning": quarter_value(0x0600),
        "clarity": quarter_value(0x0700),
        "midSharp": quarter_value(0x1600),
        "hue": quarter_value(0x0B00),
        "toneCurve": None,
        "colorBlender": {},
        "colorGrading": {},
        **tone,
    }

    # Tone curve record id=2.  Payload: "I0" + 6 level/gamma bytes + point count
    # + 20 x/y pairs + padding + 257 u16be LUT at payload offset 64.
    curve_payload = records.get(2)
    if curve_payload and len(curve_payload) >= 64 + RAW_LENGTH * 2:
        raw = [
            (curve_payload[64 + 2 * i] << 8) | curve_payload[65 + 2 * i]
            for i in range(RAW_LENGTH)
        ]
        params["toneCurve"] = {"raw": raw}
        # The camera ignores the tone sliders when a custom curve is present.
        for key in ("contrast", "highlights", "shadows", "whiteLevel", "blackLevel"):
            params[key] = 0

    # Colour Blender (tag 0x1F00): 8 colours x 3 signed bytes
    blender_payload = records.get(0x1F00)
    if blender_payload and len(blender_payload) >= 24:
        for i, colour in enumerate(BLENDER_COLORS):
            h, c, b = blender_payload[i * 3:i * 3 + 3]
            params["colorBlender"][colour] = {
                "hue": h - 0x80,
                "chroma": c - 0x80,
                "brightness": b - 0x80,
            }

    # Colour Grading (tag 0x2000): 3 ranges x (u16be hue, chroma, brightness),
    # then flags, blending, flag, balance, flags.
    grading_payload = records.get(0x2000)
    if grading_payload and len(grading_payload) >= 16:
        for i, rng in enumerate(("highlights", "midTone", "shadows")):
            o = i * 4
            params["colorGrading"][rng] = {
                "hue": ((grading_payload[o] & 0x0F) << 8) | grading_payload[o + 1],
                "chroma": grading_payload[o + 2] - 0x80,
                "brightness": grading_payload[o + 3] - 0x80,
            }
        if len(grading_payload) >= 20:
            params["colorGrading"]["blending"] = grading_payload[16] - 0x80
            params["colorGrading"]["balance"] = grading_payload[18] - 0x80

    params["_records"] = order
    return params


# --------------------------------------------------------------------------- #
# Conversion
# --------------------------------------------------------------------------- #

def tone_at(x: float, p: dict) -> float:
    """Tone-slider model used by the NP3 preview (contrast/highlights/shadows/...)."""
    k = (p.get("contrast") or 0) / 100.0
    if k >= 0:
        c = x + (smoothstep(0.0, 1.0, x) - x) * k
    else:
        c = x + (inv_smooth(x) - x) * (-k) * 0.85

    d = ((p.get("highlights") or 0) / 100.0) * 0.24 * smoothstep(0.45, 1.0, c)
    d += ((p.get("shadows") or 0) / 100.0) * 0.20 * smoothstep(0.0, 0.10, c) * (1.0 - smoothstep(0.10, 0.55, c))
    d += ((p.get("whiteLevel") or 0) / 100.0) * 0.14 * smoothstep(0.60, 1.0, c)
    d += ((p.get("blackLevel") or 0) / 100.0) * 0.10 * (1.0 - smoothstep(0.0, 0.35, c))
    return clamp(c + d, 0.0, 1.0)


def apply_grading_lightness(tone_curve: list[float], grading: dict) -> list[float]:
    """Bake Colour Grading brightness into a neutral tone curve.

    NP3's Colour Grading has separate lightness adjustments for shadows,
    midtones and highlights.  A single NCP curve cannot do split toning, but it
    *can* carry the lightness part.  We use the same smooth weights as the
    Flexible Color preview model (blending widens the overlap, balance moves
    the split).
    """
    if not grading:
        return tone_curve
    gs = (grading.get("shadows") or {}).get("brightness", 0)
    gm = (grading.get("midTone") or {}).get("brightness", 0)
    gh = (grading.get("highlights") or {}).get("brightness", 0)
    if gs == 0 and gm == 0 and gh == 0:
        return tone_curve

    blending = grading.get("blending", 50)
    balance = grading.get("balance", 0)
    bw = 0.05 + blending / 100.0 * 0.25
    s1 = 0.36 + balance / 100.0 * 0.15
    s2 = 0.70 + balance / 100.0 * 0.15

    out: list[float] = []
    for t in tone_curve:
        L = srgb_to_oklab_l(t)
        w_shadows = 1.0 - smoothstep(s1 - bw, s1 + bw, L)
        w_highlights = smoothstep(s2 - bw, s2 + bw, L)
        w_mid = clamp(1.0 - w_shadows - w_highlights, 0.0, 1.0)
        dL = (gs * w_shadows + gm * w_mid + gh * w_highlights) / 100.0 * 0.10
        out.append(clamp(oklab_l_to_srgb(L + dL), 0.0, 1.0))
    return out


def curve_to_lut(curve: list[float]) -> list[int]:
    return [int(round(clamp(v, 0.0, 1.0) * RAW_MAX)) for v in curve]


def lut_to_points(lut: list[int]) -> list[tuple[int, int]]:
    points = []
    for x in (0, 32, 64, 96, 128, 160, 192, 224, 255):
        idx = int(round(x * 256 / 255))
        y = int(round((lut[idx] / RAW_MAX) * 255))
        points.append((x, y))
    return points


def interpolate_curve(points: tuple[tuple[float, float], ...]) -> list[float]:
    """Piecewise-linear interpolation of (x, y) control points to 257 samples."""
    out: list[float] = []
    for i in range(RAW_LENGTH):
        x = i / 256.0
        for j in range(len(points) - 1):
            x0, y0 = points[j]
            x1, y1 = points[j + 1]
            if x0 <= x <= x1:
                t = 0.0 if x1 == x0 else (x - x0) / (x1 - x0)
                out.append(clamp(y0 + (y1 - y0) * t, 0.0, 1.0))
                break
        else:
            out.append(points[-1][1])
    return out


def to_ncp_params(p: dict, mode: str = "enhanced") -> dict:
    """Convert parsed NP3 values into the fields of a classic NCP."""
    mono = (p.get("saturation") is not None and p["saturation"] <= -100) or p.get("base") in MONO_BASES

    # --- tone curve --------------------------------------------------------
    if p.get("toneCurve"):
        tone = [v / RAW_MAX for v in p["toneCurve"]["raw"]]
    else:
        tone = [tone_at(i / 256.0, p) for i in range(RAW_LENGTH)]

    # Approximate a few built-in creative bases that have no NCP equivalent.
    base = p.get("base") or 0
    ncp_base = CREATIVE_NCP_BASES.get(base, 0x03C2) if mode == "enhanced" else 0x03C2
    if (
        mode == "enhanced"
        and not p.get("toneCurve")
        and base in CREATIVE_CURVES
        and all((p.get(k) or 0) == 0 for k in ("contrast", "highlights", "shadows", "whiteLevel", "blackLevel"))
    ):
        tone = interpolate_curve(CREATIVE_CURVES[base])

    if mode == "enhanced":
        tone = apply_grading_lightness(tone, p.get("colorGrading") or {})

    lut = curve_to_lut(tone)

    # --- saturation --------------------------------------------------------
    blender = p.get("colorBlender") or {}
    blender_chroma = sum((blender.get(c) or {}).get("chroma", 0) for c in BLENDER_COLORS) / 8.0
    grading = p.get("colorGrading") or {}
    grading_chroma = sum((grading.get(r) or {}).get("chroma", 0) for r in GRADING_RANGES) / 3.0

    # Flexible Color saturation is -100..100; one classic NCP step is 100/3.
    sat_units = (p.get("saturation") or 0) + blender_chroma * 0.85
    if mode == "enhanced":
        sat_units += grading_chroma * 0.5
    ncp_sat = 0 if mono else int(round(clamp(sat_units / (100.0 / 3.0), -3, 3)))

    # --- monochrome filter -------------------------------------------------
    def light(colour: str) -> int:
        return (blender.get(colour) or {}).get("brightness", 0)

    warm = (light("red") + light("orange") + light("yellow")) / 3.0 - (light("blue") + light("cyan")) / 2.0
    if mono:
        if light("green") - light("red") > 25:
            filt = 4
        elif warm > 45:
            filt = 3
        elif warm > 25:
            filt = 2
        elif warm > 10:
            filt = 1
        else:
            filt = 0
    else:
        filt = 0

    sharp = p.get("sharpning")
    sharpening = int(round(clamp(sharp if sharp is not None else 2, 0, 9)))

    return {
        "mono": mono,
        "base": 0x064D if mono else ncp_base,
        "sharpening": sharpening,
        "saturation": ncp_sat,
        "hue": 0,
        "filter": filt,
        "toning": 0,
        "curve": lut,
        "points": lut_to_points(lut),
    }


# --------------------------------------------------------------------------- #
# NCP writer
# --------------------------------------------------------------------------- #

def sanitize_name(name: str) -> str:
    """Nikon's name field is printable ASCII, max 19 characters."""
    cleaned = "".join(ch if 0x20 <= ord(ch) <= 0x7E else "_" for ch in name)
    cleaned = cleaned.strip()[:19]
    return cleaned or "CONVERTED"


def ncp_bytes(name: str, n: dict) -> bytes:
    """Build a 638-byte classic NCP (version 0100) with a custom curve."""
    b = bytearray(NCP_SIZE)

    # Record 1: "NCP\0" + id 1 + size 36 + version/name/base/sliders
    b[0:4] = b"NCP\x00"
    b[4:12] = bytes((0, 0, 0, 1, 0, 0, 0, 0x24))
    b[12:16] = b"0100"
    clean = sanitize_name(name).encode("latin-1", "replace")
    b[NCP_NAME_OFF:NCP_NAME_OFF + len(clean)] = clean

    if n["mono"]:
        b[NCP_BASE_OFF:NCP_BASE_OFF + 4] = bytes(MONO_BASE)
    else:
        base = n.get("base", 0x03C2)
        b[NCP_BASE_OFF:NCP_BASE_OFF + 2] = bytes(((base >> 8) & 0xFF, base & 0xFF))
        b[NCP_BASE_OFF + 2:NCP_BASE_OFF + 4] = bytes((0x02, 0xFF))
    s = lambda v: 0x80 + v  # noqa: E731
    b[0x28] = s(n["sharpening"])
    b[0x29] = 0x01                       # contrast handled by the curve
    b[0x2A] = 0x01                       # brightness handled by the curve
    b[0x2B] = 0xFF if n["mono"] else s(n["saturation"])
    b[0x2C] = 0xFF if n["mono"] else s(n["hue"])
    b[0x2D] = s(n["filter"]) if n["mono"] else 0xFF
    b[0x2E] = s(n["toning"]) if n["mono"] else 0xFF
    b[0x2F] = 0xFF

    # Record 2: id 2 + size 578 + curve payload
    b[0x30:0x40] = bytes((
        0, 0, 0, 2,                     # record id
        0, 0, 0x02, 0x42,               # size 578
        0x49, 0x30,                     # "I0"
        0x00, 0xFF, 0x00, 0xFF,         # input/output levels
        0x01, 0x00,                     # gamma 1.00
    ))
    points = n["points"][:20]
    b[0x40] = len(points)
    for i, (x, y) in enumerate(points):
        b[0x41 + 2 * i] = x
        b[0x42 + 2 * i] = y

    for i, value in enumerate(n["curve"]):
        value = int(clamp(round(value), 0, RAW_MAX))
        b[CURVE_AT + 2 * i] = (value >> 8) & 0xFF
        b[CURVE_AT + 2 * i + 1] = value & 0xFF

    # Terminator: u32be 0
    b[-4:] = b"\x00\x00\x00\x00"
    return bytes(b)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def find_np3_files(folder: Path) -> list[Path]:
    return sorted(
        (p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".np3"),
        key=lambda p: p.name.lower(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert Nikon NP3 recipes to classic NCP for Z6II / older Z bodies.")
    parser.add_argument("-i", "--input", default="NP3", help="folder containing .NP3 files (default: NP3)")
    parser.add_argument("-o", "--output", default="NCP", help="folder for converted .NCP files (default: NCP)")
    parser.add_argument("--card-pack", default="NIKON/CUSTOMPC", help="camera card folder for PICCONxx.NCP files (default: NIKON/CUSTOMPC)")
    parser.add_argument("--no-card-pack", action="store_true", help="do not write the PICCONxx.NCP card pack")
    parser.add_argument("--mode", choices=("enhanced", "faithful"), default="enhanced",
                        help="enhanced bakes Colour Grading brightness/chroma into the NCP; faithful matches NP3 Lab (default: enhanced)")
    parser.add_argument("--report", default=None, help="CSV report path (default: <output>/conversion_report.csv)")
    args = parser.parse_args()

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    if not input_dir.is_dir():
        parser.error(f"input folder not found: {input_dir}")

    files = find_np3_files(input_dir)
    if not files:
        parser.error(f"no .NP3 files found in {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    card_dir = Path(args.card_pack)
    if not args.no_card_pack:
        card_dir.mkdir(parents=True, exist_ok=True)

    report_path = Path(args.report) if args.report else output_dir / "conversion_report.csv"
    rows = []
    ok = 0
    failed = 0

    for index, src in enumerate(files, start=1):
        try:
            parsed = parse_np3(src.read_bytes())
            ncp = to_ncp_params(parsed, mode=args.mode)
            data = ncp_bytes(parsed["name"], ncp)

            out_path = output_dir / (src.stem + ".NCP")
            out_path.write_bytes(data)

            card_name = ""
            if not args.no_card_pack:
                card_name = f"PICCON{index:02d}.NCP"
                (card_dir / card_name).write_bytes(data)

            base = parsed["base"]
            kind = "Flexible Color" if base == 0x0020 else CREATIVE_BASE_NAMES.get(base, f"0x{base:04X}")
            notes = []
            if base == 0x0020:
                notes.append("hue/split-tone approximate")
            else:
                notes.append("creative base approximate")
                if base in CREATIVE_CURVES:
                    notes.append("creative curve approximated")
            if parsed.get("clarity") not in (None, 0, 0.0):
                notes.append("clarity dropped")
            if parsed.get("midSharp") not in (None, 0, 0.0):
                notes.append("mid-sharpening dropped")
            rows.append({
                "source": src.name,
                "internal_name": parsed["name"],
                "base": f"0x{base:04X}",
                "base_kind": kind,
                "tone_curve": "yes" if parsed.get("toneCurve") else "no",
                "sharpening": ncp["sharpening"],
                "saturation": ncp["saturation"],
                "mono": "yes" if ncp["mono"] else "no",
                "mode": args.mode,
                "output": out_path.name,
                "card_file": card_name,
                "notes": "; ".join(notes),
            })
            ok += 1
            print(f"[{index:02d}] {src.name} -> {out_path.name}")
        except Exception as exc:  # keep going for a batch
            failed += 1
            print(f"[!!] {src.name}: {exc}", file=sys.stderr)

    with report_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=[
            "source", "internal_name", "base", "base_kind", "tone_curve",
            "sharpening", "saturation", "mono", "mode", "output", "card_file", "notes",
        ])
        writer.writeheader()
        writer.writerows(rows)

    print()
    print(f"Converted: {ok}   Failed: {failed}")
    print(f"NCP folder:   {output_dir}")
    if not args.no_card_pack:
        print(f"Card pack:    {card_dir}  (copy the NIKON folder to the card root)")
    print(f"Report:       {report_path}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
