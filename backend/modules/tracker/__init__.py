"""Package `modules.tracker` — Décomposition de l'ancien monolithe tracker.py.

Sous-modules :
  - state      : constantes, paths, logger, heartbeat, CB state, equity state IO
  - market     : heures de marché, prix courant (yfinance / Alpaca)
  - killswitch : daily drawdown, emergency liquidate, blocage trading
  - evaluation : lecture/écriture journal CSV, évaluation SL/TP/trailing
  - cycle      : orchestrateur `run_cycle` appelé par tracker.py

Le fichier `tracker.py` à la racine ne contient plus que le CLI (argparse,
PID lock, setup logger, appel de `cycle.run_cycle`).
"""
