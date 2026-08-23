# Testing

```sh
pytest
python testing/mask_harness.py
python testing/mask_harness.py --annotation --fuzz 10 --no-levels
```

`pytest` runs the graph unit tests. `mask_harness.py` checks benchmark masks;
`data/` stores its regression history. `static_runner.py` is its module
loader.
