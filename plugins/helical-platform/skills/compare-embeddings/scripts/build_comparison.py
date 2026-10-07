# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "numpy>=1.24",
#     "pydantic>=2",
#     "scikit-learn>=1.3",
#     "umap-learn>=0.5.5",
# ]
# ///
"""Build one interactive HTML page that shows each model's embeddings as a UMAP.

The input is a jobs file the agent writes:

    {"models": {"scGPT": {"embeddings": [
        {"datasource": "evotoken_L5_97aff2", "path": "scgpt_evotoken.npy"},
        {"datasource": "GSE205013 PDAC", "path": "scgpt_pdac.npy"}]}}}

Each embedding is a (cells x dims) .npy. A model's embeddings are stacked, reduced to 50
principal components, and projected with UMAP; the page colours cells by datasource and
switches between models. Plotly is loaded from cdnjs, so the page needs internet access.

    uv run build_comparison.py jobs.json --out explorer.html [--target artifact]
"""

import argparse
import hashlib
import json
import sys
import time
import warnings
from pathlib import Path

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sklearn.decomposition import PCA
from sklearn.neighbors import NearestNeighbors

TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "template.html"
PLACEHOLDER = "/*__DATA__*/null"
ARTIFACT_START, ARTIFACT_END = "<!--artifact:start-->", "<!--artifact:end-->"
PCA_DIMS, NEIGHBORS, SEED = 50, 15, 0


class Embedding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    datasource: str = Field(min_length=1)
    path: Path


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")
    embeddings: list[Embedding] = Field(min_length=1)

    @field_validator("embeddings")
    @classmethod
    def unique_datasources(cls, v):
        names = [e.datasource for e in v]
        if len(set(names)) != len(names):
            raise ValueError(f"datasource names must be unique within a model, got {names}")
        return v


class Jobs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    models: dict[str, Model] = Field(min_length=1)


def load_jobs(path):
    try:
        jobs = Jobs.model_validate_json(path.read_text())
    except ValidationError as e:
        sys.exit(f"{path} is not a valid jobs file:\n{e}")
    base = path.resolve().parent
    for model in jobs.models.values():
        for e in model.embeddings:  # relative paths are relative to the jobs file
            e.path = e.path if e.path.is_absolute() else base / e.path
            if not e.path.is_file():
                sys.exit(f"embedding file not found: {e.path}")
    return jobs


def build_model(name, model):
    mats = []
    for e in model.embeddings:
        x = np.load(e.path, allow_pickle=False)
        if x.ndim != 2:
            sys.exit(f"{name} / {e.datasource}: expected a (cells x dims) matrix, got shape {x.shape}")
        if not np.isfinite(x).all():
            sys.exit(f"{name} / {e.datasource}: the embedding contains NaN or infinite values")
        mats.append(x.astype(np.float32))
    dims = {m.shape[1] for m in mats}
    if len(dims) > 1:
        sys.exit(f"{name}: all of a model's embeddings need the same width, got {sorted(dims)}")
    x = np.vstack(mats)
    source = np.repeat(np.arange(len(mats)), [len(m) for m in mats])

    z = PCA(n_components=PCA_DIMS, random_state=SEED).fit_transform(x) if x.shape[1] > PCA_DIMS else x
    import umap
    warnings.filterwarnings("ignore", message="n_jobs value", category=UserWarning)
    xy = umap.UMAP(n_neighbors=NEIGHBORS, min_dist=0.3, random_state=SEED).fit_transform(z)

    mixing = None
    if len(mats) > 1:  # share of each cell's neighbours that come from its own datasource
        idx = NearestNeighbors(n_neighbors=NEIGHBORS + 1).fit(z).kneighbors(z, return_distance=False)[:, 1:]
        freq = np.bincount(source) / len(source)
        mixing = {"same": round(float((source[idx] == source[:, None]).mean()), 3),
                  "chance": round(float((freq ** 2).sum()), 3)}
    return {
        "name": name, "dim": int(x.shape[1]),
        "datasources": [{"name": e.datasource, "file": e.path.name, "cells": len(m)}
                        for e, m in zip(model.embeddings, mats)],
        "x": np.round(xy[:, 0], 3).tolist(), "y": np.round(xy[:, 1], 3).tolist(),
        "source": source.tolist(), "mixing": mixing,
    }


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("jobs", type=Path, help="jobs JSON file")
    p.add_argument("--out", required=True, type=Path, help="HTML file to write")
    p.add_argument("--target", choices=("file", "artifact"), default="file",
                   help="file: a complete HTML document. artifact: the page body only, for publishing "
                        "as a Claude artifact, which adds its own document wrapper")
    args = p.parse_args(argv)
    t0 = time.time()
    jobs = load_jobs(args.jobs)

    seen = {}  # the same file under two names is usually a repeat run
    for name, model in jobs.models.items():
        for e in model.embeddings:
            digest = hashlib.sha256(e.path.read_bytes()).hexdigest()
            if digest in seen:
                print(f"IDENTICAL files: {seen[digest]} and {name} / {e.datasource} have the same contents")
            seen.setdefault(digest, f"{name} / {e.datasource}")

    models = []
    for name, model in jobs.models.items():
        models.append(build_model(name, model))
        print(f"{name}: {len(models[-1]['x'])} cells x {models[-1]['dim']} dims ({time.time() - t0:.0f}s)", flush=True)

    data = {"models": models, "neighbors": NEIGHBORS, "seed": SEED,
            "generated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())}
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    template = TEMPLATE.read_text()
    if args.target == "artifact":
        template = template[template.index(ARTIFACT_START) + len(ARTIFACT_START):template.index(ARTIFACT_END)].strip() + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(template.replace(PLACEHOLDER, payload))

    # A summary the agent can relay, never the coordinates.
    print(f"\nwrote {args.out} ({args.out.stat().st_size / 1e6:.2f} MB, {len(models)} models, {time.time() - t0:.0f}s)")
    for m in models:
        mix = m["mixing"]
        print(f"  {m['name']}: " + ", ".join(f"{d['name']} ({d['cells']} cells)" for d in m["datasources"])
              + (f"; {mix['same']:.0%} of neighbours share the datasource (chance {mix['chance']:.0%})" if mix else ""))


if __name__ == "__main__":
    main()
