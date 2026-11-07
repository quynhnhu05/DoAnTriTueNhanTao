"""Compatibility entry point for the provenance-aware public price crawler.

The old version copied unverified OSM tags into current service prices without
recording a source or timestamp. Run the structured public-site collector here.
"""
from pathlib import Path
import runpy


if __name__ == "__main__":
    script = Path(__file__).parents[1] / "crawl_public_prices.py"
    runpy.run_path(str(script), run_name="__main__")
