"""The optional streaming path: a live, per-generation drag nowcast (Phase 6).

Separate from the batch pipeline by construction. Only the ``stream`` commands
(``cli_stream``, lazily) and the explorer's live page import it; it writes only
to its own ``stream`` dataset in the lake; the Kafka client it needs is in the
``stream`` dependency group, which the batch image does not install. Removing
this package would leave the batch path exactly as it is.
"""
