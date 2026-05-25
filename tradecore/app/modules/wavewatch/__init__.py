"""WaveWatch — continuous surveillance of Bybit Innovation Zone assets.

Predicts when a wave is forming: combines an accumulation-phase score
(quiet base building) with a wave-onset trigger (volume + range break) to
fire a heads-up alert *before* the move extends.

Module layout:
  universe.py    refresh Bybit Innovation Zone membership, force-subscribe perps to Bybit WS
  scoring.py     pure-function pre-wave score (0..1) from candles + funding
  detector.py    per-tick orchestrator: read universe, score each, evaluate
                 onset, rank, throttle, alert
  router.py      GET /wavewatch/universe, GET /wavewatch/{symbol}/state
"""
