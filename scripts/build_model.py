"""Builds the small multilingual embedding file used by app/ml.py.

Source model: sentence-transformers/static-similarity-mrl-multilingual-v1 (Apache-2.0),
a *static* embedding model (one vector per token, no transformer at run time).
It is Matryoshka-trained, so the first N dimensions can be kept. We keep 128
dimensions in float16 -> about 27 MB, fast enough for Render's free CPU.

Run (needs internet + `pip install numpy safetensors huggingface_hub`):
    python scripts/build_model.py app/models
"""
import glob
import json
import os
import sys

import numpy as np

REPO = "sentence-transformers/static-similarity-mrl-multilingual-v1"
DIMS = int(os.environ.get("MP_DIMS", "128"))


def main(out_dir: str) -> None:
    from huggingface_hub import snapshot_download
    from safetensors.numpy import load_file

    path = snapshot_download(REPO, allow_patterns=["*.json", "*.safetensors", "*.txt", "*/*.json", "*/*.safetensors"])
    files = sorted(os.path.relpath(f, path) for f in glob.glob(os.path.join(path, "**", "*"), recursive=True))
    weights = [f for f in glob.glob(os.path.join(path, "**", "*.safetensors"), recursive=True)]
    tok = [f for f in glob.glob(os.path.join(path, "**", "tokenizer.json"), recursive=True)]
    if not weights or not tok:
        raise SystemExit(f"weights/tokenizer not found; files: {files}")
    tensors = load_file(weights[0])
    name, emb = max(tensors.items(), key=lambda kv: kv[1].size)
    emb = emb[:, :DIMS].astype(np.float16)
    os.makedirs(out_dir, exist_ok=True)
    np.save(os.path.join(out_dir, "static_mrl_multilingual_128.npy"), emb)
    with open(tok[0], "rb") as src, open(os.path.join(out_dir, "tokenizer.json"), "wb") as dst:
        dst.write(src.read())
    info = dict(source=REPO, license="Apache-2.0", tensor=name, shape=list(emb.shape), dims=DIMS, files=files)
    with open(os.path.join(out_dir, "model_info.json"), "w") as f:
        json.dump(info, f, indent=1)
    print(json.dumps(info, indent=1))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "app/models")
