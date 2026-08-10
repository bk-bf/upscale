# tools

## Converting a model to ncnn

`upscale` uses `realesrgan-ncnn-vulkan`, which loads ncnn `.param`/`.bin` pairs and
expects the input blob to be named `data` and the output `output`.

The [AnimeJaNai](https://github.com/the-database/mpv-upscale-2x_animejanai) models are
distributed as PyTorch `.pth` (SRVGGNetCompact). To convert:

```bash
python3 -m venv venv && ./venv/bin/pip install torch pnnx
./venv/bin/python trace.py 2x_AnimeJaNai_V2_SuperUltraCompact_100k.pth 24 8 suc.pt
./venv/bin/pnnx suc.pt "inputshape=[1,3,120,160]" "inputshape2=[1,3,200,264]"
sed -e 's/\bin0\b/data/g' -e 's/\bout0\b/output/g' suc.ncnn.param > animejanai-suc.param
cp suc.ncnn.bin animejanai-suc.bin
```

Two shapes are passed to pnnx so it infers *dynamic* dimensions; with one shape it can
bake in a fixed tile size and the model then fails on any other tile.

Name the model anything except `realesr-animevideov3` — that name is special-cased by
the binary into a `<name>-x<scale>.param` path.

Architecture arguments for `trace.py` are `<num_feat> <num_conv>`:

| model | num_feat | num_conv | params |
|---|---|---|---|
| Compact (= `realesr-animevideov3`) | 64 | 16 | 0.60 M |
| UltraCompact | 64 | 8 | 0.30 M |
| SuperUltraCompact | 24 | 8 | 0.05 M |

> A model with 12× fewer parameters is only faster if the GPU is actually saturated —
> see the benchmark table in the main README.

## dedup/

Perceptual frame deduplication: anime is animated on 2s/3s, so ~50% of frames repeat.
Not used by default because it judders on slow pans. `framediff.sh` measures per-frame
changed-pixel fractions in ffmpeg; `mkmap.py` turns that into a reuse map with a
run-length cap. `dedup-thumbnail.py` is an earlier, worse approach kept as a warning: it
compares 64×48 thumbnails, which cannot see a mouth move, so it merges real motion.
