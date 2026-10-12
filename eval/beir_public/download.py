"""Downloads a public BEIR dataset into eval/beir_public/_data/ (gitignored --
external data, not generated insight, unlike eval/beir_public/results/, which
IS committed).

Usage:
    uv run python eval/beir_public/download.py scifact
"""

import sys
from pathlib import Path

from beir import util

DATA_DIR = Path(__file__).resolve().parent / "_data"

# Small, CPU-feasible BEIR datasets only -- see eval/beir_public/README.md
# (if written) for why these two were picked over the rest of the BEIR suite.
DATASET_URLS = {
    "scifact": "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip",
    "fiqa": "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/fiqa.zip",
}


def download(dataset: str) -> Path:
    if dataset not in DATASET_URLS:
        sys.exit(f"Unknown dataset {dataset!r}. Known: {sorted(DATASET_URLS)}")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data_path = util.download_and_unzip(DATASET_URLS[dataset], str(DATA_DIR))
    print(f"{dataset} ready at {data_path}")
    return Path(data_path)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: uv run python eval/beir_public/download.py <dataset>")
    download(sys.argv[1])
