#!/usr/bin/env python3
"""Screenshots of a web page for iterate-with-review.

Desktop (1440 px wide) and mobile (390 px wide), the whole page cut into
screen-height pieces, so text stays readable when a model looks at them.
Motion is reduced and the page is scrolled through once first, so lazy images
and scroll-triggered sections are visible.

Usage:  python screenshots.py URL OUT_DIR [--name home]
Output: OUT_DIR/<name>-desktop-01.png ..., OUT_DIR/<name>-mobile-01.png ...
Needs:  python -m pip install playwright
        python -m playwright install chromium
"""
import argparse
import pathlib
import sys

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("Playwright is missing: python -m pip install playwright && python -m playwright install chromium")

SIZES = [("desktop", 1440, 900, False), ("mobile", 390, 844, True)]


def page_height(page):
    return page.evaluate("Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("out")
    ap.add_argument("--name", default="home", help="page name used in file names (default: home)")
    args = ap.parse_args()

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for label, width, height, mobile in SIZES:
            ctx = browser.new_context(
                viewport={"width": width, "height": height},
                device_scale_factor=1,
                is_mobile=mobile,
                has_touch=mobile,
                reduced_motion="reduce",
            )
            page = ctx.new_page()
            page.goto(args.url, wait_until="networkidle")

            y = 0
            while y < page_height(page):
                page.evaluate(f"window.scrollTo(0, {y})")
                page.wait_for_timeout(150)
                y += height
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(400)

            total = page_height(page)
            count = 0
            for top in range(0, total, height):
                count += 1
                page.screenshot(
                    path=str(out / f"{args.name}-{label}-{count:02d}.png"),
                    full_page=True,
                    clip={"x": 0, "y": top, "width": width, "height": min(height, total - top)},
                )
            print(f"{label}: {count} screenshots, page height {total} px")
            ctx.close()
        browser.close()


if __name__ == "__main__":
    main()
