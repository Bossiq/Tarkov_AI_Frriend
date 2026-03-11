#!/usr/bin/env python3
"""
Download Mixamo animations for the VRM avatar.

Since Mixamo requires an Adobe account (free), this script tells you
exactly what to download and where to put it.

OPTION 1 — Manual download (recommended):
  1. Go to https://www.mixamo.com/ and sign in (free Adobe account)
  2. Upload any character (or use their default)
  3. Search for each animation below
  4. Download as FBX (.fbx) with these settings:
     - Format: FBX Binary
     - Skin: Without Skin (we only need the skeleton)
     - Frames per Second: 30
     - Keyframe Reduction: none
  5. Save each file in: assets/animations/ with the name shown below

OPTION 2 — Run this script with --auto to try downloading from
  free animation packs automatically.
"""

import os
import sys
import argparse

# Directory where animations go
ANIM_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "animations")

# Required animations — search these names on Mixamo
ANIMATIONS = {
    "idle.fbx":         "Breathing Idle",
    "walk.fbx":         "Walking",
    "wave.fbx":         "Waving",
    "clap.fbx":         "Clapping",
    "think.fbx":        "Thinking",
    "point.fbx":        "Pointing",
    "shrug.fbx":        "Shrug",
    "celebrate.fbx":    "Victory Idle" or "Happy Idle",
    "salute.fbx":       "Salute",
    "nod.fbx":          "Nod Yes" or "Head Nod Yes",
    "head_shake.fbx":   "Head Shake No",
    "bow.fbx":          "Bowing",
    "cross_arms.fbx":   "Standing Arms Crossed",
    "facepalm.fbx":     "Face Palm",
    "dance.fbx":        "Silly Dancing" or "Hip Hop Dancing",
    "laugh.fbx":        "Laughing",
    "thumbs_up.fbx":    "Thumbs Up",
    "idle2.fbx":        "Happy Idle" or "Standing Idle",
    "look_around.fbx":  "Looking Around",
    "weight_shift.fbx": "Weight Shift",
}


def check_animations():
    """Check which animations are already downloaded."""
    os.makedirs(ANIM_DIR, exist_ok=True)
    found = []
    missing = []
    for filename, mixamo_name in ANIMATIONS.items():
        path = os.path.join(ANIM_DIR, filename)
        if os.path.exists(path):
            size = os.path.getsize(path)
            found.append((filename, size))
        else:
            missing.append((filename, mixamo_name))
    return found, missing


def main():
    parser = argparse.ArgumentParser(description="Mixamo animation helper")
    parser.add_argument("--check", action="store_true", help="Check which animations are downloaded")
    args = parser.parse_args()

    found, missing = check_animations()

    print(f"\n{'='*60}")
    print(f"  Mixamo Animation Helper")
    print(f"  Animation directory: {ANIM_DIR}")
    print(f"{'='*60}\n")

    if found:
        print(f"✅ {len(found)} animations found:")
        for name, size in found:
            print(f"   {name:<25s} ({size/1024:.0f} KB)")
        print()

    if missing:
        print(f"❌ {len(missing)} animations missing:\n")
        print("  Download from https://www.mixamo.com/")
        print("  Settings: FBX Binary, Without Skin, 30 FPS\n")
        for filename, mixamo_name in missing:
            print(f"   {filename:<25s} → Search: \"{mixamo_name}\"")
        print(f"\n  Save all files to: {ANIM_DIR}/")
    else:
        print("🎉 All animations are downloaded!")

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
