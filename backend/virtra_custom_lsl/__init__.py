"""
virtra_custom_lsl — Shared plumbing for the custom, script-driven VirTra LSL stream.

Distinct from the VirTra machine's own native stream (name "V_300", type
"VirTraEvents"). This custom stream (name "virtra_lsl", type "VirTra Events")
is embedded directly into a scenario script to expose semantics the native
stream doesn't carry (e.g. go/no-go classification). See
validate_data/VirTra_Custom_LSL_Stream_Spec.txt for the full schema.

This package is infrastructure, not a cognitive attribute itself — individual
scenario adapters subclass CustomLslSessionBase to consume
this stream with their own trial/metric logic.
"""
