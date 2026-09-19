"""Makes `scripts` an importable package, not a namespace portion.

`benchmark_recordings.py` imports the corpus definition from
`recitation_corpus.py`, and without this file that import silently resolves
to a *different* `scripts` package — this conda environment has one in
site-packages, and a namespace portion loses to any regular package later on
the path. One empty module makes ours win deterministically.
"""
