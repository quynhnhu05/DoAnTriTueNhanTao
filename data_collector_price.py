"""Safe entry point for collecting explicitly published prices from venue sites.

This deliberately replaces the old category-based price generator. It never
fills in a guessed price. See data_collector/crawl_public_prices.py.
"""
from pathlib import Path
import runpy


if __name__ == "__main__":
    script = Path(__file__).parent / "data_collector" / "crawl_public_prices.py"
    runpy.run_path(str(script), run_name="__main__")
