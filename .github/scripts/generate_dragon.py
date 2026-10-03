import argparse
import json
import math
import os
import random
import sys
import time
import urllib.request

# ---- layout -----------------------------------------------------------
CELL = 11
STEP = 14
ROWS = 7
MX = 40          # horizontal margin
MY = 46          # vertical margin (room for wings and swoops)

# ---- animation --------------------------------------------------------
SPEED = 240.0    # dragon speed in px per second
N_SEG = 18       # body segments behind the head
SEG_GAP = 9.0    # px between body segments
BODY_LEN = N_SEG * SEG_GAP
FIRE_AHEAD = 30  # how far ahead of the head the flames reach
BURN_R = 11      # flames burn contribution cells within this radius
ENTRY = 110      # how far off screen the dragon starts
# distance after the last burned cell before the graph regrows (tail clears + fade ends)
REGROW_GAP = max(BODY_LEN + 60, 1.6 * SPEED + 10)

PALETTES = {
    "light": {
        "levels": ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"],
        "flash": "#fff1a8",
        "ember": "#ff8c1a",
    },
    "dark": {
        "levels": ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"],
        "flash": "#fff1a8",
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


# ---- data -------------------------------------------------------------
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
    return f"{x:.4f}".rstrip("0").rstrip(".") or "0"


def cx(c):
    return MX + c * STEP + CELL / 2


def cy(r):
    return MY + r * STEP + CELL / 2


# ---- flight planning --------------------------------------------------
def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def seg_dist(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    ln = dx * dx + dy * dy
    if ln == 0:
        return dist(p, a)
    t = max(0, min(1, ((p[0] - ax) * dx + (p[1] - ay) * dy) / ln))
    return dist(p, (ax + t * dx, ay + t * dy))


def catmull_segments(pts):
    """Smooth curve through all waypoints, as a list of cubic Bezier segments."""
    n = len(pts)
    segs = []
    for i in range(n - 1):
        p0 = pts[i - 1] if i > 0 else pts[i]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[i + 2] if i + 2 < n else pts[i + 1]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        segs.append((p1, c1, c2, p2))
    return segs


def bez(p1, c1, c2, p2, t):
    u = 1 - t
    a, b, c, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
    return (a * p1[0] + b * c1[0] + c * c2[0] + d * p2[0],
            a * p1[1] + b * c1[1] + c * c2[1] + d * p2[1])


def simulate(pts, cellmap):
    """Fly the curve; return when (arc length) the flames first reach each cell."""
    burn = {}
    s = 0.0
    wp_s = [0.0]
    prev = pts[0]
    for p1, c1, c2, p2 in catmull_segments(pts):
        approx = dist(p1, c1) + dist(c1, c2) + dist(c2, p2)
        n = max(8, int(approx / 3))
        for k in range(1, n + 1):
            pt = bez(p1, c1, c2, p2, k / n)
            step = dist(prev, pt)
            s += step
            if step > 1e-6:
                ux, uy = (pt[0] - prev[0]) / step, (pt[1] - prev[1]) / step
                for ahead in (0, 14, FIRE_AHEAD):
                    fx, fy = pt[0] + ux * ahead, pt[1] + uy * ahead
                    c0 = int((fx - MX) // STEP)
                    r0 = int((fy - MY) // STEP)
                    for dc in (-1, 0, 1):
                        for dr in (-1, 0, 1):
                            key = (c0 + dc, r0 + dr)
                            if key in cellmap and key not in burn:
                                if math.hypot(fx - cellmap[key][0], fy - cellmap[key][1]) <= BURN_R:
                                    burn[key] = s
            prev = pt
        wp_s.append(s)
    return burn, s, wp_s


def plan_flight(grid, rnd, width, height):
    cellmap = {}
    for c, col in enumerate(grid):
        for r, count in enumerate(col):
            if count:
                cellmap[(c, r)] = (cx(c), cy(r))
    remaining = set(cellmap)

    pos = (-ENTRY, rnd.uniform(MY, height - MY))
    pts = [pos]
    swoop_in = rnd.randint(14, 22)

    while remaining:
        swoop_in -= 1
        if swoop_in <= 0:
            # wild swoop: dive above or below the graph, or cut across it
            swoop_in = rnd.randint(14, 22)
            band = rnd.choice(("top", "bottom", "mid"))
            y = {"top": rnd.uniform(6, MY * 0.6),
                 "bottom": rnd.uniform(height - MY * 0.6, height - 6),
                 "mid": rnd.uniform(MY, height - MY)}[band]
            q = (rnd.uniform(MX, width - MX), y)
            if dist(pos, q) > 120:
                pts.append(q)
                pos = q
            continue

        cands = sorted(remaining, key=lambda k: dist(pos, cellmap[k]))
        idx = min(int(rnd.expovariate(1.8)), len(cands) - 1)
        tgt = cellmap[cands[idx]]

        if rnd.random() < 0.25:  # zig-zag wiggle
            mx, my = (pos[0] + tgt[0]) / 2, (pos[1] + tgt[1]) / 2
            dx, dy = tgt[0] - pos[0], tgt[1] - pos[1]
            ln = math.hypot(dx, dy) or 1
            off = rnd.uniform(-14, 14)
            pts.append((mx - dy / ln * off, my + dx / ln * off))

        pts.append(tgt)
        for k in list(remaining):
            if dist(cellmap[k], tgt) <= BURN_R + 2:
                remaining.discard(k)
        pos = tgt

    exit_pt = (width + BODY_LEN + 140, rnd.uniform(0, height))

    # revisit anything the curve missed, then leave
    for _ in range(5):
        burn, total, wp_s = simulate(pts + [exit_pt], cellmap)
        left = [k for k in cellmap if k not in burn]
        if not left:
            break
        pos = pts[-1]
        while left:
            left.sort(key=lambda k: dist(pos, cellmap[k]))
            nxt = left.pop(0)
            pts.append(cellmap[nxt])
            pos = cellmap[nxt]
            left = [k for k in left if dist(cellmap[k], pos) > BURN_R]

    exits = [exit_pt]
    burn, total, wp_s = simulate(pts + exits, cellmap)
    # keep flying off screen until the whole body is gone and the last
    # burned cell has had time to fade, so the regrow phase is always clean
    for i in range(1, 9):
        last = max(burn.values()) if burn else 0.0
        if total - last >= REGROW_GAP / 0.99:
            break
        exits.append((width + BODY_LEN + 140 + 320 * i, rnd.uniform(-40, height + 40)))
        burn, total, wp_s = simulate(pts + exits, cellmap)
    s_exit_start = wp_s[len(pts) - 1]
    return pts + exits, burn, total, s_exit_start


# ---- dragon artwork (local +x is the flying direction) -----------------
WING = (
    "M3,-4 C0,-12 -6,-24 -22,-31 C-17,-23 -18,-19 -13,-16 "
    "C-16,-12 -12,-10 -8,-8 C-9,-6 -4,-5 -2,-3 Z"
)


def head_markup(dur):
    return f"""
<g>
  <animateMotion dur="{f(dur)}s" begin="0s" repeatCount="indefinite" rotate="auto"><mpath href="#route" xlink:href="#route"/></animateMotion>
  <g transform="translate(12,0)">
    <g>
      <animateTransform attributeName="transform" type="scale" values="0.7 1;1.2 0.85;0.9 1.15;0.7 1" dur="0.26s" repeatCount="indefinite"/>
      <polygon points="0,0 8,-5 18,-7 28,-2 36,0 28,2 18,7 8,5" fill="#ff5a00" opacity="0.95"/>
      <polygon points="0,0 7,-3 15,-4 23,0 15,4 7,3" fill="#ffd23f"/>
    </g>
  </g>
  <path d="M-3,-5 L-13,-11 L-6,-3 Z M-3,5 L-13,11 L-6,3 Z" fill="#ffb703" stroke="#6a040f" stroke-width="0.6"/>
  <polygon points="13,0 9,-3.5 4,-6 -4,-6.5 -7,-3 -7,3 -4,6.5 4,6 9,3.5" fill="#d00000" stroke="#6a040f" stroke-width="0.8"/>
  <circle cx="4" cy="-3.2" r="1.5" fill="#ffe066"/><circle cx="4" cy="3.2" r="1.5" fill="#ffe066"/>
  <circle cx="4.4" cy="-3.2" r="0.6" fill="#000"/><circle cx="4.4" cy="3.2" r="0.6" fill="#000"/>
  <circle cx="11" cy="-1.3" r="0.6" fill="#6a040f"/><circle cx="11" cy="1.3" r="0.6" fill="#6a040f"/>
</g>"""


def segment_markup(j, begin, radius, dur):
    wings = ""
    if j == 4:
        wings = f"""
  <g>
    <animateTransform attributeName="transform" type="scale" values="1 1;1 0.25;1 1" dur="0.6s" repeatCount="indefinite"/>
    <path d="{WING}" fill="#8d0801" fill-opacity="0.88" stroke="#ffb703" stroke-width="0.8"/>
    <path d="{WING}" transform="scale(1,-1)" fill="#8d0801" fill-opacity="0.88" stroke="#ffb703" stroke-width="0.8"/>
    <path d="M2,-4 L-20,-29 M2,4 L-20,29" stroke="#ffb703" stroke-width="0.6" fill="none"/>
  </g>"""
    tail = ""
    if j == N_SEG:
        tail = '<path d="M0,-2 L-9,-5 L-15,0 L-9,5 L0,2 Z" fill="#ffb703" stroke="#6a040f" stroke-width="0.6"/>'
    r = radius
    return f"""
<g>
  <animateMotion dur="{f(dur)}s" begin="{begin:.3f}s" repeatCount="indefinite" rotate="auto"><mpath href="#route" xlink:href="#route"/></animateMotion>{wings}
  {tail}
  <circle r="{f(r)}" fill="#c1121f" stroke="#6a040f" stroke-width="0.7"/>
  <polygon points="{f(r*0.8)},0 0,{f(-r*0.35)} {f(-r*0.8)},0 0,{f(r*0.35)}" fill="#ffb703"/>
</g>"""


def build_svg(grid, palette_name, flight):
    pal = PALETTES[palette_name]
    levels, flash, ember = pal["levels"], pal["flash"], pal["ember"]
    weeks = len(grid)
    waypoints, burn, total, s_exit = flight
    dur = total / SPEED

    counts = [c for col in grid for c in col if c]
    top = max(counts) if counts else 1

    def level(c):
        if not c:
            return 0
        r = c / top
        return 1 if r <= 0.25 else 2 if r <= 0.5 else 3 if r <= 0.75 else 4

    width = 2 * MX + weeks * STEP - (STEP - CELL)
    height = 2 * MY + (ROWS - 1) * STEP + CELL

    # flight path as an SVG path of cubic curves
    d = [f"M{f(waypoints[0][0])},{f(waypoints[0][1])}"]
    for _, c1, c2, p2 in catmull_segments(waypoints):
        d.append(f"C{f(c1[0])},{f(c1[1])} {f(c2[0])},{f(c2[1])} {f(p2[0])},{f(p2[1])}")
    route = " ".join(d)

    # cells regrow once the dragon's tail has left the graph
    last_burn = max(burn.values()) if burn else 0.0
    td = min(0.995, (last_burn + REGROW_GAP) / total)

    cells = []
    latest = 0.0
    for c in range(weeks):
        for r in range(ROWS):
            count = grid[c][r]
            if count is None:
                continue
            x, y = MX + c * STEP, MY + r * STEP
            base = levels[level(count)]
            key = (c, r)
            if not count or key not in burn:
                cells.append(f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2" fill="{base}"/>')
                continue
            ta = max(0.0005, burn[key] / total)
            t1, t2, t3 = ta + 0.12 / dur, ta + 0.5 / dur, ta + 1.6 / dur
            latest = max(latest, t3)
            keytimes = ";".join(f(t) for t in (0, ta, t1, t2, t3, td, 1))
            values = ";".join((base, base, flash, ember, levels[0], levels[0], base))
            cells.append(
                f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2" fill="{base}">'
                f'<animate attributeName="fill" dur="{f(dur)}s" repeatCount="indefinite" '
                f'keyTimes="{keytimes}" values="{values}"/></rect>'
            )
    assert latest < td, "burn timeline overlaps regrow phase"

    lag = SEG_GAP / SPEED
    parts = []
    for j in range(N_SEG, 0, -1):
        radius = 6.4 - (6.4 - 1.8) * ((j - 1) / (N_SEG - 1))
        parts.append(segment_markup(j, j * lag - dur, radius, dur))
    parts.append(head_markup(dur))

    return f"""<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<title>Dragon torching the contribution graph</title>
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
    ap.add_argument("--seed", type=int, help="fixed seed (default: new random route every run)")
    args = ap.parse_args()

    if args.demo:
        grid = demo_grid()
    else:
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not (args.user and token):
            sys.exit("Need --user and a GITHUB_TOKEN environment variable (or use --demo).")
        grid = fetch_grid(args.user, token)

    seed = args.seed if args.seed is not None else int(time.time())
    rnd = random.Random(seed)
    width = 2 * MX + len(grid) * STEP - (STEP - CELL)
    height = 2 * MY + (ROWS - 1) * STEP + CELL
    flight = plan_flight(grid, rnd, width, height)
    burned = len(flight[1])
    total_cells = sum(1 for col in grid for c in col if c)
    print(f"seed={seed} path={flight[2]:.0f}px loop={flight[2] / SPEED:.1f}s burned={burned}/{total_cells}")

    os.makedirs(args.out, exist_ok=True)
    for name, fname in (("light", "github-dragon.svg"), ("dark", "github-dragon-dark.svg")):
        with open(os.path.join(args.out, fname), "w", encoding="utf-8") as fh:
            fh.write(build_svg(grid, name, flight))
        print("wrote", os.path.join(args.out, fname))


if __name__ == "__main__":
    main()
