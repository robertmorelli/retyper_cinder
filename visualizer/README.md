# Typedness graph visualizer

Run from the repository root:

```sh
python visualizer/graph_visualizer_tool.py
```

It opens `http://127.0.0.1:8000`. Choose a benchmark and source variant, then
select annotation- or function-level masking and enter a binary mask. The page
reports the available units and redraws the detyped source graph as the mask
changes. Use `--no-open`, `--host`, or `--port` to change startup behavior.
