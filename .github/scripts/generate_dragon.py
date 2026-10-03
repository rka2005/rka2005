#!/usr/bin/env python3
"""Generate an animated dragon that flies over a GitHub contribution graph,
torching each day with fire. The graph regrows when the dragon leaves.

Usage:
  python generate_dragon.py --user YOUR_USERNAME --out dist     (needs GITHUB_TOKEN)
  python generate_dragon.py --demo --out dist                   (random fake data)
"""
import argparse
import json
import math
import os
import random
import sys
import urllib.request

# ---- layout -----------------------------------------------------------
CELL = 11
STEP = 14
ROWS = 7
MX = 34          # horizontal margin
MY = 40          # vertical margin (room for wings and U-turns)
ARC_R = STEP / 2

# ---- animation --------------------------------------------------------
DURATION = 48.0  # seconds for one full flight
LEAD_IN = 260    # flight path before the grid (off screen)
LEAD_OUT = 320   # flight path after the grid (off screen)
N_SEG = 18       # body segments behind the head
SEG_GAP = 9.0    # px between body segments
FIRE_REACH = 30  # how far ahead of the head the fire burns cells

PALETTES = {
    "light": {
        "levels": ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"],
        "ember": "#ff8c1a",
    },
    "dark": {
        "levels": ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"],
        "ember": "#ff7b00",
    },
}

QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        weeks { contributionDays { contributionCount weekday } }
      }
    }
  }
}
"""


def fetch_grid(user, token):
    body = json.dumps({"query": QUERY, "variables": {"login": user}}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "dragon-generator",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    if data.get("errors") or not data.get("data", {}).get("user"):
        sys.exit(f"GitHub API error: {data.get('errors') or 'user not found'}")
    weeks = data["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    grid = []
    for w in weeks:
        col = [None] * ROWS
        for d in w["contributionDays"]:
            col[d["weekday"]] = d["contributionCount"]
        grid.append(col)
    return grid


def demo_grid(weeks=53):
    rnd = random.Random(7)
    grid = []
    for _ in range(weeks):
        grid.append([0 if rnd.random() < 0.45 else rnd.randint(1, 14) for _ in range(ROWS)])
    return grid


def f(x):
    return f"{x:.4f}".rstrip("0").rstrip(".")


# ---- dragon artwork (local +x is the flying direction) -----------------
WING = (
    "M3,-4 C0,-12 -6,-22 -20,-28 C-16,-21 -17,-18 -12,-15 "
    "C-15,-11 -11,-9 -7,-7 C-8,-5 -4,-4 -2,-3 Z"
)


def head_markup():
    return f"""
<g>
  <animateMotion dur="{DURATION}s" begin="0s" repeatCount="indefinite" rotate="auto"><mpath href="#route" xlink:href="#route"/></animateMotion>
  <g transform="translate(12,0)">
    <g>
      <animateTransform attributeName="transform" type="scale" values="0.75 1;1.15 0.85;0.9 1.1;0.75 1" dur="0.28s" repeatCount="indefinite"/>
      <polygon points="0,0 7,-4 15,-6 24,-2 30,0 24,2 15,6 7,4" fill="#ff5a00" opacity="0.95"/>
      <polygon points="0,0 6,-2 13,-3 20,0 13,3 6,2" fill="#ffd23f"/>
    </g>
  </g>
  <path d="M-3,-5 L-13,-11 L-6,-3 Z M-3,5 L-13,11 L-6,3 Z" fill="#ffb703" stroke="#6a040f" stroke-width="0.6"/>
  <polygon points="13,0 9,-3.5 4,-6 -4,-6.5 -7,-3 -7,3 -4,6.5 4,6 9,3.5" fill="#d00000" stroke="#6a040f" stroke-width="0.8"/>
  <circle cx="4" cy="-3.2" r="1.5" fill="#ffe066"/><circle cx="4" cy="3.2" r="1.5" fill="#ffe066"/>
  <circle cx="4.4" cy="-3.2" r="0.6" fill="#000"/><circle cx="4.4" cy="3.2" r="0.6" fill="#000"/>
  <circle cx="11" cy="-1.3" r="0.6" fill="#6a040f"/><circle cx="11" cy="1.3" r="0.6" fill="#6a040f"/>
</g>"""


def segment_markup(j, begin, radius):
    wings = ""
    if j == 4:
        wings = f"""
  <g>
    <animateTransform attributeName="transform" type="scale" values="1 1;1 0.3;1 1" dur="0.7s" repeatCount="indefinite"/>
    <path d="{WING}" fill="#8d0801" fill-opacity="0.88" stroke="#ffb703" stroke-width="0.8"/>
    <path d="{WING}" transform="scale(1,-1)" fill="#8d0801" fill-opacity="0.88" stroke="#ffb703" stroke-width="0.8"/>
    <path d="M2,-4 L-18,-26 M2,4 L-18,26" stroke="#ffb703" stroke-width="0.6" fill="none"/>
  </g>"""
    tail = ""
    if j == N_SEG:
        tail = '<path d="M0,-2 L-9,-5 L-15,0 L-9,5 L0,2 Z" fill="#ffb703" stroke="#6a040f" stroke-width="0.6"/>'
    r = radius
    return f"""
