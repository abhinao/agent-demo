# scenarios.py
# Two demo scenarios. Each is pure *presentation* + *tiny fake payloads*.
# The backend owns the log-line text and the terminal status text; the frontend
# owns node/agent labels (it already has them, keyed by scenario name).
#
# Nothing here is real: `payload` values are the "work product" the agents drop
# on the blackboard. They are hardcoded strings / dicts on purpose — the point of
# the sandbox is topology resilience, not real output. Product names are generic
# stand-ins (customer data platform, content studio, review tool, ...) — swap in
# your own stack's services if you want.

SCENARIOS = {
    "campaign": {
        "domain": "marketing",
        # FIXED capability catalog the planner composes from (the model may only
        # use these ids, or "custom"). node/role/payload are presentation; desc is
        # what the model reads.
        "catalog": {
            "audience": {"node": "res",  "role": "res",  "desc": "Build a target audience in the customer data platform",
                         "payload": "48,210 profiles", "state": {"cdp_segment": "seg:9942", "profiles": 48210}},
            "content":  {"node": "dra",  "role": "dra",  "desc": "Generate campaign creative in the content studio",
                         "payload": "3 variants", "state": {"content_asset": "asset:7c31-hero", "variants": 3}},
            "brand":    {"node": "val",  "role": "val",  "desc": "Run brand + legal compliance review",
                         "payload": "brand+legal pass", "state": {"review_task": "REV-4471", "status": "pass"}},
            "activate": {"node": "resp", "role": "done", "desc": "Activate the journey in the journey engine",
                         "payload": "journey live", "state": {"journey_id": "JRN-2205", "state": "live"}},
            "segment_insights": {"node": "res", "role": "res", "desc": "Analyze segments & propensity in the analytics platform",
                         "payload": "top 3 segments", "state": {"analytics_segment": "seg:88a1-lapsed", "top_segments": 3}},
            "offer_strategy": {"node": "dra", "role": "dra", "desc": "Design the offer & incentive",
                         "payload": "15% + free shipping", "state": {"offer_id": "OFR-233", "offer": "15pct+free_ship"}},
            "channel_plan": {"node": "dra", "role": "dra", "desc": "Plan the channel mix & cadence",
                         "payload": "email · push · web", "state": {"channels": ["email", "push", "web"], "cadence": "3-touch"}},
            "personalize": {"node": "dra", "role": "dra", "desc": "Personalize variants per segment in the content studio",
                         "payload": "9 variants", "state": {"content_fragments": "frag:pers-6610", "variants": 9}},
            "experiment": {"node": "val", "role": "val", "desc": "Design an A/B/n test with a holdout",
                         "payload": "3 cells · 10% holdout", "state": {"experiment_id": "EXP-1187", "cells": 3, "holdout": 0.1}},
            "measure": {"node": "val", "role": "val", "desc": "Wire measurement & attribution",
                         "payload": "goals + attribution", "state": {"analytics_project": "proj:MX-77", "attribution": "data-driven"}},
        },
        # canned plan used when the model is unavailable / times out / returns junk
        "recorded_plan": [
            {"id": "s1", "capability": "audience", "title": "build the audience",     "depends_on": []},
            {"id": "s2", "capability": "content",  "title": "generate the creative",  "depends_on": []},
            {"id": "s3", "capability": "brand",    "title": "brand + legal check",    "depends_on": ["s1", "s2"]},
            {"id": "s4", "capability": "activate", "title": "activate the journey",   "depends_on": ["s3"]},
        ],
        # what the marketer asked for (goes onto the blackboard to kick things off)
        "request": "win-back journey for cart-abandoners before the weekend",
        # tiny fake work products dropped on the blackboard by each worker
        "payload": {
            "research": {"segment": "cart-abandoners", "count": 48210, "window_days": 14},
            "draft": "3 variants · subject lines + hero copy",
            "validation": "brand+legal: pass",
            "activation": {"journey_id": "JRN-2205", "enrolled": 48210, "state": "live"},
        },
        "log": {
            "start":    "case received — win-back journey · deadline Fri",
            "dispatch": "two jobs: build the audience, build the creative. dispatching.",
            "res":      "audience built in the customer data platform — 48,210 cart-abandoners, last 14 days.",
            "dra":      "the content studio assembled 3 variants — subject lines + hero copy.",
            "val":      "brand + legal check passed. offer within policy. cleared to activate.",
            "merge":    "merging results → activating the journey.",
            "done":     "journey live in the journey engine. 48,210 enrolled. campaign shipped.",
            # boss killed → nobody drives it
            "stall1":   "⚠ orchestrator offline. nobody owns this activation.",
            "stall2":   "brand check never triggered — journey not activated. launch stuck.",
            # boss killed in mesh → peers keep going on their own
            "rrWarn":   "⚠ orchestrator offline. agents coordinating peer-to-peer.",
            "rrHandoff":"content → brand · direct handoff, no coordinator needed.",
            "rrVal":    "brand check passed. activation triggered directly.",
            "rrDone":   "journey live without a coordinator. campaign shipped.",
            # terminal status-bar text
            "doneStatus":  "delivered — case complete",
            "stallStatus": "case stalled — nobody owns it",
            "rrStatus":    "recovered — finished without the boss",
        },
    },
    "creative": {
        "domain": "creative production",
        "catalog": {
            "generate": {"node": "res",  "role": "res",  "desc": "Generate on-brand art with the image model",
                         "payload": "4 options", "state": {"image_job": "img:5521", "options": 4}},
            "compose":  {"node": "dra",  "role": "dra",  "desc": "Compose layouts in the layout tool",
                         "payload": "3 sizes", "state": {"layout_doc": "doc:9d2a", "sizes": 3}},
            "review":   {"node": "val",  "role": "val",  "desc": "Run brand review in the review tool",
                         "payload": "approved", "state": {"review_asset": "rev:7788", "status": "approved"}},
            "deliver":  {"node": "resp", "role": "done", "desc": "Deliver the final asset",
                         "payload": "shipped", "state": {"delivery_asset": "asset:ship-3140", "state": "shipped"}},
            "moodboard": {"node": "res", "role": "res", "desc": "Assemble a moodboard & references in the moodboard tool",
                         "payload": "12 references", "state": {"moodboard_id": "board:2203", "refs": 12}},
            "copywrite": {"node": "dra", "role": "dra", "desc": "Write headline & body copy",
                         "payload": "3 headlines", "state": {"copy_doc": "doc:copy-31", "headlines": 3}},
            "resize":   {"node": "dra", "role": "dra", "desc": "Adapt sizes & aspect ratios in the layout tool",
                         "payload": "6 sizes", "state": {"layout_variants": "var:sz-88", "sizes": 6}},
            "localize": {"node": "dra", "role": "dra", "desc": "Localize copy & layout",
                         "payload": "EN · FR · DE", "state": {"locales": ["en", "fr", "de"]}},
            "legal_check": {"node": "val", "role": "val", "desc": "Rights & legal clearance",
                         "payload": "cleared", "state": {"review_task": "REV-9903", "status": "cleared"}},
            "package":  {"node": "val", "role": "val", "desc": "Package deliverables for handoff in the review tool",
                         "payload": "zip + links", "state": {"review_share": "share:4412", "links": 3}},
        },
        "recorded_plan": [
            {"id": "s1", "capability": "generate", "title": "generate the art",    "depends_on": []},
            {"id": "s2", "capability": "compose",  "title": "compose the layouts", "depends_on": ["s1"]},
            {"id": "s3", "capability": "review",   "title": "brand review",        "depends_on": ["s2"]},
            {"id": "s4", "capability": "deliver",  "title": "deliver the asset",   "depends_on": ["s3"]},
        ],
        "request": "hero banner for the fall launch, 3 sizes, by EOD",
        "payload": {
            "research": {"assets": 4, "source": "image model"},
            "draft": "banner composed · 3 sizes",
            "validation": "review tool: approved",
            "activation": {"delivery_asset": "asset:ship-3140", "sizes": 3, "state": "shipped"},
        },
        "log": {
            "start":    "case received — hero banner · 3 sizes · deadline EOD",
            "dispatch": "two jobs: generate art, compose layouts. dispatching.",
            "res":      "the image model generated 4 on-brand hero options.",
            "dra":      "the layout tool composed the banner in 3 sizes.",
            "val":      "review passed — brand approved, no legal flags. cleared to ship.",
            "merge":    "merging results → delivering the asset.",
            "done":     "banner delivered in 3 sizes. shipped before EOD.",
            "stall1":   "⚠ production lead offline. nobody owns delivery.",
            "stall2":   "review never triggered — banner not shipped. deadline blown.",
            "rrWarn":   "⚠ production lead offline. agents coordinating peer-to-peer.",
            "rrHandoff":"compose → review · direct handoff, no coordinator needed.",
            "rrVal":    "brand approved. delivery triggered directly.",
            "rrDone":   "banner shipped without a coordinator.",
            "doneStatus":  "delivered — case complete",
            "stallStatus": "case stalled — nobody owns it",
            "rrStatus":    "recovered — finished without the boss",
        },
    },
}