<g>
  <animateMotion dur="{DURATION}s" begin="{begin:.3f}s" repeatCount="indefinite" rotate="auto"><mpath href="#route" xlink:href="#route"/></animateMotion>{wings}
  {tail}
  <circle r="{f(r)}" fill="#c1121f" stroke="#6a040f" stroke-width="0.7"/>
  <polygon points="{f(r*0.8)},0 0,{f(-r*0.35)} {f(-r*0.8)},0 0,{f(r*0.35)}" fill="#ffb703"/>
</g>"""


def build_svg(grid, palette_name):
    pal = PALETTES[palette_name]
    levels, ember = pal["levels"], pal["ember"]
    weeks = len(grid)

    counts = [c for col in grid for c in col if c]
    top = max(counts) if counts else 1

    def level(c):
        if not c:
            return 0
        r = c / top
        return 1 if r <= 0.25 else 2 if r <= 0.5 else 3 if r <= 0.75 else 4

    def cx(c):
        return MX + c * STEP + CELL / 2

    def cy(r):
        return MY + r * STEP + CELL / 2

    width = 2 * MX + weeks * STEP - (STEP - CELL)
    height = 2 * MY + (ROWS - 1) * STEP + CELL

    # flight path: lead-in, then serpentine down/up each week column
    d = [f"M{f(cx(0) - LEAD_IN)},{f(cy(0))}", f"L{f(cx(0))},{f(cy(0))}"]
    for c in range(weeks):
        if c % 2 == 0:
            d.append(f"L{f(cx(c))},{f(cy(ROWS - 1))}")
            if c < weeks - 1:
                d.append(f"A{f(ARC_R)},{f(ARC_R)} 0 0 0 {f(cx(c + 1))},{f(cy(ROWS - 1))}")
        else:
            d.append(f"L{f(cx(c))},{f(cy(0))}")
            if c < weeks - 1:
                d.append(f"A{f(ARC_R)},{f(ARC_R)} 0 0 1 {f(cx(c + 1))},{f(cy(0))}")
    end_y = cy(ROWS - 1) if (weeks - 1) % 2 == 0 else cy(0)
    d.append(f"L{f(cx(weeks - 1) + LEAD_OUT)},{f(end_y)}")
    route = " ".join(d)

    straight = (ROWS - 1) * STEP
    total = LEAD_IN + weeks * straight + (weeks - 1) * math.pi * ARC_R + LEAD_OUT

    # contribution cells
    cells = []
    latest_burn = 0.0
    for c in range(weeks):
        for r in range(ROWS):
            count = grid[c][r]
            if count is None:
                continue
            x, y = MX + c * STEP, MY + r * STEP
            base = levels[level(count)]
            if not count:
                cells.append(f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2" fill="{base}"/>')
                continue
            along = r * STEP if c % 2 == 0 else (ROWS - 1 - r) * STEP
            dist = LEAD_IN + c * (straight + math.pi * ARC_R) + along
            ta = max(0.001, (dist - FIRE_REACH) / total)
            tb, tc, td = ta + 0.003, ta + 0.012, 0.985
            latest_burn = max(latest_burn, tc)
            keytimes = ";".join(f(t) for t in (0, ta, tb, tc, td, 1))
            values = ";".join((base, base, ember, levels[0], levels[0], base))
            cells.append(
                f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2" fill="{base}">'
                f'<animate attributeName="fill" dur="{DURATION}s" repeatCount="indefinite" '
                f'keyTimes="{keytimes}" values="{values}"/></rect>'
            )
    assert latest_burn < 0.985, "burn timeline overlaps regrow phase"

    # dragon
    lag = SEG_GAP / (total / DURATION)
    parts = []
    for j in range(N_SEG, 0, -1):
        radius = 6.4 - (6.4 - 1.8) * ((j - 1) / (N_SEG - 1))
        parts.append(segment_markup(j, j * lag - DURATION, radius))
    parts.append(head_markup())

    return f"""<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<title>Dragon flying over contribution graph</title>
<defs><path id="route" d="{route}" fill="none"/></defs>
{chr(10).join(cells)}
{''.join(parts)}
</svg>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user")
    ap.add_argument("--out", default="dist")
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()

    if args.demo:
        grid = demo_grid()
    else:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not (args.user and token):
            sys.exit("Need --user and a GITHUB_TOKEN environment variable (or use --demo).")
        grid = fetch_grid(args.user, token)

    os.makedirs(args.out, exist_ok=True)
    for name, fname in (("light", "github-dragon.svg"), ("dark", "github-dragon-dark.svg")):
        with open(os.path.join(args.out, fname), "w", encoding="utf-8") as fh:
            fh.write(build_svg(grid, name))
        print("wrote", os.path.join(args.out, fname))


if __name__ == "__main__":
    main()
